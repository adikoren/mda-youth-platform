// Thin fetch wrapper. All authorization happens on the server; the client only renders what
// it receives. Mutations send the CSRF double-submit header.

export class ApiError extends Error {
  status: number;
  code: string;
  extra: Record<string, unknown>;
  constructor(status: number, code: string, message: string, extra: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.extra = extra;
  }
}

/** No answer from our server: no connection, or the proxy/gateway could not reach it. */
export function isUnreachable(e: unknown): boolean {
  return e instanceof ApiError && (e.status === 0 || e.status === 502 || e.status === 503 || e.status === 504);
}

function csrfToken(): string {
  const m = document.cookie.match(/(?:^|; )mda_csrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

let csrfReady: Promise<unknown> | null = null;
function ensureCsrf() {
  if (!csrfToken()) csrfReady = csrfReady ?? fetch("/api/auth/csrf", { credentials: "same-origin" });
  return csrfReady ?? Promise.resolve();
}

async function request<T>(method: string, url: string, body?: unknown, background = false): Promise<T> {
  if (method !== "GET") await ensureCsrf();
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-CSRF-Token"] = csrfToken();
  if (background) headers["X-MDA-Background"] = "1"; // polling must not extend the session
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      headers,
      credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", "אין חיבור לשרת. בדקו את החיבור ונסו שוב");
  }
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const { code, message, ...extra } = data as { code?: string; message?: string };
    throw new ApiError(res.status, code ?? "error", message ?? "אירעה שגיאה", extra);
  }
  return data as T;
}

export const api = {
  get: <T,>(url: string, background = false) => request<T>("GET", url, undefined, background),
  post: <T,>(url: string, body?: unknown) => request<T>("POST", url, body ?? {}),
  put: <T,>(url: string, body: unknown) => request<T>("PUT", url, body),
  patch: <T,>(url: string, body: unknown) => request<T>("PATCH", url, body),
  del: <T,>(url: string) => request<T>("DELETE", url),
};

// ---- shared types (subset of API responses) ----
export interface Me {
  id: string;
  email: string;
  full_name: string;
  phone: string | null;
  status: "pending" | "active" | "inactive";
  level: Level | null;
  home_weekday: number | null;
  home_weekday_name: string | null;
  is_admin: boolean;
  is_youth_manager: boolean;
  managed_weekdays: number[];
  /** Client-only: restored from this device because the server is unreachable. Grants nothing. */
  offline?: boolean;
}
export type Level = "trainee" | "youth" | "mentor";
export type ShiftKind = "morning" | "reinforcement" | "evening";

export interface DaySummary {
  id: string;
  date: string;
  weekday: number;
  weekday_name: string;
  kind: string;
  registration_opens_at: string;
  registration_closes_at: string;
  publish_target_at: string;
  window: "not_open" | "open" | "closed_late_allowed" | "ended";
  is_published: boolean;
  row_version: number;
  draft_dirty?: boolean;
  attention_count?: number;
}
