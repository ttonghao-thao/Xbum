import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, captureLabel, type Photo } from "../api/client";
import {
  Empty,
  ErrorBox,
  Loading,
  PageTitle,
  Pagination,
  useAuth,
  useNotice,
} from "../components/ui";

interface DuplicateGroup {
  id: string;
  title: string;
  kind: string;
  representative_id: string;
  reason: string;
  items: (Photo & { can_choose: boolean; quality_reasons: string[] })[];
}
export function Duplicates() {
  const [page, setPage] = useState(1);
  const auth = useAuth();
  const notice = useNotice();
  const groups = useQuery({
    queryKey: ["duplicates", page],
    queryFn: () =>
      api<{ items: DuplicateGroup[]; total: number }>(
        `/duplicates?page=${page}`,
      ),
  });
  async function choose(id: string, action: string) {
    try {
      await api(`/duplicates/${id}`, "PUT", { action });
      await groups.refetch();
      notice("已保存选择，原图仍保留");
    } catch (e) {
      notice((e as Error).message);
    }
  }
  return (
    <>
      <PageTitle
        title="为相似瞬间，挑一张代表"
        description="比较连拍候选的清晰度、曝光与细节。系统建议可以更改，原始照片由你保留。"
      />
      {groups.isPending ? (
        <Loading />
      ) : groups.error ? (
        <ErrorBox error={groups.error} />
      ) : !groups.data.items.length ? (
        <Empty
          title="没有待整理的相似照片"
          text="导入的完全重复文件会直接提示；近似照片在这里比较，不能将同一主题等同为重复。"
          action="/photos"
          label="返回照片库"
        />
      ) : (
        groups.data.items.map((group) => (
          <section className="panel" key={group.id}>
            <h2>
              {group.title} · {group.items.length} 张
            </h2>
            <p className="muted">{group.reason}</p>
            <div className="duplicate-compare">
              {group.items.map((p) => (
                <article
                  key={p.id}
                  className={p.id === group.representative_id ? "chosen" : ""}
                >
                  <Link to={`/photos/${p.id}`}>
                    <img src={p.media_url} alt={p.title || "相似照片候选"} />
                  </Link>
                  <h3>{p.title || "生活里的一个瞬间"}</h3>
                  <p>{captureLabel(p)}</p>
                  <p>
                    {p.quality_reasons.join(" · ") ||
                      "质量分析没有提示明显问题"}
                  </p>
                  {p.id === group.representative_id && (
                    <span className="pill">当前代表照片</span>
                  )}
                  {auth.role !== "viewer" && p.can_choose && (
                    <div className="actions wrap">
                      <button
                        onClick={() => void choose(p.id, "representative")}
                      >
                        选为代表
                      </button>
                      <button onClick={() => void choose(p.id, "detach")}>
                        这张不是同一组
                      </button>
                    </div>
                  )}
                </article>
              ))}
            </div>
          </section>
        ))
      )}
      <Pagination
        page={page}
        total={groups.data?.total || 0}
        size={20}
        onChange={setPage}
      />
    </>
  );
}
