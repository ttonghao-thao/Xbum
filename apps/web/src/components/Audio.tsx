import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Mic, Square, Upload } from "lucide-react";
import { api, type AudioMemory } from "../api/client";
import { ErrorBox, useAuth, useNotice } from "./ui";

export function AudioRecorder({
  photoId,
  eventId,
  search = false,
  onSaved,
  onText,
}: {
  photoId?: string;
  eventId?: string;
  search?: boolean;
  onSaved?: () => void;
  onText?: (text: string) => void;
}) {
  const auth = useAuth();
  const [recording, setRecording] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();
  const [memoryId, setMemoryId] = useState("");
  const [text, setText] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const chunks = useRef<Blob[]>([]);
  const notice = useNotice();
  const memory = useQuery({
    queryKey: ["audio", memoryId],
    queryFn: () => api<AudioMemory>("/memories/audio/" + memoryId),
    enabled: !!memoryId,
    refetchInterval: (q) =>
      q.state.data?.jobs?.some((j) =>
        ["queued", "running", "retry"].includes(j.state),
      )
        ? 2000
        : false,
  });
  useEffect(() => {
    if (memory.data?.transcript) {
      setText(memory.data.transcript);
      onSaved?.();
    }
  }, [memory.data?.transcript]);
  useEffect(
    () => () => {
      if (recorder.current) recorder.current.onstop = null;
      recorder.current?.state === "recording" && recorder.current.stop();
      stream.current?.getTracks().forEach((t) => t.stop());
    },
    [],
  );
  async function save(file: File) {
    setPending(true);
    setError(undefined);
    try {
      const form = new FormData();
      form.append("file", file);
      if (photoId) form.append("photo_id", photoId);
      if (eventId) form.append("event_id", eventId);
      const result = await api<{ memory_id: string }>(
        search ? "/speech/transcribe" : "/memories/audio",
        "POST",
        form,
      );
      setMemoryId(result.memory_id);
      notice("录音已保存在本地，转写任务已入队");
      onSaved?.();
    } catch (e) {
      setError(e);
    } finally {
      setPending(false);
    }
  }
  async function start() {
    setError(undefined);
    try {
      if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder)
        throw new Error(
          "浏览器录音需要 HTTPS 或 localhost；也可以上传音频文件",
        );
      stream.current = await navigator.mediaDevices.getUserMedia({
        audio: true,
      });
      const mime = [
        "audio/webm;codecs=opus",
        "audio/mp4",
        "audio/ogg;codecs=opus",
      ].find((t) => MediaRecorder.isTypeSupported(t));
      recorder.current = new MediaRecorder(
        stream.current,
        mime ? { mimeType: mime } : undefined,
      );
      chunks.current = [];
      recorder.current.ondataavailable = (e) => chunks.current.push(e.data);
      recorder.current.onstop = () => {
        stream.current?.getTracks().forEach((t) => t.stop());
        const type = recorder.current?.mimeType || "audio/webm";
        void save(
          new File(
            chunks.current,
            "memory." + (type.includes("mp4") ? "mp4" : "webm"),
            { type },
          ),
        );
      };
      recorder.current.start(1000);
      setRecording(true);
    } catch (e) {
      setError(e);
    }
  }
  if (auth.role === "viewer") return null;
  if (!auth.household.settings_json.speech_enabled)
    return (
      <div className="note">
        语音处理已关闭。请管理员在家庭设置中启用，并安装本地 whisper.cpp
        多语言模型。文本功能可正常使用。
      </div>
    );
  const failed = memory.data?.jobs?.find(
    (j) => j.state === "failed" || j.state === "retry",
  );
  return (
    <div className="audio-recorder">
      <div className="actions">
        <button
          disabled={pending}
          className={recording ? "danger" : "primary"}
          onClick={() => {
            if (recording) {
              recorder.current?.stop();
              setRecording(false);
            } else void start();
          }}
        >
          {recording ? <Square size={16} /> : <Mic size={17} />}{" "}
          {recording
            ? "停止并保存本地"
            : pending
              ? "正在保存…"
              : search
                ? "录制语音查询"
                : "录制口述记忆"}
        </button>
        {recording && (
          <button
            onClick={() => {
              if (recorder.current) {
                recorder.current.onstop = null;
                recorder.current.stop();
              }
              stream.current?.getTracks().forEach((t) => t.stop());
              chunks.current = [];
              setRecording(false);
            }}
          >
            放弃这段录音
          </button>
        )}
        <label className="button">
          <Upload size={16} />
          上传录音
          <input
            type="file"
            hidden
            accept="audio/*"
            onChange={(e) => {
              if (e.target.files?.[0]) void save(e.target.files[0]);
            }}
          />
        </label>
      </div>
      {recording && <p role="status">正在录音。请在 10 分钟内结束。</p>}
      {error !== undefined && <ErrorBox error={error} />}
      <p className="muted">
        不使用 Web Speech API；仅将录音送到你家的本地 Mac。
      </p>
      {failed && (
        <div className="error">
          {failed.error_message_sanitized}
          <a href="/settings">查看本地模型安装状态</a>
        </div>
      )}
      {search && memoryId && (
        <label>
          转写结果（请核对后使用）
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="本地转写进行中…"
          />
          <button disabled={!text.trim()} onClick={() => onText?.(text)}>
            使用这段文字查询
          </button>
        </label>
      )}
    </div>
  );
}

export function MemoriesList({
  memories,
  onChanged,
}: {
  memories: AudioMemory[];
  onChanged: () => void;
}) {
  const notify = useNotice();
  const auth = useAuth();
  const [editing, setEditing] = useState("");
  const [text, setText] = useState("");
  async function save(id: string) {
    try {
      await api("/memories/audio/" + id, "PATCH", { transcript: text });
      setEditing("");
      onChanged();
    } catch (e) {
      notify((e as Error).message);
    }
  }
  return (
    <div className="memories-list">
      {memories.map((m) => (
        <article className="memory-note" key={m.id}>
          <audio
            controls
            preload="none"
            src={`/api/v1/memories/audio/${m.id}/media`}
          />
          <span className="pill">
            {m.transcript_confirmed ? "人工校对" : "本地语音转写 · 待核对"}
          </span>
          {editing === m.id ? (
            <>
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
              <button onClick={() => void save(m.id)}>保存校对</button>
            </>
          ) : (
            <p>{m.transcript || "转写任务处理中，可在任务页查看状态。"}</p>
          )}
          {auth.role !== "viewer" &&
            (m.uploader_id === auth.user.id || auth.role === "owner") && (
              <div className="actions">
                <button
                  onClick={() => {
                    setEditing(m.id);
                    setText(m.transcript);
                  }}
                >
                  校对文字
                </button>
                <button
                  className="danger"
                  onClick={async () => {
                    if (window.confirm("删除这段录音和转写稿？"))
                      try {
                        await api("/memories/audio/" + m.id, "DELETE");
                        onChanged();
                      } catch (e) {
                        notify((e as Error).message);
                      }
                  }}
                >
                  删除录音
                </button>
              </div>
            )}
        </article>
      ))}
    </div>
  );
}
