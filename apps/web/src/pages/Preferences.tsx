import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, apiAll, type Person } from "../api/client";
import {
  Empty,
  ErrorBox,
  Loading,
  PageTitle,
  useAuth,
  useNotice,
} from "../components/ui";

interface Rule {
  id: string;
  scope: "personal" | "household";
  mode: "less" | "never";
  target_kind: "person" | "date" | "location" | "theme";
  target_value: string;
  start_date: string | null;
  end_date: string | null;
  can_delete: boolean;
}
const labels = {
  person: "人物",
  date: "日期范围",
  location: "已知地点",
  theme: "画面主题",
};

export function Preferences() {
  const auth = useAuth();
  const notify = useNotice();
  const qc = useQueryClient();
  const [kind, setKind] = useState<Rule["target_kind"]>("date");
  const [busy, setBusy] = useState(false);
  const rules = useQuery({
    queryKey: ["recommendation-rules"],
    queryFn: () => api<{ items: Rule[] }>("/recommendation-rules"),
  });
  const people = useQuery({
    queryKey: ["preference-people"],
    queryFn: () => apiAll<Person>("/people"),
  });
  const confirmed = (people.data || []).filter((p) => p.manually_confirmed);
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form));
    try {
      await api("/recommendation-rules", "POST", {
        scope: data.scope,
        mode: data.mode,
        target_kind: kind,
        target_value: kind === "date" ? "" : data.target_value,
        start_date: kind === "date" ? data.start_date : null,
        end_date: kind === "date" ? data.end_date : null,
      });
      await qc.invalidateQueries({ queryKey: ["recommendation-rules"] });
      await qc.invalidateQueries({ queryKey: ["album-playback"] });
      notify("推荐偏好已保存");
      form.reset();
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageTitle
        title="让回忆按你的心意出现"
        description="控制人物、日期、地点或主题在主动回顾中的出现频率，随时可以恢复。照片仍保存在原相册中。"
      />
      {(auth.role === "owner" || auth.role === "member") && (
        <form className="panel form-grid" onSubmit={save}>
          <h2>添加推荐偏好</h2>
          <label>
            作用范围
            <select name="scope">
              <option value="personal">只影响我的回忆建议与自动精选</option>
              {auth.role === "owner" && (
                <option value="household">家庭相框与家庭精选</option>
              )}
            </select>
          </label>
          <label>
            偏好对象
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as Rule["target_kind"])}
            >
              {Object.entries(labels).map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {kind === "date" ? (
            <>
              <label>
                开始日期
                <input type="date" name="start_date" required />
              </label>
              <label>
                结束日期
                <input type="date" name="end_date" required />
              </label>
            </>
          ) : kind === "person" ? (
            <label>
              已确认人物
              <select name="target_value" required>
                <option value="">请选择</option>
                {confirmed.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name || "已确认人物"}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <label>
              {kind === "theme"
                ? "主题标签（与照片标签一致）"
                : "人工确认的地点"}
              <input
                name="target_value"
                required
                maxLength={300}
                placeholder={kind === "theme" ? "例如：海边" : "例如：老家"}
              />
            </label>
          )}
          <label>
            出现方式
            <select name="mode">
              <option value="less">减少推荐</option>
              <option value="never">不再主动推荐</option>
            </select>
          </label>
          <p className="muted">
            人物设置依据已确认关联，尚未识别的人物照片可能仍需手动整理。个人规则不会改变其他家人的建议。
          </p>
          <button className="primary" disabled={busy}>
            {busy ? "正在保存…" : "保存推荐偏好"}
          </button>
        </form>
      )}
      <section className="panel">
        <h2>当前推荐偏好</h2>
        {rules.isPending ? (
          <Loading />
        ) : rules.error ? (
          <ErrorBox error={rules.error} />
        ) : rules.data.items.length ? (
          <div className="stack">
            {rules.data.items.map((rule) => (
              <article key={rule.id} className="settings-row">
                <div>
                  <strong>
                    {labels[rule.target_kind]} ·{" "}
                    {rule.target_kind === "date"
                      ? `${rule.start_date} — ${rule.end_date}`
                      : rule.target_kind === "person"
                        ? confirmed.find((p) => p.id === rule.target_value)
                            ?.name || "已确认人物"
                        : rule.target_value}
                  </strong>
                  <p>
                    {rule.mode === "never" ? "不再主动推荐" : "减少推荐"} ·{" "}
                    {rule.scope === "household" ? "家庭相框与精选" : "仅我自己"}
                  </p>
                </div>
                {rule.can_delete && (
                  <button
                    onClick={async () => {
                      try {
                        await api(`/recommendation-rules/${rule.id}`, "DELETE");
                        await qc.invalidateQueries({
                          queryKey: ["recommendation-rules"],
                        });
                        notify("已恢复推荐");
                      } catch (e) {
                        notify((e as Error).message);
                      }
                    }}
                  >
                    恢复推荐
                  </button>
                )}
              </article>
            ))}
          </div>
        ) : (
          <Empty title="回忆，自然相遇" text="目前没有特别的推荐限制。" />
        )}
      </section>
    </>
  );
}
