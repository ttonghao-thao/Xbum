import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Album, ArrowRight, LockKeyhole } from "lucide-react";
import { api, setCsrf } from "../api/client";
import { ErrorBox } from "../components/ui";
export function AuthPage({
  mode,
}: {
  mode: "setup" | "login" | "invite" | "register";
}) {
  const [error, setError] = useState<unknown>();
  const [pending, setPending] = useState(false);
  const { code } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["auth-status"],
    queryFn: () => api<{ needs_setup: boolean }>("/auth/status"),
  });
  if (mode === "login" && status.data?.needs_setup)
    return <Navigate to="/setup" replace />;
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPending(true);
    setError(undefined);
    const data = Object.fromEntries(new FormData(e.currentTarget));
    try {
      const result = await api<{ csrf: string }>(
        mode === "invite" ? `/invites/${code}/join` : "/auth/" + mode,
        "POST",
        data,
      );
      setCsrf(result.csrf);
      qc.clear();
      navigate("/");
    } catch (error) {
      setError(error);
    } finally {
      setPending(false);
    }
  }
  const title =
    mode === "setup"
      ? "为家，留一份记忆"
      : mode === "invite"
        ? "一起，收藏家的日常"
        : mode === "register"
          ? "创建一个新的家"
          : "欢迎回到家";
  return (
    <div className="auth-page">
      <div className="auth-art">
        <Album size={50} strokeWidth={1} />
        <div className="auth-orbit orbit1" />
        <div className="auth-orbit orbit2" />
        <span className="eyebrow">EVERYDAY BECOMES A MEMORY</span>
        <h1>
          有些瞬间，
          <br />
          值得一看再看。
        </h1>
        <p>
          从一张照片开始，
          <br />
          把那些细小的幸福，慢慢收好。
        </p>
        <small>家庭共享 · 本地 AI · 你的记忆由你保管</small>
      </div>
      <section className="auth-form">
        <Link to="/" className="brand">
          <Album size={30} />
          Xbum
        </Link>
        <h2>{title}</h2>
        <p>
          {mode === "login"
            ? "登录你的家庭记忆相册。"
            : "账号与照片保存在这台 Mac，设置至少 12 位口令。"}
        </p>
        <form onSubmit={submit}>
          {mode !== "login" && (
            <label>
              你的称呼
              <input
                name="display_name"
                required
                maxLength={100}
                autoComplete="name"
                placeholder="家人怎么称呼你"
              />
            </label>
          )}
          {(mode === "setup" || mode === "register") && (
            <label>
              家庭名称
              <input
                name="household_name"
                required
                maxLength={100}
                placeholder="我们的家"
              />
            </label>
          )}
          <label>
            用户名
            <input
              name="username"
              required
              minLength={2}
              maxLength={100}
              pattern="[\w.@+\-]+"
              autoComplete="username"
              placeholder="用于登录"
            />
          </label>
          <label>
            口令
            <input
              name="password"
              type="password"
              required
              minLength={12}
              maxLength={256}
              autoComplete={
                mode === "login" ? "current-password" : "new-password"
              }
              placeholder="至少 12 个字符"
            />
          </label>
          {error !== undefined && <ErrorBox error={error} />}
          <button className="primary" disabled={pending}>
            {pending
              ? "正在处理…"
              : mode === "login"
                ? "登录"
                : mode === "invite"
                  ? "加入家庭"
                  : "创建家庭相册"}
            <ArrowRight size={18} />
          </button>
        </form>
        <div className="auth-foot">
          <LockKeyhole size={15} /> 不使用第三方在线 AI 推理
        </div>
        {mode === "login" ? (
          <Link to="/register">创建另一个独立家庭</Link>
        ) : (
          <Link to="/login">已有账号？返回登录</Link>
        )}
      </section>
    </div>
  );
}
