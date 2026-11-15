import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ChevronLeft, ChevronRight, Maximize, Pause, Play } from "lucide-react";
import { api, captureLabel, type AlbumPhoto } from "../api/client";

interface Film {
  items: AlbumPhoto[];
  total: number;
  selected_count?: number;
  source_total?: number;
  revision?: string;
  interval_seconds?: number;
}

// Selection and authorization live in the API. This component only plays its
// current authorized sequence and discards it if revalidation fails.
export function AlbumPlayer({
  title,
  endpoint,
}: {
  title: string;
  endpoint: string;
}) {
  const [playing, setPlaying] = useState(true);
  const [photoId, setPhotoId] = useState("");
  const [failedImage, setFailedImage] = useState("");
  const [loadedId, setLoadedId] = useState("");
  const [verifiedEndpoint, setVerifiedEndpoint] = useState("");
  const [fullscreenError, setFullscreenError] = useState("");
  const frame = useRef<HTMLElement>(null);
  const film = useQuery({
    queryKey: ["album-film", endpoint],
    queryFn: () => api<Film>(endpoint),
    refetchInterval: 2000,
    refetchIntervalInBackground: false,
    retry: false,
  });
  const items =
    film.isError || verifiedEndpoint !== endpoint ? [] : film.data?.items || [];
  const ids = items.map((p) => p.id).join(",");
  const index = Math.max(
    0,
    items.findIndex((p) => p.id === photoId),
  );
  const photo = items[index];
  const duration = Math.min(30, Math.max(3, film.data?.interval_seconds || 6));

  useEffect(() => {
    let cancelled = false;
    setPhotoId("");
    setPlaying(true);
    setFailedImage("");
    setLoadedId("");
    setVerifiedEndpoint("");
    void film
      .refetch()
      .then((result) => {
        if (!cancelled && result.isSuccess && !document.hidden)
          setVerifiedEndpoint(endpoint);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [endpoint, film.refetch]);
  useEffect(() => {
    if (
      !playing ||
      !ids ||
      items.length < 2 ||
      document.hidden ||
      loadedId !== photo?.id
    )
      return;
    const timer = window.setInterval(() => {
      const sequence = ids.split(",");
      setPhotoId(
        (current) =>
          sequence[
            (Math.max(0, sequence.indexOf(current)) + 1) % sequence.length
          ],
      );
    }, duration * 1000);
    return () => window.clearInterval(timer);
  }, [ids, duration, playing, items.length, loadedId, photo?.id]);
  useEffect(() => {
    let cancelled = false;
    // Discard visible media while suspended and reauthorize before showing it again.
    const revalidate = () => {
      setVerifiedEndpoint("");
      setLoadedId("");
      if (document.hidden) setPlaying(false);
      else
        void film
          .refetch()
          .then((result) => {
            if (!cancelled && result.isSuccess && !document.hidden)
              setVerifiedEndpoint(endpoint);
          })
          .catch(() => {});
    };
    document.addEventListener("visibilitychange", revalidate);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", revalidate);
    };
  }, [endpoint, film.refetch]);

  function step(delta: number) {
    if (items.length)
      setPhotoId(items[(index + delta + items.length) % items.length].id);
  }
  return (
    <section className="album-film" ref={frame} aria-label={title + "回忆影片"}>
      {photo && failedImage !== photo.id ? (
        <>
          <div
            className="album-film-backdrop"
            style={{ backgroundImage: `url("${photo.thumbnail_url}")` }}
          />
          <img
            className={"album-film-image " + (playing ? "is-playing" : "")}
            key={photo.id}
            src={photo.media_url}
            alt={photo.title || title + "中的照片"}
            onLoad={() => setLoadedId(photo.id)}
            onError={() => setFailedImage(photo.id)}
          />
          <div className="album-film-caption">
            <span className="eyebrow">属于这段时光的回忆影片</span>
            <h2>{title}</h2>
            <p>
              {captureLabel(photo)}
              {photo.visibility === "private" ? " · 仅自己可见" : ""}
            </p>
            <p>{photo.album_reason || photo.caption_user || photo.title}</p>
            <Link to={"/photos/" + photo.id}>查看当前照片</Link>
          </div>
        </>
      ) : (
        <div className="album-film-empty">
          <Play size={36} />
          <h2>{title} · 回忆影片</h2>
          <p>
            {film.isError
              ? "暂时无法验证照片权限，已停止播放。连接恢复后自动更新。"
              : film.isPending || verifiedEndpoint !== endpoint
                ? "正在准备并验证这段回忆…"
                : failedImage === photo?.id
                  ? "当前照片暂时不可读，可切换其他照片。"
                  : "这里还没有可播放的照片，下方会随整理进度更新。"}
          </p>
          {(film.isError || failedImage === photo?.id) && (
            <button
              onClick={() => {
                setFailedImage("");
                void film
                  .refetch()
                  .then((result) => {
                    if (result.isSuccess && !document.hidden)
                      setVerifiedEndpoint(endpoint);
                  })
                  .catch(() => {});
              }}
            >
              重试相册影片
            </button>
          )}
        </div>
      )}
      <div className="album-film-controls">
        <button
          aria-label="上一张回忆"
          onClick={() => step(-1)}
          disabled={!photo}
        >
          <ChevronLeft size={20} />
        </button>
        <button
          aria-label={playing ? "暂停相册影片" : "播放相册影片"}
          onClick={() => setPlaying((v) => !v)}
          disabled={!photo}
        >
          {playing ? <Pause size={20} /> : <Play size={20} />}
          <span>{playing ? "暂停" : "播放"}</span>
        </button>
        <button
          aria-label="下一张回忆"
          onClick={() => step(1)}
          disabled={!photo}
        >
          <ChevronRight size={20} />
        </button>
        <span className="album-film-counter">
          {photo ? `${index + 1} / ${items.length}` : "0 张"}
        </span>
        <button
          aria-label="全屏相册影片"
          onClick={async () => {
            try {
              if (document.fullscreenElement) await document.exitFullscreen();
              else await frame.current?.requestFullscreen();
            } catch {
              setFullscreenError("浏览器暂不支持全屏，可以继续在页面中播放。");
            }
          }}
          disabled={!photo}
        >
          <Maximize size={19} />
        </button>
      </div>
      {fullscreenError && (
        <p className="album-film-message" role="status">
          {fullscreenError}
        </p>
      )}
      {!!items.length && (
        <p className="album-film-note">
          从本相册 {film.data?.source_total ?? film.data?.total ?? items.length}{" "}
          张照片中精选 {items.length} 张 · 下方保留全部照片 · 静音播放
        </p>
      )}
    </section>
  );
}
