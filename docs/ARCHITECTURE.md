# 实现边界

React/TypeScript/Vite + React Router + TanStack Query；FastAPI/Pydantic v2；SQLAlchemy 2/Alembic；SQLite WAL；本地受权媒体；原生 macOS 单 Worker。

API 校验/解码上传，原子保存原图和 320/960 衍生图，然后为每个独立分析步骤入队。质量与元数据无需模型；embedding、VLM、face、ASR 各自通过子进程运行，结束释放 Python/Metal 内存。Ollama keep_alive=0；无云端回退。Worker 全局 flock + SQLite 原子领取/租约，子进程超时小于租约，重启恢复过期任务。失败步骤不阻断其他分析或基础相册。

所有数据通过 household_id 隔离。原图仅上传者/private 或家庭授权用户可见；display 只能取合格 family 衍生图。展示/搜索/故事每次读取重新检查照片资格，包括已经生成的列表与视频。人工事件锁、人物命名、照片标题说明与拍摄时间独立存储；重算不覆盖。

上传策略默认 private；family 策略表示家庭建议共享，不能替其他成员自动授权。上传页仍逐批要求明确勾选，未确认始终按 private 保存。图片请求体预先有界暂存，解码最多同时 2 张，避免多手机上传与模型竞争 16GB 内存。

向量存 float32 BLOB，并带 model_id/version/dimension；搜索文本编码也入队，不在 API 内加载模型。事件以可信拍摄时间为主，缺失时间不虚构活动；日历按家庭时区和 lunardate 离线转换。推荐确定性加权 + 贪心多样性重排，记录配置快照和理由。

`services/albums.py` 从已有分析、事件、人物及可信日期构建三个完整相册，不复用有数量上限和互斥 scene_type 的播放列表。首页、专属相册和推荐共用分类规则；API 不触发同步推理。成长默认自动整理，人物 `growth_mode=auto/include/exclude` 保存可选人工覆盖，旧 `growth_enabled` 契约仍可写入明确选择。人工锁定事件不被自动归类覆盖。详情见 AUTOMATIC_ALBUMS.md。

个人相册使用 `album_candidates(actor)`：本人 private + 已授权 family，hidden/deleted 排除；推荐/展示/故事/检索保留 `family_candidates` 边界。模型旧结果由 Worker 的 `reconcile_vision` 按输出契约版本补偿，复用上传队列幂等键、单并发和有限重试；完整 JSON 通过后才记录当前 pipeline_version。不能靠把 private 改成 family 解决个人整理为空的问题。

安全边界：浏览器缓存不能在服务器无法联系时证明照片尚未撤权。因此外网断开、局域网服务仍可达时正常离线运行；API 不可达时展示端停止显示缓存照片（fail closed），优先遵守撤权要求。此取舍必须在验收报告明确标记。
