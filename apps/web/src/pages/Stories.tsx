import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  Download,
  Film,
  Play,
  Plus,
  X,
} from "lucide-react";
import {
  api,
  type FamilyEvent,
  type Job,
  type List,
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
  move,
  useAuth,
  useNotice,
} from "../components/ui";
import { StoryPlayer } from "./Display";
import {
  StoryEnhancements,
  MemorySuggestions,
} from "../components/StoryEnhancements";

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
      <MemorySuggestions />
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
    refetchInterval: (q) =>
      q.state.data?.composition?.state === "queued" ? 2000 : false,
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
  const composing = draft.composition?.state === "queued";
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
  if (composing)
    return (
      <>
        <PageTitle
          title="正在编排这段记忆"
          description="系统正在挑选代表照片并按日期组织章节，完成后即可编辑。"
          actions={
            <Link className="button" to="/stories">
              返回故事列表
            </Link>
          }
        />
        {draft.composition?.job_state === "failed" ? (
          <div className="panel">
            <ErrorBox
              error={draft.composition.error_message || "本地编排失败，请重试"}
            />
            <button
              onClick={async () => {
                try {
                  await api(`/jobs/${draft.composition?.job_id}/retry`, "POST");
                  await query.refetch();
                } catch (e) {
                  notify((e as Error).message);
                }
              }}
            >
              重试故事编排
            </button>
          </div>
        ) : (
          <Loading />
        )}
      </>
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
      <StoryEnhancements storyId={id} onUpdated={() => void query.refetch()} />
      {draft.composition && (
        <div className="panel">
          <h2>故事编排</h2>
          <p>
            {draft.composition.state === "queued"
              ? "正在本地挑选照片与组织章节，完成后自动更新。"
              : draft.composition.state === "ready"
                ? "已按日期与画面多样性编排，文案来自可追溯事实。"
                : "编排已停止，保留人工修改。"}
          </p>
          {draft.composition.chapters?.map((c) => (
            <span className="pill" key={c.title}>
              {c.title} · {c.photo_ids.length} 张
            </span>
          ))}
        </div>
      )}
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
