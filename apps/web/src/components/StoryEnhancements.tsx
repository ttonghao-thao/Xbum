import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Story } from "../api/client";
import { useAuth, useNotice } from "./ui";

interface Narration {
  id: string;
  photo_id: string;
  audio_memory_id: string | null;
  transcript: string;
  valid: boolean;
}
interface Choices {
  id: string;
  transcript: string;
  photo_ids: string[];
}
interface Props {
  storyId?: string;
  eventId?: string;
  personId?: string;
  onUpdated?: () => void;
}

export function StoryEnhancements({
  storyId,
  eventId,
  personId,
  onUpdated,
}: Props) {
  const auth = useAuth();
  const notify = useNotice();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [length, setLength] = useState("medium");
  const [busy, setBusy] = useState(false);
  const [choiceId, setChoiceId] = useState("");
  const [photoId, setPhotoId] = useState("");
  const editable = auth.role === "owner" || auth.role === "member";
  const narrations = useQuery({
    queryKey: ["story-narrations", storyId],
    queryFn: () =>
      api<{ items: Narration[]; choices: Choices[] }>(
        `/stories/${storyId}/narrations`,
      ),
    enabled: !!storyId && editable,
    retry: false,
  });
  const chosen = narrations.data?.choices.find((row) => row.id === choiceId);
  async function update() {
    await qc.invalidateQueries({ queryKey: ["story-narrations", storyId] });
    await qc.invalidateQueries({ queryKey: ["story", storyId] });
    onUpdated?.();
  }
  async function compose() {
    setBusy(true);
    try {
      let body: Record<string, unknown> = { length };
      if (eventId) body.event_id = eventId;
      else if (personId) body.person_id = personId;
      else if (storyId) {
        const original = await api<Story>(`/stories/${storyId}`);
        body = {
          ...body,
          photo_ids: original.slides.map((slide) => slide.photo_id),
          title: original.title + " · 精选",
        };
      }
      const result = await api<{ story: Story; job_id: string }>(
        "/stories/compose",
        "POST",
        body,
      );
      notify("已加入本地故事编排，素材会按日期与多样性组织");
      navigate(`/stories/${result.story.id}/edit`);
      onUpdated?.();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  if (!editable) return null;
  return (
    <section className="panel story-enhancements">
      <h2>把这些瞬间，编成故事</h2>
      <p className="muted">
        依据已共享照片的时间与人工说明挑选代表画面，按日组织章节。另建精选故事会保留当前编辑版本。
      </p>
      <div className="filters">
        <label>
          故事长度
          <select value={length} onChange={(e) => setLength(e.target.value)}>
            <option value="short">短 · 最多 12 张</option>
            <option value="medium">中 · 最多 24 张</option>
            <option value="long">长 · 最多 60 张</option>
          </select>
        </label>
        <button disabled={busy} onClick={compose}>
          {busy ? "正在加入队列…" : storyId ? "另建精选故事" : "自动编排故事"}
        </button>
      </div>
      {!!storyId && (
        <>
          <h3>家人的原声旁白</h3>
          <p className="muted">
            仅可使用已人工校对、与当前故事照片或事件关联的口述记忆。原声较长时，影片会延长对应画面的停留时间。
          </p>
          {narrations.error ? (
            <p className="muted">当前故事尚无法编辑旁白。</p>
          ) : (
            <>
              {narrations.data?.items.map((row) => (
                <div className="settings-row" key={row.id}>
                  <p className={row.valid ? "" : "error"}>{row.transcript}</p>
                  <button
                    onClick={async () => {
                      try {
                        await api(
                          `/stories/${storyId}/narrations/${row.id}`,
                          "DELETE",
                        );
                        await update();
                      } catch (e) {
                        notify((e as Error).message);
                      }
                    }}
                  >
                    移除旁白
                  </button>
                </div>
              ))}
              {narrations.data?.choices.length ? (
                <div className="filters">
                  <label>
                    已确认的讲述
                    <select
                      value={choiceId}
                      onChange={(e) => {
                        setChoiceId(e.target.value);
                        setPhotoId("");
                      }}
                    >
                      <option value="">请选择口述记忆</option>
                      {narrations.data.choices.map((row) => (
                        <option key={row.id} value={row.id}>
                          {row.transcript.slice(0, 60)}
                        </option>
                      ))}
                    </select>
                  </label>
                  {chosen && (
                    <label>
                      对应画面
                      <select
                        value={photoId || chosen.photo_ids[0]}
                        onChange={(e) => setPhotoId(e.target.value)}
                      >
                        {chosen.photo_ids.map((pid, i) => (
                          <option key={pid} value={pid}>
                            相关照片 {i + 1}
                          </option>
                        ))}
                      </select>
                    </label>
                  )}
                  <button
                    disabled={!chosen}
                    onClick={async () => {
                      if (!chosen) return;
                      try {
                        await api(`/stories/${storyId}/narrations`, "POST", {
                          audio_memory_id: choiceId,
                          photo_id: photoId || chosen.photo_ids[0],
                        });
                        await update();
                        notify("原声旁白已关联，重新导出可带原声播放");
                      } catch (e) {
                        notify((e as Error).message);
                      }
                    }}
                  >
                    加入原声旁白
                  </button>
                </div>
              ) : (
                <p className="muted">
                  还没有可用的口述记忆。先在照片或事件中录音，并校对确认转写。
                </p>
              )}
            </>
          )}
        </>
      )}
    </section>
  );
}

export function MemorySuggestions() {
  const auth = useAuth();
  const notify = useNotice();
  const navigate = useNavigate();
  const [length, setLength] = useState("medium");
  const [busy, setBusy] = useState<string | null>(null);
  const suggestions = useQuery({
    queryKey: ["memory-suggestions"],
    queryFn: () =>
      api<{
        items: {
          kind: string;
          title: string;
          photo_ids: string[];
          photo_count: number;
          cover: { thumbnail_url: string };
          reason: string;
        }[];
      }>("/memory-suggestions"),
    refetchInterval: 30000,
  });
  const editable = auth.role === "owner" || auth.role === "member";
  if (suggestions.isPending)
    return <p className="muted">正在整理家庭回忆建议…</p>;
  if (suggestions.error)
    return <p className="muted">回忆建议暂时无法读取，请稍后重试。</p>;
  if (!suggestions.data.items.length)
    return (
      <section className="panel">
        <h2>等待下一段故事</h2>
        <p className="muted">
          共享照片中的往年、成长和出游线索会自动整理成回忆建议。个人私密照片仍在本人的自动相册中浏览。
        </p>
      </section>
    );
  return (
    <section className="panel">
      <div className="section-heading">
        <h2>这些瞬间，可以成为故事</h2>
        {editable && (
          <label>
            故事长度
            <select value={length} onChange={(e) => setLength(e.target.value)}>
              <option value="short">短 · 最多12张</option>
              <option value="medium">中 · 最多24张</option>
              <option value="long">长 · 最多60张</option>
            </select>
          </label>
        )}
      </div>
      <div className="memory-scenes">
        {suggestions.data.items.map((item) => (
          <article key={item.kind} className="memory-scene">
            <img
              src={item.cover.thumbnail_url}
              alt={item.title}
              loading="lazy"
            />
            <div>
              <h3>{item.title}</h3>
              <p>{item.photo_count} 个共享瞬间</p>
              <p className="muted">{item.reason}</p>
              {editable && (
                <button
                  disabled={busy !== null}
                  onClick={async () => {
                    setBusy(item.kind);
                    try {
                      const result = await api<{ story: Story }>(
                        "/stories/compose",
                        "POST",
                        {
                          photo_ids: item.photo_ids,
                          length,
                          title: item.title,
                        },
                      );
                      navigate(`/stories/${result.story.id}/edit`);
                    } catch (e) {
                      notify((e as Error).message);
                    } finally {
                      setBusy(null);
                    }
                  }}
                >
                  {busy === item.kind ? "正在加入队列…" : "编成故事"}
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
