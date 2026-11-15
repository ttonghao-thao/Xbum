import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  BookOpen,
  CalendarDays,
  Merge,
  Plus,
  RefreshCw,
  Scissors,
} from "lucide-react";
import {
  api,
  captureLabel,
  eventNames,
  type FamilyEvent,
  type List,
  type Person,
  type Photo,
  type Story,
} from "../api/client";
import {
  Check,
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
import { AudioRecorder, MemoriesList } from "../components/Audio";

export function Events() {
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ["events", page],
    queryFn: () => api<List<FamilyEvent>>("/events?page=" + page),
  });
  const [chosen, setChosen] = useState(new Set<string>());
  const [title, setTitle] = useState("一起走过的日子");
  const auth = useAuth();
  const notify = useNotice();
  const qc = useQueryClient();
  async function merge() {
    try {
      await api("/events/merge", "POST", { event_ids: [...chosen], title });
      setChosen(new Set());
      await qc.invalidateQueries({ queryKey: ["events"] });
      notify("已合并并锁定，自动聚类不会覆盖");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  return (
    <>
      <PageTitle
        title="每段日子，都有一页故事"
        description="系统按时间与照片线索整理候选事件。你可以确认、合并或拆分，家的故事由你定义。"
        actions={
          auth.role !== "viewer" && (
            <button
              onClick={async () => {
                try {
                  await api("/events/recluster", "POST");
                  notify("重新整理任务已入队");
                } catch (e) {
                  notify((e as Error).message);
                }
              }}
            >
              <RefreshCw size={16} />
              重新整理候选
            </button>
          )
        }
      />
      {chosen.size > 1 && (
        <div className="selection-bar">
          <input
            aria-label="合并事件标题"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
          <button onClick={() => void merge()}>
            <Merge size={16} />
            合并 {chosen.size} 个事件并锁定
          </button>
        </div>
      )}
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : query.data.items.length ? (
        <div className="event-grid">
          {query.data.items.map((e) => (
            <article className="event-card" key={e.id}>
              {auth.role !== "viewer" && (
                <label className="photo-select">
                  <input
                    type="checkbox"
                    aria-label={"选择事件 " + e.title}
                    checked={chosen.has(e.id)}
                    onChange={() =>
                      setChosen((s) => {
                        const n = new Set(s);
                        n.has(e.id) ? n.delete(e.id) : n.add(e.id);
                        return n;
                      })
                    }
                  />
                </label>
              )}
              <Link to={"/events/" + e.id}>
                {e.cover_photo_id && (
                  <img
                    src={`/api/v1/photos/${e.cover_photo_id}/media/320`}
                    alt={e.title}
                  />
                )}
                <div>
                  <span className="pill">
                    {eventNames[e.event_type]} ·{" "}
                    {e.confirmed ? "人工确认" : "AI 候选"}
                  </span>
                  <h3>{e.title}</h3>
                  <p>
                    {e.start_at?.slice(0, 10) || "时间未知"} ·{" "}
                    {e.photo_ids.length} 张
                  </p>
                  <small>{e.locked ? "人工锁定" : "可自动整理"}</small>
                </div>
              </Link>
            </article>
          ))}
        </div>
      ) : (
        <Empty
          title="事件，会在照片里慢慢长出来"
          text="上传有拍摄时间的共享照片，后台会整理事件候选；无时间的照片请先校正。"
          action={auth.role === "viewer" ? "" : "/upload"}
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

export function EventDetail() {
  const { id } = useParams();
  const auth = useAuth();
  const editable = auth.role !== "viewer";
  const notify = useNotice();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const query = useQuery({
    queryKey: ["event", id],
    queryFn: () => api<FamilyEvent>("/events/" + id),
  });
  const [pickerPage, setPickerPage] = useState(1);
  const [addedPhotos, setAddedPhotos] = useState<Record<string, Photo>>({});
  const library = useQuery({
    queryKey: ["photo-picker", pickerPage],
    queryFn: () =>
      api<List<Photo>>(
        `/photos?visibility=family&page_size=100&page=${pickerPage}`,
      ),
    enabled: editable,
  });
  const [chosen, setChosen] = useState(new Set<string>());
  const [splitTitle, setSplitTitle] = useState("另一段记忆");
  const [order, setOrder] = useState<string[]>([]);
  const [addId, setAddId] = useState("");
  useEffect(() => {
    if (query.data) setOrder(query.data.photo_ids);
  }, [query.data]);
  async function patch(body: unknown) {
    try {
      await api("/events/" + id, "PATCH", body);
      await qc.invalidateQueries({ queryKey: ["event", id] });
      notify("已保存人工修正并锁定");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  if (query.isPending) return <Loading />;
  if (query.error) return <ErrorBox error={query.error} />;
  const event = query.data;
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    await patch({
      ...Object.fromEntries(data),
      confirmed: data.get("confirmed") === "on",
      photo_ids: order,
    });
  }
  return (
    <>
      <PageTitle
        title={event.title}
        description={`${event.start_at?.slice(0, 10) || "日期未知"} — ${event.end_at?.slice(0, 10) || "日期未知"} · ${event.confirmed ? "人工确认" : "AI 候选"} · 修订 ${event.revision}`}
        actions={
          <>
            <Link to="/events" className="button">
              所有事件
            </Link>
            {editable && (
              <button
                className="primary"
                onClick={async () => {
                  try {
                    const story = await api<Story>(
                      "/stories/generate",
                      "POST",
                      { event_id: id },
                    );
                    navigate("/stories/" + story.id + "/edit");
                  } catch (e) {
                    notify((e as Error).message);
                  }
                }}
              >
                <BookOpen size={17} />
                生成故事草稿
              </button>
            )}
          </>
        }
      />
      {editable && (
        <form className="panel form-grid" onSubmit={save}>
          <label>
            事件标题
            <input
              name="title"
              defaultValue={event.title}
              required
              maxLength={300}
            />
          </label>
          <label>
            类型
            <select name="event_type" defaultValue={event.event_type}>
              {Object.entries(eventNames).map(([k, v]) => (
                <option value={k} key={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
          <label>
            真实地点
            <input
              name="location_label"
              defaultValue={event.location_label}
              placeholder="不确定时留空"
            />
          </label>
          <label>
            封面
            <select
              name="cover_photo_id"
              defaultValue={event.cover_photo_id || ""}
            >
              {order.map((pid) => (
                <option value={pid} key={pid}>
                  {event.photos?.find((p) => p.id === pid)?.title ||
                    pid.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          <label className="full">
            简述
            <textarea name="summary" defaultValue={event.summary} />
          </label>
          <label className="check">
            <input
              type="checkbox"
              name="confirmed"
              defaultChecked={event.confirmed}
            />
            我确认这是实际发生的事件
          </label>
          <button className="primary">保存事件与照片顺序</button>
        </form>
      )}
      <div className="section-heading">
        <h2>事件里的瞬间</h2>
        <span>{order.length} 张照片</span>
      </div>
      {editable && (
        <div className="filters">
          <select
            aria-label="添加照片到事件"
            value={addId}
            onChange={(e) => setAddId(e.target.value)}
          >
            <option value="">选择共享照片加入</option>
            {library.data?.items
              .filter((p) => !order.includes(p.id) && !p.is_hidden)
              .map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title || captureLabel(p)}
                </option>
              ))}
          </select>
          <button
            disabled={!addId}
            onClick={() => {
              const photo = library.data?.items.find((p) => p.id === addId);
              if (photo)
                setAddedPhotos((existing) => ({
                  ...existing,
                  [photo.id]: photo,
                }));
              setOrder([...order, addId]);
              setAddId("");
            }}
          >
            <Plus size={16} />
            加入（保存生效）
          </button>
          {chosen.size > 0 && (
            <>
              <button
                onClick={() => {
                  setOrder(order.filter((pid) => !chosen.has(pid)));
                  setChosen(new Set());
                }}
              >
                从事件移除所选
              </button>
              <input
                aria-label="拆分后事件标题"
                value={splitTitle}
                onChange={(e) => setSplitTitle(e.target.value)}
              />
              <button
                onClick={async () => {
                  try {
                    await api(`/events/${id}/split`, "POST", {
                      photo_ids: [...chosen],
                      title: splitTitle,
                    });
                    setChosen(new Set());
                    await qc.invalidateQueries({ queryKey: ["event", id] });
                    notify("已拆分，两个事件均已人工锁定");
                  } catch (e) {
                    notify((e as Error).message);
                  }
                }}
              >
                <Scissors size={16} />
                拆分所选
              </button>
            </>
          )}
        </div>
      )}
      {editable && (
        <Pagination
          page={pickerPage}
          total={library.data?.total || 0}
          size={100}
          onChange={(p) => {
            setPickerPage(p);
            setAddId("");
          }}
        />
      )}
      <div className="ordered-photos">
        {order.map((pid, index) => {
          const p =
            event.photos?.find((p) => p.id === pid) ||
            library.data?.items.find((p) => p.id === pid) ||
            addedPhotos[pid];
          return (
            p && (
              <div className="ordered-row" key={pid}>
                {editable && (
                  <input
                    type="checkbox"
                    aria-label={"选择 " + pid}
                    checked={chosen.has(pid)}
                    onChange={() =>
                      setChosen((s) => {
                        const n = new Set(s);
                        n.has(pid) ? n.delete(pid) : n.add(pid);
                        return n;
                      })
                    }
                  />
                )}
                <Link to={"/photos/" + pid}>
                  <img src={p.thumbnail_url} alt={p.title || "事件照片"} />
                </Link>
                <div>
                  <strong>{p.title || "生活里的一个瞬间"}</strong>
                  <small>{captureLabel(p)}</small>
                </div>
                {editable && (
                  <>
                    <button
                      aria-label="向前移动照片"
                      disabled={index === 0}
                      onClick={() => setOrder(move(order, index, -1))}
                    >
                      <ArrowUp size={16} />
                    </button>
                    <button
                      aria-label="向后移动照片"
                      disabled={index === order.length - 1}
                      onClick={() => setOrder(move(order, index, 1))}
                    >
                      <ArrowDown size={16} />
                    </button>
                  </>
                )}
              </div>
            )
          );
        })}
      </div>
      {event.segments_json.length > 1 && (
        <section className="panel">
          <h3>按天片段</h3>
          <div className="tags">
            {event.segments_json.map((s) => (
              <span key={s.date}>
                {s.date} · {s.photo_ids.length} 张
              </span>
            ))}
          </div>
        </section>
      )}
      <section className="panel">
        <h2>给这段日子留一段声音</h2>
        <AudioRecorder
          eventId={id}
          onSaved={() => qc.invalidateQueries({ queryKey: ["event", id] })}
        />
        <MemoriesList
          memories={event.memories || []}
          onChanged={() => qc.invalidateQueries({ queryKey: ["event", id] })}
        />
      </section>
    </>
  );
}

function PersonCard({
  person,
  onChanged,
}: {
  person: Person;
  onChanged: () => void;
}) {
  const auth = useAuth();
  const notify = useNotice();
  const [edit, setEdit] = useState(false);
  const [chosen, setChosen] = useState(new Set<string>());
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    try {
      await api("/people/" + person.id, "PATCH", {
        name: f.get("name"),
        relationship: f.get("relationship"),
        birthday: f.get("birthday") || null,
        is_family: f.get("is_family") === "on",
        manually_confirmed: f.get("manually_confirmed") === "on",
        growth_mode: f.get("growth_mode"),
      });
      setEdit(false);
      onChanged();
    } catch (e) {
      notify((e as Error).message);
    }
  }
  return (
    <article className="panel person-card">
      {person.photo_ids[0] ? (
        <img
          className="person-cover"
          src={`/api/v1/photos/${person.photo_ids[0]}/media/320`}
          alt={person.name || "未知人物候选"}
        />
      ) : (
        <div className="person-placeholder">{person.name?.[0] || "?"}</div>
      )}
      <h2>{person.name || "未命名人物"}</h2>
      <p>
        {person.relationship || "关系未填写"} · {person.photo_ids.length} 张照片
      </p>
      <span className="pill">
        {person.manually_confirmed ? "人工确认" : "人脸聚类候选"}
      </span>
      {person.growth_enabled && (
        <Link className="text-link" to={"/timeline?person=" + person.id}>
          查看成长线 →
        </Link>
      )}
      {auth.role !== "viewer" && (
        <button onClick={() => setEdit(!edit)}>
          {edit ? "收起" : "命名与纠错"}
        </button>
      )}
      {edit && (
        <>
          <form onSubmit={save}>
            <label>
              人物姓名
              <input name="name" defaultValue={person.name || ""} />
            </label>
            <label>
              关系（由你填写）
              <input
                name="relationship"
                defaultValue={person.relationship || ""}
              />
            </label>
            <label>
              生日
              <input
                type="date"
                name="birthday"
                defaultValue={person.birthday || ""}
              />
            </label>
            <label className="check">
              <input
                type="checkbox"
                name="manually_confirmed"
                defaultChecked={person.manually_confirmed}
              />
              人工确认身份
            </label>
            <label className="check">
              <input
                type="checkbox"
                name="is_family"
                defaultChecked={person.is_family}
              />
              已登记的家庭成员
            </label>
            <label>
              成长相册整理
              <select
                name="growth_mode"
                defaultValue={
                  person.growth_mode ||
                  (person.growth_enabled ? "include" : "auto")
                }
              >
                <option value="auto">由系统自动整理</option>
                <option value="include">手动指定为成长人物</option>
                <option value="exclude">不纳入成长相册</option>
              </select>
            </label>
            <button className="primary">保存人物</button>
          </form>
          <details>
            <summary>误聚类？选择人脸拆开</summary>
            <div className="face-picker">
              {person.faces.map((f) => (
                <label key={f.id}>
                  <img src={`/api/v1/faces/${f.id}/media`} alt="人脸来源照片" />
                  <input
                    type="checkbox"
                    checked={chosen.has(f.id)}
                    onChange={() =>
                      setChosen((s) => {
                        const n = new Set(s);
                        n.has(f.id) ? n.delete(f.id) : n.add(f.id);
                        return n;
                      })
                    }
                  />
                </label>
              ))}
            </div>
            <button
              disabled={!chosen.size}
              onClick={async () => {
                try {
                  await api("/people/split", "POST", {
                    person_id: person.id,
                    face_ids: [...chosen],
                  });
                  setChosen(new Set());
                  onChanged();
                } catch (e) {
                  notify((e as Error).message);
                }
              }}
            >
              拆为另一个未知人物
            </button>
          </details>
        </>
      )}
    </article>
  );
}
export function People() {
  const auth = useAuth();
  const [page, setPage] = useState(1);
  const [chosen, setChosen] = useState(new Set<string>());
  const [name, setName] = useState("");
  const qc = useQueryClient();
  const notify = useNotice();
  const query = useQuery({
    queryKey: ["people", page],
    queryFn: () => api<List<Person>>("/people?page=" + page),
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["people"] });
  };
  return (
    <>
      <PageTitle
        title="是家人，也是故事的主角"
        description="人物姓名、关系和生日由你确认。人脸分析默认关闭，识别结果只是候选。"
        actions={
          auth.role === "owner" && (
            <Link to="/settings" className="button">
              人脸授权设置
            </Link>
          )
        }
      />
      {auth.role !== "viewer" && (
        <div className="panel filters">
          <input
            aria-label="新人物姓名"
            placeholder="也可不做人脸分析，手工登记人物"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button
            disabled={!name.trim()}
            onClick={async () => {
              try {
                await api("/people", "POST", {
                  name,
                  manually_confirmed: true,
                  is_family: true,
                });
                setName("");
                refresh();
              } catch (e) {
                notify((e as Error).message);
              }
            }}
          >
            <Plus size={16} />
            登记家庭人物
          </button>
          {chosen.size > 1 && (
            <button
              onClick={async () => {
                try {
                  await api("/people/merge", "POST", {
                    person_ids: [...chosen],
                  });
                  setChosen(new Set());
                  refresh();
                } catch (e) {
                  notify((e as Error).message);
                }
              }}
            >
              合并所选人物
            </button>
          )}
        </div>
      )}
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : query.data.items.length ? (
        <div className="people-grid">
          {query.data.items.map((p) => (
            <div key={p.id}>
              {auth.role !== "viewer" && (
                <Check
                  label="选择合并"
                  checked={chosen.has(p.id)}
                  onChange={() =>
                    setChosen((s) => {
                      const n = new Set(s);
                      n.has(p.id) ? n.delete(p.id) : n.add(p.id);
                      return n;
                    })
                  }
                />
              )}
              <PersonCard person={p} onChanged={refresh} />
            </div>
          ))}
        </div>
      ) : (
        <Empty
          title="把熟悉的面孔，写上名字"
          text="可以手工登记家人并关联照片，也可以明确授权后，使用本地人脸候选聚类。"
          action=""
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

export function Timeline() {
  const auth = useAuth();
  const notify = useNotice();
  const qc = useQueryClient();
  const [person, setPerson] = useState(
    new URLSearchParams(location.search).get("person") || "",
  );
  const [page, setPage] = useState(1);
  const people = useQuery({
    queryKey: ["people-timeline"],
    queryFn: () => api<List<Person>>("/people?page_size=100"),
  });
  const query = useQuery({
    queryKey: ["timeline", person, page],
    queryFn: () =>
      api<
        List<Photo> & {
          milestones?: {
            id: string;
            title: string;
            date: string;
            note: string;
          }[];
        }
      >(
        (person
          ? "/timeline?page_size=100&page="
          : "/albums/growth?page_size=100&page=") +
          page +
          (person ? "&person_id=" + person : ""),
      ),
  });
  const navigate = useNavigate();
  async function mark(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.currentTarget));
    try {
      await api("/milestones", "POST", { ...f, person_id: person });
      await qc.invalidateQueries({ queryKey: ["timeline"] });
      notify("已记录人工确认的里程碑");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  const groups: Record<string, Photo[]> = {};
  query.data?.items.forEach((p) => {
    const parts = p.taken_at
      ? new Intl.DateTimeFormat("en-CA", {
          timeZone: auth.household.timezone,
          year: "numeric",
          month: "2-digit",
        }).formatToParts(new Date(p.taken_at))
      : [];
    const key = parts.length
      ? `${parts.find((v) => v.type === "year")?.value}-${parts.find((v) => v.type === "month")?.value}`
      : "日期未知";
    (groups[key] ??= []).push(p);
  });
  return (
    <>
      <PageTitle
        title="一点一点，慢慢长大"
        description="系统自动整理成长相关画面和跨月人物记录，人工指定优先。姓名、关系与重要的「第一次」由你确认。"
        actions={
          person &&
          auth.role !== "viewer" && (
            <button
              className="primary"
              onClick={async () => {
                try {
                  const s = await api<Story>("/stories/generate", "POST", {
                    person_id: person,
                  });
                  navigate("/stories/" + s.id + "/edit");
                } catch (e) {
                  notify((e as Error).message);
                }
              }}
            >
              生成成长故事
            </button>
          )
        }
      />
      <div className="filters">
        <select
          aria-label="成长人物"
          value={person}
          onChange={(e) => {
            setPerson(e.target.value);
            setPage(1);
          }}
        >
          <option value="">自动整理的成长相册</option>
          {people.data?.items
            .filter(
              (p) => p.manually_confirmed && p.is_family && p.growth_enabled,
            )
            .map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}的成长线
              </option>
            ))}
        </select>
      </div>
      {person && auth.role !== "viewer" && (
        <form className="panel filters" onSubmit={mark}>
          <input
            name="title"
            aria-label="里程碑名称"
            placeholder="亲自确认的重要时刻"
            required
          />
          <input name="date" aria-label="里程碑日期" type="date" required />
          <input
            name="note"
            aria-label="里程碑说明"
            placeholder="为这一天留几句话"
          />
          <button>
            <Plus size={16} />
            记录里程碑
          </button>
        </form>
      )}
      {query.data?.milestones?.map((m) => (
        <div className="milestone" key={m.id}>
          <CalendarDays size={22} />
          <div>
            <small>{m.date} · 人工确认</small>
            <h3>{m.title}</h3>
            <p>{m.note}</p>
          </div>
          {auth.role !== "viewer" && (
            <button
              className="danger"
              onClick={async () => {
                try {
                  await api("/milestones/" + m.id, "DELETE");
                  await qc.invalidateQueries({ queryKey: ["timeline"] });
                } catch (e) {
                  notify((e as Error).message);
                }
              }}
            >
              删除
            </button>
          )}
        </div>
      ))}
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : Object.keys(groups).length ? (
        Object.entries(groups).map(([month, photos]) => (
          <section className="timeline-section" key={month}>
            <h2>
              {month.replace("-", " 年 ")}
              {month !== "日期未知" ? " 月" : ""}
              <small>{photos.length} 个瞬间</small>
            </h2>
            <PhotoGrid photos={photos} />
          </section>
        ))
      ) : (
        <Empty
          title="时光的刻度，等待你来记录"
          text="后台分析完成后会自动整理成长相关画面，无需先指定人物。暂未发现成长线索的照片不会填充进来。"
          action="/photos"
          label="查看照片与处理状态"
        />
      )}
      <Pagination
        page={page}
        total={query.data?.total || 0}
        size={100}
        onChange={setPage}
      />
    </>
  );
}
