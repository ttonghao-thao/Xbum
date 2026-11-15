import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, captureLabel, type MemoryAlbum } from "../api/client";
import { ErrorBox, Loading } from "./ui";

function YearPhoto({ personId, year }: { personId: string; year: string }) {
  const [selected, setSelected] = useState("");
  const query = useQuery({
    queryKey: ["growth-comparison", personId, year],
    queryFn: () =>
      api<MemoryAlbum>(
        `/albums/growth?person_id=${encodeURIComponent(personId)}&period=year&group=${encodeURIComponent(year)}&page_size=1`,
      ),
    refetchInterval: 5000,
  });
  const photos = query.data?.highlights?.length
    ? query.data.highlights
    : query.data?.items || [];
  const photo =
    photos.find((p) => p.id === selected) ||
    photos.find((p) => p.preferred) ||
    photos[0];
  if (query.error) return <ErrorBox error={query.error} />;
  if (query.isPending) return <Loading />;
  return (
    <div className="year-comparison-photo">
      {photo ? (
        <>
          <Link to={`/photos/${photo.id}`}>
            <img src={photo.media_url} alt={`${year} 年的成长记录`} />
          </Link>
          <p>
            {captureLabel(photo)}
            {photo.visibility === "private" ? " · 仅自己可见" : ""}
          </p>
          <label>
            本年代表照片
            <select
              aria-label={`${year} 年对照照片`}
              value={photo.id}
              onChange={(e) => setSelected(e.target.value)}
            >
              {photos.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title || captureLabel(p)}
                </option>
              ))}
            </select>
          </label>
        </>
      ) : (
        <p>这一年没有当前可见的照片。</p>
      )}
    </div>
  );
}

export function YearComparison({ personId }: { personId: string }) {
  const [left, setLeft] = useState("");
  const [right, setRight] = useState("");
  const query = useQuery({
    queryKey: ["growth-comparison-years", personId],
    queryFn: () =>
      api<MemoryAlbum>(
        `/albums/growth?person_id=${encodeURIComponent(personId)}&period=year&page_size=1`,
      ),
    refetchInterval: 5000,
  });
  const years = query.data?.groups?.filter((g) => /^\d{4}$/.test(g.id)) || [];
  const leftYear = years.some((g) => g.id === left) ? left : years[0]?.id;
  const rightYear = years.some((g) => g.id === right)
    ? right
    : years.at(-1)?.id;
  return (
    <section className="panel">
      <h2>同一人物的跨年对照</h2>
      <p className="muted">
        仅按人工确认的人物和可信拍摄年份组织，不推测年龄或未记录的经历。缺少的年份保持空缺。
      </p>
      {query.error ? (
        <ErrorBox error={query.error} />
      ) : query.isPending ? (
        <Loading />
      ) : years.length < 2 ? (
        <p>还需要至少两个年份的照片才能对照。</p>
      ) : (
        <div className="year-comparison">
          {[
            { year: leftYear, set: setLeft, label: "左侧年份" },
            { year: rightYear, set: setRight, label: "右侧年份" },
          ].map(({ year, set, label }) => (
            <div key={label}>
              <label>
                {label}
                <select
                  aria-label={label}
                  value={year}
                  onChange={(e) => set(e.target.value)}
                >
                  {years.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.title} · {g.total} 张
                    </option>
                  ))}
                </select>
              </label>
              {year && (
                <YearPhoto
                  key={`${personId}:${year}`}
                  personId={personId}
                  year={year}
                />
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
