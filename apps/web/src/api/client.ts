export interface Analysis {
  quality_score: number | null;
  quality_reasons_json: string[];
  tags_json: string[];
  caption_ai: string;
  analysis_json: Record<string, unknown>;
  status: string;
}
export interface Photo {
  preferred?: boolean;
  id: string;
  household_id: string;
  uploader_id: string;
  title: string;
  caption_user: string;
  location_user: string;
  taken_at: string | null;
  capture_time_source: string;
  household_timezone?: string;
  visibility: "private" | "family";
  is_hidden: boolean;
  is_deleted: boolean;
  duplicate_group: string | null;
  width: number;
  height: number;
  gps_lat: number | null;
  gps_lon: number | null;
  analysis: Analysis | null;
  media_url: string;
  thumbnail_url: string;
  created_at: string;
  can_edit?: boolean;
  jobs?: Job[];
  faces?: Face[];
  memories?: AudioMemory[];
  match_reasons?: string[];
  score?: number;
}
export interface Job {
  id: string;
  target_id: string;
  target_type: string;
  job_type: string;
  state: string;
  attempts: number;
  error_message_sanitized: string | null;
  duration_ms: number | null;
  model_version: string;
  created_at: string;
  result_json: Record<string, unknown>;
}
export interface Face {
  id: string;
  photo_id: string;
  person_id: string;
  bounding_box_json: number[];
  status: string;
  locked: boolean;
}
export interface Person {
  id: string;
  name: string | null;
  relationship: string | null;
  consent_flag: boolean;
  manually_confirmed: boolean;
  is_family: boolean;
  growth_enabled: boolean;
  growth_mode: "auto" | "include" | "exclude";
  birthday: string | null;
  faces: Face[];
  photo_ids: string[];
}
export interface FamilyEvent {
  id: string;
  title: string;
  event_type: string;
  start_at: string | null;
  end_at: string | null;
  location_label: string;
  location_source: string;
  summary: string;
  confirmed: boolean;
  locked: boolean;
  revision: number;
  cover_photo_id: string | null;
  photo_ids: string[];
  photos?: Photo[];
  memories?: AudioMemory[];
  segments_json: { date: string; photo_ids: string[] }[];
  participants_json: string[];
}
export interface Slide {
  narration_url?: string;
  narration_transcript?: string;
  narration_invalid?: boolean;
  photo_id: string;
  position?: number;
  duration_s: number;
  caption: string;
  transition: "fade" | "cut";
}
export interface Story {
  composition?: {
    state: string;
    length: string;
    generator: string;
    job_id?: string;
    job_state?: string;
    error_message?: string;
    chapters?: { title: string; photo_ids: string[] }[];
    sources?: {
      photo_id: string;
      caption_source: string;
      date: string | null;
    }[];
  } | null;
  id: string;
  title: string;
  theme: "warm" | "classic" | "night";
  background: string;
  ending: string;
  status: string;
  version: number;
  cover_photo_id: string | null;
  slides: Slide[];
  start_at: string | null;
  end_at: string | null;
}
export interface AudioMemory {
  id: string;
  transcript: string;
  transcript_confirmed: boolean;
  uploader_id: string;
  jobs?: Job[];
}
export interface Settings {
  faces_enabled: boolean;
  speech_enabled: boolean;
  embedding_enabled: boolean;
  vlm_enabled: boolean;
  ocr_enabled: boolean;
  background_start_hour: number;
  background_end_hour: number;
  min_free_memory_mb: number;
  min_free_disk_mb: number;
  jobs_paused: boolean;
  daily_photo_limit: number;
  display_interval_seconds: number;
  quiet_start: string;
  quiet_end: string;
  autoplay: boolean;
  idle_seconds: number;
  default_visibility: string;
  quality_floor: number;
  recent_days: number;
  event_gap_hours: number;
  travel_gap_hours: number;
  face_similarity: number;
  daily_generation_hour: number;
  config_version: number;
  scenes: Record<string, boolean>;
  weights: Record<string, number>;
}
export interface Household {
  id: string;
  name: string;
  timezone: string;
  settings_json: Settings;
  privacy_revision: number;
}
export interface Auth {
  capabilities?: { max_upload_mb: number };
  user: { id: string; display_name: string };
  role: "owner" | "member" | "viewer";
  csrf: string;
  household: Household;
}
export type AccountAuth =
  | Auth
  | (Omit<Auth, "role" | "household"> & {
      role: "account";
      household: null;
      needs_household: true;
    });
export interface List<T> {
  items: T[];
  total: number;
  page?: number;
}
export interface AlbumSummary {
  pinned?: boolean;
  kind: "on_this_day" | "growth" | "travel" | "recent";
  title: string;
  description: string;
  total: number;
  date_basis: string;
  mode: "automatic" | "manual";
  match_window_days: number | null;
  pending_count: number;
  failed_count: number;
  cover?: Photo | null;
}
export interface AlbumPhoto extends Photo {
  album_reason: string;
  album_group: string;
  album_group_title: string;
  album_event_id: string | null;
}
export type MemoryAlbum = AlbumSummary &
  List<AlbumPhoto> & {
    private_photo_count: number;
    scope: "visible" | "family";
    highlights?: AlbumPhoto[];
    groups?: {
      id: string;
      title: string;
      total: number;
      dates?: Record<string, number>;
      milestones?: { id: string; date: string; title: string; note: string }[];
    }[];
    people?: { id: string; name: string | null }[];
    pinned?: boolean;
    explanation?: string | string[] | { messages?: string[] };
  };
export interface PlaylistItem {
  id: string;
  photo_id: string;
  story_id: string | null;
  position: number;
  score: number;
  reason_codes_json: string[];
  scene_type: string;
  play_count: number;
  photo: Photo;
}
export interface Playlist {
  id?: string;
  items: PlaylistItem[];
  playlist_date: string;
  privacy_revision: number;
  quiet?: boolean;
  autoplay?: boolean;
  interval_seconds?: number;
  server_time?: string;
}
export const sceneNames: Record<string, string> = {
  daily: "日常精选",
  on_this_day: "往年今日",
  travel: "家庭旅行",
  birthday: "生日回忆",
  festival: "节日记忆",
  growth: "孩子成长",
  new_share: "家人新分享",
};
export const eventNames: Record<string, string> = {
  travel: "旅行",
  birthday: "生日",
  festival: "节日",
  family_gathering: "家庭聚会",
  outing: "出游",
  daily: "日常",
  milestone: "里程碑",
  other: "其他",
};
export const jobNames: Record<string, string> = {
  metadata: "元数据",
  quality: "质量评价",
  embedding: "图像向量",
  vlm: "视觉理解",
  faces: "人脸候选",
  postprocess: "索引整理",
  events: "事件整理",
  recommendation: "推荐更新",
  text_embedding: "语义查询",
  asr: "本地转写",
  export: "视频导出",
  data_export: "数据导出",
};
export let csrf = "";
export function setCsrf(value: string) {
  csrf = value;
}
export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const form = body instanceof FormData;
  const response = await fetch("/api/v1" + path, {
    method,
    credentials: "same-origin",
    headers: {
      ...(form ? {} : { "Content-Type": "application/json" }),
      "X-CSRF-Token": csrf,
      "X-Display-Request": "1",
    },
    body: body === undefined ? undefined : form ? body : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ message: "服务暂不可用" }));
    throw new ApiError(response.status, error.message || "请求失败");
  }
  return response.json();
}
export function uploadPhoto(
  file: File,
  shared: boolean,
  onProgress: (p: number) => void,
): Promise<{ photo_id: string; status: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/v1/photos/upload");
    xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable)
        onProgress(Math.round((e.loaded / e.total) * 100));
    };
    xhr.onerror = () => reject(new Error("网络中断，请重试"));
    xhr.onload = () => {
      let body;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        reject(new Error("服务返回异常"));
        return;
      }
      if (xhr.status < 300) resolve(body);
      else reject(new Error(body.message || "上传失败"));
    };
    const form = new FormData();
    form.append("file", file);
    form.append("shared", String(shared));
    xhr.send(form);
  });
}
export function captureLabel(
  photo: Pick<Photo, "taken_at" | "capture_time_source" | "household_timezone">,
) {
  if (!photo.taken_at) return "拍摄时间未知";
  const label = new Date(photo.taken_at).toLocaleDateString("zh-CN", {
    timeZone: photo.household_timezone,
  });
  return (
    label +
    (photo.capture_time_source === "exif_assumed_timezone"
      ? " · 时区按家庭设置推定"
      : "")
  );
}

export async function apiAll<T>(path: string): Promise<T[]> {
  const items: T[] = [];
  for (let page = 1; ; page++) {
    const result = await api<List<T>>(
      `${path}${path.includes("?") ? "&" : "?"}page=${page}&page_size=100`,
    );
    items.push(...result.items);
    if (!result.items.length || items.length >= result.total) return items;
  }
}
