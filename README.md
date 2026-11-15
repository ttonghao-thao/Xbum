# Xbum · AI 家庭记忆相册

手机选择照片 → 本地后台理解 → 事件与成长记忆 → 可解释精选 → 浏览器相框 → 故事、搜索与人工纠错。

原生运行于 Apple Silicon Mac，默认按 16GB 内存设计。React + TypeScript / FastAPI / SQLite WAL / 本地受权文件存储。AI 仅在本机执行：SigLIP2、Ollama Qwen3-VL 2B、YuNet/SFace、whisper.cpp；FFmpeg 导出静音 MP4。没有第三方在线推理 API，也没有生产 fake provider。

本项目覆盖规格 F01–F10 的代码路径；**实现范围、实际通过的测试与尚未完成的真实场景验收，请分别看 [实现清单](docs/FEATURE_MATRIX.md) 和 [测试报告](docs/TEST_REPORT.md)**。不能把合成图片和测试 provider 的结果视为真实家庭样本准确率。

## 安装与启动

需要 macOS、Homebrew、Python 3.12、Node 22.12+。脚本会探测并安装缺失依赖；Homebrew 安装入口为 [brew.sh](https://brew.sh)。从项目目录执行：

```bash
make setup
make start
```

打开 [本机相册](http://127.0.0.1:8000/setup)，创建家庭并自行设置至少 12 位口令。没有内置管理员口令，也不会自动导入真实照片。CLI 初始化可用 `.venv/bin/python scripts/bootstrap_admin.py`。

`make start` 在同一终端监督 API 和单 Worker，并先检查本机 Ollama：已运行则复用；未运行且已安装 `ollama` 命令时启动 `ollama serve`。前端由 API 提供已构建文件。开发热更新使用 `make dev`，打开 [开发页面](http://127.0.0.1:5173)，Worker 仍由同一命令启动。不要同时再启动第二个 Worker。单独启动 Worker 的命令是 `make worker`。

### 停止 Xbum 与 Ollama

在执行 `make start` / `make dev` 的终端按 **Ctrl+C**，会停止本次启动的 API、Worker、开发服务器，以及由本次脚本启动的 Ollama。启动前就已运行的 Ollama 会保留。后台启动时，先核对启动器 PID 和工作目录，再使用 `kill -TERM`；具体命令见 [运维文档：启动与停止](docs/OPERATIONS.md#启动与停止)，不要复制上一次运行的 PID。当前没有 `make stop` 目标。

只卸载内存中的模型、保留 Ollama 后台服务：

```bash
ollama ps
ollama stop qwen3-vl:2b-instruct
ollama ps
```

`ollama ps` 只有表头表示没有模型驻留。`ollama stop` 不会关闭 `ollama serve`；Worker 若仍有任务，之后仍可能重新加载模型。需要完全停止时按上面的方式停止 Xbum，再停止独立运行的 Ollama。停止服务不删除照片、数据库或已下载权重；持久任务在下次启动恢复。

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
4. 首页「往年今日 / 慢慢长大 / 一起走过」进入各自的自动相册；后台分析后自动整理，不需要先配置人物或确认旅行。可选的人物、事件与日期修正优先于自动结果；姓名、亲属关系、生日和重要里程碑仍由用户确认。具体规则与隐私范围见 [自动整理相册](docs/AUTOMATIC_ALBUMS.md)。
   本人私密照片也参与本人登录后的自动整理，其他成员与家庭相框仍不可见。升级会自动补处理旧分析契约或缺失结果，不必重新上传。往年今日包含各往年同日前后 3 天。
5. 批量共享：在照片库点击照片左上角复选框，或「选中本页我的照片」→「加入家庭共享」→确认。翻页清除本页选择，可逐页重复；仅共享所选照片，后续上传仍默认私密。
6. 在家庭设置创建展示配对码，在 `/display` 输入一次性码。展示设备仅有共享衍生图与故事的读取、播放与反馈权限，不能下载原图或调用管理接口。
7. 事件一键生成基于真实元数据与人工说明的故事草稿，编辑顺序、时长、说明、主题、颜色、封面及结尾，预览或入队导出 MP4。文案模板不冒充大模型生成。
8. `/search` 支持日期、节日、人物、地点和事件条件；语义文本编码也入队。语音先本地转写、人工核对后再查询。照片和事件可关联录音、修改转写或删除。

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
- [Mac 部署与手机 HTTPS](docs/DEPLOY_MAC.md)
- [模型登记、下载与许可](docs/MODEL_MANIFEST.md)
- [API 与权限契约](docs/API.md)
- [运维、删除和故障恢复](docs/OPERATIONS.md)
- [实际测试与 19 项验收报告](docs/TEST_REPORT.md)

默认只绑定 `127.0.0.1`。手机访问需要管理员显式配置可信 LAN 地址、可信 Host 与 HTTPS；浏览器录音需要 HTTPS 或 localhost。不使用 Docker GPU、不适配真实中屏硬件、不连接云相册。
