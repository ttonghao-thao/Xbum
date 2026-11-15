import { useEffect } from "react";
import {
  Link,
  NavLink,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Album,
  BookOpen,
  CalendarDays,
  Clock3,
  Heart,
  Home as HomeIcon,
  Image,
  Layers,
  LogOut,
  MonitorPlay,
  Search as SearchIcon,
  Settings as SettingsIcon,
  Upload as UploadIcon,
  Users,
} from "lucide-react";
import { api, setCsrf, type AccountAuth } from "./api/client";
import {
  AuthContext,
  ErrorBox,
  Loading,
  useAuth,
  useNotice,
} from "./components/ui";
import { AuthPage, CreateHousehold } from "./pages/Auth";
import { Home, PhotoDetail, Photos, Upload } from "./pages/Library";
import { Album as MemoryAlbumPage } from "./pages/Albums";
import { EventDetail, Events, People, Timeline } from "./pages/Memory";
import { Stories, StoryEdit } from "./pages/Stories";
import {
  DiscoverSearch,
  Collections,
  CollectionDetail,
} from "./pages/Discover";
import { Preferences } from "./pages/Preferences";
import { Duplicates } from "./pages/Duplicates";
import { Jobs, Settings } from "./pages/Admin";
import { Display } from "./pages/Display";

const nav = [
  ["/", "家庭记忆", HomeIcon],
  ["/photos", "照片库", Image],
  ["/events", "家庭事件", Layers],
  ["/people", "家庭人物", Users],
  ["/timeline", "成长时间线", CalendarDays],
  ["/stories", "照片故事", BookOpen],
  ["/search", "寻找记忆", SearchIcon],
  ["/collections", "智能相册", Album],
  ["/duplicates", "相似照片", Image],
  ["/preferences", "推荐偏好", Heart],
] as const;
function Shell() {
  const auth = useAuth();
  const navigate = useNavigate();
  const query = useQueryClient();
  const notify = useNotice();
  const location = useLocation();
  useEffect(() => {
    if (!auth.household.settings_json.autoplay || location.pathname !== "/")
      return;
    let timer: ReturnType<typeof setTimeout>;
    const reset = () => {
      clearTimeout(timer);
      timer = setTimeout(
        () => navigate("/display"),
        auth.household.settings_json.idle_seconds * 1000,
      );
    };
    reset();
    window.addEventListener("pointerdown", reset);
    window.addEventListener("keydown", reset);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("pointerdown", reset);
      window.removeEventListener("keydown", reset);
    };
  }, [auth, location.pathname, navigate]);
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link to="/" className="brand">
          <div className="brand-icon">
            <Album size={27} />
          </div>
          <div>
            Xbum<span>家庭记忆相册</span>
          </div>
        </Link>
        <div className="family-label">
          <span className="green-dot" />
          {auth.household.name}
          <small>只属于我们的记忆</small>
        </div>
        <nav>
          {nav.map(([path, label, Icon]) => (
            <NavLink to={path} end={path === "/"} key={path}>
              <Icon size={19} strokeWidth={1.6} />
              {label}
            </NavLink>
          ))}
          <NavLink className="mobile-nav" to="/jobs">
            <Clock3 size={16} />
            处理任务
          </NavLink>
          <NavLink className="mobile-nav" to="/settings">
            <SettingsIcon size={16} />
            家庭设置
          </NavLink>
        </nav>
        <div className="sidebar-bottom">
          <NavLink to="/display">
            <MonitorPlay size={19} />
            打开记忆相框
            <Arrow />
          </NavLink>
          <NavLink to="/jobs">
            <Clock3 size={18} />
            处理任务
          </NavLink>
          <NavLink to="/settings">
            <SettingsIcon size={18} />
            家庭设置
          </NavLink>
          <div className="profile">
            <span className="avatar">{auth.user.display_name[0]}</span>
            <div>
              {auth.user.display_name}
              <small>
                {auth.role === "owner"
                  ? "家庭管理员"
                  : auth.role === "viewer"
                    ? "只读成员"
                    : "家庭成员"}
              </small>
            </div>
            <button
              aria-label="退出登录"
              className="icon-button"
              onClick={async () => {
                try {
                  await api("/auth/logout", "POST");
                  query.clear();
                  navigate("/login");
                } catch (e) {
                  notify((e as Error).message);
                }
              }}
            >
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <main className="main">
        <div className="topbar">
          <span>
            <span className="green-dot" />
            记忆安心留在本地
          </span>
          <div>
            <Link to="/search" aria-label="搜索">
              <SearchIcon size={18} />
            </Link>
            {auth.role !== "viewer" && (
              <Link to="/upload" className="button small primary">
                <UploadIcon size={16} />
                上传照片
              </Link>
            )}
          </div>
        </div>
        <div className="page-content">
          <Outlet />
        </div>
        <footer>
          Xbum <span>把平凡的日子，留成家的故事。</span>
          <span>
            <Heart size={12} /> 本地保存 · 私密共享
          </span>
        </footer>
      </main>
    </div>
  );
}
function Arrow() {
  return <span className="nav-arrow">↗</span>;
}
function Protected() {
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<AccountAuth>("/auth/me"),
  });
  useEffect(() => {
    if (me.data) setCsrf(me.data.csrf);
  }, [me.data]);
  if (me.isPending) return <Loading />;
  if (me.error) return <Navigate to="/login" replace />;
  if (me.data.role === "account")
    return <Navigate to="/households/new" replace />;
  return (
    <AuthContext.Provider value={me.data}>
      <Shell />
    </AuthContext.Provider>
  );
}
export default function App() {
  return (
    <Routes>
      <Route path="/setup" element={<AuthPage mode="setup" />} />
      <Route path="/login" element={<AuthPage mode="login" />} />
      <Route path="/register" element={<AuthPage mode="register" />} />
      <Route path="/households/new" element={<CreateHousehold />} />
      <Route path="/invite/:code" element={<AuthPage mode="invite" />} />
      <Route path="/display" element={<Display />} />
      <Route element={<Protected />}>
        <Route index element={<Home />} />
        <Route path="upload" element={<Upload />} />
        <Route path="photos" element={<Photos />} />
        <Route path="photos/:id" element={<PhotoDetail />} />
        <Route path="albums/:kind" element={<MemoryAlbumPage />} />
        <Route path="events" element={<Events />} />
        <Route path="events/:id" element={<EventDetail />} />
        <Route path="people" element={<People />} />
        <Route path="timeline" element={<Timeline />} />
        <Route path="stories" element={<Stories />} />
        <Route path="stories/:id/edit" element={<StoryEdit />} />
        <Route path="search" element={<DiscoverSearch />} />
        <Route path="collections" element={<Collections />} />
        <Route path="collections/:id" element={<CollectionDetail />} />
        <Route path="duplicates" element={<Duplicates />} />
        <Route path="preferences" element={<Preferences />} />
        <Route path="settings" element={<Settings />} />
        <Route path="jobs" element={<Jobs />} />
        <Route
          path="*"
          element={
            <div>
              <ErrorBox error="页面不存在" />
              <Link to="/">回到家庭记忆</Link>
            </div>
          }
        />
      </Route>
    </Routes>
  );
}
