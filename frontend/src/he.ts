// Hebrew labels and Asia/Jerusalem formatting. Server values are UTC instants.
import type { Level, ShiftKind } from "./api";

export const WEEKDAYS = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"];
export const SHIFT: Record<ShiftKind, string> = { morning: "בוקר", reinforcement: "תגבור", evening: "ערב" };
export const LEVEL: Record<Level, string> = { trainee: "חניך", youth: "נוער", mentor: "חונך" };
export const DAY_KIND: Record<string, string> = {
  regular: "יום רגיל",
  friday: "שישי",
  saturday: "שבת",
  holiday: "חג",
  holiday_eve: "ערב חג",
};
export const WINDOW: Record<string, string> = {
  not_open: "ההרשמה טרם נפתחה",
  open: "ההרשמה פתוחה",
  closed_late_allowed: "ההרשמה נסגרה",
  ended: "התאריך עבר",
};

const TZ = "Asia/Jerusalem";

export function fmtTime(iso: string): string {
  return new Intl.DateTimeFormat("he-IL", { timeZone: TZ, hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
}

export function fmtDateTime(iso: string): string {
  return new Intl.DateTimeFormat("he-IL", {
    timeZone: TZ,
    weekday: "short",
    day: "numeric",
    month: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(iso));
}

export function fmtDate(date: string): string {
  // date is a local calendar date (YYYY-MM-DD); format without timezone shifts
  const [y, m, d] = date.split("-").map(Number);
  return `${d}.${m}.${String(y).slice(2)}`;
}

/** Today's calendar date in Asia/Jerusalem (YYYY-MM-DD), not UTC. */
export function localToday(): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date());
}

/** Schedule list label: DD/MM only (the weekday is shown next to it). */
export function fmtDM(date: string): string {
  const [, m, d] = date.split("-").map(Number);
  return `${String(d).padStart(2, "0")}/${String(m).padStart(2, "0")}`;
}

export function dayTitle(d: { date: string; weekday: number }): string {
  return `יום ${WEEKDAYS[d.weekday]} ${fmtDate(d.date)}`;
}

export function phoneStatus(s: string): string {
  return (
    {
      queued: "ממתין למשלוח",
      accepted: "נמסר לשירות ההתראות",
      failed: "המשלוח נכשל",
      simulated: "מוצג באפליקציה בלבד — התראות לטלפון עוד לא חוברו",
    }[s] ?? s
  );
}
