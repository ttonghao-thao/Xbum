import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  apiAll,
  captureLabel,
  type Photo,
  type Person,
  type List,
  type Job,
} from "../api/client";
import {
  Empty,
  ErrorBox,
  Loading,
  PageTitle,
  Pagination,
  PhotoGrid,
  useAuth,
  useNotice,
} from "../components/ui";
import { AlbumPlayer } from "../components/AlbumPlayer";
import { AudioRecorder } from "../components/Audio";

interface SearchBody {
  query: string;
  start?: string;
  end?: string;
  person_ids: string[];
  people_mode: "all" | "any";
  terms: string[];
  exclude_terms: string[];
  reference_photo_id?: string;
  semantic: boolean;
  query_id?: string;
  page: number;
}
interface SearchResult extends List<Photo> {
  query_id: string | null;
  semantic_state: string;
  time_basis: string;
  unknown_conditions: string[];
  parsed_conditions: {
    start: string | null;
    end: string | null;
    terms: string[];
    exclude_terms: string[];
    people: { id: string; name: string }[];
    people_mode: string;
  };
}
interface Collection extends List<Photo> {
  id: string;
  title: string;
  mode: "static" | "dynamic";
  pinned: boolean;
  cover: Photo | null;
  query_json: SearchBody;
  include_ids_json: string[];
  exclude_ids_json: string[];
  rule_warning?: string;
}
const words = (value: string) =>
  value
    .split(/[,，\n]/)
    .map((t) => t.trim())
    .filter(Boolean);

export function DiscoverSearch() {
  const auth = useAuth();
  const notice = useNotice();
  const navigate = useNavigate();
  const [url] = useSearchParams();
  const [text, setText] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [persons, setPersons] = useState<string[]>([]);
  const [mode, setMode] = useState<"all" | "any">("all");
  const [terms, setTerms] = useState("");
  const [exclude, setExclude] = useState("");
  const [semantic, setSemantic] = useState(true);
  const [reference, setReference] = useState(url.get("reference") || "");
  const [body, setBody] = useState<SearchBody>();
  const [result, setResult] = useState<SearchResult>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [title, setTitle] = useState("");
  const semanticPending =
    !!result && ["queued", "running", "retry"].includes(result.semantic_state);
  const people = useQuery({
    queryKey: ["search-people"],
    queryFn: () => apiAll<Person>("/people"),
  });
  const poll = useQuery({
    queryKey: ["search-semantic", result?.query_id, body],
    queryFn: () =>
      api<SearchResult>("/search", "POST", {
        ...body,
        query_id: result?.query_id,
      }),
    enabled:
      !!body &&
      !!result?.query_id &&
      ["queued", "running", "retry"].includes(result.semantic_state),
    refetchInterval: 1500,
  });
  useEffect(() => {
    if (poll.data) setResult(poll.data);
  }, [poll.data]);
  async function run(payload: SearchBody) {
    setBusy(true);
    setError(undefined);
    setBody(payload);
    try {
      setResult(await api<SearchResult>("/search", "POST", payload));
    } catch (e) {
      setError(e);
      setResult(undefined);
    } finally {
      setBusy(false);
    }
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    void run({
      query: text,
      start: start || undefined,
      end: end || undefined,
      person_ids: persons,
      people_mode: mode,
      terms: words(terms),
      exclude_terms: words(exclude),
      reference_photo_id: reference || undefined,
      semantic,
      page: 1,
    });
  }
  async function save(kind: "static" | "dynamic") {
    if (!body || !result) return;
    if (kind === "static" && semanticPending) return;
    setBusy(true);
    try {
      const ids: string[] = [];
      if (kind === "static") {
        for (let page = 1; ; page++) {
          const rows = await api<SearchResult>("/search", "POST", {
            ...body,
            query_id: result.query_id || undefined,
            semantic: false,
            page,
            page_size: 100,
          });
          ids.push(...rows.items.map((p) => p.id));
          if (!rows.items.length || ids.length >= rows.total) break;
        }
      }
      const created = await api<Collection>("/collections", "POST", {
        title: title.trim() || text.trim() || "我的记忆相册",
        mode: kind,
        query: body,
        photo_ids: ids,
      });
      navigate("/collections/" + created.id);
    } catch (e) {
      notice((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        title="寻找记忆"
        description="组合拍摄时间、已确认人物和画面线索，寻找家庭共享的照片。"
        actions={
          <Link className="button" to="/collections">
            我的智能相册
          </Link>
        }
      />
      <form className="panel form-grid" onSubmit={submit}>
        <label className="span-all">
          搜索家庭记忆
          <input
            aria-label="搜索家庭记忆"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="去年夏天，小明和妈妈在海边的合照"
          />
        </label>
        <label>
          开始日期
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label>
          结束日期
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
        <label>
          人物条件
          <select
            multiple
            value={persons}
            onChange={(e) =>
              setPersons(Array.from(e.target.selectedOptions, (p) => p.value))
            }
          >
            {people.data
              ?.filter((p) => p.manually_confirmed)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name || "已确认人物"}
                  {p.relationship ? " · " + p.relationship : ""}
                </option>
              ))}
          </select>
        </label>
        <label>
          人物匹配方式
          <select
            value={mode}
            onChange={(e) => setMode(e.target.value as "all" | "any")}
          >
            <option value="all">所选人物全部同框</option>
            <option value="any">任意一人出现</option>
          </select>
        </label>
        <label>
          必须匹配的内容
          <input
            value={terms}
            onChange={(e) => setTerms(e.target.value)}
            placeholder="海边，合照"
          />
        </label>
        <label>
          排除内容
          <input
            value={exclude}
            onChange={(e) => setExclude(e.target.value)}
            placeholder="截图，票据"
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={semantic}
            onChange={(e) => setSemantic(e.target.checked)}
          />
          使用本地语义排序
        </label>
        {reference && (
          <p>
            正在以已授权照片寻找相似画面。
            <button type="button" onClick={() => setReference("")}>
              取消以图找图
            </button>
          </p>
        )}
        <button className="primary" disabled={busy}>
          寻找记忆
        </button>
      </form>
      {auth.role !== "viewer" && <AudioRecorder search onText={setText} />}
      {error && <ErrorBox error={error} />}
      {poll.error && <ErrorBox error={poll.error} />}
      {busy && <p role="status">正在整理搜索条件…</p>}
      {result && (
        <>
          <h2>找到 {result.total} 个候选瞬间</h2>
          <div className="panel">
            <strong>本次匹配条件</strong>
            <p>
              {result.parsed_conditions?.start || "不限开始日期"} —{" "}
              {result.parsed_conditions?.end || "不限结束日期"} ·{" "}
              {result.parsed_conditions?.people_mode === "all"
                ? "全部同框"
                : "任一出现"}
            </p>
            <p>
              {result.parsed_conditions?.people.map((p) => p.name).join("、") ||
                "未指定人物"}{" "}
              · 必须包含：
              {result.parsed_conditions?.terms.join("、") || "未指定"} · 排除：
              {result.parsed_conditions?.exclude_terms.join("、") || "无"}
            </p>
            {result.unknown_conditions.map((t) => (
              <p className="error" key={t}>
                {t}；请先确认或调整条件。
              </p>
            ))}
            {["queued", "running", "retry"].includes(result.semantic_state) && (
              <p role="status">
                已显示结构化匹配；本地语义排序正在等待或处理中。
              </p>
            )}
            {result.semantic_state === "failed" && (
              <p className="error">语义排序失败，当前为文字及筛选结果。</p>
            )}
          </div>
          {result.items.length ? (
            <PhotoGrid photos={result.items} />
          ) : (
            <Empty
              title="还没找到这段记忆"
              text="请检查人物与日期，或主动放宽某项条件；系统不会用其他照片补足。"
            />
          )}
          {result.items.map((p) => (
            <details key={p.id}>
              <summary>{p.title || captureLabel(p)} · 匹配依据</summary>
              <ul>
                {p.match_reasons?.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
              <Link to={"/photos/" + p.id}>查看照片和文字区域</Link>
            </details>
          ))}
          <Pagination
            page={body?.page || 1}
            total={result.total}
            onChange={(page) =>
              body &&
              void run({
                ...body,
                query_id: result.query_id || undefined,
                page,
              })
            }
          />
          {auth.role !== "viewer" && result.total > 0 && (
            <section className="panel">
              <h3>把这段记忆留下来</h3>
              <label>
                相册名称
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder={text || "我的记忆相册"}
                  maxLength={300}
                />
              </label>
              <p>
                保存照片会固定当前匹配集合；保存规则将按文字和筛选条件自动补充新照片。相册仅你可见，照片共享范围保持原值。
              </p>
              <button
                disabled={busy || result.total > 3000 || semanticPending}
                onClick={() => void save("static")}
              >
                保存这批照片
              </button>{" "}
              <button
                disabled={busy || !!body?.reference_photo_id}
                onClick={() => void save("dynamic")}
              >
                保存规则，自动补充
              </button>
              {semanticPending && (
                <p>语义排序完成后可保存完整的当前照片集合。</p>
              )}
            </section>
          )}
        </>
      )}
    </>
  );
}

export function Collections() {
  const query = useQuery({
    queryKey: ["collections"],
    queryFn: () => api<{ items: Collection[]; total: number }>("/collections"),
    refetchInterval: 5000,
  });
  return (
    <>
      <PageTitle
        title="我的智能相册"
        description="由你保存的照片集合和自动补充规则；只有你能查看和编辑。"
        actions={
          <Link className="button" to="/search">
            一句话整理相册
          </Link>
        }
      />
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : query.data.items.length ? (
        <div className="people-grid">
          {query.data.items.map((c) => (
            <Link className="panel" key={c.id} to={"/collections/" + c.id}>
              {c.cover && (
                <img
                  className="collection-cover"
                  src={c.cover.thumbnail_url}
                  alt={c.title}
                />
              )}
              <h2>
                {c.pinned ? "★ " : ""}
                {c.title}
              </h2>
              <p>
                {c.mode === "dynamic" ? "自动补充" : "固定照片"} · {c.total} 张
              </p>
            </Link>
          ))}
        </div>
      ) : (
        <Empty
          title="留下一段属于你的记忆"
          text="在搜索中找到照片后，保存当前集合或自动补充规则。"
          action="/search"
          label="寻找照片"
        />
      )}
    </>
  );
}

export function CollectionDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const notice = useNotice();
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [chosen, setChosen] = useState<string[]>([]);
  const [add, setAdd] = useState<string[]>([]);
  const [libraryPage, setLibraryPage] = useState(1);
  const query = useQuery({
    queryKey: ["collection", id, page],
    queryFn: () => api<Collection>(`/collections/${id}?page=${page}`),
    refetchInterval: 5000,
  });
  const library = useQuery({
    queryKey: ["collection-picker", libraryPage],
    queryFn: () =>
      api<List<Photo>>(`/photos?visibility=family&page=${libraryPage}`),
  });
  async function patch(data: unknown) {
    try {
      await api(`/collections/${id}`, "PATCH", data);
      setChosen([]);
      setAdd([]);
      await qc.invalidateQueries({ queryKey: ["collection", id] });
      await qc.invalidateQueries({ queryKey: ["collections"] });
    } catch (e) {
      notice((e as Error).message);
    }
  }
  const c = query.data;
  return (
    <>
      <PageTitle
        title={c?.title || "智能相册"}
        description="上方回忆影片与下方全部照片来自同一个集合。"
        actions={
          <Link className="button" to="/collections">
            全部智能相册
          </Link>
        }
      />
      <AlbumPlayer
        title={c?.title || "智能相册"}
        endpoint={`/collections/${id}/playback`}
      />
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : (
        c && (
          <>
            {c.rule_warning && (
              <p className="error">
                {c.rule_warning} <Link to="/search">重新搜索并保存</Link>
              </p>
            )}
            <section className="panel filters">
              <button onClick={() => void patch({ pinned: !c.pinned })}>
                {c.pinned ? "取消固定" : "固定到列表前方"}
              </button>
              <button
                onClick={() => {
                  const title = window.prompt("相册名称", c.title);
                  if (title?.trim()) void patch({ title });
                }}
              >
                修改名称
              </button>
              <button
                disabled={!chosen.length}
                onClick={() =>
                  void patch({
                    exclude_ids: [
                      ...new Set([...c.exclude_ids_json, ...chosen]),
                    ],
                    include_ids: c.include_ids_json.filter(
                      (p) => !chosen.includes(p),
                    ),
                  })
                }
              >
                从相册移除所选
              </button>
              <button
                disabled={chosen.length !== 1}
                onClick={() => void patch({ cover_photo_id: chosen[0] })}
              >
                将所选照片设为封面
              </button>
              {c.exclude_ids_json.length > 0 && (
                <button onClick={() => void patch({ exclude_ids: [] })}>
                  恢复当前可见的已排除照片
                </button>
              )}
              <button
                className="danger"
                onClick={async () => {
                  if (window.confirm("删除此相册？原照片仍然保留。")) {
                    try {
                      await api(`/collections/${id}`, "DELETE");
                      navigate("/collections");
                    } catch (e) {
                      notice((e as Error).message);
                    }
                  }
                }}
              >
                删除相册
              </button>
            </section>
            <h2>相册全部照片 · {c.total} 张</h2>
            <div className="filters">
              {c.items.map((p) => (
                <label key={p.id}>
                  <input
                    type="checkbox"
                    checked={chosen.includes(p.id)}
                    onChange={(e) =>
                      setChosen((v) =>
                        e.target.checked
                          ? [...v, p.id]
                          : v.filter((i) => i !== p.id),
                      )
                    }
                  />
                  {p.title || captureLabel(p)}
                </label>
              ))}
            </div>
            <PhotoGrid photos={c.items} />
            <Pagination
              page={page}
              total={c.total}
              onChange={(value) => {
                setChosen([]);
                setPage(value);
              }}
            />
            <details className="panel">
              <summary>手动加入家庭共享照片</summary>
              <p>
                手动选择优先于自动匹配，移除或加入相册不会删除原图或修改共享范围。
              </p>
              <div className="photo-grid">
                {library.data?.items.map((p) => (
                  <label key={p.id}>
                    <img
                      src={p.thumbnail_url}
                      alt={p.title || "家庭共享照片"}
                    />
                    <input
                      type="checkbox"
                      checked={add.includes(p.id)}
                      onChange={(e) =>
                        setAdd((v) =>
                          e.target.checked
                            ? [...v, p.id]
                            : v.filter((i) => i !== p.id),
                        )
                      }
                    />
                    {p.title || captureLabel(p)}
                  </label>
                ))}
              </div>
              <Pagination
                page={libraryPage}
                total={library.data?.total || 0}
                onChange={(value) => {
                  setAdd([]);
                  setLibraryPage(value);
                }}
              />
              <button
                disabled={!add.length}
                onClick={() =>
                  void patch({
                    include_ids: [...new Set([...c.include_ids_json, ...add])],
                    exclude_ids: c.exclude_ids_json.filter(
                      (i) => !add.includes(i),
                    ),
                  })
                }
              >
                加入所选照片
              </button>
            </details>
          </>
        )
      )}
    </>
  );
}

interface TextRecord {
  text: string;
  corrected_text: string | null;
  confirmed: boolean;
  regions_json: { text: string; bbox: number[] }[];
  model_version: string;
}
export function PhotoTextEditor({ photoId }: { photoId: string }) {
  const auth = useAuth();
  const notice = useNotice();
  const qc = useQueryClient();
  const [value, setValue] = useState("");
  const dirty = useRef(false);
  const [regionIndex, setRegionIndex] = useState<number | null>(null);
  const photo = useQuery({
    queryKey: ["photo", photoId],
    queryFn: () => api<Photo>(`/photos/${photoId}`),
  });
  const query = useQuery({
    queryKey: ["photo-text", photoId],
    queryFn: () =>
      api<{ text: TextRecord | null; job: Job | null }>(
        `/photos/${photoId}/text`,
      ),
    refetchInterval: 3000,
  });
  useEffect(() => {
    dirty.current = false;
    setValue("");
    setRegionIndex(null);
  }, [photoId]);
  useEffect(() => {
    if (query.data?.text && !dirty.current)
      setValue(
        query.data.text.confirmed
          ? query.data.text.corrected_text || ""
          : query.data.text.text,
      );
  }, [photoId, query.data?.text?.text, query.data?.text?.corrected_text]);
  async function request() {
    try {
      await api(`/photos/${photoId}/ocr`, "POST");
      notice("图片文字识别已加入本地队列");
      await qc.invalidateQueries({ queryKey: ["photo-text", photoId] });
    } catch (e) {
      notice((e as Error).message);
    }
  }
  async function save() {
    try {
      await api(`/photos/${photoId}/text`, "PATCH", { text: value });
      dirty.current = false;
      notice("图片文字校对已保存");
      await qc.invalidateQueries({ queryKey: ["photo-text", photoId] });
    } catch (e) {
      notice((e as Error).message);
    }
  }
  return (
    <section className="panel">
      <h3>照片中的文字</h3>
      <p>本地识别的文字只作为查找线索，不会自动修改拍摄地点或日期。</p>
      {query.error && <ErrorBox error={query.error} />}
      <p>
        {query.data?.text?.confirmed
          ? "人工校对"
          : query.data?.text
            ? "本地识别，待核对"
            : "尚未识别图片文字"}
        {query.data?.job ? " · " + query.data.job.state : ""}
      </p>
      {query.data?.job?.state === "failed" && (
        <p className="error">{query.data.job.error_message_sanitized}</p>
      )}
      <textarea
        aria-label="图片文字校对"
        value={value}
        onChange={(e) => {
          dirty.current = true;
          setValue(e.target.value);
        }}
        rows={5}
        readOnly={auth.role === "viewer"}
      />
      {query.data?.text?.regions_json.length ? (
        <details>
          <summary>查看识别区域</summary>
          {photo.data && !photo.isError && (
            <div style={{ position: "relative", maxWidth: 960 }}>
              <img
                src={photo.data.media_url}
                alt="核对图片文字原图"
                style={{ display: "block", width: "100%", height: "auto" }}
              />
              {query.data.text.regions_json.map((region, i) => (
                <button
                  type="button"
                  key={i}
                  aria-label={`高亮文字区域 ${i + 1}：${region.text}`}
                  aria-pressed={regionIndex === i}
                  onClick={() => setRegionIndex(i)}
                  style={{
                    position: "absolute",
                    left: region.bbox[0] * 100 + "%",
                    top: region.bbox[1] * 100 + "%",
                    width: region.bbox[2] * 100 + "%",
                    height: region.bbox[3] * 100 + "%",
                    padding: 0,
                    minHeight: 0,
                    border:
                      "2px solid " +
                      (regionIndex === i ? "#dc6b29" : "#2c9a85"),
                    background:
                      regionIndex === i
                        ? "rgba(255,174,40,.25)"
                        : "transparent",
                    borderRadius: 0,
                  }}
                />
              ))}
            </div>
          )}
          <p>点击图上的框或下方文字，核对识别内容。</p>
          {query.data.text.regions_json.map((region, i) => (
            <button
              type="button"
              key={i}
              onClick={() => setRegionIndex(i)}
              aria-pressed={regionIndex === i}
            >
              {regionIndex === i ? <mark>{region.text}</mark> : region.text}
            </button>
          ))}
        </details>
      ) : null}
      {auth.role !== "viewer" && (
        <>
          <button onClick={() => void request()}>识别图片文字</button>{" "}
          <button onClick={() => void save()}>保存文字校对</button>
        </>
      )}
    </section>
  );
}
