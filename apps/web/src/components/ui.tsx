import { createContext, useContext, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Camera, ArrowUpRight, X } from "lucide-react";
import type { Auth, Photo } from "../api/client";
import { captureLabel } from "../api/client";
export const AuthContext = createContext<Auth | null>(null);
export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("没有登录");
  return value;
}
const NotifyContext = createContext<(message: string) => void>(() => {});
export function Notices({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState("");
  return (
    <NotifyContext.Provider value={setMessage}>
      {children}
      {message && (
        <div className="toast" role="status">
          {message}
          <button aria-label="关闭提示" onClick={() => setMessage("")}>
            <X size={18} />
          </button>
        </div>
      )}
    </NotifyContext.Provider>
  );
}
export function useNotice() {
  return useContext(NotifyContext);
}
export function PageTitle({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-title">
      <div>
        <span className="eyebrow">{eyebrow || "OUR FAMILY, OUR STORIES"}</span>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      <div className="actions">{actions}</div>
    </header>
  );
}
export function Empty({
  title = "还没有照片，记忆从这里开始",
  text = "把喜欢的日常留在这里，由你决定与家人分享哪些瞬间。",
  action = "/upload",
  label = "上传第一张照片",
}: {
  title?: string;
  text?: string;
  action?: string;
  label?: string;
}) {
  return (
    <div className="empty">
      <div className="empty-symbol">
        <Camera size={36} strokeWidth={1} />
      </div>
      <h2>{title}</h2>
      <p>{text}</p>
      {action && (
        <Link className="button primary" to={action}>
          {label}
          <ArrowUpRight size={17} />
        </Link>
      )}
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading" role="status">
      正在整理记忆…
    </div>
  );
}
export function ErrorBox({ error }: { error: unknown }) {
  return (
    <div className="error" role="alert">
      {error instanceof Error ? error.message : String(error)}
    </div>
  );
}
export function PhotoGrid({
  photos,
  selected,
  onSelect,
}: {
  photos: Photo[];
  selected?: Set<string>;
  onSelect?: (id: string) => void;
}) {
  return (
    <div className="photo-grid">
      {photos.map((p) => (
        <article
          className={"photo-card " + (selected?.has(p.id) ? "selected" : "")}
          key={p.id}
        >
          {onSelect && (
            <label className="photo-select">
              <input
                type="checkbox"
                aria-label={"选择 " + (p.title || p.id)}
                checked={selected?.has(p.id) || false}
                onChange={() => onSelect(p.id)}
              />
            </label>
          )}
          <Link to={"/photos/" + p.id}>
            <div className="photo-image">
              <img
                src={p.thumbnail_url}
                alt={p.title || "家庭照片"}
                loading="lazy"
              />
              {p.is_hidden && <span className="image-badge">已隐藏</span>}
              {p.visibility === "private" && (
                <span className="image-badge">仅自己</span>
              )}
            </div>
            <div className="photo-caption">
              <strong>{p.title || "生活里的一个瞬间"}</strong>
              <small>{captureLabel(p)}</small>
              {"album_reason" in p && <small>{String(p.album_reason)}</small>}
            </div>
          </Link>
        </article>
      ))}
    </div>
  );
}
export function Check({
  label,
  checked,
  onChange,
  disabled = false,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="check">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
      />
      <span>{label}</span>
    </label>
  );
}
export function Pagination({
  page,
  total,
  size = 40,
  onChange,
}: {
  page: number;
  total: number;
  size?: number;
  onChange: (p: number) => void;
}) {
  return total > size ? (
    <div className="pagination">
      <button disabled={page === 1} onClick={() => onChange(page - 1)}>
        上一页
      </button>
      <span>
        {page} / {Math.ceil(total / size)}
      </span>
      <button
        disabled={page * size >= total}
        onClick={() => onChange(page + 1)}
      >
        下一页
      </button>
    </div>
  ) : null;
}
export function move<T>(list: T[], index: number, direction: number) {
  const next = [...list];
  const target = index + direction;
  if (target < 0 || target >= list.length) return next;
  [next[index], next[target]] = [next[target], next[index]];
  return next;
}
