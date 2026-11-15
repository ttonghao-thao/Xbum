# 运行、恢复与数据生命周期

`make start` 监督 API、单 Worker 与必要时的本机 Ollama。`make dev` 额外启动 Vite。`make worker` 仅用于单独启动 Worker，不能与监督命令重复运行。Worker 全局文件锁保证一个重模型任务，SQLite 每个连接设置 WAL 与外键。

## 启动与停止

从仓库目录启动：

```bash
make start
# 开发时使用 make dev，不要与 make start 同时运行
```

启动器先探测本机 Ollama：已运行则复用；未运行且 PATH 中有 `ollama` 时启动 `ollama serve`。模型按任务加载，项目请求使用 `keep_alive=0`，任务完成后卸载模型；Ollama 服务进程可继续监听。

**前台运行：**在启动终端按 **Ctrl+C**。启动器会停止自己的 API、Worker、Vite（仅 dev）和 Ollama 子进程；原本已由其他终端、应用或服务管理器启动的 Ollama 不会被它关闭。单独用 `make worker` 启动时，也在对应终端按 Ctrl+C。

**后台运行或找不到原终端：**先查启动器，而不是直接终止 Worker 子进程：

```bash
ps -ax -o pid=,command= | rg '[s]cripts/run\.py'
```

下方命令中的 `XBUM_PID` 必须替换为上一步找到的实际数字。先用 `lsof` 核对该 PID 的 `cwd` 是本仓库，再执行停止命令；有多个项目时逐个确认，不要批量终止所有 Python 进程。

```bash
lsof -a -p XBUM_PID -d cwd
kill -TERM XBUM_PID
```

`SIGTERM` 会走启动器的子进程清理流程，与 Ctrl+C 一样关闭本次启动的服务。PID 每次启动可能不同，不能复用旧 PID。当前 Makefile 没有 `make stop` 目标。

**仅释放模型内存：**先查看已加载模型，然后停止相应模型：

```bash
ollama ps
ollama stop qwen3-vl:2b-instruct
ollama ps
```

模型名须与 `ollama ps` 的 NAME 列一致，以上是本项目默认模型。只有表头表示当前没有模型驻留；这不代表 Ollama 后台服务已关闭。Worker 仍在处理任务时会按需重新加载模型；需要保持停用时可先在「处理任务」暂停领取，或停止 Xbum。暂停领取不会中断已经运行的任务。

**关闭独立运行的 Ollama 服务：**若是在另一终端手动执行 `ollama serve`，在那个终端按 Ctrl+C。若它在后台，默认端口为 11434，可先查监听进程，再将 `OLLAMA_PID` 替换为核对后的实际数字：

```bash
lsof -nP -iTCP:11434 -sTCP:LISTEN
kill -TERM OLLAMA_PID
lsof -nP -iTCP:11434 -sTCP:LISTEN
```

最后一次 `lsof` 无输出表示该端口没有监听。若配置了其他端口，应查询实际端口。由应用或服务管理器托管的 Ollama 应通过原管理方式退出，否则可能被自动重新启动。由 Xbum 启动器管理的 Ollama 应随 Xbum 整体停止；单独终止它会使启动器检测到子进程退出，并关闭其余子进程。

以上操作不删除照片、数据库或模型权重；中断的持久任务会在下次 Worker 启动时恢复。不要使用 `ollama rm` 代替停止命令，它用于删除本地模型。

## 日常运行

每天家庭当地配置小时（默认 04:00）生成列表。新增分析结果合并为 15 秒去抖的事件/推荐维护任务，避免每张图重复全库计算。照片即刻可浏览。列表保留配置快照；展示在一轮结束时切换排序，撤权/隐藏/质量失效及时剔除。

## 任务与恢复

queued → running → succeeded，或 retry → failed；关闭能力产生 skipped，目标删除产生 cancelled。领取由数据库条件更新原子执行，含 lease_token、lease_until、次数与下次运行时间；有限指数退避。全局 Worker 锁持有者重启时恢复 running。幂等键防止同一步骤重复创建；重跑产生新修订。

管理员可暂停领取，当前任务完成后生效。连接/缺失/JSON/资源错误有脱敏原因，可重试，不回退为 fake。元数据重跑校验原图 SHA，重提取非锁定字段并原子再生缩略图。子进程超时终止进程组；服务停止会清理当前推理子进程。

AI 故障不阻止上传、普通筛选、浏览、质量评价、推荐或播放。语义模型未就绪时先显示文字/结构化结果；语音模型未安装明确提示，不伪造转写。

## 备份与恢复

`make backup` 或 `scripts/backup.sh <受保护目录>`。维护锁协调 API/Worker，快照内所有文件 SHA-256，SQLite backup API 保存并执行 integrity_check / foreign_key_check。

停止服务后 `scripts/restore.sh /绝对路径/备份.tar.gz`，输入 RESTORE。恢复拒绝仍运行的服务、目录逃逸、符号/硬链接、设备文件、哈希不符与数据库校验失败。旧 DATA_DIR 改名保留，恢复目录重设权限。DATABASE_URL 必须在 DATA_DIR，且与备份的相对位置一致。

备份含口令哈希、注释及人脸特征。脚本提供 0600/0700 文件保护，**不提供加密**；请用 FileVault 或加密外部卷。授权导出仅打包当前共享原图与相关元数据；隐私版本变化后，旧导出下载失效。

## 隐私与删除

- private 仅上传者；family 明确共享。隐藏、回收站不进入展示、搜索或新故事。
- 撤回共享即时影响后续 API，历史推荐和故事视频也重新校验。浏览器每 2 秒验证展示资格；已经下载的内容不可远程销毁。API 不可达时停止显示缓存。
- 软删除可恢复。永久删除须先软删除，再以照片 ID 确认；原图、衍生图、向量、人脸、事件/故事/播放关系与相关音频任务级联清理。涉及该图的故事视频失效并删除。
- 关闭人脸阻止后续任务；彻底删除选项删除机器特征/关联，保留人工人物及无生物特征的手工标签。推理返回再次检查最新授权。
- 临时语义查询与语音搜索录音保留 24 小时，由调度清理；关联的家庭口述记忆保留到用户删除。
- 成员移除撤销会话，共享照片仍是家庭记录，不自动删除。删除家庭需名称确认，数据库与媒体目录同步删除。用户账号仍可受邀加入其他家庭。

## 观测与故障定位

健康 `/api/v1/system/health`；owner 设置页显示模型文件、Ollama 和磁盘状态。任务页记录版本、次数、耗时与脱敏错误。AI 成功率只统计模型任务终态；没有样本显示未知，不把元数据/跳过任务当模型成功。

日志只含 request_id、状态、耗时、job_id、类型和错误码，不打印图片/base64/录音/转写正文。真实模型测试报告只包含明确的合成输入结果。

磁盘不足：暂停任务、备份、释放明确无用的测试产物，再重试。模型内存不足：关闭其他重负载程序，保持单 Worker；SigLIP2 自动 MPS→CPU，仍失败保留任务错误。Ollama 仅环回，启动器设置 OLLAMA_NO_CLOUD=1、NUM_PARALLEL=1、MAX_LOADED_MODELS=1、KEEP_ALIVE=0。
