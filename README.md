# Xbum · AI 家庭记忆相册

手机选择照片 → 本地后台理解 → 事件与成长记忆 → 可解释精选 → 浏览器相框 → 故事、搜索与人工纠错。

原生运行于 Apple Silicon Mac，默认按 16GB 内存设计。React + TypeScript / FastAPI / SQLite WAL / 本地受权文件存储。AI 仅在本机执行：SigLIP2、Ollama Qwen3-VL 2B、YuNet/SFace、whisper.cpp、Apple Vision OCR；FFmpeg 导出照片 MP4，可绑定已确认口述原声，无音轨时保持静音。没有第三方在线推理 API，也没有生产 fake provider。

本项目覆盖规格 F01–F10 的代码路径；**实现范围、实际通过的测试与尚未完成的真实场景验收，请分别看 [实现清单](docs/FEATURE_MATRIX.md) 和 [测试报告](docs/TEST_REPORT.md)**。不能把合成图片和测试 provider 的结果视为真实家庭样本准确率。

2026-11-16 增强范围为 A01–A12 和双层相册详情：自动照片影片、完整集合/精选、成长年鉴、旅行分组、组合搜索、以图搜图、OCR、智能集合、原声故事与推荐控制。具体行为和保留边界见[增强实施说明](docs/AI_ENHANCEMENT_IMPLEMENTATION.md)。宠物个体身份、视频/Live Photo 图库和生成式修图保留后续评估。

## 安装与启动

需要 macOS、Homebrew、Python 3.12、Node 22.12+。脚本会探测并安装缺失依赖；Homebrew 安装入口为 [brew.sh](https://brew.sh)。从项目目录执行：

```bash
make setup
make start
```

打开 [本机相册](http://127.0.0.1:8000/setup)，创建家庭并自行设置至少 12 位口令。没有内置管理员口令，也不会自动导入真实照片。CLI 初始化可用 `.venv/bin/python scripts/bootstrap_admin.py`。

`make start` 在同一终端监督 API 和单 Worker，并先检查本机 Ollama：已运行则复用；未运行且已安装 `ollama` 命令时启动 `ollama serve`。前端由 API 提供已构建文件。开发热更新使用 `make dev`，打开 [开发页面](http://127.0.0.1:5173)，Worker 仍由同一命令启动。不要同时再启动第二个 Worker。单独启动 Worker 的命令是 `make worker`。

### 停止、重启 Xbum 与 Ollama

从项目目录执行即可，无需查 PID、安装 rg 或找回原终端：

```bash
make stop                 # 停止当前仓库的 Xbum，重复执行也安全
make restart              # 等待停止完成后，以正常模式重新启动
make restart DEV=1        # 等待停止完成后，以开发模式重新启动（含 Vite）
```

`restart` 与 `start` 一样在当前终端前台运行；按 **Ctrl+C** 或在另一终端执行 `make stop` 即可停止。会关闭本次启动的 API、Worker、开发服务器和 Ollama；启动前就已独立运行的 Ollama 保留。命令也能识别升级前启动的 `scripts/run.py` 实例。详见 [运维文档：启动与停止](docs/OPERATIONS.md#启动与停止)。

只卸载内存中的模型、保留 Ollama 后台服务：

```bash
ollama ps
ollama stop qwen3-vl:2b-instruct
ollama ps
```

`ollama ps` 只有表头表示没有模型驻留。`ollama stop` 不会关闭 `ollama serve`；Worker 若仍有任务，之后仍可能重新加载模型。需要完全停止时按上面的方式停止 Xbum，再停止独立运行的 Ollama。停止服务不删除照片、数据库或已下载权重；持久任务在下次启动恢复。

### 开发阶段后台管理

在项目目录执行本地命令，服务停止时也能查询数据库：

```bash
make status          # 服务/API、数据库、各表数量、账号归属、照片/任务状态、模型和磁盘
make users           # 全部账号、称呼、创建时间、家庭角色、口令是否已设置、照片数量
make photos          # 全部照片元数据，包含私密、隐藏和回收站状态
make status-all      # 完整脱敏 JSON：运行状态和所有数据表记录，不受页面分页限制
make factory-reset   # 输入 RESET_XBUM 确认后，自动停止服务并恢复出厂设置
```

这些是本机操作者命令，可检查所有家庭及私密照片元数据；不输出口令哈希、会话/邀请令牌或人脸向量，不提供 HTTP 管理后门。完整输出含个人资料，应只保存在本机忽略目录。

恢复出厂设置会永久清空 `DATA_DIR` 内的所有账号、家庭、照片、录音、视频、分析、任务及审计，重建空数据库；保留源码、`.env`、依赖、已下载模型，以及数据目录外的备份和诊断文件。确认前可用 `make backup` 留存。完成后服务保持停止，运行 `make start`，打开 `/setup` 重新建档。更多范围、自动化调用与失败恢复见 [运维文档](docs/OPERATIONS.md#开发阶段本地管理与恢复出厂)。

删除家庭与恢复出厂设置是独立操作。删除家庭会保留账号；使用原用户名和原口令登录后，若已无家庭，页面会引导创建新家庭或通过邀请加入已有家庭。

### 模型安装

首次下载模型需要联网；基础相册无需安装模型也能上传、浏览、纠错、推荐和播放。运行时不会自动下载模型。

```bash
# make start 运行期间，在另一个终端执行
bash scripts/download_models.sh all
bash scripts/download_models.sh verify
make model-smoke
```

组件可单独安装：`siglip`、`faces`、`whisper`、`vlm`。如果 Ollama 的下载器拒绝当前网络的地址解析，可显式运行 `bash scripts/download_models.sh vlm-cache`：它从固定的官方 registry manifest 下载并校验 blob，通过本机官方 Blob API 缓存后再执行 pull，不关闭 Ollama 的安全校验。详见 [模型与许可登记](docs/MODEL_MANIFEST.md)。

人脸分析和语音处理默认关闭。由管理员在「家庭设置」明确授权后启用；人脸关闭后不再新增特征，可单独彻底删除机器特征。已经失败的任务可以在「处理任务」重试。

## 使用

1. 在「家庭设置」生成限时、限次数邀请链接；受邀成员创建账号或验证已有口令加入。`viewer` 为只读，不能上传或编辑。
2. 手机或电脑打开 `/upload`，手动选择文件。**共享勾选默认关闭**；勾选后这批照片才进入家庭共享、搜索、故事与自动展示。上传逐文件显示进度并可重试。
3. 在照片详情校正说明、时间和地点。拍摄时间未知时显示未知；上传日期仅作为不可信候选，不冒充历史拍摄日期。EXIF 无时区时会明确标记家庭时区推定。
4. 首页「往年今日 / 慢慢长大 / 一起走过 / 最近的日子」进入各自的自动相册；**上方自动播放相册照片影片，下方直接浏览对应完整图片**，无需先生成故事。可切换精选/全部、暂停/前后张/全屏、固定入口；成长支持确认人物和年/月分组，旅行显示每日片段。后台自动整理，可选人工修正优先；姓名、亲属关系、生日和里程碑仍由用户确认。具体规则与隐私范围见 [自动整理相册](docs/AUTOMATIC_ALBUMS.md)。
   本人私密照片也参与本人登录后的自动整理，其他成员与家庭相框仍不可见。升级会自动补处理旧分析契约或缺失结果，不必重新上传。往年今日包含各往年同日前后 3 天。
5. 批量共享：在照片库点击照片左上角复选框，或「选中本页我的照片」→「加入家庭共享」→确认。翻页清除本页选择，可逐页重复；仅共享所选照片，后续上传仍默认私密。
6. 在家庭设置创建展示配对码，在 `/display` 输入一次性码。展示设备仅有共享衍生图与故事的读取、播放与反馈权限，不能下载原图或调用管理接口。
7. 事件可生成事实模板故事；回忆建议可异步编排短/中/长精选、日期章节和来源，再编辑、播放或导出 MP4。已有编辑可另建精选版。口述原声经校对确认后可绑定故事；删除/变更来源会使旧音轨和导出失效。编排保持 `generated_by_ai=false`，不声称 LLM 叙事。
8. `/search` 支持日期/季节、已确认人物全部同框或任一、地点/事件/主题与排除条件；显示解析与匹配原因，支持已有向量以图搜图。语音先本地转写、人工核对后再查，确认口述也可检索。照片详情可查看本地 OCR 文字、区域并人工校对。
9. `/collections` 保存固定照片或动态规则，支持封面、固定、人工加入/排除及双层浏览。集合仅本人可见，素材仅限已共享 family；动态集合按确定性条件更新，不等同向量搜索候选全集，保存相册不会自动分享照片。
10. `/duplicates` 比较重复/相似连拍、固定本人代表或拆组；`/preferences` 控制人物、日期、地点或主题的主动推荐，个人设置和家庭相框策略分别生效。原图保留，推荐偏好不改变共享范围或搜索资格。

相册顶部为浏览器静音照片影片，未预先合成 MP4；播放清单由后端选择并定期重检，图片分页不截断影片素材。API 不可达时停止显示旧画面。设置页可配置 OCR、后台小时窗口及内存/磁盘保留量；这会暂缓新重任务，不中断已有任务，不是温控调度。

默认静默时段为家庭当地 **22:00–07:00**；此时相框不显示照片。需要测试时可调整设置。空闲自动展示仅从首页进入，不抢占上传、编辑或搜索页面。

## 本地数据与权限

- `data/album.sqlite3`：迁移管理的 SQLite WAL 数据库；所有核心对象带家庭隔离，关联使用外键。
- `data/<household_id>/photos/<photo_id>/`：原图和 320/960 JPEG 衍生图。仅保存相对键；媒体不经过公共静态目录。
- `data/<household_id>/audio/`、`stories/`、`exports/`：录音、视频及授权导出。
- `models/`：离线权重、固定版本与本机 SHA-256 安装清单；Ollama 的权重由本机 Ollama 管理。
- `.env`、数据、权重、备份、测试运行产物全部忽略。目录默认 0700，文件与备份默认 0600。

私密照片只有上传者可见，管理员也不能越权读取其他成员的私密原图。隐藏、回收站和撤回共享在 API 每次读取时重新校验，包含历史列表、故事、录音与导出下载。已下载到浏览器的内容无法被服务器追溯收回；相框每 2 秒校验当前资格，断开 API 时清空展示。**外网断开而本地服务可达时正常工作；API 完全不可达时不播放陈旧缓存照片**，这是对即时撤权优先的明确取舍。

## 测试与验收

```bash
make test                 # Ruff + pytest + Vitest
make build                # TypeScript（含未使用代码检查）+ Vite
npm exec --prefix apps/web -- playwright install chromium  # 没有 Chrome 时安装测试浏览器
make e2e                 # 独立临时数据库、真实 API/Worker/FFmpeg、桌面与移动 UI
make smoke               # 服务已启动时，检查健康、前端和匿名拒绝
make model-smoke         # 真实本地模型；缺失或失败即非零退出
make real-smoke          # 临时数据，真实 HTTP→队列→Worker→模型与语音闭环
.venv/bin/python scripts/ocr_smoke.py  # 真实 macOS Vision，仅程序生成中英文文字图片
make types               # API 启动后由 OpenAPI 生成 TypeScript 声明
make sample              # 生成 100 张明确标识的合成图片，不自动上传
```

性能命令使用临时数据、真实图片导入/元数据/质量/队列，不使用 fake AI；报告明确不包含模型推理吞吐：

```bash
.venv/bin/python scripts/benchmark.py --count 1000 --output runtime/performance-1000.json
.venv/bin/python scripts/benchmark.py --count 3000 --output runtime/performance-3000.json
```

测试 fake provider 仅在 `apps/api/tests/conftest.py` 注入；生产默认使用本地真实 provider。E2E 与基准不注入 fake。真实模型冒烟、质量标注集、Mac 实际重启和断网条件在验收报告中分别说明。

## 备份与恢复

```bash
make backup
# 或指定外部磁盘目录（不能位于 DATA_DIR 内）
bash scripts/backup.sh /Volumes/ProtectedDisk/Xbum

# 先停止 API 和 Worker；此命令交互要求输入 RESTORE
bash scripts/restore.sh /绝对路径/xbum-备份.tar.gz
```

备份使用共享维护锁和 SQLite backup API，验证数据库完整性、外键及文件哈希；可以在本应用运行时备份。恢复拒绝路径逃逸、符号/硬链接、校验失败和正在运行的服务；旧数据保留在 `data.before-restore-*` 便于回退。自动化恢复可显式使用 `--confirm-restore`。完整备份包含口令哈希和生物特征，务必放在受保护、加密的磁盘，不要提交或公开分享。

## 部署和工程文档

- [原始完整规格](AI_Family_Album_Codex_Spec.md)
- [实现与验证计划](docs/IMPLEMENTATION_PLAN.md)
- [架构边界](docs/ARCHITECTURE.md)
- [功能与路由清单](docs/FEATURE_MATRIX.md)
- [手机相册调研与借鉴分析（含后续扩展）](docs/ALBUM_FEATURE_BORROWING_ANALYSIS_2026-11-15.md)
- [A01–A12 与双层相册增强实施说明](docs/AI_ENHANCEMENT_IMPLEMENTATION.md)
- [Mac 部署与手机 HTTPS](docs/DEPLOY_MAC.md)
- [模型登记、下载与许可](docs/MODEL_MANIFEST.md)
- [API 与权限契约](docs/API.md)
- [运维、删除和故障恢复](docs/OPERATIONS.md)
- [实际测试与 19 项验收报告](docs/TEST_REPORT.md)

默认只绑定 `127.0.0.1`。手机访问需要管理员显式配置可信 LAN 地址、可信 Host 与 HTTPS；浏览器录音需要 HTTPS 或 localhost。不使用 Docker GPU、不适配真实中屏硬件、不连接云相册。
