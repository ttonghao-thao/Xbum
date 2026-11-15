# 全量功能实现清单

按规格 F01–F10 统一交付，不是分阶段产品。这里记录代码路径；实际执行证据和剩余限制见 TEST_REPORT.md。

| 功能 | 实现 | 主要模块 |
|---|---|---|
| F01 家庭与权限 | 首次建档、独立家庭注册、登录/注销、Argon2id、会话/CSRF、持久限流、邀请过期/限次/撤销、成员角色/移除、至少一位 owner、删除家庭、独立一次性展示配对/撤销 | api/auth.py、core/security.py、api/system.py、pages/Auth.tsx、Admin.tsx |
| F02 上传与照片 | 批量/拖拽、逐文件进度/重试、显式共享、JPEG/PNG/WEBP/HEIF 解码、30MB/像素限制、EXIF/GPS/旋转、SHA/pHash/图像差异去重、320/960 衍生图、人工锁定、筛选、本页多选及确认后批量共享、回收站/恢复/二次确认永久删除 | services/photos.py、storage.py、api/library.py、pages/Library.tsx |
| F03 本地分析 | 独立元数据校验/衍生图再生、OpenCV 模糊/曝光/分辨率质量、SigLIP2 图文向量/MPS→CPU、Ollama 必填完整 JSON/截断拒绝、YuNet/SFace 授权候选、步骤版本/耗时/有限重试、历史与新增统一契约自动补处理 | ai/providers.py、runner.py、workers/queue.py、worker.py |
| F04 家庭记忆 | 可信时间优先，GPS/向量/人物辅助、跨年隔离、跨天旅行片段、事件合并/拆分/重排/增删/封面/修订/人工锁、人物命名/合并/拆分/手工关联、自动成长相册与可选人工覆盖、明确里程碑 | services/events.py、albums.py、api/memories.py、pages/Memory.tsx、Albums.tsx |
| F05 主动场景 | 日常精选、往年同日及 ±3 天、自动旅行/出游候选、登记生日/匹配节日、自动成长画面及跨月人物记录、未播新分享；三个完整专属相册独立于播放列表；本人私密整理与家庭共享展示隔离；家庭时区/夜间静默/质量/隐私过滤，候选不足不凑数 | services/albums.py、calendar.py、recommendations.py、pages/Library.tsx、Albums.tsx、Display.tsx |
| F06 推荐 | 六特征归一化权重、收藏/喜欢/不喜欢/7 天暂缓/隐藏反馈、播放疲劳、近重复控制、人物覆盖、事件配额和不足时明确放宽、确定性排序、配置快照/理由/历史列表/曝光记录、04:00 本地生成与增量去抖 | services/recommendations.py、api/system.py |
| F07 故事 | 事实模板草稿（明确 generated_by_ai=false）、照片/文字/顺序/封面/时长/转场/主题/颜色/结尾编辑、全屏浏览器播放、异步 FFmpeg 720p 静音 MP4、中文字幕、JSON/MP4 下载、版本失效/重试 | services/stories.py、api/memories.py、pages/Stories.tsx、Display.tsx |
| F08 搜索/语音 | 家庭共享资格过滤、基础中文年月/节日/上月解析、人物/标签/事件/日期/人工地点过滤、同版本 SigLIP2 异步语义排序/依据、无匹配空结果、浏览器录音/音频上传、本地 whisper.cpp、人工校对后查询、录音关联/校对/删除 | services/search.py、calendar.py、providers.py、components/Audio.tsx |
| F09 展示 | 16:9 优先、照片与文字分区、暂停/前后/触摸/键盘/全屏/退出、收藏/不喜欢/隐藏、故事、预加载、仅首页空闲自动进入、夜间静默、独立 display Cookie、每次读取重新授权 | pages/Display.tsx、App.tsx、styles.css、api/system.py |
| F10 管理 | 任务/失败/耗时/重试/暂停/继续、模型安装/服务/磁盘状态、照片/事件/人物/播放/任务指标、授权 ZIP 导出与撤权失效、SQLite/媒体备份恢复、健康检查、无正文结构化日志 | pages/Admin.tsx、api/system.py、services/system.py、scripts/ |

数据库通过 Alembic 创建规格核心表，以及 sessions、login_attempts 和 search_queries。外键级联、复合唯一键、家庭时间与队列索引均在迁移中。媒体不对外静态公开。

前端路由：`/setup`、`/login`、`/register`、`/invite/:code`、`/`、`/upload`、`/photos`、`/photos/:id`、`/albums/on_this_day`、`/albums/growth`、`/albums/travel`、`/events`、`/events/:id`、`/people`、`/timeline`、`/stories`、`/stories/:id/edit`、`/search`、`/display`、`/settings`、`/jobs`。

F09 的「API 完全不可达时仍播缓存」与即时撤权存在不可兼得的边界，当前实现选择停止展示；外网断开、本地服务可达不受影响。不能将这一项表述为原规格无条件全通过。
