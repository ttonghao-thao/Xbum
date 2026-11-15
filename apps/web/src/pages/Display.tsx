import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Album,
  ChevronLeft,
  ChevronRight,
  Heart,
  Maximize,
  Moon,
  Pause,
  Play,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  api,
  captureLabel,
  sceneNames,
  setCsrf,
  type Auth,
  type Playlist,
  type Story,
} from "../api/client";
import { ErrorBox } from "../components/ui";

export function StoryPlayer({
  story,
  onClose,
  display = false,
}: {
  story: Story;
  onClose: () => void;
  display?: boolean;
}) {
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [sound, setSound] = useState(false);
  const [audioError, setAudioError] = useState("");
  const audio = useRef<HTMLAudioElement>(null);
  const started = useRef(performance.now());
  const slide = story.slides[index];
  const next = useCallback(() => {
    setIndex((i) => (i + 1 < story.slides.length ? i + 1 : i));
    setElapsed(0);
    started.current = performance.now();
    setLoaded(false);
    if (index === story.slides.length - 1) setPlaying(false);
  }, [index, story.slides.length]);
  useEffect(() => {
    const handle = window.setInterval(() => {
      if (playing && loaded && slide) {
        const value = (performance.now() - started.current) / 1000;
        setElapsed(value);
        const waitingForVoice =
          sound &&
          slide.narration_url &&
          audio.current &&
          !audio.current.ended &&
          !audioError;
        if (value >= slide.duration_s && !waitingForVoice) next();
      }
    }, 100);
    return () => clearInterval(handle);
  }, [playing, loaded, slide, next, sound, audioError]);
  useEffect(() => {
    const element = audio.current;
    if (!element) return;
    if (playing && loaded && sound && !error) {
      void element
        .play()
        .catch(() =>
          setAudioError("原声暂未开始，请再次点击开启原声或继续静音观看。"),
        );
    } else element.pause();
    return () => element.pause();
  }, [playing, loaded, sound, index, error]);
  useEffect(() => {
    setAudioError("");
  }, [index]);
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      if (e.code === "Space") {
        e.preventDefault();
        setPlaying((p) => !p);
      }
      if (e.key === "ArrowRight") next();
      if (e.key === "ArrowLeft") {
        setIndex((i) => Math.max(0, i - 1));
        setLoaded(false);
      }
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [next, onClose]);
  useEffect(() => {
    let cancelled = false;
    const timer = setInterval(() => {
      void api(`/stories/${story.id}${display ? "?display=1" : ""}`).catch(
        () => {
          if (!cancelled) {
            setError("故事授权已变化或服务已断开，已停止播放");
            setPlaying(false);
          }
        },
      );
    }, 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [story.id, display]);
  useEffect(() => {
    started.current = performance.now() - elapsed * 1000;
  }, [playing]);
  if (!slide)
    return (
      <div className="story-player">
        <p>故事还没有可播放的照片。</p>
        <button onClick={onClose}>返回</button>
      </div>
    );
  return (
    <div
      className={"story-player theme-" + story.theme}
      style={{ background: story.background }}
      role="dialog"
      aria-modal="true"
      aria-label="故事播放"
    >
      <header>
        <span>{story.title}</span>
        <button onClick={onClose} aria-label="退出故事">
          <X size={22} />
        </button>
      </header>
      {error ? (
        <div className="display-empty">
          <p>{error}</p>
        </div>
      ) : (
        <>
          <div className={"story-picture transition-" + slide.transition}>
            <img
              key={index}
              src={`/api/v1/photos/${slide.photo_id}/media/960${display ? "?display=1" : ""}`}
              alt={slide.caption || "故事照片"}
              onLoad={() => {
                setLoaded(true);
                started.current = performance.now();
              }}
              onError={() => setError("照片已撤回共享或媒体不可用")}
            />
          </div>
          <div className="story-caption">
            <p>{slide.caption}</p>
            {slide.narration_transcript && (
              <p className="muted">家人口述：{slide.narration_transcript}</p>
            )}
            {slide.narration_url && (
              <audio
                key={slide.narration_url}
                ref={audio}
                src={slide.narration_url + (display ? "?display=1" : "")}
                preload="none"
                onError={() => setAudioError("原声暂不可用，可继续静音观看。")}
              />
            )}
            {audioError && <p role="status">{audioError}</p>}
            {index === story.slides.length - 1 && <small>{story.ending}</small>}
          </div>
        </>
      )}
      <div className="story-controls">
        <button
          aria-label="上一页故事"
          disabled={index === 0}
          onClick={() => {
            setIndex((i) => Math.max(0, i - 1));
            setLoaded(false);
            setElapsed(0);
          }}
        >
          <ChevronLeft />
        </button>
        <button
          aria-label={playing ? "暂停故事" : "播放故事"}
          onClick={() => setPlaying(!playing)}
        >
          {playing ? <Pause /> : <Play />}
        </button>
        <span>
          {index + 1} / {story.slides.length}
        </span>
        {slide.narration_url && (
          <button
            onClick={() => {
              setAudioError("");
              setSound((v) => !v);
            }}
          >
            {sound ? "关闭原声" : "开启原声"}
          </button>
        )}
        <button
          aria-label="下一页故事"
          disabled={index === story.slides.length - 1}
          onClick={next}
        >
          <ChevronRight />
        </button>
        <button
          aria-label="全屏故事"
          onClick={() => {
            void document.documentElement.requestFullscreen?.();
          }}
        >
          <Maximize />
        </button>
      </div>
      <progress
        className="display-progress"
        value={elapsed}
        max={slide.duration_s}
      />
    </div>
  );
}

interface DisplayFeed extends Playlist {
  authorized_photo_ids?: string[];
  household_name?: string;
}
export function Display() {
  const [params, setParams] = useSearchParams();
  const [code, setCode] = useState(params.get("pair") || "");
  const [pairError, setPairError] = useState<unknown>();
  const [pairing, setPairing] = useState(false);
  const [active, setActive] = useState<DisplayFeed | null>(null);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [progress, setProgress] = useState(0);
  const [story, setStory] = useState<Story | null>(null);
  const [detail, setDetail] = useState(false);
  const started = useRef(performance.now());
  const pending = useRef<DisplayFeed | null>(null);
  const feed = useQuery({
    queryKey: ["display-feed"],
    queryFn: () => api<DisplayFeed>("/display/feed"),
    refetchInterval: 2000,
    retry: false,
  });
  const me = useQuery({
    queryKey: ["display-owner"],
    queryFn: () => api<Auth>("/auth/me"),
    retry: false,
  });
  const item = active?.items[index];
  const quiet = feed.data?.quiet;
  async function pair(value: string) {
    setPairing(true);
    setPairError(undefined);
    try {
      await api("/display/pair", "POST", { code: value });
      setParams({});
      await feed.refetch();
    } catch (e) {
      setPairError(e);
    } finally {
      setPairing(false);
    }
  }
  useEffect(() => {
    if (params.get("pair")) void pair(params.get("pair")!);
  }, []);
  useEffect(() => {
    if (!feed.data) return;
    pending.current = feed.data;
    if (!active || (!active.items.length && feed.data.items.length > 0)) {
      setActive(feed.data);
      setIndex(0);
      return;
    }
    const allowed = new Set(
      feed.data.authorized_photo_ids || feed.data.items.map((i) => i.photo_id),
    );
    if (
      active.privacy_revision !== feed.data.privacy_revision ||
      active.items.some((i) => !allowed.has(i.photo_id))
    ) {
      const old = active.items.filter((i) => allowed.has(i.photo_id));
      const currentId = active.items[index]?.photo_id;
      const retainedIndex = old.findIndex((i) => i.photo_id === currentId);
      setActive({
        ...active,
        privacy_revision: feed.data.privacy_revision,
        items: old,
      });
      setIndex(retainedIndex >= 0 ? retainedIndex : 0);
      if (retainedIndex < 0) setLoaded(false);
    }
  }, [feed.data]);
  const go = useCallback(
    (direction: number) => {
      setIndex((old) => {
        if (!active?.items.length) return 0;
        let next =
          (old + direction + active.items.length) % active.items.length;
        if (
          direction > 0 &&
          next === 0 &&
          pending.current &&
          pending.current.id !== active.id
        ) {
          setActive(pending.current);
          next = 0;
        }
        return next;
      });
      setProgress(0);
      if ((active?.items.length || 0) > 1) setLoaded(false);
      started.current = performance.now();
    },
    [active],
  );
  useEffect(() => {
    const tick = setInterval(() => {
      if (playing && loaded && !quiet && !feed.error && feed.data?.autoplay) {
        const seconds = (performance.now() - started.current) / 1000;
        setProgress(seconds);
        if (seconds >= (feed.data?.interval_seconds || 10)) go(1);
      }
    }, 100);
    return () => clearInterval(tick);
  }, [
    playing,
    loaded,
    quiet,
    feed.error,
    feed.data?.interval_seconds,
    feed.data?.autoplay,
    go,
  ]);
  useEffect(() => {
    const listener = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT") return;
      if (e.code === "Space") {
        e.preventDefault();
        setPlaying((p) => !p);
      }
      if (e.key === "ArrowRight") go(1);
      if (e.key === "ArrowLeft") go(-1);
      if (e.key === "Escape") {
        setDetail(false);
        setStory(null);
      }
    };
    window.addEventListener("keydown", listener);
    return () => window.removeEventListener("keydown", listener);
  }, [go]);
  useEffect(() => {
    if (item) {
      const next = active?.items[(index + 1) % (active?.items.length || 1)];
      if (next) {
        const image = new Image();
        image.src = next.photo.media_url;
      }
    }
  }, [item?.photo_id]);
  useEffect(() => {
    started.current = performance.now() - progress * 1000;
  }, [playing]);
  useEffect(() => {
    const sid = params.get("story");
    if (sid && feed.data)
      void api<Story>(`/stories/${sid}?display=1`)
        .then(setStory)
        .catch((e) => setPairError(e));
  }, [params.get("story"), !!feed.data]);
  async function event(action: string) {
    if (!item) return;
    try {
      await api("/display/events", "POST", {
        photo_id: item.photo_id,
        action,
        dwell_seconds: Math.min(progress, 86400),
      });
      if (action === "hide") {
        setActive((a) =>
          a
            ? {
                ...a,
                items: a.items.filter((p) => p.photo_id !== item.photo_id),
              }
            : a,
        );
        setIndex(0);
        setLoaded(false);
      }
      await feed.refetch();
    } catch (e) {
      setPairError(e);
    }
  }
  if (story)
    return (
      <StoryPlayer
        story={story}
        display
        onClose={() => {
          setStory(null);
          setParams({});
        }}
      />
    );
  if (feed.error && !feed.data)
    return (
      <div className="display-pair">
        <Link to="/" className="display-brand">
          <Album size={28} />
          Xbum
        </Link>
        <section>
          <ShieldCheck size={46} strokeWidth={1} />
          <span className="eyebrow">A LITTLE WINDOW TO YOUR FAMILY</span>
          <h1>
            让家的记忆，
            <br />
            在这里亮起来。
          </h1>
          <p>
            请输入管理员在「家庭设置」生成的配对码。
            <br />
            这台相框只会获得展示权限。
          </p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void pair(code);
            }}
          >
            <input
              aria-label="展示配对码"
              value={code}
              onChange={(e) => setCode(e.target.value.toUpperCase())}
              placeholder="10 位配对码"
              minLength={8}
              maxLength={32}
              required
              autoComplete="off"
            />
            <button className="primary" disabled={pairing}>
              {pairing ? "正在配对…" : "授权此相框"}
            </button>
          </form>
          {me.data?.role === "owner" && (
            <button
              onClick={async () => {
                try {
                  setCsrf(me.data!.csrf);
                  const result = await api<{ code: string }>(
                    "/display/devices",
                    "POST",
                    { name: "浏览器记忆相框" },
                  );
                  await pair(result.code);
                } catch (e) {
                  setPairError(e);
                }
              }}
            >
              作为管理员，在此浏览器创建展示会话
            </button>
          )}
          {pairError !== undefined && <ErrorBox error={pairError} />}
          <Link to="/">返回家庭相册 →</Link>
        </section>
        <small>本地保存 · 无在线推理 · 独立低权限会话</small>
      </div>
    );
  const blank = feed.error || quiet || !item;
  return (
    <div className="display-page">
      <header>
        <Link to="/" className="display-brand">
          <Album size={24} />
          Xbum<span>{feed.data?.household_name || "家庭记忆相框"}</span>
        </Link>
        <div>
          <span className="green-dot" />
          {new Date().toLocaleTimeString("zh-CN", {
            hour: "2-digit",
            minute: "2-digit",
          })}
          <button
            aria-label="全屏展示"
            onClick={() => void document.documentElement.requestFullscreen?.()}
          >
            <Maximize size={20} />
          </button>
          <Link to="/" aria-label="退出相框">
            <X size={21} />
          </Link>
        </div>
      </header>
      {blank ? (
        <div className="display-empty">
          {quiet ? (
            <>
              <Moon size={58} strokeWidth={1} />
              <h1>晚安，明天再见。</h1>
              <p>现在是家庭静默时段，相框正在休息。</p>
            </>
          ) : feed.error ? (
            <>
              <ShieldCheck size={52} />
              <h2>与家里的服务暂时断开</h2>
              <p>为保护共享权限，连接恢复前不显示缓存照片。</p>
            </>
          ) : (
            <>
              <Album size={58} strokeWidth={1} />
              <h1>好的记忆，值得等待。</h1>
              <p>还没有可展示的共享照片。添加照片后，后台会为你整理精选。</p>
              <Link to="/" className="button">
                返回家庭相册
              </Link>
            </>
          )}
        </div>
      ) : (
        <>
          <section className="display-stage">
            <div
              className="display-photo"
              onClick={() => setDetail(!detail)}
              role="button"
              tabIndex={0}
              aria-label="查看当前照片详情"
              onKeyDown={(e) => {
                if (e.key === "Enter") setDetail(!detail);
              }}
            >
              <img
                key={item.photo_id}
                src={item.photo.media_url}
                alt={item.photo.title || item.reason_codes_json[0]}
                onLoad={() => {
                  setLoaded(true);
                  started.current = performance.now();
                  setProgress(0);
                  void api("/display/events", "POST", {
                    photo_id: item.photo_id,
                    action: "play",
                    dwell_seconds: 0,
                  });
                }}
                onError={() => {
                  setLoaded(false);
                  void feed.refetch();
                }}
              />
            </div>
            <aside className="display-caption">
              <span className="eyebrow">
                {sceneNames[item.scene_type] || "家的记忆"}
              </span>
              <h1>{item.photo.title || "平凡日子里的小幸福"}</h1>
              <p>
                {item.photo.caption_user || item.reason_codes_json.join(" · ")}
              </p>
              <span className="display-date">{captureLabel(item.photo)}</span>
              <small>
                {index + 1} / {active?.items.length} 个瞬间
              </small>
              {item.story_id && (
                <button
                  onClick={() =>
                    void api<Story>(`/stories/${item.story_id}?display=1`)
                      .then(setStory)
                      .catch((e) => setPairError(e))
                  }
                >
                  播放这段旅行故事 →
                </button>
              )}
            </aside>
          </section>
          <div className="display-controls">
            <div>
              <button aria-label="上一张" onClick={() => go(-1)}>
                <ChevronLeft />
              </button>
              <button
                aria-label={playing ? "暂停轮播" : "开始轮播"}
                onClick={() => setPlaying(!playing)}
              >
                {playing ? <Pause /> : <Play />}
              </button>
              <button
                aria-label="下一张"
                onClick={() => {
                  void event("skip");
                  go(1);
                }}
              >
                <ChevronRight />
              </button>
            </div>
            <span>
              {playing ? "让时光，慢慢流淌。" : "留一会儿，再看一眼。"}
            </span>
            <div>
              <button
                aria-label="收藏当前照片"
                onClick={() => void event("favorite")}
              >
                <Heart size={20} />
              </button>
              <button onClick={() => void event("dislike")}>不喜欢</button>
              <button onClick={() => void event("hide")}>隐藏</button>
            </div>
          </div>
          <progress
            className="display-progress"
            value={progress}
            max={feed.data?.interval_seconds || 10}
          />
        </>
      )}
      {detail && item && !blank && (
        <div className="display-detail">
          <button aria-label="关闭照片详情" onClick={() => setDetail(false)}>
            <X />
          </button>
          <h2>{item.photo.title || "一个家的瞬间"}</h2>
          <p>{item.photo.caption_user || "尚无人工说明"}</p>
          <p>{captureLabel(item.photo)}</p>
          <p>展示理由：{item.reason_codes_json.join(" · ")}</p>
        </div>
      )}
      {pairError !== undefined && (
        <div className="display-feedback">
          <ErrorBox error={pairError} />
          <button onClick={() => setPairError(undefined)}>关闭</button>
        </div>
      )}
    </div>
  );
}
