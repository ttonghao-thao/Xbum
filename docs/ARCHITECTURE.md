# 实现边界

React/TypeScript/Vite + React Router + TanStack Query；FastAPI/Pydantic v2；SQLAlchemy 2/Alembic；SQLite WAL；本地受权媒体；原生 macOS 单 Worker。

`scripts/run.py` 统一提供 start/stop/restart，保持前台监督。仓库 `runtime/` 的 flock 防止重复启动，控制锁串行化停止/重启；PID、进程创建时间、命令和工作目录共同校验目标，兼容旧版无运行记录的启动器。重启先等待完整进程树退出；超时不启动第二份服务。子进程单独进程组，退出时先 TERM、超时清理自有后代（含 Vite/模型子进程）；仅关闭本次启动的 Ollama，外部已有服务保留。运行记录不含业务数据，也不依赖 DATA_DIR，可在调整数据路径后停止旧实例。

API 校验/解码上传，原子保存原图和 320/960 衍生图，然后为每个独立分析步骤入队。质量与元数据无需模型；embedding、VLM、face、ASR、OCR 各自通过子进程运行，结束释放 Python/Metal 内存。Ollama keep_alive=0；无云端回退。Worker 全局 flock + SQLite 原子领取/租约，子进程超时小于租约，重启恢复过期任务。失败步骤不阻断其他分析或基础相册。

所有数据通过 household_id 隔离。原图仅上传者/private 或家庭授权用户可见；display 只能取合格 family 衍生图。展示/搜索/故事每次读取重新检查照片资格，包括已经生成的列表与视频。人工事件锁、人物命名、照片标题说明与拍摄时间独立存储；重算不覆盖。

上传策略默认 private；family 策略表示家庭建议共享，不能替其他成员自动授权。上传页仍逐批要求明确勾选，未确认始终按 private 保存。图片请求体预先有界暂存，解码最多同时 2 张，避免多手机上传与模型竞争 16GB 内存。

账号与家庭资格分别校验：`account_current` 允许有效的账号会话（sessions.household_id 可空），`current` 继续要求 active Membership；owner/writer/media 的家庭边界不放宽。删除家庭保留用户并注销家庭会话，无家庭用户以原口令登录后只进入创建/加入家庭流程。会话迁移保留原家庭外键 CASCADE。

开发运维 CLI `scripts/manage.py` 使用 SQLite 只读快照查询所有家庭的脱敏元数据，不通过 API 引入管理绕过。恢复出厂复用启动器控制锁与 stop，再以 service.lock 独占维护；先准备新库再替换和清理 DATA_DIR，失败时保留可恢复旧数据。配置、代码、模型及 DATA_DIR 外备份不属于重置范围。

向量存 float32 BLOB，并带 model_id/version/dimension；搜索文本编码也入队，不在 API 内加载模型。事件以可信拍摄时间为主，缺失时间不虚构活动；日历按家庭时区和 lunardate 离线转换。推荐确定性加权 + 贪心多样性重排，记录配置快照和理由。

`services/albums.py` 从已有分析、事件、人物及可信日期构建四个完整相册（含最近的日子），不复用有数量上限和互斥 scene_type 的播放列表。首页、专属相册和推荐共用分类规则；API 不触发同步推理。成长默认自动整理，人物 `growth_mode=auto/include/exclude` 保存可选人工覆盖，旧 `growth_enabled` 契约仍可写入明确选择。人工锁定事件不被自动归类覆盖。详情见 AUTOMATIC_ALBUMS.md。

个人相册使用 `album_candidates(actor)`：本人 private + 已授权 family，hidden/deleted 排除；推荐/展示/故事/检索保留 `family_candidates` 边界。模型旧结果由 Worker 的 `reconcile_vision` 按输出契约版本补偿，复用上传队列幂等键、单并发和有限重试；完整 JSON 通过后才记录当前 pipeline_version。不能靠把 private 改成 family 解决个人整理为空的问题。

安全边界：浏览器缓存不能在服务器无法联系时证明照片尚未撤权。因此外网断开、局域网服务仍可达时正常离线运行；API 不可达时展示端停止显示缓存照片（fail closed），优先遵守撤权要求。此取舍必须在验收报告明确标记。

## 2026-11-16 本地相册增强

`api/albums.py` 提供完整集合、筛选、元数据与独立 playback 读模型。`select_highlights` 统一按日期覆盖、现有质量和重复组选择代表照片，人工固定优先；事件封面及故事复用该逻辑。`AlbumPlayer` 只消费 API 已授权序列，自动相册播放不复用家庭每日列表，也不受下方分页限制。两层详情使用同一筛选；用户暂停状态保留，轮询失败清空画面。影片为浏览器照片播放，未新增个人私密视频文件或放宽家庭 Story 权限。

搜索在 family 候选范围执行结构化时间、人物交集/并集、事件、地点、主题及排除条件，再进行文字和同版本向量排序。未确认关系等未知条件不放宽。OCR 和已确认口述转写参与搜索；混有撤回来源的事件文字不能透过仍共享照片泄露。以图搜图要求参考照片和结果都在当前 family 范围。

`SmartCollection` 属于创建者个人配置，`collection_access` 同时验证家庭与 created_by；照片素材始终 family-only。静态集合记录选定照片，动态集合复用可重现的硬条件搜索（semantic=false），人工 include/exclude 优先且暂时撤权不删除覆盖记录。返回内容过滤撤回的内部 ID、封面及计数；不因保存集合变更照片 visibility。

`PhotoText` 将机器文本/区域/版本与人工 corrected_text/confirmed 分开保存。`ocr` 在既有模型子进程内调用系统 Swift/Vision（apple-vision-text-1），无独立权重下载或云回退。Worker 仅为缺失/过期 family 结果排幂等 OCR 任务，模型重跑不覆盖校对文本，权限变化在写回时重检；API 只读记录或排任务。

`StoryPlan` 记录选片源、长度、日期章节、事实依据和 expected_version，story_compose 持久任务不能覆盖已人工改动版本。编排是确定性规则与事实模板，保持 generated_by_ai=false。`StoryNarration` 只绑定已确认且当前有权限的口述来源，保存摘要；源删除、转写修改、事件关系变化、照片撤权都会阻断旧故事音轨/导出。音轨通过故事专用授权 URL 提供，display 只能沿该故事边界读取。FFmpeg 合成原声时延长对应画面，并在发布衍生文件前重检来源。

`RecommendationRule` 区分 personal 与 household。个人规则仅作用于创建者的主动精选/建议，家庭规则由 owner 管理并在家庭列表生成及读时执行；不改搜索或原片权限。`processing_status` 在领取重型后台任务前读取可用内存、磁盘和家庭小时窗口；暂停只阻止新任务，不抢占已运行任务，不读取温度或承诺自动降频。

增强表由 3aa922f8c286_local_album_intelligence 迁移；包括 album_pins、album_photo_choices、photo_texts、smart_collections、recommendation_rules、story_plans、story_narrations，沿用外键级联与对象授权。完整功能和范围限制见[增强实施说明](AI_ENHANCEMENT_IMPLEMENTATION.md)。
