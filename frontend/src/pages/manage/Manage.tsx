import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type DaySummary, type Level, type ShiftKind } from "../../api";
import { DAY_KIND, SHIFT, WINDOW, dayTitle, fmtDateTime, fmtTime } from "../../he";
import { LevelBadge, Loading, Shell, errorText, useLoad } from "../../ui";

export function ManageDays() {
  const days = useLoad(() => api.get<DaySummary[]>("/api/manage/days"));
  return (
    <Shell title="ניהול סידורים">
      <Link className="btn" to="/manage/trainees">חניכים — יומנים והתקדמות</Link>
      {!days.data && <Loading error={days.error} />}
      {days.data?.length === 0 && <div className="card muted">אין ימים בניהולך בטווח הקרוב.</div>}
      {days.data?.map((d) => (
        <Link key={d.id} to={`/manage/${d.id}`} className="card stack" style={{ textDecoration: "none", color: "inherit" }}>
          <div className="row spread">
            <strong>{dayTitle(d)}</strong>
            <span className="row">
              {d.is_published ? <span className="badge youth">פורסם</span> : <span className="badge">טרם פורסם</span>}
              {d.draft_dirty && <span className="badge late">שינויים לא פורסמו</span>}
              {!!d.attention_count && <span className="badge red">נדרש טיפול: {d.attention_count}</span>}
            </span>
          </div>
          <div className="muted">{DAY_KIND[d.kind]} · {WINDOW[d.window]} · יעד פרסום {fmtDateTime(d.publish_target_at)}</div>
        </Link>
      ))}
    </Shell>
  );
}

interface Person {
  user_id: string;
  full_name: string;
  level: Level | null;
  is_guest: boolean;
  source: string;
  via_evening: boolean;
  arrival_time: string | null;
  is_late: boolean;
  attention_id: string | null;
  confirmation: string | null;
  assigned_other_shifts: ShiftKind[];
  rank?: number | null;
  progress?: { ride_count: number; last_ride_date: string | null } | null;
}
interface Slot { slot_key: string; ordinal: number; capacity: number; status: "active" | "cancelled"; crew: Person[]; warning: string | null;
  capacity_over_limit?: boolean }
// Upper bound for the capacity setting (the backend enforces it too). More crew than capacity is still allowed.
const MAX_CAPACITY = 3;
interface BoardShift { id: string; kind: ShiftKind; starts_at: string; ends_at: string; arrival_at: string; slots: Slot[]; waiting: Person[];
  self_selectable: boolean; registration_toggle: boolean }
interface Board {
  day: DaySummary;
  draft: { id: string; revision: number; edit_count: number; dirty: boolean; last_edit_at: string | null } | null;
  published: { id: string; revision: number; published_at: string } | null;
  shifts: BoardShift[];
  attentions: { id: string; shift_id: string; user_id: string; full_name: string; label: string }[];
}
interface SearchUser { id: string; full_name: string; level: Level | null; home_weekday: number | null }

export function BoardPage() {
  const { dayId } = useParams();
  const [board, setBoard] = useState<Board | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      // Opening the board ensures a working draft exists (a copy of the live publication).
      setBoard(await api.post<Board>(`/api/manage/days/${dayId}/draft`));
    } catch (e) {
      setError(errorText(e));
    }
  }, [dayId]);
  useEffect(() => {
    load();
  }, [load]);

  const dirty = !!board?.draft?.dirty;
  useEffect(() => {
    // Best-effort navigation warning; the server also reminds after 10 idle minutes.
    const handler = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  async function act(fn: () => Promise<unknown>, okText = "נשמר בטיוטה") {
    setError(null);
    try {
      await fn();
      setSaved(okText);
      setBoard(await api.get<Board>(`/api/manage/days/${dayId}/board`));
    } catch (e) {
      setError(errorText(e));
      setBoard(await api.get<Board>(`/api/manage/days/${dayId}/board`).catch(() => board));
    }
  }

  async function publish() {
    if (!board?.draft) return;
    const msg = board.published ? "לפרסם את השינויים? המתנדבים יקבלו הודעה." : "לפרסם את הסידור? המתנדבים יראו אותו ויקבלו הודעה.";
    if (!window.confirm(msg)) return;
    await act(
      () => api.post(`/api/manage/days/${dayId}/publish`, { draft_id: board.draft!.id, expected_edit_count: board.draft!.edit_count }),
      "פורסם",
    );
    await load();
  }

  if (!board) return <Shell title="בניית סידור" back="/manage"><Loading error={error} /></Shell>;
  const allSlots = (sh: BoardShift) => sh.slots.filter((s) => s.status === "active");

  return (
    <Shell title={dayTitle(board.day)} back="/manage">
      <div className="card stack">
        <div className="row spread">
          <strong>{DAY_KIND[board.day.kind]}</strong>
          <span className="muted">{WINDOW[board.day.window]}</span>
        </div>
        <div className="muted">
          {board.published ? `פורסם ${fmtDateTime(board.published.published_at)} (גרסה ${board.published.revision})` : "טרם פורסם"}
          {" · "}יעד פרסום {fmtDateTime(board.day.publish_target_at)}
        </div>
        <div className="notice">
          טיוטה פרטית — שינויים נשמרים אוטומטית ואינם גלויים למתנדבים ואינם משנים הרשאות עד לחיצה על פרסום.
        </div>
        {board.published && <Link to={`/schedule/${board.day.id}`}>לצפייה בסידור המפורסם</Link>}
      </div>

      {board.attentions.map((a) => (
        <div key={a.id} className="card alert row spread">
          <span><strong>{a.full_name}</strong> · {a.label}</span>
          <button className="btn small" onClick={() => act(() => api.post(`/api/manage/attentions/${a.id}/resolve`), "סומן כטופל")}>טופל</button>
        </div>
      ))}

      {board.shifts.map((sh) => (
        <div key={sh.id} className="card stack">
          <div className="row spread">
            <h3>{SHIFT[sh.kind]}</h3>
            <span className="muted">{fmtTime(sh.starts_at)}–{fmtTime(sh.ends_at)} · הגעה {fmtTime(sh.arrival_at)}</span>
          </div>
          {sh.registration_toggle && (
            // Sunday–Friday morning: hidden from volunteer registration until opened here.
            <div className="row spread">
              <span className={sh.self_selectable ? "badge youth" : "badge"}>
                {sh.self_selectable ? "פתוחה להרשמת מתנדבים" : "לא מוצגת להרשמה (שיבוץ ידני בלבד)"}
              </span>
              <button className="btn small" onClick={() => act(
                () => api.post(`/api/manage/shifts/${sh.id}/registration`, { open: !sh.self_selectable }),
                sh.self_selectable ? "הבוקר הוסתר מההרשמה. תשובות קיימות נשמרו" : "הבוקר נפתח להרשמה")}>
                {sh.self_selectable ? "הסתרה מההרשמה" : "פתיחה להרשמה"}
              </button>
            </div>
          )}

          {sh.slots.map((sl) => (
            <div key={sl.slot_key} className={`slot ${sl.status === "cancelled" ? "cancelled" : ""}`}>
              <div className="row spread">
                <strong>אמבולנס {sl.ordinal}{sl.status === "cancelled" ? " · מבוטל בטיוטה" : ""}</strong>
                <span className="row">
                  {sl.status === "active" ? (
                    <>
                      <label className="muted">
                        מקומות{" "}
                        <select value={sl.capacity}
                                onChange={(e) => act(() => api.patch(`/api/manage/slots/${sl.slot_key}`, { capacity: Number(e.target.value) }))}>
                          {Array.from({ length: MAX_CAPACITY + 1 }, (_, n) => <option key={n} value={n}>{n}</option>)}
                          {sl.capacity > MAX_CAPACITY && <option value={sl.capacity} disabled>{sl.capacity}</option>}
                        </select>
                      </label>
                      {sl.capacity_over_limit && <span className="badge late">מעל {MAX_CAPACITY} — לבדיקה</span>}
                      <button className="btn small ghost" onClick={() => window.confirm("לבטל את האמבולנס בטיוטה? המשובצים יחזרו להמתנה לאחר פרסום.") &&
                        act(() => api.post(`/api/manage/slots/${sl.slot_key}/cancel`))}>ביטול</button>
                    </>
                  ) : (
                    <button className="btn small" onClick={() => act(() => api.post(`/api/manage/slots/${sl.slot_key}/restore`))}>שחזור</button>
                  )}
                </span>
              </div>
              {sl.warning && <div className="notice">{sl.warning}</div>}
              {sl.crew.map((p) => (
                <div key={p.user_id} className="person">
                  <PersonLine p={p} />
                  <select aria-label="העברה" value="" onChange={(e) => e.target.value &&
                    act(() => api.post(`/api/manage/shifts/${sh.id}/move`, { user_id: p.user_id, to_slot_key: e.target.value }))}>
                    <option value="">העבר…</option>
                    {allSlots(sh).filter((o) => o.slot_key !== sl.slot_key).map((o) => (
                      <option key={o.slot_key} value={o.slot_key}>אמבולנס {o.ordinal}</option>
                    ))}
                  </select>
                  <button className="btn small ghost" onClick={() => act(() => api.post(`/api/manage/shifts/${sh.id}/unassign`, { user_id: p.user_id }))}>הסרה</button>
                </div>
              ))}
            </div>
          ))}
          <button className="btn small" onClick={() => act(() => api.post(`/api/manage/shifts/${sh.id}/slots`, { capacity: 3 }))}>+ אמבולנס</button>

          <div className="stack">
            <strong>ממתינים ({sh.waiting.length})</strong>
            {sh.waiting.length === 0 && <span className="muted">אין ממתינים</span>}
            {sh.waiting.map((p, i) => (
              <div key={p.user_id} className="person">
                <span className="muted">{i + 1}.</span>
                <PersonLine p={p} />
                <button className="btn small ghost" aria-label="הזזה למעלה" disabled={i === 0}
                        onClick={() => act(() => api.put(`/api/manage/shifts/${sh.id}/order`, { user_ids: reorder(sh.waiting, i, i - 1) }), "הסדר נשמר")}>▲</button>
                <button className="btn small ghost" aria-label="הזזה למטה" disabled={i === sh.waiting.length - 1}
                        onClick={() => act(() => api.put(`/api/manage/shifts/${sh.id}/order`, { user_ids: reorder(sh.waiting, i, i + 1) }), "הסדר נשמר")}>▼</button>
                <select aria-label="שיבוץ" value="" onChange={(e) => e.target.value &&
                  act(() => api.post(`/api/manage/slots/${e.target.value}/assign`, { user_id: p.user_id }))}>
                  <option value="">שבץ…</option>
                  {allSlots(sh).map((o) => <option key={o.slot_key} value={o.slot_key}>אמבולנס {o.ordinal}</option>)}
                </select>
                {p.source === "manager" && (
                  <button className="btn small ghost" onClick={() => act(() => api.del(`/api/manage/shifts/${sh.id}/participants/${p.user_id}`))}>הוצאה</button>
                )}
              </div>
            ))}
            <PersonPicker label="הוספת משתתף למשמרת" onPick={(u) => act(() => api.post(`/api/manage/shifts/${sh.id}/participants`, { user_id: u.id }))} />
          </div>
        </div>
      ))}

      <div className="card stack">
        <strong>הזמנה אישית ליום זה</strong>
        <div className="muted">למתנדב מיום אחר. הוא יראה כרטיס הזמנה ויוכל לסמן זמינות.</div>
        <PersonPicker label="חיפוש מתנדב" onPick={(u) => act(() => api.post(`/api/manage/days/${dayId}/invitations`, { user_id: u.id }), "ההזמנה נשלחה")} />
      </div>

      <div className="sticky-actions">
        {error && <div className="error">{error}</div>}
        {saved && !error && <div className="ok">{saved}</div>}
        <button className="btn primary block" disabled={!board.draft || (!!board.published && !dirty)} onClick={publish}>
          {board.published ? (dirty ? "פרסום שינויים" : "אין שינויים לפרסום") : "פרסום הסידור"}
        </button>
      </div>
    </Shell>
  );
}

function reorder(list: Person[], from: number, to: number): string[] {
  const ids = list.map((p) => p.user_id);
  const [x] = ids.splice(from, 1);
  ids.splice(to, 0, x);
  return ids;
}

function PersonLine({ p }: { p: Person }) {
  return (
    <>
      <span className="name">{p.full_name}</span>
      <LevelBadge level={p.level} />
      {p.is_guest && <span className="badge guest">אורח/ת{p.confirmation === "pending" ? " · ממתין לאישור" : p.confirmation === "confirmed" ? " · אישר" : ""}</span>}
      {p.source === "manager" && <span className="badge">נוסף ע״י אחראי</span>}
      {p.via_evening && <span className="badge">מהרשמת ערב</span>}
      {p.arrival_time && <span className="badge">מגיע {p.arrival_time}</span>}
      {p.is_late && <span className="badge late">אחרי סגירה</span>}
      {p.assigned_other_shifts.length > 0 && <span className="badge">גם ב{p.assigned_other_shifts.map((k) => SHIFT[k]).join(", ")}</span>}
      {p.attention_id && <span className="badge red">ביטל זמינות — נדרש טיפול</span>}
      {p.progress && <span className="badge trainee">{p.progress.ride_count} נסיעות · {p.progress.last_ride_date?.split("-").reverse().join(".") ?? "—"}</span>}
    </>
  );
}

export function PersonPicker({ label, onPick }: { label: string; onPick: (u: SearchUser) => void }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<SearchUser[]>([]);
  useEffect(() => {
    if (q.trim().length < 2) {
      setResults([]);
      return;
    }
    const t = setTimeout(() => {
      api.get<SearchUser[]>(`/api/users/search?q=${encodeURIComponent(q)}`).then(setResults).catch(() => setResults([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q]);
  return (
    <div className="stack">
      <input className="field" style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 10 }}
             placeholder={label} value={q} onChange={(e) => setQ(e.target.value)} />
      {results.map((u) => (
        <button key={u.id} className="btn small" style={{ textAlign: "start" }} onClick={() => { onPick(u); setQ(""); }}>
          {u.full_name} {u.level ? `· ${({ trainee: "חניך", youth: "נוער", mentor: "חונך" } as const)[u.level]}` : ""}
        </button>
      ))}
    </div>
  );
}
