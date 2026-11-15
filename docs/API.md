# API 契约与权限

服务启动后 `/api/docs` 为当前 OpenAPI 生成的离线文档，不依赖外部 CDN。机器可读 schema 位于 `/api/openapi.json`。`make types` 从运行服务生成 TypeScript 声明；UI 的领域类型与请求适配见 `apps/web/src/api/client.ts`。

统一前缀 `/api/v1`。错误结构：`code`、`message`、`details`（仅字段名/类型，不回显输入）和 `request_id`。支持分页的列表使用 `page` / `page_size`，返回 `items` / `total`；时间为带时区 ISO 8601，日期条件按家庭时区解释。未知拍摄时间不能命中历史年份筛选。

普通会话 Cookie 为 HttpOnly / SameSite=Strict；`GET /auth/me` 返回当前会话 CSRF token，修改操作必须带 `X-CSRF-Token`。生产 HTTPS 设置 `SESSION_COOKIE_SECURE=true`。登录/注册/配对/邀请加入有持久限流，JSON 输入校验和跨站点检查。

删除家庭保留账号。`POST /auth/login` 在原口令正确但无 active 家庭时返回 `role=account`、`needs_household=true` 和 CSRF，并签发 household_id=null 的账号会话；`GET /auth/me` 返回本人、household=null 及上述状态。正常家庭登录保持原 csrf/role 语义并增加 needs_household=false；显式指定无资格 household_id 仍为 403。账号会话可调用 `/auth/me`、`/auth/logout`、GET/POST `/households`，或按原凭据通过邀请加入；所有家庭媒体/内容/管理接口仍通过 `current` 校验家庭成员，缺少家庭返回 403。新页面 `/households/new` 完成创建后获得正常 owner 会话。没有新增 dev HTTP 管理接口；本地全量检查及恢复出厂使用 scripts/manage.py。

| 领域 | 路径与行为 |
|---|---|
| 账号 | POST `/auth/setup`、`/auth/register`、`/auth/login`、`/auth/logout`；GET `/auth/status`、`/auth/me` |
| 家庭 | GET/POST `/households`；GET/PATCH/DELETE `/households/{id}`；删除须完整家庭名称 confirm |
| 邀请/成员 | POST/GET `/households/{id}/invites`；DELETE `/invites/{id}`；POST `/invites/{code}/join`；GET `/households/{id}/members`；PATCH/DELETE `/members/{id}` |
| 照片 | POST `/photos/upload`（multipart file/shared）；GET `/photos`、`/photos/{id}`；PATCH/DELETE `/photos/{id}`；POST `/photos/batch`、`/{id}/restore`、`/{id}/analyze?step=...`、`/{id}/feedback` |
| 媒体 | GET `/photos/{id}/media/320`、`960`、`original`；每次授权，无公开静态路径；原图禁止 display |
| 人物 | GET/POST `/people`；PATCH `/{id}`；POST `/people/merge`、`/people/split`、`/people/{id}/photos/{photo_id}` |
| 事件 | GET `/events`、`/{id}`；PATCH `/{id}`；POST `/events/recluster`、`/events/merge`、`/events/{id}/split` |
| 自动相册 | GET `/albums` 返回四个相册的 total/cover/pinned；GET `/albums/{on_this_day\|growth\|travel\|recent}` 返回完整分类与精选；GET `/{kind}/playback` 返回跨分页的受权照片影片序列；scope 默认 visible（本人 private + 已授权 family），可传 family 仅共享 |
| 成长 | GET `/timeline?person_id=...`；POST `/milestones`；PATCH/DELETE `/milestones/{id}` |
| 故事 | POST `/stories/generate`；GET `/stories`、`/{id}`、`/{id}/json`；PATCH/DELETE `/{id}`；POST `/{id}/export` 入队，GET 同路径下载就绪 MP4 |
| 搜索 | POST `/search`：query、日期/人物/事件/地点条件、semantic；返回结果与编码 job_id/query_id；编码完成后携带同一 query_id 再查 |
| 语音 | POST `/speech/transcribe` 或 `/memories/audio`（multipart file、照片/事件 ID）；GET/PATCH/DELETE `/memories/audio/{id}`；GET `/{id}/media` |
| 推荐 | GET `/playlists/today`、`/playlists/{YYYY-MM-DD}`；POST `/playlists/regenerate` |
| 相册选择 | GET `/album-pins`；PUT `/album-pins/{kind}`、`/album-photos/{photo_id}/preferred`；GET `/duplicates`、PUT `/duplicates/{photo_id}` |
| 智能集合 | GET/POST `/collections`；GET/PATCH/DELETE `/collections/{id}`；GET `/{id}/playback`；集合仅创建者可见，素材仅 family |
| 图片文字 | GET/PATCH `/photos/{id}/text`；POST `/photos/{id}/ocr` 排队本地识别/重试 |
| 增强故事 | GET `/memory-suggestions`；POST `/stories/compose`、`/stories/{id}/compose`；GET/POST `/stories/{id}/narrations`，DELETE `/{narration_id}`，GET `/{narration_id}/audio` |
| 推荐规则 | GET/POST `/recommendation-rules`；DELETE `/recommendation-rules/{id}`；personal 为本人，household 由 owner 管理 |
| 展示 | owner POST/GET `/display/devices`、DELETE `/{id}`；POST `/display/pair`；GET `/display/feed`；POST `/display/events` |
| 任务 | GET `/jobs`、`/{id}`；POST `/{id}/retry`、`/jobs/pause`、`/jobs/resume` |
| 运维 | GET/PATCH `/settings`；GET `/system/health`、`/system/models`、`/system/stats`、`/system/processing`；POST `/system/export`、GET `/system/export/{job_id}` |

上传返回 photo_id/job_id/status；完全重复返回 status=duplicate，不删除原图。只识别调用者自己或已授权共享的重复文件，其他成员私密哈希不泄露。语义编码、图像分析、ASR、MP4、授权导出均通过持久任务处理；结构化查询与事实模板草稿为轻量数据库操作。

权限：owner 管理家庭；member 管理自己内容并纠错共享事件/人物；viewer 仅查看与单图反馈；私密照片仅上传者可见。对象均校验 household_id，成员移除撤销家庭会话。最后一位 owner 不能被移除或降级。

展示 Cookie 与普通 Cookie 分离，不能调用管理 API。展示衍生图/故事 URL 显式带 `?display=1`，避免借用当前浏览器的 owner Cookie。展示反馈 POST 带 `X-Display-Request: 1`；动作限定 play/skip/pause/favorite/hide/dislike。展示列表不返回模型分析、人脸特征、原图路径或上传者身份。

人脸开启必须 face_consent=true；关闭与删除特征是独立操作。姓名/关系只接受人工字段，VLM 契约不包含身份字段。人工字段与模型字段独立，重跑不会覆盖。

人物新增 `growth_mode=auto/include/exclude`，默认 auto；include 保留已确认家庭人物校验，exclude 阻止该人物的关联照片被自动重新纳入。PATCH 可以恢复 auto。旧字段 `growth_enabled=true/false` 分别映射 include/exclude，同时提交冲突字段返回 422。迁移保留旧 true 为 include，旧未启用人物转 auto。`/timeline` 无 person_id 的原家庭时间查询契约保留；Web 默认成长时间线读取 `/albums/growth`，不以全部家庭照片充当成长记录。

自动相册项目包含 `album_reason`、`album_group`、`album_group_title`、`album_event_id`；元数据包含 mode、date_basis、match_window_days、pending_count 和 failed_count。private_photo_count 只统计调用者本人未隐藏/未删除的私密照片：默认 visible 范围表示已参与本人整理，family 范围仍不纳入这些照片；不暴露其他成员私密数量。failed_count 区分当前视觉契约失败与等待；关闭模型时不会把待跳过的模型任务显示为正在分析。展示会话不能调用个人相册 API。

`POST /photos/batch` 新增 action=share：最多 200 个 photo_ids，去重后整批验证，只接受调用者本人未删除的照片；确认后写 visibility=family、人工锁、审计，并补排事件/推荐。不会重跑已有效的视觉分析，也不更改后续上传默认共享策略。单张更改日期/共享范围同样触发派生更新。

## 2026-11-16 增强契约

### 自动相册与照片影片

`GET /albums/{kind}` 与 `GET /albums/{kind}/playback` 共用 `day`、`scope`、`person_id`、`group`、`period=day|month|year` 条件。`on_this_day` 始终覆盖各往年同日前后 3 天；`recent` 的 day 维度覆盖所选日向前 30 天，month 维度覆盖所选月份。成长可按确认人物及年/月分组。未知日期不改用上传日期。

相册响应保留 `items/total/page/page_size`，增加 `highlights`、`pinned`、`groups`（含逐日计数/适用里程碑）、`people`、`explanation`、`missing_date_count` 与 `revision`。playback 从完整筛选集合选材，不接收图片页码，`limit` 为 1–200（默认 24）；返回 `mode=photo_film`、`items`、`source_total`、`selected_count`、`interval_seconds`、`revision`。这是连续照片播放清单，不是视频文件下载。

`PUT /album-pins/{kind}` 接受 `{ "pinned": true }`，固定属于当前成员。`PUT /album-photos/{id}/preferred` 接受 `{ "preferred": true }`，只能为本人上传照片固定代表。`GET /duplicates` 返回完全重复或相似连拍候选、代表 ID、质量理由及可操作标记；`PUT /duplicates/{photo_id}` 接受 `action=representative|detach`，仅本人照片可改。以上选择不删除原图，不修改共享范围。

### 搜索与智能集合

`POST /search` 新增 `person_ids`（最多 100）、`people_mode=all|any`（默认 all）、`terms`、`exclude_terms`（各最多 30）、`reference_photo_id`。原 `person_id` 仍有效，人物条件须为当前家庭已确认人物；“全家”使用全部登记家庭人物。`terms` 为必须命中的主题条件，`exclude_terms` 为排除条件，向量不能绕过。`parsed_conditions` 返回解析出的时间、人物、主题、排除及自由文字，`unknown_conditions` 返回未确定关系/事件等问题；存在未知硬条件时不返回宽泛候选。

以图搜图复用参考照片已有向量，要求参考图与结果都属于当前 family 范围且向量模型/版本/维度一致；参考图尚无向量为 409，越权为 404。搜索文字可匹配 OCR（区分人工校对/本地识别）及已确认口述记忆；不扩展为个人 private 搜索。结果的 `text_regions` 是相对图片的归一化文字区域。

`POST /collections` 接受 `title`、`mode=static|dynamic`、`query`（SearchInput）、`photo_ids`（最多 3000）、`pinned`。静态集合须有已获授权 family 照片；动态集合保存可重现规则，将 semantic 设为 false，不能保存以图检索条件（该结果须保存固定集合）。动态成员与向量候选可能不同，页面明确提示。

`PATCH /collections/{id}` 可改 title/pinned/cover_photo_id、include_ids/exclude_ids。两种人工覆盖不能包含同一照片；封面必须在当前集合。集合仍保存暂时撤回内容的覆盖记录，但响应不返回撤回的 ID/封面/照片；重新授权后继续遵守人工排除。动态规则失去已选人物/事件时保持空候选并给出 rule_warning，不回退全库。所有集合路径同时检查家庭和 created_by，其他家庭成员和 display 均不可查看集合标题或内容。删除集合不删除照片，也不自动共享。

### OCR

`POST /photos/{id}/ocr` 使用 `photo:{id}:ocr:apple-vision-text-1` 幂等键，仅处理未隐藏/未删除的 family；OCR 关闭返回 409，私密素材拒绝处理。历史补齐只排对应 OCR 版本，不重跑其他模型。达到终态失败需明确重试，不在每次调度时重置尝试次数。

`GET /photos/{id}/text` 返回 text 记录和最近 job；`PATCH` 接受 `{ "text": "人工校对文字" }`，最多 64000 字符，保存 corrected_text/confirmed，保留机器 text/regions_json/model_version。写操作使用照片编辑权限，读取仍执行照片授权。区域 bbox 为左上角原点的 `[x,y,width,height]`，各数值 0–1；OCR 文字不是拍摄地点/时间事实。

### 故事、原声与偏好

`POST /stories/compose` 接受 event_id、person_id 或 photo_ids 三选一，可加 title、length=short|medium|long，返回 story/job_id/status。分别最多选 12/24/60 张，持久任务 `story_compose` 输出日期章节及来源；用户已修改故事版本时任务 skipped，不覆盖。`POST /stories/{id}/compose` 仅用于未改动编排版本；已有人工编辑时 409，前端可基于当前素材另建精选故事。`generated_by_ai=false`，不将事实模板称为 LLM 生成。

故事响应新增 `composition`（length/state/generator/chapters/sources/job_id/job_state/error_message）和适用的 slide.narration_url/audio_memory_id/narration_transcript。返回的来源/章节过滤到当前幻灯片集合，不公开已移出内容的旧来源 ID。

`POST /stories/{id}/narrations` 接受 audio_memory_id/photo_id；源须人工校对、非临时查询录音、与当前共享照片或事件关联。一个画面和一个录音各最多一个绑定，冲突 409。GET 返回可用来源与现有绑定，DELETE 可移除无效绑定。源删除、转写或事件关联改变、照片撤权后，旧音轨及导出不可读。音频 GET 通过故事权限校验，display 显式使用 `?display=1`，不会放宽普通口述录音端点。FFmpeg 可混入确认原声，无原声保持静音。

`POST /recommendation-rules` 接受 scope=personal|household、mode=less|never、target_kind=person|date|location|theme、target_value；日期类型要求 start_date/end_date。人物须确认，地点只匹配人工地点或确认事件地点，主题按已有标签匹配。家庭规则须 owner；个人规则只影响本人。GET 可见家庭规则与本人个人规则，DELETE 恢复推荐。规则不隐藏原片或禁止主动搜索；家庭 never 同时过滤新生成与历史播放列表。

`GET /system/processing` 返回 can_process_background、reasons、available_memory_mb、available_disk_mb、in_window。设置新增 ocr_enabled、background_start_hour/background_end_hour、min_free_memory_mb/min_free_disk_mb；小时相等表示全天。系统资源检查限制新后台重任务领取，不能中断运行任务，也不代表温控能力。
