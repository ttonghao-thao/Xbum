import { useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  HardDrive,
  Pause,
  Play,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import {
  api,
  apiAll,
  jobNames,
  sceneNames,
  type Household,
  type Job,
  type List,
  type Settings as Config,
} from "../api/client";
import {
  Check,
  Empty,
  ErrorBox,
  Loading,
  PageTitle,
  Pagination,
  useAuth,
  useNotice,
} from "../components/ui";

interface Member {
  id: string;
  display_name: string;
  role: string;
  user_id: string;
}
interface Invite {
  id: string;
  role: string;
  expires_at: string;
  max_uses: number;
  used_count: number;
  revoked: boolean;
}
interface Device {
  id: string;
  name: string;
  paired_at: string | null;
  revoked_at: string | null;
  expires_at: string;
}
interface ModelState {
  installed: boolean;
  running?: boolean;
  model?: string;
  message?: string;
  version?: string;
}
interface Models {
  ollama: ModelState;
  siglip2: ModelState;
  faces: ModelState;
  whisper: ModelState;
  ffmpeg: ModelState;
  disk_free_bytes: number;
  data_dir: string;
  ai_concurrency: number;
}
const weights: Record<string, string> = {
  event_relevance: "已确认事件",
  quality: "照片质量",
  calendar_relevance: "纪念日与日历",
  user_preference: "家庭偏好",
  freshness: "新鲜程度",
  underexposure: "历史曝光稀缺度",
};
export function Settings() {
  const auth = useAuth();
  const notify = useNotice();
  const qc = useQueryClient();
  const [config, setConfig] = useState<Config | null>(null);
  const [code, setCode] = useState("");
  const [pair, setPair] = useState("");
  const [jobId, setJobId] = useState("");
  const [faceConsent, setFaceConsent] = useState(false);
  const isOwner = auth.role === "owner";
  const query = useQuery({
    queryKey: ["settings"],
    queryFn: () => api<{ household: Household; settings: Config }>("/settings"),
  });
  const models = useQuery({
    queryKey: ["models"],
    queryFn: () => api<Models>("/system/models"),
    enabled: isOwner,
  });
  const members = useQuery({
    queryKey: ["members"],
    queryFn: () =>
      apiAll<Member>(`/households/${auth.household.id}/members`).then(
        (items) => ({ items, total: items.length }),
      ),
  });
  const invites = useQuery({
    queryKey: ["invites"],
    queryFn: () =>
      apiAll<Invite>(`/households/${auth.household.id}/invites`).then(
        (items) => ({ items, total: items.length }),
      ),
    enabled: isOwner,
  });
  const devices = useQuery({
    queryKey: ["devices"],
    queryFn: () =>
      apiAll<Device>("/display/devices").then((items) => ({
        items,
        total: items.length,
      })),
    enabled: isOwner,
  });
  const exportJob = useQuery({
    queryKey: ["data-export", jobId],
    queryFn: () => api<Job>("/jobs/" + jobId),
    enabled: !!jobId,
    refetchInterval: (q) =>
      q.state.data && ["succeeded", "failed"].includes(q.state.data.state)
        ? false
        : 2000,
  });
  useEffect(() => {
    if (query.data) setConfig(query.data.settings);
  }, [query.data]);
  async function mutation(path: string, method = "POST", body?: unknown) {
    try {
      const r = await api(path, method, body);
      await qc.invalidateQueries({ queryKey: ["settings"] });
      await qc.invalidateQueries({ queryKey: ["me"] });
      await qc.invalidateQueries({ queryKey: ["members"] });
      notify("设置已保存");
      return r;
    } catch (e) {
      notify((e as Error).message);
    }
  }
  async function save(e: FormEvent) {
    e.preventDefault();
    if (!config) return;
    const { jobs_paused, config_version, ...rest } = config;
    await mutation("/settings", "PATCH", {
      ...rest,
      face_consent: faceConsent,
    });
  }
  async function logout() {
    try {
      await api("/auth/logout", "POST");
      qc.clear();
      location.assign("/login");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  if (query.isPending) return <Loading />;
  if (query.error) return <ErrorBox error={query.error} />;
  if (!config) return <Loading />;
  if (!isOwner)
    return (
      <>
        <PageTitle
          title="家庭设置"
          description="此页面的修改操作需要家庭管理员权限。"
          actions={<button onClick={() => void logout()}>退出登录</button>}
        />
        <section className="panel">
          <h2>{auth.household.name}</h2>
          <p>家庭时区：{auth.household.timezone}</p>
          <p>
            人脸分析：{config.faces_enabled ? "已授权开启" : "默认关闭"} ·
            语音处理：{config.speech_enabled ? "开启" : "关闭"}
          </p>
          <h3>家庭成员</h3>
          {members.data?.items.map((m) => (
            <p key={m.id}>
              {m.display_name} · {m.role}
            </p>
          ))}
        </section>
      </>
    );
  function update<K extends keyof Config>(key: K, value: Config[K]) {
    setConfig((c) => (c ? { ...c, [key]: value } : c));
  }
  return (
    <>
      <PageTitle
        title="安心收藏，由你掌握"
        description="家庭共享、模型授权、展示习惯与备份，所有重要决定都在这里。"
        actions={<button onClick={() => void logout()}>退出登录</button>}
      />
      <section className="panel">
        <h2>我们的家庭</h2>
        <form
          className="form-grid"
          onSubmit={(e) => {
            e.preventDefault();
            void mutation(
              "/households/" + auth.household.id,
              "PATCH",
              Object.fromEntries(new FormData(e.currentTarget)),
            );
          }}
        >
          <label>
            家庭名称
            <input
              name="name"
              defaultValue={query.data.household.name}
              required
            />
          </label>
          <label>
            家庭时区
            <input
              name="timezone"
              defaultValue={query.data.household.timezone}
              required
            />
          </label>
          <button>保存家庭信息</button>
        </form>
      </section>
      <form onSubmit={save}>
        <section className="panel">
          <div className="section-heading">
            <h2>隐私与本地 AI</h2>
            <ShieldCheck size={24} />
          </div>
          <Check
            label="启用本地 SigLIP2 图文编码"
            checked={config.embedding_enabled}
            onChange={(v) => update("embedding_enabled", v)}
          />
          <Check
            label="启用本机 Qwen3-VL 视觉理解"
            checked={config.vlm_enabled}
            onChange={(v) => update("vlm_enabled", v)}
          />
          <Check
            label="启用本地语音处理（whisper.cpp）"
            checked={config.speech_enabled}
            onChange={(v) => update("speech_enabled", v)}
          />
          <Check
            label="启用人脸检测与候选聚类"
            checked={config.faces_enabled}
            onChange={(v) => update("faces_enabled", v)}
          />
          {config.faces_enabled && !query.data.settings.faces_enabled && (
            <div className="consent-box">
              <p>
                人脸特征属于敏感生物信息，仅保存在本地。照片中的儿童也需要监护人授权。模型不会自动填写姓名和亲属关系。你可以随时撤回授权并删除特征。
              </p>
              <Check
                checked={faceConsent}
                onChange={setFaceConsent}
                label="我已告知相关家人并获得必要授权，同意在本地处理人脸特征（含儿童数据提示）"
              />
            </div>
          )}
          <div className="actions">
            <button
              type="button"
              className="danger"
              onClick={async () => {
                if (
                  window.confirm(
                    "彻底删除本家庭的机器检测人脸特征和聚类关联？手工登记的人物与手工照片标注会保留。",
                  )
                ) {
                  await mutation("/settings", "PATCH", {
                    faces_enabled: false,
                    delete_biometrics: true,
                  });
                  await qc.invalidateQueries({ queryKey: ["people"] });
                }
              }}
            >
              撤回授权并彻底删除生物特征
            </button>
          </div>
          <p className="muted">
            上传默认不自动共享；每批照片都需要明确选择共享。AI
            任务固定单并发，重模型按任务加载和卸载。
          </p>
          <div className="form-grid">
            <label>
              上传共享策略（始终逐批确认）
              <select
                value={config.default_visibility}
                onChange={(e) => update("default_visibility", e.target.value)}
              >
                <option value="private">默认仅自己可见</option>
                <option value="family">建议家庭共享，须上传者确认</option>
              </select>
            </label>
            <label>
              候选人脸相似度阈值
              <input
                type="number"
                min={0.1}
                max={0.95}
                step={0.01}
                value={config.face_similarity}
                onChange={(e) =>
                  update("face_similarity", Number(e.target.value))
                }
              />
            </label>
            <label>
              短活动时间窗口（小时）
              <input
                type="number"
                min={1}
                max={24}
                value={config.event_gap_hours}
                onChange={(e) =>
                  update("event_gap_hours", Number(e.target.value))
                }
              />
            </label>
            <label>
              旅行跨天窗口（小时）
              <input
                type="number"
                min={24}
                max={96}
                value={config.travel_gap_hours}
                onChange={(e) =>
                  update("travel_gap_hours", Number(e.target.value))
                }
              />
            </label>
          </div>
          <small>阈值是可校准参数，并非识别准确率保证。</small>
        </section>
        <section className="panel">
          <h2>相框与主动展示</h2>
          <div className="form-grid">
            <label>
              每日精选数量
              <input
                type="number"
                min={10}
                max={30}
                value={config.daily_photo_limit}
                onChange={(e) =>
                  update("daily_photo_limit", Number(e.target.value))
                }
              />
            </label>
            <label>
              每张停留秒数
              <input
                type="number"
                min={3}
                max={120}
                value={config.display_interval_seconds}
                onChange={(e) =>
                  update("display_interval_seconds", Number(e.target.value))
                }
              />
            </label>
            <label>
              夜间静默开始
              <input
                type="time"
                value={config.quiet_start}
                onChange={(e) => update("quiet_start", e.target.value)}
              />
            </label>
            <label>
              夜间静默结束
              <input
                type="time"
                value={config.quiet_end}
                onChange={(e) => update("quiet_end", e.target.value)}
              />
            </label>
            <label>
              首页空闲后进入相框（秒）
              <input
                type="number"
                min={30}
                max={120}
                value={config.idle_seconds}
                onChange={(e) => update("idle_seconds", Number(e.target.value))}
              />
            </label>
            <label>
              历史疲劳窗口（天）
              <input
                type="number"
                min={1}
                max={90}
                value={config.recent_days}
                onChange={(e) => update("recent_days", Number(e.target.value))}
              />
            </label>
            <label>
              最低推荐质量
              <input
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={config.quality_floor}
                onChange={(e) =>
                  update("quality_floor", Number(e.target.value))
                }
              />
            </label>
            <label>
              每日生成时间（家庭本地小时）
              <input
                type="number"
                min={0}
                max={23}
                value={config.daily_generation_hour}
                onChange={(e) =>
                  update("daily_generation_hour", Number(e.target.value))
                }
              />
            </label>
          </div>
          <Check
            label="开启空闲自动展示与轮播"
            checked={config.autoplay}
            onChange={(v) => update("autoplay", v)}
          />
          <h3>展示类别</h3>
          <div className="check-grid">
            {Object.entries(config.scenes).map(([k, v]) => (
              <Check
                key={k}
                label={sceneNames[k] || k}
                checked={v}
                onChange={(value) =>
                  update("scenes", { ...config.scenes, [k]: value })
                }
              />
            ))}
          </div>
        </section>
        <section className="panel">
          <h2>家庭偏好与推荐权重</h2>
          <p>
            权重将归一化。选择仍受隐私、去重、活动与人物多样性以及播放疲劳约束。
          </p>
          <div className="form-grid">
            {Object.entries(config.weights).map(([k, v]) => (
              <label key={k}>
                {weights[k]}
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  value={v}
                  onChange={(e) =>
                    update("weights", {
                      ...config.weights,
                      [k]: Number(e.target.value),
                    })
                  }
                />
              </label>
            ))}
          </div>
        </section>
        <div className="sticky-save">
          <button className="primary">保存隐私、展示与推荐设置</button>
          <button
            type="button"
            onClick={async () => {
              try {
                await api("/playlists/regenerate", "POST");
                notify("今日精选已刷新，相框在本轮结束后切换");
              } catch (e) {
                notify((e as Error).message);
              }
            }}
          >
            刷新今日精选
          </button>
          <span>配置版本 {config.config_version}</span>
        </div>
      </form>
      <section className="panel">
        <h2>本地模型与存储状态</h2>
        {models.error && <ErrorBox error={models.error} />}
        <div className="model-grid">
          {models.data &&
            (["ollama", "siglip2", "faces", "whisper", "ffmpeg"] as const).map(
              (k) => {
                const model = models.data![k];
                return (
                  <div className="model-state" key={k}>
                    <span
                      className={
                        "status-dot " + (model.installed ? "ready" : "")
                      }
                    />
                    <strong>{k}</strong>
                    <span>
                      {model.installed ? "已安装" : model.message || "尚未安装"}
                    </span>
                    <small>{model.model || model.version || "本地执行"}</small>
                  </div>
                );
              },
            )}
        </div>
        <p>
          <HardDrive size={17} /> 数据目录：{models.data?.data_dir || "读取中"}{" "}
          · 可用{" "}
          {models.data
            ? (models.data.disk_free_bytes / 1024 ** 3).toFixed(1)
            : "—"}{" "}
          GB
        </p>
        <pre>bash scripts/download_models.sh all</pre>
        <p>
          安装说明：
          <a
            href="https://github.com/ttonghao-thao/Xbum"
            target="_blank"
            rel="noreferrer"
          >
            项目仓库
          </a>
          ；本地完整指引位于 docs/MODEL_MANIFEST.md。
        </p>
        <button onClick={() => void models.refetch()}>刷新模型状态</button>
      </section>
      <section className="panel">
        <h2>一起收藏记忆的人</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>家人</th>
                <th>角色</th>
                <th>管理</th>
              </tr>
            </thead>
            <tbody>
              {members.data?.items.map((m) => (
                <tr key={m.id}>
                  <td>{m.display_name}</td>
                  <td>
                    <select
                      aria-label={m.display_name + "的角色"}
                      value={m.role}
                      onChange={(e) =>
                        void mutation("/members/" + m.id, "PATCH", {
                          role: e.target.value,
                        })
                      }
                    >
                      <option value="owner">owner</option>
                      <option value="member">member</option>
                      <option value="viewer">viewer</option>
                    </select>
                  </td>
                  <td>
                    <button
                      className="danger"
                      onClick={() => {
                        if (window.confirm("移除此成员并撤销其会话？"))
                          void mutation("/members/" + m.id, "DELETE");
                      }}
                    >
                      移除
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <form
          className="filters"
          onSubmit={async (e) => {
            e.preventDefault();
            const form = new FormData(e.currentTarget);
            try {
              const r = await api<{ url: string }>(
                `/households/${auth.household.id}/invites`,
                "POST",
                {
                  role: form.get("role"),
                  hours: Number(form.get("hours")),
                  max_uses: Number(form.get("max_uses")),
                },
              );
              setCode(location.origin + r.url);
              await invites.refetch();
            } catch (e) {
              notify((e as Error).message);
            }
          }}
        >
          <label>
            邀请角色
            <select name="role">
              <option value="member">可上传成员</option>
              <option value="viewer">只读成员</option>
            </select>
          </label>
          <label>
            有效小时
            <input
              name="hours"
              type="number"
              defaultValue={24}
              min={1}
              max={168}
            />
          </label>
          <label>
            可用次数
            <input
              name="max_uses"
              type="number"
              defaultValue={1}
              min={1}
              max={50}
            />
          </label>
          <button>生成邀请链接</button>
        </form>
        {code && (
          <div className="generated-code">
            <label>
              请私下分享此邀请链接
              <input readOnly value={code} onFocus={(e) => e.target.select()} />
            </label>
          </div>
        )}
        {invites.data?.items
          .filter((i) => !i.revoked)
          .map((i) => (
            <div className="job-line" key={i.id}>
              <span>
                {i.role} · {i.used_count}/{i.max_uses} 次 · 到期{" "}
                {new Date(i.expires_at).toLocaleString()}
              </span>
              <button
                onClick={async () => {
                  await mutation("/invites/" + i.id, "DELETE");
                  await invites.refetch();
                }}
              >
                撤销
              </button>
            </div>
          ))}
      </section>
      <section className="panel">
        <h2>给记忆相框一把小钥匙</h2>
        <p>
          配对码 10 分钟有效，只能使用一次。展示会话不能下载原图或管理家庭。
        </p>
        <button
          onClick={async () => {
            try {
              const r = await api<{ code: string }>(
                "/display/devices",
                "POST",
                { name: "家庭记忆相框" },
              );
              setPair(r.code);
              await devices.refetch();
            } catch (e) {
              notify((e as Error).message);
            }
          }}
        >
          生成展示设备配对码
        </button>
        {pair && (
          <div className="pair-code">
            {pair}
            <Link className="button" to={"/display?pair=" + pair}>
              在此浏览器配对
            </Link>
          </div>
        )}
        {devices.data?.items.map((d) => (
          <div key={d.id} className="job-line">
            <span>
              {d.name} ·{" "}
              {d.revoked_at ? "已撤销" : d.paired_at ? "已配对" : "等待配对"}
            </span>
            <button
              disabled={!!d.revoked_at}
              onClick={async () => {
                await mutation("/display/devices/" + d.id, "DELETE");
                await devices.refetch();
              }}
            >
              撤销设备
            </button>
          </div>
        ))}
      </section>
      <section className="panel">
        <h2>导出与备份</h2>
        <p>
          导出仅包含当前授权的家庭共享原图和元数据。完整备份包含敏感特征与数据库，须保存在受保护磁盘。
        </p>
        <button
          onClick={async () => {
            try {
              const r = await api<{ job_id: string }>("/system/export", "POST");
              setJobId(r.job_id);
            } catch (e) {
              notify((e as Error).message);
            }
          }}
        >
          <Download size={16} />
          生成授权照片导出包
        </button>
        {exportJob.data && (
          <p>
            {exportJob.data.state} {exportJob.data.error_message_sanitized}
            {exportJob.data.state === "succeeded" && (
              <a
                className="button"
                href={`/api/v1/system/export/${jobId}`}
                download
              >
                下载导出包
              </a>
            )}
          </p>
        )}
        <pre>
          make backup{`\n`}bash scripts/restore.sh /绝对路径/备份.tar.gz
        </pre>
        <p className="muted">
          恢复会校验完整性、拒绝路径逃逸，并要求停止运行服务及明确确认覆盖。
        </p>
      </section>
      <section className="panel danger-zone">
        <h3>删除家庭空间</h3>
        <p>
          这会删除当前家庭的照片、录音、故事和数据库记录，无法恢复。请先备份。
        </p>
        <button
          className="danger"
          onClick={async () => {
            const text = window.prompt(
              "输入家庭名称以确认永久删除：" + auth.household.name,
            );
            if (text === auth.household.name)
              try {
                await api(
                  "/households/" +
                    auth.household.id +
                    "?confirm=" +
                    encodeURIComponent(text),
                  "DELETE",
                );
                qc.clear();
                location.assign("/login");
              } catch (e) {
                notify((e as Error).message);
              }
          }}
        >
          永久删除此家庭
        </button>
      </section>
    </>
  );
}

export function Jobs() {
  const auth = useAuth();
  const notify = useNotice();
  const [state, setState] = useState("");
  const [page, setPage] = useState(1);
  const query = useQuery({
    queryKey: ["jobs", page, state],
    queryFn: () => api<List<Job>>(`/jobs?page=${page}&state=${state}`),
    refetchInterval: 4000,
  });
  const stats = useQuery({
    queryKey: ["stats"],
    queryFn: () =>
      api<{
        photos: number;
        events: number;
        people: number;
        playbacks: number;
        pending: number;
        ai_success_rate: number | null;
        mean_job_ms: number | null;
      }>("/system/stats"),
    refetchInterval: 5000,
  });
  async function control(action: string) {
    try {
      await api("/jobs/" + action, "POST");
      notify(action === "pause" ? "当前任务完成后暂停" : "任务已继续");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  return (
    <>
      <PageTitle
        title="记忆正在被用心整理"
        description="模型只在本机运行，任务依次处理。失败不会影响相册浏览与展示。"
        actions={
          auth.role === "owner" && (
            <>
              <button onClick={() => void control("pause")}>
                <Pause size={16} />
                暂停领取
              </button>
              <button onClick={() => void control("resume")}>
                <Play size={16} />
                继续处理
              </button>
            </>
          )
        }
      />
      <div className="stat-grid">
        {[
          ["照片", stats.data?.photos],
          ["事件", stats.data?.events],
          ["人物", stats.data?.people],
          ["待处理", stats.data?.pending],
          ["播放次数", stats.data?.playbacks],
          [
            "AI 任务成功率",
            stats.data?.ai_success_rate != null
              ? `${(stats.data.ai_success_rate * 100).toFixed(1)}%`
              : "—",
          ],
        ].map(([label, value]) => (
          <div className="stat" key={String(label)}>
            <span>{label}</span>
            <strong>{value ?? "—"}</strong>
          </div>
        ))}
      </div>
      <div className="filters">
        <select
          aria-label="任务状态筛选"
          value={state}
          onChange={(e) => {
            setState(e.target.value);
            setPage(1);
          }}
        >
          <option value="">全部状态</option>
          {[
            "queued",
            "running",
            "retry",
            "succeeded",
            "failed",
            "skipped",
            "cancelled",
          ].map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <button onClick={() => void query.refetch()}>
          <RefreshCw size={15} />
          刷新
        </button>
        <span className="muted">
          平均任务耗时 {stats.data?.mean_job_ms?.toFixed(0) || "—"}{" "}
          ms（含非模型任务）
        </span>
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorBox error={query.error} />
      ) : query.data.items.length ? (
        <section className="panel table-wrap">
          <table>
            <thead>
              <tr>
                <th>步骤 / 对象</th>
                <th>状态</th>
                <th>耗时 / 次数</th>
                <th>模型版本 / 恢复</th>
              </tr>
            </thead>
            <tbody>
              {query.data.items.map((job) => (
                <tr key={job.id}>
                  <td>
                    <strong>{jobNames[job.job_type] || job.job_type}</strong>
                    <small>
                      {job.target_type === "photo" ? (
                        <Link to={"/photos/" + job.target_id}>
                          {job.target_id.slice(0, 10)}
                        </Link>
                      ) : (
                        job.target_id.slice(0, 10)
                      )}
                    </small>
                  </td>
                  <td>
                    <span className={"pill state-" + job.state}>
                      {job.state}
                    </span>
                    <small className="job-error">
                      {job.error_message_sanitized}
                    </small>
                  </td>
                  <td>
                    {job.duration_ms === null ? "—" : job.duration_ms + " ms"}
                    <small>{job.attempts} 次尝试</small>
                  </td>
                  <td>
                    <small>{job.model_version || "—"}</small>
                    {auth.role !== "viewer" && job.state !== "running" && (
                      <button
                        onClick={async () => {
                          try {
                            await api(`/jobs/${job.id}/retry`, "POST");
                            await query.refetch();
                          } catch (e) {
                            notify((e as Error).message);
                          }
                        }}
                      >
                        重试 / 重跑
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : (
        <Empty
          title="这里暂时很安静"
          text="上传照片或导出故事后，会在这里看到真实的处理进度。"
          action=""
        />
      )}
      <Pagination
        page={page}
        total={query.data?.total || 0}
        size={50}
        onChange={setPage}
      />
    </>
  );
}
