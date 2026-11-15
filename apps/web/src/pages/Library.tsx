import { useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  Camera,
  CheckCircle2,
  CloudUpload,
  Heart,
  ImagePlus,
  MonitorPlay,
  Sparkles,
} from "lucide-react";
import {
  api,
  apiAll,
  captureLabel,
  sceneNames,
  uploadPhoto,
  type FamilyEvent,
  type List,
  type Photo,
  type Playlist,
  type Person,
  type AlbumSummary,
} from "../api/client";
import {
  Check,
  Empty,
  ErrorBox,
  Loading,
  PageTitle,
  Pagination,
  PhotoGrid,
  useAuth,
  useNotice,
} from "../components/ui";
import { AudioRecorder, MemoriesList } from "../components/Audio";

export function Home() {
  const auth = useAuth();
  const notify = useNotice();
  const feed = useQuery({
    queryKey: ["playlist"],
    queryFn: () => api<Playlist>("/playlists/today"),
  });
  const events = useQuery({
    queryKey: ["events-home"],
    queryFn: () => api<List<FamilyEvent>>("/events?page_size=3"),
  });
  const recent = useQuery({
    queryKey: ["recent"],
    queryFn: () => api<List<Photo>>("/photos?visibility=family&page_size=8"),
  });
  const albums = useQuery({
    queryKey: ["albums"],
    queryFn: () =>
      api<{ items: AlbumSummary[]; private_photo_count: number }>("/albums"),
    refetchInterval: 5000,
  });
  const featured = feed.data?.items[0];
  const personalAlbum = albums.data?.items.find((a) => a.total > 0 && a.cover);
  const heroPhoto = featured?.photo || personalAlbum?.cover;
  const today = new Date().toLocaleDateString("zh-CN", {
    timeZone: auth.household.timezone,
    month: "long",
    day: "numeric",
    weekday: "long",
  });
  return (
    <>
      <PageTitle
        eyebrow={today + " · 家的每一天"}
        title="让记忆，常常相见。"
        description={`${auth.household.name}，每一个平凡瞬间都值得被珍藏。`}
        actions={
          <>
            {auth.role !== "viewer" && (
              <button
                onClick={async () => {
                  try {
                    await api("/playlists/regenerate", "POST");
                    await feed.refetch();
                    notify("今日精选已刷新，相框会在本轮结束后切换");
                  } catch (e) {
                    notify((e as Error).message);
                  }
                }}
              >
                刷新今日精选
              </button>
            )}
            <Link to="/display" className="button">
              <MonitorPlay size={18} />
              进入记忆相框
            </Link>
          </>
        }
      />
      <section className="home-hero">
        {heroPhoto ? (
          <>
            <Link to={"/photos/" + heroPhoto.id} className="hero-photo">
              <img
                src={heroPhoto.media_url}
                alt={heroPhoto.title || "今日记忆"}
              />
              <span className="hero-photo-label">
                {captureLabel(heroPhoto)}
              </span>
            </Link>
            <div className="hero-story">
              <span className="eyebrow">
                <Sparkles size={15} /> TODAY'S LITTLE JOY
              </span>
              <h2>
                {heroPhoto.title ||
                  "把这一刻，<br>再看一遍。".replace("<br>", "")}
              </h2>
              <p>
                {heroPhoto.caption_user ||
                  featured?.reason_codes_json.join(" · ") ||
                  personalAlbum?.description}
              </p>
              <div className="pill">
                {featured
                  ? sceneNames[featured.scene_type]
                  : personalAlbum?.title}
              </div>
              <Link
                to={featured ? "/display" : "/albums/" + personalAlbum?.kind}
                className="button primary"
              >
                {featured ? "播放今日记忆" : "回顾自动整理的记忆"}{" "}
                <ArrowRight size={17} />
              </Link>
              <small>
                共 {featured ? feed.data?.items.length : personalAlbum?.total}{" "}
                个瞬间 · {featured ? "为家人用心挑选" : "私密照片只有你能看到"}
              </small>
            </div>
          </>
        ) : (
          <div className="hero-intro">
            <div className="hero-drawing">
              <Camera size={86} strokeWidth={0.7} />
              <span>01</span>
            </div>
            <div>
              <span className="eyebrow">A PLACE FOR YOUR MEMORIES</span>
              <h2>
                {albums.data?.private_photo_count
                  ? "照片已留住，"
                  : "第一张照片，"}
                <br />
                {albums.data?.private_photo_count
                  ? "记忆正在整理。"
                  : "就是故事的开始。"}
              </h2>
              <p>
                上传并选择与家人共享，让珍贵的日常
                <br />
                在这里慢慢汇成家的模样。
              </p>
              {auth.role !== "viewer" && (
                <Link className="button primary" to="/upload">
                  <ImagePlus size={18} />
                  留住一个瞬间
                </Link>
              )}
            </div>
          </div>
        )}
      </section>
      {feed.error && <ErrorBox error={feed.error} />}
      <div className="section-heading">
        <div>
          <span className="eyebrow">LIFE, IN LITTLE CHAPTERS</span>
          <h2>把日子，过成故事</h2>
        </div>
        <Link to="/events">
          所有家庭事件 <ArrowRight size={15} />
        </Link>
      </div>
      <div className="memory-scenes">
        {[
          ["on_this_day", "往年今日", "暂无往年同日或前后 3 天的照片"],
          ["growth", "慢慢长大", "分析完成后自动整理成长相关画面"],
          ["travel", "一起走过", "分析完成后自动整理旅行与出游"],
        ].map(([scene, title, subtitle], i) => {
          const album = albums.data?.items.find((p) => p.kind === scene);
          return (
            <Link
              to={"/albums/" + scene}
              className={"scene-card scene-" + i}
              key={scene}
            >
              {album?.cover ? (
                <img src={album.cover.thumbnail_url} alt={title} />
              ) : (
                <span className="scene-number">0{i + 1}</span>
              )}
              <div>
                <h3>{title}</h3>
                <p>
                  {albums.isPending
                    ? "正在整理…"
                    : albums.error
                      ? "暂时无法读取相册"
                      : album?.total
                        ? `${album.total} 张照片 · ${album.mode === "manual" ? "按人工选择整理" : "已自动整理"}`
                        : subtitle}
                </p>
              </div>
              <ArrowRight size={18} />
            </Link>
          );
        })}
      </div>
      {albums.error && <ErrorBox error={albums.error} />}
      {!!albums.data?.private_photo_count && (
        <p className="muted">
          其中 {albums.data.private_photo_count}{" "}
          张照片仅自己可见，已参与本人的自动相册整理；家庭相框仍只播放已共享照片。
        </p>
      )}
      {events.data && events.data.items.length > 0 && (
        <>
          <div className="section-heading">
            <h2>我们的最近一页</h2>
            <Link to="/events">查看全部 →</Link>
          </div>
          <div className="event-grid">
            {events.data.items.map((e) => (
              <Link className="event-card" to={"/events/" + e.id} key={e.id}>
                {e.cover_photo_id && (
                  <img
                    src={`/api/v1/photos/${e.cover_photo_id}/media/320`}
                    alt="事件封面"
                  />
                )}
                <div>
                  <small>{e.start_at?.slice(0, 10) || "日期待确认"}</small>
                  <h3>{e.title}</h3>
                  <span>
                    {e.photo_ids.length} 张照片 ·{" "}
                    {e.confirmed ? "人工确认" : "待确认"}
                  </span>
                </div>
              </Link>
            ))}
          </div>
        </>
      )}
      <div className="section-heading">
        <div>
          <span className="eyebrow">FRESH FROM THE FAMILY</span>
          <h2>家人新分享</h2>
        </div>
        <Link to="/photos">看看所有照片 →</Link>
      </div>
      {recent.isPending ? (
        <Loading />
      ) : recent.data?.items.length ? (
        <PhotoGrid photos={recent.data.items} />
      ) : (
        <Empty
          title="分享，让家的记忆多一点"
          text="照片默认仅自己可见。上传时勾选共享，家人才会在这里看到。"
          action={auth.role === "viewer" ? "" : "/upload"}
          label="选择照片"
        />
      )}
    </>
  );
}

export interface UploadItem {
  file: File;
  progress: number;
  state: "waiting" | "uploading" | "success" | "duplicate" | "failed";
  error?: string;
  shared: boolean;
}
export function UploadQueue({
  items,
  onRetry,
}: {
  items: UploadItem[];
  onRetry: (index: number) => void;
}) {
  return (
    <div className="upload-list">
      {items.map((item, index) => (
        <div className="upload-row" key={index}>
          <Camera size={19} />
          <div>
            <strong>{item.file.name}</strong>
            <small>
              {item.state === "success"
                ? "上传成功"
                : item.state === "duplicate"
                  ? "识别为完全重复，保留已有原图"
                  : item.state === "failed"
                    ? item.error
                    : item.state === "uploading"
                      ? `上传中 ${item.progress}%`
                      : "等待上传"}{" "}
              · {item.shared ? "家庭共享" : "仅自己"}
            </small>
            <progress value={item.progress} max={100} />
          </div>
          {item.state === "failed" ? (
            <button onClick={() => onRetry(index)}>重试</button>
          ) : item.state === "success" || item.state === "duplicate" ? (
            <CheckCircle2 size={20} color="#547664" />
          ) : (
            <span>{item.progress}%</span>
          )}
        </div>
      ))}
    </div>
  );
}
export function Upload() {
  const auth = useAuth();
  const [shared, setShared] = useState(false);
  const [items, setItems] = useState<UploadItem[]>([]);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const queue = useRef<UploadItem[]>([]);
  const running = useRef(false);
  const qc = useQueryClient();
  const update = () => setItems([...queue.current]);
  async function drain() {
    if (running.current) return;
    running.current = true;
    try {
      for (let index = 0; index < queue.current.length; index++) {
        const item = queue.current[index];
        if (item.state !== "waiting") continue;
        item.state = "uploading";
        update();
        try {
          const result = await uploadPhoto(item.file, item.shared, (p) => {
            item.progress = p;
            update();
          });
          item.state = result.status === "duplicate" ? "duplicate" : "success";
          item.progress = 100;
        } catch (e) {
          item.state = "failed";
          item.error = (e as Error).message;
        }
        update();
      }
      await qc.invalidateQueries({ queryKey: ["recent"] });
      await qc.invalidateQueries({ queryKey: ["photos"] });
    } finally {
      running.current = false;
    }
  }
  function add(files: FileList | File[] | null) {
    if (!files) return;
    queue.current.push(
      ...Array.from(files).map((file) => ({
        file,
        progress: 0,
        state: "waiting" as const,
        shared,
      })),
    );
    update();
    void drain();
  }
  if (auth.role === "viewer")
    return (
      <Empty
        title="当前账号为只读成员"
        text="请联系家庭管理员调整角色后上传。"
        action=""
      />
    );
  return (
    <>
      <PageTitle
        title="留住新的瞬间"
        description="手动选择想收藏的照片。原图留在家里的 Mac，AI 会在后台慢慢整理。"
      />
      <section className="panel upload-panel">
        <div className="share-choice">
          <Check
            checked={shared}
            onChange={setShared}
            label="加入家庭共享相册"
          />
          <p>
            {shared
              ? "本批照片可由家庭成员查看，并用于相框展示、搜索和故事。"
              : auth.household.settings_json.default_visibility === "family"
                ? "家庭建议共享；勾选确认前，本批照片仍仅自己可见。"
                : "本批照片仅自己可见，不进入家庭展示、搜索和故事。"}
          </p>
        </div>
        <div
          className={"dropzone " + (drag ? "dragging" : "")}
          role="button"
          tabIndex={0}
          onClick={() => input.current?.click()}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") input.current?.click();
          }}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            add(e.dataTransfer.files);
          }}
        >
          <CloudUpload size={42} strokeWidth={1.1} />
          <h2>把喜欢的日常，放进来</h2>
          <p>点击选择，或把照片拖到这里</p>
          <span className="button primary">选择照片</span>
          <small>
            JPEG · PNG · WEBP · HEIC / HEIF　每张最多{" "}
            {auth.capabilities?.max_upload_mb || 30}MB
          </small>
        </div>
        <input
          ref={input}
          type="file"
          multiple
          accept="image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif"
          hidden
          onChange={(e) => {
            add(e.target.files);
            e.target.value = "";
          }}
        />
        {items.length > 0 && (
          <>
            <div className="section-heading">
              <h3>上传进度</h3>
              <span>
                成功 {items.filter((i) => i.state === "success").length} · 重复{" "}
                {items.filter((i) => i.state === "duplicate").length} · 失败{" "}
                {items.filter((i) => i.state === "failed").length}
              </span>
            </div>
            <UploadQueue
              items={items}
              onRetry={(i) => {
                queue.current[i].state = "waiting";
                void drain();
              }}
            />
            <Link to="/jobs">查看后台分析任务 →</Link>
          </>
        )}
      </section>
      <div className="note">
        <Sparkles size={20} />
        <div>
          <strong>每一张都妥善保管</strong>
          <p>
            完全重复会被识别，近似连拍只做标记。AI
            不会删除你的原图，也不会猜测家人的姓名或关系。
          </p>
        </div>
      </div>
    </>
  );
}

export function Photos() {
  const auth = useAuth();
  const people = useQuery({
    queryKey: ["photo-filter-people"],
    queryFn: () => apiAll<Person>("/people"),
  });
  const events = useQuery({
    queryKey: ["photo-filter-events"],
    queryFn: () => apiAll<FamilyEvent>("/events"),
  });
  const members = useQuery({
    queryKey: ["photo-filter-members"],
    queryFn: () =>
      api<List<{ user_id: string; display_name: string }>>(
        `/households/${auth.household.id}/members`,
      ),
  });
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(new Set<string>());
  const [filters, setFilters] = useState({
    q: "",
    visibility: "",
    quality: "",
    deleted: false,
    duplicates: false,
    start: "",
    end: "",
    person_id: "",
    event_id: "",
    uploader_id: "",
  });
  const notify = useNotice();
  const qc = useQueryClient();
  const params = new URLSearchParams({
    page: String(page),
    ...Object.fromEntries(
      Object.entries(filters)
        .filter(([, v]) => v !== "")
        .map(([k, v]) => [k, String(v)]),
    ),
  });
  const query = useQuery({
    queryKey: ["photos", params.toString()],
    queryFn: () => api<List<Photo>>("/photos?" + params),
  });
  function toggle(id: string) {
    setSelected((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  }
  async function batch(action: string) {
    if (
      action === "share" &&
      !window.confirm(
        `将所选 ${selected.size} 张照片加入家庭共享？家庭成员及已授权相框将可查看，后续上传的默认设置不变。`,
      )
    )
      return;
    try {
      await api("/photos/batch", "POST", { photo_ids: [...selected], action });
      setSelected(new Set());
      await qc.invalidateQueries({ queryKey: ["photos"] });
      await qc.invalidateQueries({ queryKey: ["albums"] });
      await qc.invalidateQueries({ queryKey: ["album"] });
      notify("已更新所选照片");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  return (
    <>
      <PageTitle
        title={filters.deleted ? "回收站" : "我们的照片库"}
        description="那些一起经历的日子，都在这里。照片说明和拍摄时间随时可以校正。"
        actions={
          auth.role !== "viewer" && (
            <Link to="/upload" className="button primary">
              <ImagePlus size={17} />
              添加照片
            </Link>
          )
        }
      />
      <div className="filters">
        <input
          aria-label="搜索照片说明"
          placeholder="搜索标题、说明、地点…"
          value={filters.q}
          onChange={(e) => {
            setPage(1);
            setFilters({ ...filters, q: e.target.value });
          }}
        />
        <select
          aria-label="可见范围"
          value={filters.visibility}
          onChange={(e) =>
            setFilters({ ...filters, visibility: e.target.value })
          }
        >
          <option value="">所有可见照片</option>
          <option value="family">家庭共享</option>
          <option value="private">仅自己</option>
        </select>
        <select
          aria-label="照片质量"
          value={filters.quality}
          onChange={(e) => setFilters({ ...filters, quality: e.target.value })}
        >
          <option value="">全部质量</option>
          <option value="low">待检查的照片</option>
          <option value="good">质量合格</option>
        </select>
        <Check
          label="近似重复"
          checked={filters.duplicates}
          onChange={(v) => setFilters({ ...filters, duplicates: v })}
        />
        <Check
          label="回收站"
          checked={filters.deleted}
          onChange={(v) => {
            setFilters({ ...filters, deleted: v });
            setSelected(new Set());
          }}
        />
      </div>
      <details className="filter-extra">
        <summary>更多筛选（时间 / 人物 / 事件 / 上传者）</summary>
        <div className="filters">
          <input
            aria-label="开始日期"
            type="date"
            value={filters.start}
            onChange={(e) => setFilters({ ...filters, start: e.target.value })}
          />
          <input
            aria-label="结束日期"
            type="date"
            value={filters.end}
            onChange={(e) => setFilters({ ...filters, end: e.target.value })}
          />
          {["person_id", "event_id", "uploader_id"].map((k) => (
            <select
              key={k}
              aria-label={
                k === "person_id"
                  ? "人物筛选"
                  : k === "event_id"
                    ? "事件筛选"
                    : "上传者筛选"
              }
              value={filters[k as keyof typeof filters] as string}
              onChange={(e) => setFilters({ ...filters, [k]: e.target.value })}
            >
              <option value="">
                {k === "person_id"
                  ? "所有人物"
                  : k === "event_id"
                    ? "所有事件"
                    : "所有上传者"}
              </option>
              {k === "person_id"
                ? people.data?.map((p) => (
                    <option value={p.id} key={p.id}>
                      {p.name || "未命名人物候选"}
                    </option>
                  ))
                : k === "event_id"
                  ? events.data?.map((e) => (
                      <option value={e.id} key={e.id}>
                        {e.title}
                      </option>
                    ))
                  : members.data?.items.map((m) => (
                      <option value={m.user_id} key={m.user_id}>
                        {m.display_name}
                      </option>
                    ))}
            </select>
          ))}
        </div>
      </details>
      {auth.role !== "viewer" && !!query.data?.items.length && (
        <div className="filters">
          <button
            onClick={() =>
              setSelected(
                new Set(
                  query.data.items
                    .filter((p) => p.uploader_id === auth.user.id)
                    .map((p) => p.id),
                ),
              )
            }
          >
            选中本页我的照片
          </button>
          <span className="muted">
            也可点击照片左上角复选框多选；切换分页会清除选择。
          </span>
        </div>
      )}
      {selected.size > 0 && (
        <div className="selection-bar">
          已选 {selected.size} 张
          {!filters.deleted && (
            <button className="primary" onClick={() => void batch("share")}>
              加入家庭共享
            </button>
          )}
          {[
            "favorite",
            "hide",
            filters.deleted ? "restore" : "delete",
            "analyze",
          ].map((a, i) => (
            <button key={a} onClick={() => void batch(a)}>
              {
                [
                  "收藏",
                  "隐藏",
                  filters.deleted ? "恢复" : "移入回收站",
                  "重新分析",
                ][i]
              }
            </button>
          ))}
          <button onClick={() => setSelected(new Set())}>取消选择</button>
        </div>
      )}
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : query.data.items.length ? (
        <PhotoGrid
          photos={query.data.items}
          selected={selected}
          onSelect={auth.role === "viewer" ? undefined : toggle}
        />
      ) : (
        <Empty
          title={filters.deleted ? "回收站是空的" : "没有符合条件的照片"}
          text="可以放宽筛选条件，或为相册添一些新记忆。"
          action={auth.role === "viewer" ? "" : "/upload"}
        />
      )}
      <Pagination
        page={page}
        total={query.data?.total || 0}
        onChange={(p) => {
          setPage(p);
          setSelected(new Set());
        }}
      />
    </>
  );
}

export function PhotoDetail() {
  const { id } = useParams();
  const query = useQuery({
    queryKey: ["photo", id],
    queryFn: () => api<Photo>("/photos/" + id),
    refetchInterval: 5000,
  });
  const notify = useNotice();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [edit, setEdit] = useState(false);
  const [person, setPerson] = useState("");
  const people = useQuery({
    queryKey: ["people"],
    queryFn: () => api<List<{ id: string; name: string | null }>>("/people"),
  });
  async function action(path: string, method = "POST", body?: unknown) {
    try {
      await api(path, method, body);
      await qc.invalidateQueries({ queryKey: ["photo", id] });
      notify("已保存");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  if (query.isPending) return <Loading />;
  if (query.error) return <ErrorBox error={query.error} />;
  const p = query.data;
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const date = f.get("taken_at") as string;
    await action("/photos/" + id, "PATCH", {
      title: f.get("title"),
      caption_user: f.get("caption_user"),
      location_user: f.get("location_user"),
      ...(date ? { taken_at: new Date(date).toISOString() } : {}),
    });
    setEdit(false);
  }
  return (
    <>
      <PageTitle
        title={p.title || "生活里的一个瞬间"}
        description={captureLabel(p)}
        actions={
          <>
            <Link to="/photos" className="button">
              返回照片库
            </Link>
            {p.can_edit && (
              <button onClick={() => setEdit(!edit)}>编辑照片</button>
            )}
          </>
        }
      />
      <div className="detail-layout">
        <div className="detail-media">
          <img src={p.media_url} alt={p.title || "照片"} />
        </div>
        <section className="panel detail-info">
          <span className="eyebrow">A MOMENT TO KEEP</span>
          <h2>{p.title || "一张值得留下的照片"}</h2>
          <p>{p.caption_user || "还没有人工说明。为这个瞬间留下几句话吧。"}</p>
          <dl>
            <dt>拍摄时间</dt>
            <dd>{captureLabel(p)}</dd>
            <dt>地点</dt>
            <dd>
              {p.location_user ||
                (p.gps_lat !== null
                  ? `${p.gps_lat}, ${p.gps_lon}（EXIF）`
                  : "未知")}
            </dd>
            <dt>尺寸</dt>
            <dd>
              {p.width} × {p.height}
            </dd>
            <dt>共享范围</dt>
            <dd>
              {p.visibility === "family" ? "家庭共享" : "仅自己"}
              {p.is_hidden ? " · 已隐藏" : ""}
            </dd>
            <dt>质量评价</dt>
            <dd>
              {p.analysis?.quality_score !== null
                ? p.analysis?.quality_score?.toFixed(2)
                : "等待分析"}
            </dd>
          </dl>
          <div className="actions wrap">
            <button
              onClick={() =>
                void action(`/photos/${id}/feedback`, "POST", {
                  type: "favorite",
                })
              }
            >
              <Heart size={16} />
              收藏
            </button>
            <button
              onClick={() =>
                void action(`/photos/${id}/feedback`, "POST", {
                  type: "unfavorite",
                })
              }
            >
              取消收藏
            </button>
            <button
              onClick={() =>
                void action(`/photos/${id}/feedback`, "POST", { type: "like" })
              }
            >
              喜欢
            </button>
            <button
              onClick={() =>
                void action(`/photos/${id}/feedback`, "POST", {
                  type: "dislike",
                })
              }
            >
              不喜欢
            </button>
            <button
              onClick={() =>
                void action(`/photos/${id}/feedback`, "POST", {
                  type: "snooze",
                })
              }
            >
              近期不再展示
            </button>
            {p.can_edit && (
              <>
                <button
                  onClick={() =>
                    void action("/photos/" + id, "PATCH", {
                      visibility:
                        p.visibility === "family" ? "private" : "family",
                    })
                  }
                >
                  {p.visibility === "family" ? "撤回共享" : "共享给家人"}
                </button>
                <button
                  onClick={() =>
                    void action("/photos/" + id, "PATCH", {
                      is_hidden: !p.is_hidden,
                    })
                  }
                >
                  {p.is_hidden ? "取消隐藏" : "隐藏"}
                </button>
                <button onClick={() => void action(`/photos/${id}/analyze`)}>
                  重新分析
                </button>
                {p.is_deleted ? (
                  <>
                    <button
                      onClick={() => void action(`/photos/${id}/restore`)}
                    >
                      恢复
                    </button>
                    <button
                      className="danger"
                      onClick={async () => {
                        if (
                          window.confirm(
                            "永久删除原图和所有衍生数据？此操作不可恢复。",
                          )
                        ) {
                          try {
                            await api(
                              `/photos/${id}?permanent=true&confirm=${id}`,
                              "DELETE",
                            );
                            navigate("/photos");
                          } catch (e) {
                            notify((e as Error).message);
                          }
                        }
                      }}
                    >
                      永久删除
                    </button>
                  </>
                ) : (
                  <button
                    className="danger"
                    onClick={() => void action("/photos/" + id, "DELETE")}
                  >
                    移入回收站
                  </button>
                )}
              </>
            )}
          </div>
          {!p.is_deleted && (
            <a
              className="text-link"
              href={`/api/v1/photos/${id}/media/original`}
              download
            >
              下载授权原图
            </a>
          )}
        </section>
      </div>
      {edit && (
        <form className="panel form-grid" onSubmit={save}>
          <label>
            标题
            <input name="title" defaultValue={p.title} maxLength={300} />
          </label>
          <label>
            拍摄时间（本机时区，保存后锁定）
            <input name="taken_at" type="datetime-local" />
          </label>
          <label>
            人工地点
            <input name="location_user" defaultValue={p.location_user} />
          </label>
          <label className="full">
            人工说明
            <textarea
              name="caption_user"
              defaultValue={p.caption_user}
              maxLength={4000}
            />
          </label>
          <button className="primary">保存人工校正</button>
        </form>
      )}
      <section className="panel">
        <div className="section-heading">
          <h2>AI 推测 · 等你确认</h2>
          <span className="pill">
            {p.analysis?.status === "succeeded"
              ? "本地模型已分析"
              : "后台处理中 / 模型可能尚未安装"}
          </span>
        </div>
        <p>
          {p.analysis?.caption_ai ||
            "尚无视觉描述。模型安装完成后可在任务页重试。"}
        </p>
        <div className="tags">
          {p.analysis?.tags_json.map((tag, i) => (
            <span key={i}>{tag}</span>
          ))}
        </div>
        {p.analysis?.quality_reasons_json.map((r) => (
          <p key={r}>{r}</p>
        ))}
        <details>
          <summary>分析依据与不确定性</summary>
          <pre>{JSON.stringify(p.analysis?.analysis_json || {}, null, 2)}</pre>
        </details>
        {p.can_edit && (
          <div className="filters">
            <select
              aria-label="手工关联人物"
              value={person}
              onChange={(e) => setPerson(e.target.value)}
            >
              <option value="">手工关联已登记人物</option>
              {people.data?.items.map((person) => (
                <option value={person.id} key={person.id}>
                  {person.name || "未命名人物"}
                </option>
              ))}
            </select>
            <button
              disabled={!person}
              onClick={() => void action(`/people/${person}/photos/${id}`)}
            >
              确认照片中的人物
            </button>
          </div>
        )}
        <details>
          <summary>查看每一步任务状态</summary>
          {p.jobs?.map((j) => (
            <div className="job-line" key={j.id}>
              <span>{j.job_type}</span>
              <span>
                {j.state} · {j.attempts} 次
              </span>
              <small>{j.error_message_sanitized}</small>
            </div>
          ))}
        </details>
      </section>
      <section className="panel">
        <h2>这张照片背后的故事</h2>
        <p className="muted">
          用自己的声音，为照片留一段真实记忆。录音与转写均保存在本地。
        </p>
        <AudioRecorder
          photoId={id}
          onSaved={() => qc.invalidateQueries({ queryKey: ["photo", id] })}
        />
        <MemoriesList
          memories={p.memories || []}
          onChanged={() => qc.invalidateQueries({ queryKey: ["photo", id] })}
        />
      </section>
    </>
  );
}
