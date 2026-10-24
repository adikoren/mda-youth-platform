// Shared lifecycle status for a registration day and its shifts. The server decides every right (direct, request,
// published, state, reason, schedule_visible); this module only turns them into the label a volunteer sees.
// Used by the home list, the day rows and the shift cards, so the three never disagree.

export type LifecycleTone = "todo" | "done" | "no" | "wait" | "late" | "plain";
export interface Lifecycle { tone: LifecycleTone; label: string }

export interface LifecycleShift {
  kind: string;
  state: "before" | "started" | "ended";
  direct: boolean;   // registration is possible now (window open, unpublished, not started)
  request: boolean;  // a written late request is possible now
  available: boolean | null;
  cancelled_at: string | null;
}

export interface LifecycleDay {
  window: string;          // not_open | open | closed_late_allowed | ended
  published: boolean;
  shifts: LifecycleShift[];
}

// One shift. Order matters: an explicit cancellation or an answer is shown as is; otherwise the schedule and the
// shift's own timing decide. "עדיין לא נרשמת" appears only while registration is open, unpublished and not started.
export function shiftLifecycle(s: LifecycleShift, day: Pick<LifecycleDay, "window" | "published">): Lifecycle {
  if (s.cancelled_at) return { tone: "no", label: "בוטל" };
  if (s.available === true) return { tone: "done", label: "נרשמת" };
  if (s.available === false) return { tone: "no", label: "לא מגיע/ה" };
  if (s.state === "started") return { tone: "wait", label: "המשמרת כבר התחילה" };
  if (s.state === "ended") return { tone: "wait", label: "המשמרת הסתיימה" };
  if (day.published) return { tone: "wait", label: "הסידור פורסם" };
  if (s.direct) return { tone: "plain", label: "עדיין לא נרשמת" };
  if (day.window === "not_open") return { tone: "wait", label: "טרם נפתחה" };
  return { tone: "late", label: "ההרשמה נסגרה" };
}

// The whole date (home list row). Published wins over "not registered" so no registration prompt survives publication.
export function dayLifecycle(day: LifecycleDay, shiftNames: Record<string, string>, invited = false): Lifecycle {
  const live = day.shifts.filter((s) => !s.cancelled_at);
  const yes = live.filter((s) => s.available === true).map((s) => shiftNames[s.kind]);
  if (yes.length && day.published) return { tone: "done", label: `נרשמת · ${yes.join(" + ")}` };
  if (day.published) return { tone: "wait", label: "הסידור פורסם" };
  if (yes.length) return { tone: "done", label: `נרשמת · ${yes.join(" + ")}` };
  if (live.some((s) => s.available === false)) return { tone: "no", label: "סימנת: לא מגיע/ה" };
  if (day.shifts.some((s) => s.cancelled_at)) return { tone: "no", label: "בוטל" };
  if (day.window === "not_open") return { tone: "wait", label: "טרם נפתחה" };
  if (day.shifts.length && day.shifts.every((s) => s.state === "ended")) return { tone: "wait", label: "המשמרת הסתיימה" };
  if (day.shifts.length && day.shifts.every((s) => s.state !== "before")) return { tone: "wait", label: "המשמרת כבר התחילה" };
  if (invited && day.shifts.some((s) => s.direct)) return { tone: "todo", label: "הוזמנת להירשם" };
  if (day.shifts.some((s) => s.direct)) return { tone: "plain", label: "עדיין לא נרשמת" };
  return { tone: "late", label: "ההרשמה נסגרה" };
}

// Countdown to the registration closing instant, for a volunteer who may still register. Times are compared on the
// server's clock (see the caller). Days are rounded up, so 24 hours or more never shows zero days.
export const HOUR_MS = 60 * 60 * 1000;
export const DAY_MS = 24 * HOUR_MS;

export interface CountdownParts {
  text: string;     // Hebrew text before the clock (or the whole sentence when there is no clock)
  clock?: string;   // "HH:MM", rendered as isolated LTR text
  unit?: string;    // "שעות" after the clock
}

export function countdownParts(remainingMs: number): CountdownParts {
  const ms = Math.max(0, remainingMs);
  if (ms >= DAY_MS) {
    const days = Math.ceil(ms / DAY_MS);
    return { text: days === 1 ? "ההרשמה נסגרת בעוד יום אחד" : `ההרשמה נסגרת בעוד ${days} ימים` };
  }
  const pad = (n: number) => String(n).padStart(2, "0");
  const hours = Math.floor(ms / HOUR_MS);
  const minutes = Math.floor((ms % HOUR_MS) / 60000);
  return { text: "ההרשמה נסגרת בעוד", clock: `${pad(hours)}:${pad(minutes)}`, unit: "שעות" };
}
