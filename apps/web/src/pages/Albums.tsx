import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type AlbumPhoto, type MemoryAlbum } from "../api/client";
import { AlbumPlayer } from "../components/AlbumPlayer";
import { YearComparison } from "../components/YearComparison";
import {
  Empty,
  ErrorBox,
  Loading,
  PageTitle,
  Pagination,
  PhotoGrid,
  useNotice,
  useAuth,
} from "../components/ui";

const titles: Record<string, string> = {
  on_this_day: "往年今日",
  growth: "慢慢长大",
  travel: "一起走过",
  recent: "最近的日子",
};
const emptyText: Record<string, string> = {
  on_this_day:
    "没有符合日期的往年照片。这里只收录有拍摄时间的往年同月同日及前后 3 天照片，不会用全部照片填充。",
  growth:
    "还没有足够的成长线索。系统会在分析完成后自动整理成长相关画面与跨月人物记录，无需先配置人物。",
  travel:
    "还没有发现旅行或出游线索。照片分析与事件整理完成后会自动出现在这里，无需先手动创建旅行事件。",
  recent:
    "没有这一时段的照片。可选择其他日期，拍摄时间未知的照片不会冒充最近拍摄。",
};

export function Album() {
  const notify = useNotice();
  const auth = useAuth();
  const { kind = "" } = useParams();
  const [page, setPage] = useState(1);
  const [day, setDay] = useState("");
  const [personId, setPersonId] = useState("");
  const [group, setGroup] = useState("");
  const [period, setPeriod] = useState("month");
  const [view, setView] = useState("all");
  useEffect(() => {
    setPage(1);
    setDay("");
    setPersonId("");
    setGroup("");
    setView("all");
    setPeriod("month");
  }, [kind]);
  const params = new URLSearchParams();
  if (day) params.set("day", day);
  if (personId) params.set("person_id", personId);
  if (group) params.set("group", group);
  if (kind === "recent" || kind === "growth") params.set("period", period);
  const filter = params.toString();
  const query = useQuery({
    queryKey: ["album", kind, page, filter],
    queryFn: () => api<MemoryAlbum>(`/albums/${kind}?page=${page}&${filter}`),
    enabled: kind in titles,
    refetchInterval: 5000,
  });
  if (!(kind in titles)) return <ErrorBox error="相册不存在" />;
  const groups = new Map<string, AlbumPhoto[]>();
  (view === "highlights" ? query.data?.highlights : query.data?.items)?.forEach(
    (p) => {
      groups.set(p.album_group, [...(groups.get(p.album_group) || []), p]);
    },
  );
  return (
    <>
      <PageTitle
        title={titles[kind]}
        description={query.data?.description || "系统正在读取自动整理的相册。"}
        actions={
          <Link className="button" to="/">
            回到家庭记忆
          </Link>
        }
      />
      <nav className="filters" aria-label="自动相册">
        {Object.entries(titles).map(([key, title]) => (
          <Link
            key={key}
            to={"/albums/" + key}
            className={"button " + (kind === key ? "primary" : "")}
            aria-current={kind === key ? "page" : undefined}
          >
            {title}
          </Link>
        ))}
        {kind === "growth" && (
          <Link className="button" to="/timeline">
            查看成长时间线
          </Link>
        )}
      </nav>
      {(kind === "on_this_day" || kind === "recent") && (
        <div className="filters">
          <label>
            {kind === "on_this_day" ? "回忆日期" : "查看日期"}
            <input
              type="date"
              aria-label={kind === "on_this_day" ? "回忆日期" : "查看日期"}
              value={day || query.data?.date_basis || ""}
              onChange={(e) => {
                setDay(e.target.value);
                setPage(1);
              }}
            />
          </label>
          <button
            onClick={() => {
              setDay("");
              setPage(1);
            }}
          >
            回到今天
          </button>
        </div>
      )}
      {kind === "recent" && (
        <label className="filters">
          整理维度
          <select
            aria-label="整理维度"
            value={period}
            onChange={(e) => {
              setPeriod(e.target.value);
              setPage(1);
              setGroup("");
            }}
          >
            <option value="month">所选月份</option>
            <option value="day">最近 30 天（按日）</option>
          </select>
        </label>
      )}
      {kind === "growth" && (
        <label className="filters">
          成长年鉴
          <select
            aria-label="成长年鉴维度"
            value={period}
            onChange={(e) => {
              setPeriod(e.target.value);
              setPage(1);
              setGroup("");
            }}
          >
            <option value="month">按月回顾</option>
            <option value="year">按年回顾</option>
          </select>
        </label>
      )}
      <AlbumPlayer
        title={titles[kind]}
        endpoint={`/albums/${kind}/playback?${filter}`}
      />
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : (
        <>
          <div className="filters album-content-controls">
            <h2>对应照片</h2>
            <button
              className={view === "all" ? "primary" : ""}
              onClick={() => setView("all")}
            >
              全部照片
            </button>
            <button
              className={view === "highlights" ? "primary" : ""}
              onClick={() => setView("highlights")}
            >
              精选照片
            </button>
            {query.data.groups && (
              <select
                aria-label="相册分组"
                value={group}
                onChange={(e) => {
                  setGroup(e.target.value);
                  setPage(1);
                }}
              >
                <option value="">全部分组</option>
                {query.data.groups.map((g) => (
                  <option key={g.id} value={g.id}>
                    {g.title} · {g.total} 张
                  </option>
                ))}
              </select>
            )}
            {kind === "growth" && (
              <label>
                已确认人物
                <select
                  aria-label="成长人物"
                  value={personId}
                  onChange={(e) => {
                    setPersonId(e.target.value);
                    setPage(1);
                    setGroup("");
                  }}
                >
                  <option value="">自动成长画面</option>
                  {query.data.people?.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name || "已确认人物"}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button
              onClick={async () => {
                try {
                  await api(`/album-pins/${kind}`, "PUT", {
                    pinned: !query.data.pinned,
                  });
                  await query.refetch();
                } catch (e) {
                  notify((e as Error).message);
                }
              }}
            >
              {query.data.pinned ? "取消固定" : "固定此相册"}
            </button>
          </div>
          <p className="muted">
            共 {query.data.total} 张照片 ·{" "}
            {query.data.mode === "manual" ? "按人工选择整理" : "系统自动整理"}
            {kind === "on_this_day" &&
              (query.data.match_window_days === 0
                ? " · 往年同日"
                : " · 往年前后 3 天")}
            {query.data.pending_count > 0 &&
              ` · ${query.data.pending_count} 张照片仍在后台分析，完成后自动更新`}
          </p>
          {query.data.explanation && (
            <p className="muted">
              {typeof query.data.explanation === "string"
                ? query.data.explanation
                : Array.isArray(query.data.explanation)
                  ? query.data.explanation.join(" · ")
                  : query.data.explanation.messages?.join(" · ")}
            </p>
          )}
          <p className="muted">
            当前整理范围：我的照片与家人共享的照片。私密照片仅本人登录可见，不会进入家庭相框、共享故事或家庭搜索。
          </p>
          {!!query.data.private_photo_count && (
            <p className="muted">
              你有 {query.data.private_photo_count}{" "}
              张仅自己可见的照片，已参与本人的自动整理，原共享权限保持不变。
            </p>
          )}
          {query.data.failed_count > 0 && (
            <p className="error">
              {query.data.failed_count}{" "}
              张照片的视觉分析失败，已有日期分类不受影响；可在
              <Link to="/jobs">处理任务</Link>查看原因。
            </p>
          )}
          {kind === "growth" && (
            <p className="muted">
              未命名画面只作为成长候选，不表示同一孩子；人脸聚类仅在授权后运行。可随时在家庭人物中修正自动选择。
            </p>
          )}
          {kind === "growth" && personId && (
            <YearComparison key={personId} personId={personId} />
          )}
          {groups.size ? (
            [...groups].map(([key, photos]) => (
              <section className="timeline-section" key={key}>
                <h2>
                  {photos[0].album_group_title}{" "}
                  <small>{photos.length} 个瞬间</small>
                </h2>
                {photos[0].album_event_id && (
                  <Link to={"/events/" + photos[0].album_event_id}>
                    查看这段事件 →
                  </Link>
                )}
                {query.data.groups
                  ?.find((g) => g.id === key)
                  ?.milestones?.map((m) => (
                    <div className="milestone" key={m.id}>
                      <div>
                        <small>{m.date} · 人工确认的里程碑</small>
                        <h3>{m.title}</h3>
                        <p>{m.note}</p>
                      </div>
                    </div>
                  ))}
                {kind === "travel" && (
                  <p className="muted">
                    {Object.entries(
                      query.data.groups?.find((g) => g.id === key)?.dates || {},
                    )
                      .map(([date, count]) => `${date} · ${count} 张`)
                      .join(" / ")}
                  </p>
                )}
                <PhotoGrid
                  photos={photos}
                  preferUserId={
                    auth.role === "viewer" ? undefined : auth.user.id
                  }
                  onPreferred={async (p) => {
                    try {
                      await api(`/album-photos/${p.id}/preferred`, "PUT", {
                        preferred: !p.preferred,
                      });
                      await query.refetch();
                    } catch (e) {
                      notify((e as Error).message);
                    }
                  }}
                />
              </section>
            ))
          ) : (
            <Empty
              title={
                query.data.pending_count > 0 && kind !== "on_this_day"
                  ? "正在整理已有照片"
                  : "这段记忆，等待相遇"
              }
              text={
                query.data.pending_count > 0 && kind !== "on_this_day"
                  ? "历史照片与新照片正在使用本地模型依次分析，完成后会自动更新此相册，无需重新上传或手动分类。"
                  : emptyText[kind]
              }
              action="/photos"
              label="查看照片与处理状态"
            />
          )}
          {view === "all" && (
            <Pagination
              page={page}
              total={query.data.total}
              onChange={setPage}
            />
          )}
        </>
      )}
    </>
  );
}
