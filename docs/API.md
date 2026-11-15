# API 契约与权限

服务启动后 `/api/docs` 为当前 OpenAPI 生成的离线文档，不依赖外部 CDN。机器可读 schema 位于 `/api/openapi.json`。`make types` 从运行服务生成 TypeScript 声明；UI 的领域类型与请求适配见 `apps/web/src/api/client.ts`。

统一前缀 `/api/v1`。错误结构：`code`、`message`、`details`（仅字段名/类型，不回显输入）和 `request_id`。列表使用 `page` / `page_size`，返回 `items` / `total`；时间为带时区 ISO 8601，日期条件按家庭时区解释。未知拍摄时间不能命中历史年份筛选。

普通会话 Cookie 为 HttpOnly / SameSite=Strict；`GET /auth/me` 返回当前会话 CSRF token，修改操作必须带 `X-CSRF-Token`。生产 HTTPS 设置 `SESSION_COOKIE_SECURE=true`。登录/注册/配对/邀请加入有持久限流，JSON 输入校验和跨站点检查。

| 领域 | 路径与行为 |
|---|---|
| 账号 | POST `/auth/setup`、`/auth/register`、`/auth/login`、`/auth/logout`；GET `/auth/status`、`/auth/me` |
| 家庭 | GET/POST `/households`；GET/PATCH/DELETE `/households/{id}`；删除须完整家庭名称 confirm |
| 邀请/成员 | POST/GET `/households/{id}/invites`；DELETE `/invites/{id}`；POST `/invites/{code}/join`；GET `/households/{id}/members`；PATCH/DELETE `/members/{id}` |
| 照片 | POST `/photos/upload`（multipart file/shared）；GET `/photos`、`/photos/{id}`；PATCH/DELETE `/photos/{id}`；POST `/photos/batch`、`/{id}/restore`、`/{id}/analyze?step=...`、`/{id}/feedback` |
| 媒体 | GET `/photos/{id}/media/320`、`960`、`original`；每次授权，无公开静态路径；原图禁止 display |
| 人物 | GET/POST `/people`；PATCH `/{id}`；POST `/people/merge`、`/people/split`、`/people/{id}/photos/{photo_id}` |
| 事件 | GET `/events`、`/{id}`；PATCH `/{id}`；POST `/events/recluster`、`/events/merge`、`/events/{id}/split` |
| 自动相册 | GET `/albums` 返回三个相册的 total/cover；GET `/albums/{on_this_day\|growth\|travel}?page=...&page_size=...` 返回完整分类结果与依据；on_this_day 可传 day=YYYY-MM-DD，始终 ±3 天；scope 默认 visible（本人 private + 已授权 family），可传 family 仅共享，无全部照片回退 |
| 成长 | GET `/timeline?person_id=...`；POST `/milestones`；PATCH/DELETE `/milestones/{id}` |
| 故事 | POST `/stories/generate`；GET `/stories`、`/{id}`、`/{id}/json`；PATCH/DELETE `/{id}`；POST `/{id}/export` 入队，GET 同路径下载就绪 MP4 |
| 搜索 | POST `/search`：query、日期/人物/事件/地点条件、semantic；返回结果与编码 job_id/query_id；编码完成后携带同一 query_id 再查 |
| 语音 | POST `/speech/transcribe` 或 `/memories/audio`（multipart file、照片/事件 ID）；GET/PATCH/DELETE `/memories/audio/{id}`；GET `/{id}/media` |
| 推荐 | GET `/playlists/today`、`/playlists/{YYYY-MM-DD}`；POST `/playlists/regenerate` |
| 展示 | owner POST/GET `/display/devices`、DELETE `/{id}`；POST `/display/pair`；GET `/display/feed`；POST `/display/events` |
| 任务 | GET `/jobs`、`/{id}`；POST `/{id}/retry`、`/jobs/pause`、`/jobs/resume` |
| 运维 | GET/PATCH `/settings`；GET `/system/health`、`/system/models`、`/system/stats`；POST `/system/export`、GET `/system/export/{job_id}` |

上传返回 photo_id/job_id/status；完全重复返回 status=duplicate，不删除原图。只识别调用者自己或已授权共享的重复文件，其他成员私密哈希不泄露。语义编码、图像分析、ASR、MP4、授权导出均通过持久任务处理；结构化查询与事实模板草稿为轻量数据库操作。

权限：owner 管理家庭；member 管理自己内容并纠错共享事件/人物；viewer 仅查看与单图反馈；私密照片仅上传者可见。对象均校验 household_id，成员移除撤销家庭会话。最后一位 owner 不能被移除或降级。

展示 Cookie 与普通 Cookie 分离，不能调用管理 API。展示衍生图/故事 URL 显式带 `?display=1`，避免借用当前浏览器的 owner Cookie。展示反馈 POST 带 `X-Display-Request: 1`；动作限定 play/skip/pause/favorite/hide/dislike。展示列表不返回模型分析、人脸特征、原图路径或上传者身份。

人脸开启必须 face_consent=true；关闭与删除特征是独立操作。姓名/关系只接受人工字段，VLM 契约不包含身份字段。人工字段与模型字段独立，重跑不会覆盖。

人物新增 `growth_mode=auto/include/exclude`，默认 auto；include 保留已确认家庭人物校验，exclude 阻止该人物的关联照片被自动重新纳入。PATCH 可以恢复 auto。旧字段 `growth_enabled=true/false` 分别映射 include/exclude，同时提交冲突字段返回 422。迁移保留旧 true 为 include，旧未启用人物转 auto。`/timeline` 无 person_id 的原家庭时间查询契约保留；Web 默认成长时间线读取 `/albums/growth`，不以全部家庭照片充当成长记录。

自动相册项目包含 `album_reason`、`album_group`、`album_group_title`、`album_event_id`；元数据包含 mode、date_basis、match_window_days、pending_count 和 failed_count。private_photo_count 只统计调用者本人未隐藏/未删除的私密照片：默认 visible 范围表示已参与本人整理，family 范围仍不纳入这些照片；不暴露其他成员私密数量。failed_count 区分当前视觉契约失败与等待；关闭模型时不会把待跳过的模型任务显示为正在分析。展示会话不能调用个人相册 API。

`POST /photos/batch` 新增 action=share：最多 200 个 photo_ids，去重后整批验证，只接受调用者本人未删除的照片；确认后写 visibility=family、人工锁、审计，并补排事件/推荐。不会重跑已有效的视觉分析，也不更改后续上传默认共享策略。单张更改日期/共享范围同样触发派生更新。
