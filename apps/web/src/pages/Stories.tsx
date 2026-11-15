import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  Download,
  Film,
  Play,
  Plus,
  Search as SearchIcon,
  X,
} from "lucide-react";
import {
  api,
  type FamilyEvent,
  type Job,
  type List,
  type Person,
  type Photo,
  type Slide,
  type Story,
} from "../api/client";
import {
  Empty,
  ErrorBox,
  Loading,
  PageTitle,
  Pagination,
  PhotoGrid,
  move,
  useAuth,
  useNotice,
} from "../components/ui";
import { AudioRecorder } from "../components/Audio";
import { StoryPlayer } from "./Display";

export function Stories() {
  const [page, setPage] = useState(1);
  const auth = useAuth();
  const notify = useNotice();
  const navigate = useNavigate();
  const query = useQuery({
    queryKey: ["stories", page],
    queryFn: () => api<List<Story>>("/stories?page=" + page),
  });
  const events = useQuery({
    queryKey: ["story-events"],
    queryFn: () => api<List<FamilyEvent>>("/events?page_size=100"),
  });
  const [event, setEvent] = useState("");
  return (
    <>
      <PageTitle
        title="把照片，连成家的故事"
        description="以真实的照片与人工说明为基础，编排一段可以一看再看的家庭纪录片。"
      />
      {auth.role !== "viewer" && (
        <div className="panel filters">
          <select
            aria-label="选择故事事件"
            value={event}
            onChange={(e) => setEvent(e.target.value)}
          >
            <option value="">选择一段家庭事件</option>
            {events.data?.items.map((e) => (
              <option key={e.id} value={e.id}>
                {e.title}
              </option>
            ))}
          </select>
          <button
            className="primary"
            disabled={!event}
            onClick={async () => {
              try {
                const s = await api<Story>("/stories/generate", "POST", {
                  event_id: event,
                });
                navigate("/stories/" + s.id + "/edit");
              } catch (e) {
                notify((e as Error).message);
              }
            }}
          >
            <Plus size={17} />
            生成故事草稿
          </button>
        </div>
      )}
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : query.data.items.length ? (
        <div className="event-grid">
          {query.data.items.map((s) => (
            <article key={s.id} className="event-card">
              {s.cover_photo_id && (
                <img
                  src={`/api/v1/photos/${s.cover_photo_id}/media/320`}
                  alt={s.title}
                />
              )}
              <div>
                <span className="pill">
                  {s.status === "ready" ? "视频已就绪" : "故事草稿"}
                </span>
                <h3>{s.title}</h3>
                <p>
                  {s.slides.length} 页 · 版本 {s.version}
                </p>
                <div className="actions">
                  <Link className="button" to={"/display?story=" + s.id}>
                    <Play size={15} />
                    播放
                  </Link>
                  {auth.role !== "viewer" && (
                    <Link
                      className="button primary"
                      to={"/stories/" + s.id + "/edit"}
                    >
                      编辑故事
                    </Link>
                  )}
                </div>
              </div>
            </article>
          ))}
        </div>
      ) : (
        <Empty
          title="一段故事，藏在几张照片之间"
          text="先把照片整理为事件，再生成一个可以编辑、播放和导出的真实故事。"
          action="/events"
          label="看看家庭事件"
        />
      )}
      <Pagination
        page={page}
        total={query.data?.total || 0}
        onChange={setPage}
      />
    </>
  );
}

export function StoryEdit() {
  const { id } = useParams();
  const auth = useAuth();
  const notify = useNotice();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["story", id],
    queryFn: () => api<Story>("/stories/" + id),
  });
  const [story, setStory] = useState<Story | null>(null);
  const [preview, setPreview] = useState(false);
  const [jobId, setJobId] = useState("");
  const [pickerPage, setPickerPage] = useState(1);
  const library = useQuery({
    queryKey: ["story-photo-picker", pickerPage],
    queryFn: () =>
      api<List<Photo>>("/photos?visibility=family&page=" + pickerPage),
  });
  const job = useQuery({
    queryKey: ["export-job", jobId],
    queryFn: () => api<Job>("/jobs/" + jobId),
    enabled: !!jobId,
    refetchInterval: (q) =>
      q.state.data && ["succeeded", "failed"].includes(q.state.data.state)
        ? false
        : 2000,
  });
  useEffect(() => {
    if (query.data) setStory(query.data);
  }, [query.data]);
  useEffect(() => {
    if (job.data?.state === "succeeded") {
      void qc.invalidateQueries({ queryKey: ["story", id] });
      notify("MP4 已生成，可下载到本地");
    }
  }, [job.data?.state]);
  if (query.isPending) return <Loading />;
  if (query.error) return <ErrorBox error={query.error} />;
  if (!story) return <Loading />;
  const draft = story;
  function slidePatch(index: number, key: keyof Slide, value: unknown) {
    setStory((s) =>
      s
        ? {
            ...s,
            slides: s.slides.map((slide, i) =>
              i === index ? { ...slide, [key]: value } : slide,
            ),
          }
        : s,
    );
  }
  async function save() {
    try {
      const result = await api<Story>("/stories/" + id, "PATCH", {
        title: draft.title,
        theme: draft.theme,
        background: draft.background,
        ending: draft.ending,
        cover_photo_id: draft.cover_photo_id,
        slides: draft.slides.map(
          ({ photo_id, caption, duration_s, transition }) => ({
            photo_id,
            caption,
            duration_s,
            transition,
          }),
        ),
      });
      setStory(result);
      notify("故事已保存");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  if (auth.role === "viewer")
    return (
      <Empty
        title="此账号只能观看故事"
        action="/stories"
        label="返回故事列表"
      />
    );
  return (
    <>
      <PageTitle
        title="编排一段家的故事"
        description="草稿来自真实元数据与人工说明，不把 AI 推测写成已经发生的事实。"
        actions={
          <>
            <button onClick={() => setPreview(true)}>
              <Play size={17} />
              预览
            </button>
            <button className="primary" onClick={() => void save()}>
              保存故事
            </button>
          </>
        }
      />
      <section className="panel form-grid">
        <label>
          故事标题
          <input
            value={draft.title}
            maxLength={300}
            onChange={(e) => setStory({ ...draft, title: e.target.value })}
          />
        </label>
        <label>
          主题
          <select
            value={draft.theme}
            onChange={(e) =>
              setStory({ ...draft, theme: e.target.value as Story["theme"] })
            }
          >
            <option value="warm">温暖日常</option>
            <option value="classic">时光胶片</option>
            <option value="night">静夜回忆</option>
          </select>
        </label>
        <label>
          背景颜色
          <input
            type="color"
            value={draft.background}
            onChange={(e) => setStory({ ...draft, background: e.target.value })}
          />
        </label>
        <label>
          封面
          <select
            value={draft.cover_photo_id || ""}
            onChange={(e) =>
              setStory({ ...draft, cover_photo_id: e.target.value })
            }
          >
            {draft.slides.map((s, i) => (
              <option value={s.photo_id} key={i}>
                第 {i + 1} 张
              </option>
            ))}
          </select>
        </label>
        <label className="full">
          结尾
          <textarea
            value={draft.ending}
            maxLength={2000}
            onChange={(e) => setStory({ ...draft, ending: e.target.value })}
          />
        </label>
      </section>
      <div className="story-editor">
        {draft.slides.map((slide, index) => (
          <article className="panel slide-editor" key={slide.photo_id + index}>
            <span className="slide-index">
              {String(index + 1).padStart(2, "0")}
            </span>
            <img
              src={`/api/v1/photos/${slide.photo_id}/media/320`}
              alt={`故事第 ${index + 1} 页`}
            />
            <div>
              <label>
                这一页的文字
                <textarea
                  value={slide.caption}
                  maxLength={2000}
                  onChange={(e) => slidePatch(index, "caption", e.target.value)}
                />
              </label>
              <div className="filters">
                <label>
                  停留秒数
                  <input
                    type="number"
                    min={1}
                    max={60}
                    value={slide.duration_s}
                    onChange={(e) =>
                      slidePatch(index, "duration_s", Number(e.target.value))
                    }
                  />
                </label>
                <label>
                  转场
                  <select
                    value={slide.transition}
                    onChange={(e) =>
                      slidePatch(index, "transition", e.target.value)
                    }
                  >
                    <option value="fade">淡入淡出</option>
                    <option value="cut">直接切换</option>
                  </select>
                </label>
              </div>
            </div>
            <div className="slide-tools">
              <button
                aria-label="故事页上移"
                disabled={index === 0}
                onClick={() =>
                  setStory({ ...draft, slides: move(draft.slides, index, -1) })
                }
              >
                <ArrowUp size={16} />
              </button>
              <button
                aria-label="故事页下移"
                disabled={index === draft.slides.length - 1}
                onClick={() =>
                  setStory({ ...draft, slides: move(draft.slides, index, 1) })
                }
              >
                <ArrowDown size={16} />
              </button>
              <button
                aria-label="删除故事页"
                disabled={draft.slides.length === 1}
                onClick={() =>
                  setStory({
                    ...draft,
                    slides: draft.slides.filter((_, i) => i !== index),
                  })
                }
              >
                <X size={16} />
              </button>
            </div>
          </article>
        ))}
      </div>
      <details className="panel">
        <summary>添加照片到故事</summary>
        <div className="photo-picker">
          {library.data?.items
            .filter(
              (p) =>
                !p.is_hidden && !draft.slides.some((s) => s.photo_id === p.id),
            )
            .map((p) => (
              <button
                key={p.id}
                onClick={() =>
                  setStory({
                    ...draft,
                    slides: [
                      ...draft.slides,
                      {
                        photo_id: p.id,
                        duration_s: 6,
                        caption: p.caption_user || p.title,
                        transition: "fade",
                      },
                    ],
                  })
                }
              >
                <img src={p.thumbnail_url} alt={p.title || "候选照片"} />
                <Plus size={16} />
              </button>
            ))}
        </div>
        <Pagination
          page={pickerPage}
          total={library.data?.total || 0}
          onChange={setPickerPage}
        />
      </details>
      <section className="panel export-panel">
        <Film size={36} strokeWidth={1} />
        <div>
          <h2>让记忆成为一部小电影</h2>
          <p>
            本地 FFmpeg 合成 720p 静音 MP4，文字位于照片下方。请先保存最新编辑。
          </p>
          <div className="actions">
            <button
              onClick={async () => {
                try {
                  const r = await api<{ job_id: string }>(
                    `/stories/${id}/export`,
                    "POST",
                  );
                  setJobId(r.job_id);
                  notify("视频导出任务已入队");
                } catch (e) {
                  notify((e as Error).message);
                }
              }}
            >
              生成 MP4
            </button>
            <a className="button" href={`/api/v1/stories/${id}/json`} download>
              <Download size={16} />
              故事 JSON
            </a>
            {(draft.status === "ready" || job.data?.state === "succeeded") && (
              <a
                className="button primary"
                href={`/api/v1/stories/${id}/export`}
                download
              >
                下载 MP4
              </a>
            )}
          </div>
          {job.data && (
            <p role="status">
              导出状态：{job.data.state} {job.data.error_message_sanitized}
            </p>
          )}
        </div>
      </section>
      <button
        className="danger"
        onClick={async () => {
          if (window.confirm("删除这个故事？原始照片会保留。"))
            try {
              await api("/stories/" + id, "DELETE");
              navigate("/stories");
            } catch (e) {
              notify((e as Error).message);
            }
        }}
      >
        删除故事
      </button>
      {preview && (
        <StoryPlayer story={draft} onClose={() => setPreview(false)} />
      )}
    </>
  );
}

interface SearchResult extends List<Photo> {
  query_id: string | null;
  job_id: string | null;
  semantic_state: string;
  time_basis: string;
  unknown_conditions: string[];
}
export function Search() {
  const [text, setText] = useState("");
  const [filters, setFilters] = useState({
    start: "",
    end: "",
    person_id: "",
    event_id: "",
    location: "",
  });
  const [active, setActive] = useState<Record<string, unknown> | null>(null);
  const queryId = useRef<string | null>(null);
  const [page, setPage] = useState(1);
  const people = useQuery({
    queryKey: ["search-people"],
    queryFn: () => api<List<Person>>("/people?page_size=100"),
  });
  const events = useQuery({
    queryKey: ["search-events"],
    queryFn: () => api<List<FamilyEvent>>("/events?page_size=100"),
  });
  const query = useQuery({
    queryKey: ["search", active, page],
    queryFn: async () => {
      const r = await api<SearchResult>("/search", "POST", {
        ...active,
        page,
        ...(queryId.current ? { query_id: queryId.current } : {}),
      });
      queryId.current = r.query_id;
      return r;
    },
    enabled: active !== null,
    refetchInterval: (q) =>
      q.state.data &&
      ["queued", "retry", "running"].includes(q.state.data.semantic_state)
        ? 3000
        : false,
  });
  function submit(e?: FormEvent) {
    e?.preventDefault();
    queryId.current = null;
    setPage(1);
    setActive({
      query: text,
      semantic: true,
      ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)),
    });
  }
  return (
    <>
      <PageTitle
        title="你想起了，哪一段时光？"
        description="用一句话，或一段声音，寻找留在家里的记忆。结果会告诉你匹配的依据。"
      />
      <form className="panel search-panel" onSubmit={submit}>
        <div className="search-bar">
          <SearchIcon size={23} />
          <input
            aria-label="搜索家庭记忆"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="试试「去年春节的合影」或「海边的照片」"
            maxLength={500}
          />
          <button className="primary">寻找记忆</button>
        </div>
        <div className="filters">
          <label>
            开始日期
            <input
              type="date"
              value={filters.start}
              onChange={(e) =>
                setFilters({ ...filters, start: e.target.value })
              }
            />
          </label>
          <label>
            结束日期
            <input
              type="date"
              value={filters.end}
              onChange={(e) => setFilters({ ...filters, end: e.target.value })}
            />
          </label>
          <label>
            人物
            <select
              value={filters.person_id}
              onChange={(e) =>
                setFilters({ ...filters, person_id: e.target.value })
              }
            >
              <option value="">所有已确认人物</option>
              {people.data?.items
                .filter((p) => p.manually_confirmed)
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
            </select>
          </label>
          <label>
            事件
            <select
              value={filters.event_id}
              onChange={(e) =>
                setFilters({ ...filters, event_id: e.target.value })
              }
            >
              <option value="">所有事件</option>
              {events.data?.items.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            地点
            <input
              value={filters.location}
              onChange={(e) =>
                setFilters({ ...filters, location: e.target.value })
              }
              placeholder="人工地点"
            />
          </label>
        </div>
      </form>
      <details className="panel">
        <summary>用本地语音寻找记忆</summary>
        <AudioRecorder
          search
          onText={(value) => {
            setText(value);
          }}
        />
        <p className="muted">校对转写结果并使用文字后，再点击「寻找记忆」。</p>
      </details>
      {!active ? (
        <Empty
          title="有些记忆，只差一句话"
          text="也可以选择人物、事件、日期或真实地点。未知时间的照片不会假装成往年回忆。"
          action=""
        />
      ) : query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : (
        query.data && (
          <>
            <div className="section-heading">
              <h2>找到 {query.data.total} 个候选瞬间</h2>
              <span>
                {query.data.time_basis}
                {["queued", "running", "retry"].includes(
                  query.data.semantic_state,
                )
                  ? " · 本地图文编码正在排队"
                  : " · " +
                    (query.data.semantic_state === "failed"
                      ? "语义模型不可用，已显示文字匹配"
                      : "文字 / 语义检索")}
              </span>
            </div>
            {query.data.unknown_conditions.map((s) => (
              <p className="note" key={s}>
                {s}
              </p>
            ))}
            {query.data.items.length ? (
              <>
                <PhotoGrid photos={query.data.items} />
                <div className="match-reasons">
                  {query.data.items.map((p) => (
                    <p key={p.id}>
                      <Link to={"/photos/" + p.id}>
                        {p.title || p.id.slice(0, 8)}
                      </Link>
                      ：{p.match_reasons?.join(" · ")}
                    </p>
                  ))}
                </div>
              </>
            ) : (
              <Empty
                title="还没有找到这段记忆"
                text="可以放宽日期、移除人物条件，或换一个关键词。没有匹配时，我们不会制造回忆。"
                action=""
              />
            )}
            <Pagination
              page={page}
              total={query.data.total}
              onChange={setPage}
            />
          </>
        )
      )}
    </>
  );
}
