import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, type DaySummary, type ShiftKind } from "../api";
import { useAuth } from "../auth";
import { DAY_KIND, SHIFT, WINDOW, dayTitle, fmtDateTime, fmtTime, phoneStatus } from "../he";
import { LevelBadge, Loading, Shell, TimeRange, WhatsAppLink, errorText, useLoad } from "../ui";
import { newRideId } from "../offline";
import { countdownParts, dayLifecycle, shiftLifecycle, type Lifecycle } from "../lifecycle";
import { NotificationNotice } from "./Journal";

interface RegShift {
  shift_id: string;
  kind: ShiftKind;
  starts_at: string;
  ends_at: string;
  arrival_at: string;
  allows_late_arrival: boolean;
  available: boolean | null;
  // Explicit "בוטל": set when the volunteer cancelled before the shift started. Not the same as "לא מגיע/ה".
  cancelled_at: string | null;
  arrival_time: string | null;
  is_late: boolean;
  // Server's view of the shift's own cutoff: before its start, started, or ended.
  state: "before" | "started" | "ended";
  // Server's decision for this volunteer right now. The page only shows what the server allows.
  direct: boolean; // may set availability now
  request: boolean; // may send a written late request now
  reason: string | null; // why neither is allowed: schedule_published, shift_started, shift_ended, ...
  reason_text: string | null;
}
interface Reg {
  server_now: string;
  published: boolean;
  schedule_visible: boolean; // the viewer may open the published schedule (same rule as the schedule page)
  requests?: { id: string; status: string; created_at: string }[];
  activity_day_id: string;
  date: string;
  weekday: number;
  kind: string;
  window: string;
  opens_at: string;
  closes_at: string;
  revision: number;
  shifts: RegShift[];
}
interface Invitation { id: string; kind: "invitation" | "direct_add"; activity_day_id: string; date: string; weekday: number; shift: ShiftKind | null }
interface Note { id: string; text: string; created_at: string; read: boolean; phone_status: string; payload: { date?: string; shifts?: { shift: ShiftKind; change: string }[] } }
interface HistoryItem { activity_day_id: string; date: string; weekday: number; shift: ShiftKind; starts_at: string; ends_at: string; ambulance: number }
interface NextAssignment {
  activity_day_id: string; shift_id: string; date: string; weekday: number; kind: ShiftKind;
  starts_at: string; ends_at: string; ambulance: number; state: "before" | "started" | "ended"; cancellable: boolean;
}

// ---- volunteer home ----
// Three kinds of content, kept visually distinct (and never by colour alone):
//   נדרשת פעולה  — red edge + label + one clear action button
//   השיבוצים שלי — published assignments, solid white cards with an ambulance chip
//   הרשמה        — dates with a status chip; outline cards
// Counts down to the registration closing instant on the server's clock (the phone's clock only supplies the
// ticks). At the deadline it asks the server again, so the direct controls disappear without a manual reload.
function ClosingCountdown({ closesAt, serverNow, onExpire }: { closesAt: string; serverNow: string; onExpire: () => void }) {
  const offset = useMemo(() => Date.parse(serverNow) - Date.now(), [serverNow]);
  const closeMs = Date.parse(closesAt);
  const [now, setNow] = useState(() => Date.now() + offset);
  const fired = useRef(false);
  useEffect(() => {
    setNow(Date.now() + offset);
    const t = setInterval(() => setNow(Date.now() + offset), 1000);
    return () => clearInterval(t);
  }, [offset]);
  const remaining = closeMs - now;
  useEffect(() => {
    if (remaining <= 0 && !fired.current) {
      fired.current = true;
      onExpire();
    }
  }, [remaining <= 0]); // eslint-disable-line react-hooks/exhaustive-deps
  if (remaining <= 0) return <span className="status status-late">ההרשמה נסגרה</span>;
  const p = countdownParts(remaining);
  return (
    <span className="countdown" role="timer" aria-live="off">
      {p.clock ? (
        <>
          {p.text} <bdi dir="ltr" className="countdown-clock">{p.clock}</bdi> {p.unit}
        </>
      ) : (
        p.text
      )}
    </span>
  );
}

// Home list status for a date: the shared lifecycle decides, the page only passes the shift names.
function regStatus(r: Reg, invited = false): Lifecycle {
  return dayLifecycle(r, SHIFT, invited);
}



function noteLine(n: Note): string {
  const parts = [n.text];
  if (n.payload.date) parts.push(n.payload.date.split("-").reverse().join("."));
  // Name the shifts with a personal change; waiting-only shifts only when nothing personal changed.
  const all = n.payload.shifts ?? [];
  const personal = all.filter((x) => ["assigned", "unassigned", "changed"].includes(x.change));
  const shown = personal.length ? personal : all;
  if (shown.length) parts.push(shown.map((x) => SHIFT[x.shift]).join(", "));
  return parts.join(" · ");
}

export function Home() {
  const { me } = useAuth();
  const regs = useLoad(() => api.get<Reg[]>("/api/registrations"));
  const invites = useLoad(() => api.get<Invitation[]>("/api/invitations"));
  const next = useLoad(() => api.get<{ assignment: NextAssignment | null }>("/api/me/next-assignment"));
  const [cancelError, setCancelError] = useState<string | null>(null);
  const notes = useLoad(() => api.get<Note[]>("/api/me/notifications?limit=10", true));
  const [allNotes, setAllNotes] = useState(false);
  // "נדרשת פעולה" holds only distinct tasks. Registration itself (invitations to register, dates still
  // to answer) lives in "הרשמה למשמרות", so the same date is never shown twice.
  const directAdds = (invites.data ?? []).filter((i) => i.kind === "direct_add");
  const invitedDays = new Set((invites.data ?? []).filter((i) => i.kind === "invitation").map((i) => i.activity_day_id));
  const actionCount = directAdds.length;

  async function confirm(id: string) {
    await api.post(`/api/invitations/${id}/confirm`);
    invites.reload();
  }

  // The server decides whether the shift has started; a refusal here only means the screen was stale.
  async function cancelShift(shiftId: string) {
    setCancelError(null);
    try {
      await api.post(`/api/shifts/${shiftId}/cancel`, {});
    } catch (e) {
      setCancelError(errorText(e));
    }
    next.reload();
    regs.reload();
  }

  return (
    <Shell title="מתנדבי נוער" brand>
      <section className="hello">
        <div>
          <h2>שלום, {me?.full_name}</h2>
          <span className="muted">{me?.home_weekday_name ? `יום קבוע: ${me.home_weekday_name}` : "ללא יום קבוע"}</span>
        </div>
        <LevelBadge level={me?.level ?? null} />
      </section>
      <NotificationNotice />

      {actionCount > 0 && (
        <section className="section">
          <h3 className="section-title">נדרשת פעולה <span className="count">{actionCount}</span></h3>
          {directAdds.map((inv) => (
            <div key={inv.id} className="action-card">
              <span className="tag tag-action">אישור שיבוץ</span>
              <strong>נוספת למשמרת {inv.shift ? SHIFT[inv.shift] : ""} ב{dayTitle(inv)}</strong>
              <span className="muted">האחראי הוסיף אותך. נשמח לאישור שראית.</span>
              <button className="btn primary block" onClick={() => confirm(inv.id)}>ראיתי, מאשר/ת</button>
            </div>
          ))}
        </section>
      )}

      <section className="section">
        <h3 className="section-title">השיבוץ הבא שלי</h3>
        {next.data && !next.data.assignment && (
          <div className="empty">מתי המשמרת הבאה שלך? מתגעגעים אליך בתחנה ❤️</div>
        )}
        {next.data?.assignment && (
          <div className="assign-card">
            <span className="amb" aria-label={`אמבולנס ${next.data.assignment.ambulance}`}>
              <small>אמבולנס</small>{next.data.assignment.ambulance}
            </span>
            <span className="assign-body">
              <span className="tag tag-published">{next.data.assignment.state === "started" ? "המשמרת מתבצעת" : "שובצת · פורסם"}</span>
              <strong>{dayTitle(next.data.assignment)}</strong>
              <span className="muted">משמרת {SHIFT[next.data.assignment.kind]} · <TimeRange from={next.data.assignment.starts_at} to={next.data.assignment.ends_at} /></span>
              <Link className="btn small" to={`/schedule/${next.data.assignment.activity_day_id}`}>לצפייה בסידור</Link>
              {next.data.assignment.cancellable && (
                <button className="btn small ghost" onClick={() => cancelShift(next.data!.assignment!.shift_id)}>ביטול שיבוץ</button>
              )}
            </span>
          </div>
        )}
        {cancelError && <div className="error">{cancelError}</div>}
      </section>

      <section className="section">
        <h3 className="section-title">הרשמה למשמרות</h3>
        {!regs.data && <Loading error={regs.error} />}
        {regs.data?.length === 0 && <div className="empty">אין כרגע תאריכים להרשמה.</div>}
        <div className="reg-list">
          {/* Registration not yet open: hidden from volunteers. Closed entries stay accessible (read-only, request only). */}
          {regs.data?.filter((r) => r.window !== "not_open").map((r) => {
            const st = regStatus(r, invitedDays.has(r.activity_day_id));
            const label = dayTitle(r);
            return (
              <div key={r.activity_day_id} className="reg-row">
                <span className="reg-date">
                  <strong>{label}</strong>
                  <span className="muted">{DAY_KIND[r.kind]}</span>
                </span>
                <span className="reg-end">
                  {st.tone === "plain" ? (
                    <ClosingCountdown closesAt={r.closes_at} serverNow={r.server_now} onExpire={() => regs.reload()} />
                  ) : (
                    <span className={`status status-${st.tone}`}>{st.label}</span>
                  )}
                  {r.published ? (
                    r.schedule_visible && (
                      <Link className="btn small" to={`/schedule/${r.activity_day_id}`} aria-label={`לצפייה בסידור ${label}`}>לצפייה בסידור</Link>
                    )
                  ) : r.shifts.some((s) => s.direct) ? (
                    <Link className="btn primary small" to={`/register/${r.activity_day_id}`} aria-label={`להרשמה למשמרת ${label}`}>
                      להרשמה למשמרת
                    </Link>
                  ) : (
                    <Link className="btn small" to={`/register/${r.activity_day_id}`} aria-label={`לפרטים ${label}`}>לפרטים</Link>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      </section>

      <section className="section">
        <h3 className="section-title">הודעות</h3>
        {(notes.data ?? []).length === 0 && <div className="empty">אין הודעות.</div>}
        <div className="note-list">
          {(allNotes ? notes.data : notes.data?.slice(0, 3))?.map((n) => (
            <div key={n.id} className={n.read ? "note" : "note unread"}>
              <strong>{noteLine(n)}</strong>
              <span className="muted">{fmtDateTime(n.created_at)} · {phoneStatus(n.phone_status)}</span>
            </div>
          ))}
        </div>
        {(notes.data?.length ?? 0) > 3 && (
          <button className="btn ghost small" onClick={() => setAllNotes(!allNotes)}>{allNotes ? "פחות" : "כל ההודעות"}</button>
        )}
      </section>

      <nav className="quick" aria-label="קיצורים">
        <Link to="/history">ההיסטוריה שלי</Link>
        {me?.level === "trainee" && <Link to="/journal">יומן נסיעות</Link>}
        {me?.level === "trainee" && <Link to="/notes">סיכומים אישיים</Link>}
      </nav>
    </Shell>
  );
}

const LATE_TIMES = (() => {
  const out: string[] = [];
  for (let h = 15; h <= 22; h++) for (const m of [0, 15, 30, 45]) out.push(`${String(h).padStart(2, "0")}:${m === 0 ? "00" : m}`);
  return out;
})();

const WINDOW_TONE: Record<string, string> = { open: "done", closed_late_allowed: "late", not_open: "wait", ended: "wait" };

export function RegisterDay() {
  const { dayId } = useParams();
  const reg = useLoad(() => api.get<Reg>(`/api/registrations/${dayId}`), [dayId]);
  const [saved, setSaved] = useState<{ shift: string; late: boolean } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [lateOpen, setLateOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  // Written request after closing. Nothing is sent until the explicit submit button is pressed.
  const [reqOpen, setReqOpen] = useState(false);
  const [reqText, setReqText] = useState("");
  const [reqId, setReqId] = useState<string | null>(null); // same id on retry, so a retry never duplicates
  const [reqSending, setReqSending] = useState(false);
  const [reqErr, setReqErr] = useState<string | null>(null);
  const [reqShift, setReqShift] = useState<string>("");
  const [reqDuplicate, setReqDuplicate] = useState(false); // an identical request is still pending

  // Volunteer cancellation before the shift starts; no approval. The server refuses at or after the start, so a
  // screen opened before the start cannot cancel late: the refusal is shown and the page reloads.
  async function cancelMine(s: RegShift) {
    setBusy(s.shift_id);
    setErr(null);
    try {
      await api.post(`/api/shifts/${s.shift_id}/cancel`, {});
      setSaved(null);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(null);
      reg.reload();
    }
  }

  function openRequest() {
    setReqDuplicate(false);
    setReqId(newRideId());
    setReqErr(null);
    setReqOpen(true);
  }

  async function sendRequest(confirmDuplicate = false) {
    if (!reqId) return;
    setReqSending(true);
    setReqErr(null);
    try {
      await api.post(`/api/registrations/${dayId}/requests`,
                     { id: reqId, message: reqText, shift_id: reqShift, confirm_duplicate: confirmDuplicate });
      setReqOpen(false);
      setReqText("");
      setReqId(null);
      setReqDuplicate(false);
      reg.reload();
    } catch (e) {
      if (e instanceof ApiError && e.code === "duplicate_request") {
        setReqDuplicate(true);
      } else if (e instanceof ApiError && (e.code === "shift_started" || e.code === "shift_ended")) {
        // The shift started while the form was open: the server refused, so refresh and close the form.
        setReqOpen(false);
        setReqText("");
        setReqId(null);
        setReqDuplicate(false);
        setErr(e.code === "shift_started" ? "המשמרת כבר התחילה. לא ניתן לשלוח בקשה להרשמה." : "המשמרת הסתיימה. לא ניתן לשלוח בקשה להרשמה.");
        reg.reload();
      } else {
        setReqErr(errorText(e));
      }
    } finally {
      setReqSending(false);
    }
  }

  useEffect(() => {
    // At the closing instant (server clock), ask the server again so direct controls go away without a reload.
    const d = reg.data;
    if (!d || d.window !== "open") return;
    const wait = Date.parse(d.closes_at) - (Date.now() + (Date.parse(d.server_now) - Date.now()));
    const t = setTimeout(() => reg.reload(), Math.max(0, wait) + 500);
    return () => clearTimeout(t);
  }, [reg.data]);

  useEffect(() => {
    const ev = reg.data?.shifts.find((s) => s.allows_late_arrival);
    if (ev?.arrival_time) setLateOpen(true);
  }, [reg.data]);

  async function save(shift: RegShift, available: boolean, arrival: string | null = null) {
    setErr(null);
    setBusy(shift.shift_id);
    try {
      const res = await api.put<{ late: boolean; changed: boolean; registration: Reg }>(`/api/registrations/${dayId}`, {
        choices: [{ shift_id: shift.shift_id, available, arrival_time: available ? arrival : null }],
        expected_revision: reg.data?.revision,
      });
      reg.setData(res.registration);
      setSaved({ shift: shift.shift_id, late: res.late });
    } catch (e) {
      setErr(errorText(e));
      reg.reload();
    } finally {
      setBusy(null);
    }
  }

  if (!reg.data) return <Shell title="הרשמה" back="/"><Loading error={reg.error} /></Shell>;
  const r = reg.data;
  // Same rules for everyone, Admin included: the server decides per shift (publication, start, window).
  const canAnswer = (s: RegShift) => s.direct;
  const requestShifts = r.shifts.filter((s) => s.request);
  return (
    <Shell title="הרשמה למשמרת" back="/">
      <section className="day-head">
        <span className="day-kind">{DAY_KIND[r.kind]}</span>
        <h2>{dayTitle(r)}</h2>
        {r.published ? (
          <span className="status status-wait">הסידור פורסם</span>
        ) : (
          <>
            <span className={`status status-${WINDOW_TONE[r.window]}`}>{WINDOW[r.window]}</span>
            <span className="muted">
              {r.window === "not_open" ? `נפתחת ${fmtDateTime(r.opens_at)}` : `נסגרת ${fmtDateTime(r.closes_at)}`}
            </span>
          </>
        )}
      </section>
      {r.published && (
        <section className="notice stack" style={{ gap: 6 }}>
          <strong>הסידור פורסם</strong>
          {r.schedule_visible && <Link className="btn small" to={`/schedule/${r.activity_day_id}`}>לצפייה בסידור</Link>}
        </section>
      )}
      {!r.published && r.window === "closed_late_allowed" && requestShifts.length > 0 && (
        <div className="notice">ההרשמה נסגרה. לבקשה להרשמה באיחור, השתמשו בכפתור למטה.</div>
      )}
      {!r.published && r.window === "closed_late_allowed" && requestShifts.length === 0 && (
        <div className="notice">ההרשמה נסגרה.</div>
      )}
      {r.window === "not_open" && <div className="notice">ההרשמה טרם נפתחה.</div>}
      {err && <div className="error" role="alert">{err}</div>}
      {requestShifts.length > 0 && (
        <section className="request-box stack">
          {(r.requests?.length ?? 0) > 0 && (
            <div className="stack" style={{ gap: 2 }}>
              <div className="saved" role="status">הבקשה נשלחה לאחראי · {fmtDateTime(r.requests![r.requests!.length - 1].created_at)}</div>
              <span className="muted">ההודעה מוצגת לאחראי באפליקציה בלבד.</span>
            </div>
          )}
          {!reqOpen ? (
            <button className="btn" onClick={() => { openRequest(); setReqShift(requestShifts[0].shift_id); }}>בקשה להרשמה באיחור</button>
          ) : (
            <div className="stack">
              {requestShifts.length > 1 && (
                <label className="field">
                  לאיזו משמרת?
                  <select value={reqShift} disabled={reqSending} onChange={(e) => setReqShift(e.target.value)}>
                    {requestShifts.map((s) => <option key={s.shift_id} value={s.shift_id}>משמרת {SHIFT[s.kind]}</option>)}
                  </select>
                </label>
              )}
              <label className="field">
                הבקשה לאחראי
                <textarea rows={3} maxLength={1000} value={reqText} disabled={reqSending}
                          onChange={(e) => setReqText(e.target.value)} />
              </label>
              {reqDuplicate ? (
                <div className="notice stack" role="alert">
                  <span>כבר נשלחה בקשה זהה שעדיין לא טופלה. לשלוח שוב?</span>
                  <div className="row">
                    <button className="btn primary" disabled={reqSending} onClick={() => sendRequest(true)}>שליחה בכל זאת</button>
                    <button className="btn ghost" disabled={reqSending} onClick={() => setReqDuplicate(false)}>ביטול</button>
                  </div>
                </div>
              ) : (
                <div className="row">
                  <button className="btn primary" disabled={reqSending || reqText.trim().length < 2 || !reqShift} onClick={() => sendRequest(false)}>
                    {reqErr ? "ניסיון נוסף" : "שליחה לאחראי"}
                  </button>
                  <button className="btn ghost" disabled={reqSending} onClick={() => setReqOpen(false)}>ביטול</button>
                </div>
              )}
              {reqErr && <div className="error" role="alert">השליחה נכשלה: {reqErr}. הטקסט נשמר, אפשר לנסות שוב.</div>}
            </div>
          )}
        </section>
      )}
      {r.shifts.length === 0 && <div className="empty">אין ביום זה משמרות פתוחות להרשמה.</div>}

      {r.window !== "not_open" && r.shifts.map((s) => (
        <section key={s.shift_id} className={`shift-card${!s.cancelled_at && s.available === true ? " is-yes" : !s.cancelled_at && s.available === false ? " is-no" : ""}`}>
          <div className="shift-head">
            <h3>משמרת {SHIFT[s.kind]}</h3>
            <span className="shift-time"><TimeRange from={s.starts_at} to={s.ends_at} /></span>
          </div>
          <span className="muted">הגעה {fmtTime(s.arrival_at)}</span>
          {s.cancelled_at && <span className="status status-no">בוטל · {fmtDateTime(s.cancelled_at)}</span>}
          {!canAnswer(s) && (
            <span className={`status status-${shiftLifecycle(s, r).tone}`}>{shiftLifecycle(s, r).label}</span>
          )}
          {canAnswer(s) && (
          <div className="segmented" role="group" aria-label={`הרשמה למשמרת ${SHIFT[s.kind]}`}>
            <button className="seg yes" aria-pressed={!s.cancelled_at && s.available === true} disabled={busy === s.shift_id}
                    onClick={() => save(s, true, s.arrival_time)}>
              <span aria-hidden>✓</span> מגיע/ה
            </button>
            <button className="seg no" aria-pressed={!s.cancelled_at && s.available === false} disabled={busy === s.shift_id}
                    onClick={() => save(s, false)}>
              <span aria-hidden>✕</span> לא מגיע/ה
            </button>
          </div>
          )}
          {s.state === "before" && s.available === true && !s.cancelled_at && (
            <button className="btn ghost small" disabled={busy === s.shift_id}
                    onClick={() => cancelMine(s)}>ביטול הרשמה למשמרת</button>
          )}
          {canAnswer(s) && s.allows_late_arrival && s.available && (
            lateOpen || s.arrival_time ? (
              <label className="field">
                שעת הגעה
                <select value={s.arrival_time ?? ""} onChange={(e) => save(s, true, e.target.value || null)}>
                  <option value="">בזמן ({fmtTime(s.arrival_at)})</option>
                  {LATE_TIMES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
            ) : (
              <button className="btn ghost small" disabled={!canAnswer(s)} onClick={() => setLateOpen(true)}>אגיע מאוחר יותר…</button>
            )
          )}
          <div className="shift-foot">
            {s.is_late && <span className="status status-late">עודכן אחרי הסגירה</span>}
            {saved?.shift === s.shift_id && (
              <span className="saved" role="status">✓ נשמר{saved.late ? " · האחראי יקבל הודעה" : ""}</span>
            )}
          </div>
        </section>
      ))}
      {!r.published && <p className="muted center">הסימון נשמר מיד. סימון זמינות אינו שיבוץ — השיבוץ יופיע אחרי שהאחראי יפרסם את הסידור.</p>}
    </Shell>
  );
}

interface PubPerson { user_id: string; full_name: string; level: "trainee" | "youth" | "mentor" | null; is_me: boolean; is_guest: boolean; home_weekday_name?: string | null; phone?: string }
interface PubSchedule {
  day: DaySummary;
  revision: number;
  published_at: string;
  assigned_outside_home: boolean;
  shifts: { id: string; kind: ShiftKind; starts_at: string; ends_at: string; arrival_at: string;
            slots: { slot_key: string; ordinal: number; crew: PubPerson[]; empty_trainee_positions: number;
                     operator: { user_id: string; full_name: string; start: string | null; end: string | null } | null }[] }[];
  // Scheduled participants on the boarding order (סדר עלייה), in order. ambulance is null until placed.
  boarding: { position: number; shift: ShiftKind; user_id: string; full_name: string; level: PubPerson["level"];
              is_me: boolean; is_guest: boolean; home_weekday_name?: string | null; ambulance: number | null }[];
}

export function ScheduleView() {
  const { dayId } = useParams();
  const s = useLoad(() => api.get<PubSchedule>(`/api/schedule/${dayId}`), [dayId]);
  if (!s.data) return <Shell title="סידור" back="/schedule"><Loading error={s.error} /></Shell>;
  const d = s.data;
  return (
    <Shell title={dayTitle(d.day)} back="/schedule">
      <div className="muted">פורסם {fmtDateTime(d.published_at)} · גרסה {d.revision}</div>
      {d.assigned_outside_home && <div className="notice">שובצת לתאריך זה (לא יום הבית שלך). הסידור המלא זמין לך גם אחרי המשמרת, בהיסטוריית הסידורים.</div>}
      {d.shifts.map((sh) => (
        <div key={sh.id} className="card stack">
          <div className="row spread">
            <h3>{SHIFT[sh.kind]}</h3>
            <span className="muted"><TimeRange from={sh.starts_at} to={sh.ends_at} /> · הגעה {fmtTime(sh.arrival_at)}</span>
          </div>
          {sh.slots.map((sl) => (
            <div key={sl.slot_key} className="slot">
              <strong>אמבולנס {sl.ordinal}</strong>
              {sl.operator && (
                <div className="operator-line">
                  <span className="badge">מפעיל/ה</span> <strong>{sl.operator.full_name}</strong>{" "}
                  <span className="time-range" dir="ltr">{sl.operator.start}{sl.operator.end ? `–${sl.operator.end}` : ""}</span>
                  {!sl.operator.end && <span className="muted"> עד סוף המשמרת</span>}
                </div>
              )}
              {sl.crew.map((p) => (
                <div key={p.user_id} className="person">
                  {/* Own name only: other volunteers' profiles are for managers (see Profile page). */}
                  <span className="name">{p.full_name}{p.is_me ? " (אני)" : ""}</span>
                  <LevelBadge level={p.level} />
                  {p.is_guest && <span className="badge guest">אורח/ת{p.home_weekday_name ? ` · יום ${p.home_weekday_name}` : ""}</span>}
                  {p.phone && <WhatsAppLink phone={p.phone} name={p.full_name} />}
                </div>
              ))}
              {/* Unfilled trainee positions on a populated ambulance (boarding is assigned separately). */}
              {Array.from({ length: sl.empty_trainee_positions }, (_, i) => (
                <div key={`empty-${i}`} className="person empty-position">
                  <span className="muted">חניך/ה</span>
                </div>
              ))}
            </div>
          ))}
        </div>
      ))}
      {d.boarding.length > 0 && (
        <section className="card stack">
          <h3 className="section-title">סדר עלייה</h3>
          <ol className="boarding-list">
            {d.boarding.map((b) => (
              <li key={b.user_id} className="person">
                <span className="name">{b.full_name}{b.is_me ? " (אני)" : ""}</span>
                <LevelBadge level={b.level} />
                <span className="badge">משמרת {SHIFT[b.shift]}</span>
                {b.is_guest && <span className="badge guest">אורח/ת{b.home_weekday_name ? ` · יום ${b.home_weekday_name}` : ""}</span>}
                {b.ambulance && <span className="muted">אמבולנס {b.ambulance}</span>}
              </li>
            ))}
          </ol>
        </section>
      )}
    </Shell>
  );
}

export function History() {
  const h = useLoad(() => api.get<HistoryItem[]>("/api/me/history"));
  return (
    <Shell title="ההיסטוריה שלי" back="/">
      <div className="muted">שיבוצים שפורסמו ולא בוטלו (השתתפות מתוכננת, לא נוכחות בפועל).</div>
      {!h.data && <Loading error={h.error} />}
      {h.data?.length === 0 && <div className="card muted">עוד אין שיבוצים.</div>}
      {h.data?.map((x) => (
        <div key={x.activity_day_id + x.shift} className="card row spread">
          <span>{dayTitle(x)} · {SHIFT[x.shift]}</span>
          <span className="muted">אמבולנס {x.ambulance}</span>
        </div>
      ))}
    </Shell>
  );
}
