import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type AlbumPhoto, type MemoryAlbum } from "../api/client";
import {
  Empty,
  ErrorBox,
  Loading,
  PageTitle,
  Pagination,
  PhotoGrid,
} from "../components/ui";

const titles: Record<string, string> = {
  on_this_day: "往年今日",
  growth: "慢慢长大",
  travel: "一起走过",
};
const emptyText: Record<string, string> = {
  on_this_day:
    "没有符合日期的往年照片。这里只收录有拍摄时间的往年同月同日及前后 3 天照片，不会用全部照片填充。",
  growth:
    "还没有足够的成长线索。系统会在分析完成后自动整理成长相关画面与跨月人物记录，无需先配置人物。",
  travel:
    "还没有发现旅行或出游线索。照片分析与事件整理完成后会自动出现在这里，无需先手动创建旅行事件。",
};

export function Album() {
  const { kind = "" } = useParams();
  const [page, setPage] = useState(1);
  const [day, setDay] = useState("");
  useEffect(() => {
    setPage(1);
    setDay("");
  }, [kind]);
  const query = useQuery({
    queryKey: ["album", kind, page, day],
    queryFn: () =>
      api<MemoryAlbum>(
        `/albums/${kind}?page=${page}${kind === "on_this_day" && day ? "&day=" + day : ""}`,
      ),
    enabled: kind in titles,
    refetchInterval: 5000,
  });
  if (!(kind in titles)) return <ErrorBox error="相册不存在" />;
  const groups = new Map<string, AlbumPhoto[]>();
  query.data?.items.forEach((p) => {
    groups.set(p.album_group, [...(groups.get(p.album_group) || []), p]);
  });
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
      {kind === "on_this_day" && (
        <div className="filters">
          <label>
            回忆日期
            <input
              type="date"
              aria-label="回忆日期"
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
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : (
        <>
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
                <PhotoGrid photos={photos} />
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
          <Pagination page={page} total={query.data.total} onChange={setPage} />
        </>
      )}
    </>
  );
}
