import { useCallback, useEffect, useState } from "react";
import { Link, Navigate, useParams } from "react-router-dom";
import { api, ApiError, type DaySummary, type Level, type ShiftKind } from "../../api";
import { useAuth } from "../../auth";
import { DAY_KIND, SHIFT, WEEKDAYS, WINDOW, dayTitle, fmtDateTime, fmtTime } from "../../he";
import { LevelBadge, Loading, Shell, TimeRange, errorText } from "../../ui";
import { TraineeList } from "../Journal";

// Ordered boarding list entry (סדר עלייה). Being on the list is scheduled participation.
interface BoardingRow { position: number; shift: ShiftKind; shift_id: string; user_id: string; full_name: string;
  level: Level | null; is_guest: boolean; home_weekday_name?: string | null; ambulance: number | null }

// A 404 on a management screen means the day is no longer managed by this person (expired or revoked, or never
// granted). Explain it in Hebrew instead of showing a generic error. No change was applied.
export function accessMessage(e: unknown): string {
  if (e instanceof ApiError && e.status === 404) {
    return "ההרשאה לניהול היום הזה הסתיימה או בוטלה. לא בוצע שינוי. יש לחזור לתפריט הניהול.";
  }
  return errorText(e);
}

const DAY_NAME = (wd: number) => (wd === 6 ? "שבת" : `יום ${WEEKDAYS[wd]}`);

// Unified management hub (decision of 2026-10-17). What is shown follows the signed-in user's existing permissions;
// nothing here grants access by itself. Every destination enforces its own rules on the server.
export function ManageHub() {
  const { me } = useAuth();
  if (me?.is_admin || me?.is_youth_manager) return <YouthManagementHub />;
  return <DayManagerHub />;
}

function YouthManagementHub() {
  return (
    <Shell title="ניהול">
      <nav className="youth-grid" aria-label="ימים">
        {[0, 1, 2, 3, 4, 5].map((wd) => (
          <Link key={wd} className="hub-card" to={`/manage/day/${wd}`}>
            <strong>{WEEKDAYS[wd]}</strong>
          </Link>
        ))}
        <Link className="hub-card wide" to="/manage/day/6">
          <strong>שבת</strong>
        </Link>
        <Link className="hub-card wide" to="/manage/trainees">
          <strong>חניכים</strong>
          <span className="muted">התקדמות החניכים לפי יום</span>
        </Link>
        {["אבטחות", "אט״ן", "קורס 60"].map((label) => (
          <button key={label} type="button" className="hub-card wide" disabled>
            <strong>{label}</strong>
            <span className="muted">בקרוב</span>
          </button>
        ))}
      </nav>
    </Shell>
  );
}

function DayManagerHub() {
  const { me } = useAuth();
  const days = [...(me?.managed_weekdays ?? [])].sort((a, b) => a - b);
  const temporary = new Map((me?.temporary_days ?? []).map((t) => [t.weekday, t.ends_at]));
  const [picked, setPicked] = useState<number | null>(null);
  const chosen = days.length === 1 ? days[0] : picked;
  const expiry = (wd: number) => {
    const ends = temporary.get(wd);
    return ends ? `עד ${fmtDateTime(ends)}` : null;
  };
  if (days.length === 0) return <Shell title="ניהול"><div className="card muted">אין לך הרשאות ניהול פעילות.</div></Shell>;

  if (chosen === null) {
    return (
      <Shell title="ניהול" back="/">
        <h2>בחירת יום לניהול</h2>
        <nav className="hub-grid" aria-label="בחירת יום">
          {days.map((wd) => (
            <button key={wd} type="button" className="hub-card" onClick={() => setPicked(wd)}>
              <strong>{DAY_NAME(wd)}</strong>
              {expiry(wd) && <span className="muted">גישה זמנית {expiry(wd)}</span>}
            </button>
          ))}
        </nav>
      </Shell>
    );
  }
  const isSaturday = chosen === 6;
  const label = DAY_NAME(chosen);
  return (
    <Shell title={isSaturday ? "שבת" : "ניהול"} back={days.length > 1 ? "/manage" : "/"}>
      <h2>{label}</h2>
      {expiry(chosen) && <div className="notice" role="status">גישה זמנית {expiry(chosen)}</div>}
      {days.length > 1 && (
        <button type="button" className="btn small ghost" onClick={() => setPicked(null)}>החלפת יום</button>
      )}
      <nav className="hub-grid" aria-label={label}>
        <Link className="hub-card" to={`/schedule?weekday=${chosen}`}>
          <strong>ניהול משמרת</strong>
          <span className="muted">הסידורים של {label}</span>
        </Link>
        {!isSaturday && (
          <Link className="hub-card" to={`/manage/trainees/day/${chosen}`}>
            <strong>חניכים</strong>
            <span className="muted">חניכי {label}</span>
          </Link>
        )}
      </nav>
    </Shell>
  );
}

// Trainee entry (decision of 2026-10-17): youth managers and admin pick a weekday; a Day Manager with one ordinary
// day opens it directly. Saturday managers have no trainee access.
export function TraineesEntry() {
  const { me } = useAuth();
  const youthView = !!(me?.is_admin || me?.is_youth_manager);
  const ordinary = (me?.managed_weekdays ?? []).filter((w) => w !== 6).sort((a, b) => a - b);
  if (!youthView && ordinary.length === 0) return <Navigate to="/manage" replace />;
  if (!youthView && ordinary.length === 1) return <TraineeList weekday={ordinary[0]} back="/manage" />;
  const choices = youthView ? [0, 1, 2, 3, 4, 5, 6] : ordinary;
  return (
    <Shell title="חניכים" back="/manage">
      <h2>בחירת יום</h2>
      <nav className="youth-grid" aria-label="בחירת יום לחניכים">
        {choices.map((wd) => (
          <Link key={wd} className="hub-card" to={`/manage/trainees/day/${wd}`}>
            <strong>{DAY_NAME(wd)}</strong>
          </Link>
        ))}
      </nav>
    </Shell>
  );
}

// One day's trainees. Back goes to the day picker (youth/admin) or to the management hub (Day Manager).
export function TraineeDay({ weekday }: { weekday: number }) {
  const { me } = useAuth();
  const youthView = !!(me?.is_admin || me?.is_youth_manager);
  if (!youthView && (weekday === 6 || !(me?.managed_weekdays ?? []).includes(weekday))) return <Navigate to="/manage" replace />;
  return <TraineeList weekday={weekday} back={youthView ? "/manage/trainees" : "/manage"} />;
}


interface Person {
  user_id: string;
  full_name: string;
  home_weekday_name?: string | null;
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
  capacity_over_limit?: boolean; operator: { user_id: string; full_name: string; start: string | null; end: string | null } | null }
// Upper bound for the capacity setting (the backend enforces it too). More crew than capacity is still allowed.
const MAX_CAPACITY = 3;
interface BoardShift { id: string; kind: ShiftKind; starts_at: string; ends_at: string; arrival_at: string; slots: Slot[]; waiting: Person[]; cancelled: (Person & { cancelled_at: string })[];
  self_selectable: boolean; registration_toggle: boolean }
interface Board {
  day: DaySummary;
  boarding: BoardingRow[];
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
  // Adding reinforcement may open volunteer registration at once (before publication only). Separate, explicit choice.
  const [openReinforcementNow, setOpenReinforcementNow] = useState(false);

  const [requests, setRequests] = useState<{ id: string; full_name: string; message: string; shift: string | null; status: string; created_at: string; resolved_at: string | null }[]>([]);

  const loadRequests = useCallback(async () => {
    try {
      setRequests(await api.get(`/api/manage/days/${dayId}/requests`));
    } catch {
      // Keep the last list; the board itself reports its own errors.
    }
  }, [dayId]);

  const load = useCallback(async () => {
    try {
      // Opening the board ensures a working draft exists (a copy of the live publication).
      setBoard(await api.post<Board>(`/api/manage/days/${dayId}/draft`));
      await loadRequests();
    } catch (e) {
      setError(accessMessage(e));
    }
  }, [dayId, loadRequests]);
  useEffect(() => {
    load();
  }, [load]);

  // Local, unsaved boarding order. It follows the server only when the saved list itself changes, so other
  // actions in the board do not discard it.
  const [boardingDraft, setBoardingDraft] = useState<BoardingRow[]>([]);
  const serverBoarding = JSON.stringify(board?.boarding ?? []);
  useEffect(() => {
    setBoardingDraft(board?.boarding ?? []);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [serverBoarding]);
  const boardingDirty = JSON.stringify(boardingDraft.map((e) => [e.user_id, e.shift_id])) !==
    JSON.stringify((board?.boarding ?? []).map((e) => [e.user_id, e.shift_id]));

  // Everyone on this date's board: candidates for the operating volunteer of an additional ambulance.
  const allPeople: Person[] = board ? Array.from(new Map(
    board.shifts.flatMap((sh) => [...sh.waiting, ...sh.slots.flatMap((sl) => sl.crew)]).map((p) => [p.user_id, p])
  ).values()) : [];

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
      setError(accessMessage(e));
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

  if (!board) return <Shell title="בניית סידור" back="/schedule"><Loading error={error} /></Shell>;
  const allSlots = (sh: BoardShift) => sh.slots.filter((s) => s.status === "active");

  return (
    <Shell title={dayTitle(board.day)} back="/schedule">
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

      {requests.filter((q) => q.status === "pending").map((q) => (
        <div key={q.id} className="card stack">
          <div className="row spread">
            <span><strong>{q.full_name}</strong> · בקשה להרשמה{q.shift ? ` · משמרת ${SHIFT[q.shift as keyof typeof SHIFT]}` : ""}</span>
          </div>
          <span className="muted">{q.message}</span>
          <div className="row">
            <button className="btn small primary" onClick={async () => {
              await act(() => api.post(`/api/manage/requests/${q.id}/decision`, { decision: "approved" }), "הבקשה אושרה");
              await loadRequests();
            }}>אישור</button>
            <button className="btn small ghost" onClick={async () => {
              await act(() => api.post(`/api/manage/requests/${q.id}/decision`, { decision: "rejected" }), "הבקשה נדחתה");
              await loadRequests();
            }}>דחייה</button>
          </div>
          <span className="muted">לא ניתן לפרסם את הסידור כל עוד יש בקשות ממתינות. אישור או דחייה אינם משנים הרשמה או שיבוץ.</span>
        </div>
      ))}
      {requests.some((q) => q.status !== "pending") && (
        <details className="card">
          <summary>בקשות שטופלו ({requests.filter((q) => q.status !== "pending").length})</summary>
          <div className="stack" style={{ marginTop: 8 }}>
            {requests.filter((q) => q.status !== "pending").map((q) => (
              <div key={q.id} className="muted">
                {q.full_name} · {q.status === "approved" ? "אושרה" : q.status === "rejected" ? "נדחתה" : "טופלה"} · {fmtDateTime(q.created_at)} · {q.message}
              </div>
            ))}
          </div>
        </details>
      )}

      {board.attentions.map((a) => (
        <div key={a.id} className="card alert row spread">
          <span><strong>{a.full_name}</strong> · {a.label}</span>
          <button className="btn small" onClick={() => act(() => api.post(`/api/manage/attentions/${a.id}/resolve`), "סומן כטופל")}>טופל</button>
        </div>
      ))}

      {(() => {
        // Shift types a date may still add (decision 2026-10-16). Evening always exists; special days have no reinforcement.
        const special = ["friday", "saturday", "holiday", "holiday_eve"].includes(board.day.kind);
        const allowed: ShiftKind[] = special ? ["morning", "evening"] : ["morning", "reinforcement", "evening"];
        const missing = allowed.filter((k) => !board.shifts.some((sh) => sh.kind === k));
        if (missing.length === 0) return null;
        return (
          <section className="card stack">
            <strong>הוספת סוג משמרת</strong>
            <span className="muted">הוספת משמרת לא פותחת הרשמה למתנדבים. לתגבור אפשר לפתוח הרשמה במפורש לפני הפרסום בלבד. אחרי פרסום לא נפתחת הרשמה חדשה. משבצים ישירות. מתנדבי היום הביתי יקבלו הודעה.</span>
            {missing.includes("reinforcement") && !board.published && (
              <label className="field">
                <input type="checkbox" checked={openReinforcementNow}
                       onChange={(e) => setOpenReinforcementNow(e.target.checked)} />
                לתגבור: פתיחת הרשמה למתנדבים מיד
              </label>
            )}
            <div className="row">
              {missing.map((k) => (
                <button key={k} className="btn small" onClick={() => act(
                  () => api.post(`/api/manage/days/${dayId}/shifts`, { kind: k, open_registration: k === "reinforcement" && openReinforcementNow }),
                  `נוספה משמרת ${SHIFT[k]}`)}>
                  + משמרת {SHIFT[k]}
                </button>
              ))}
            </div>
          </section>
        );
      })()}

      <section className="card stack">
        <strong>סדר עלייה ({boardingDraft.length})</strong>
        <span className="muted">רשימת חניכים לפי סדר העלייה. הרשומים מתוכננים למשמרת גם בלי אמבולנס. בחירת אמבולנס היא נפרדת.</span>
        {boardingDraft.length === 0 && <span className="muted">אין חניכים ברשימה עדיין</span>}
        {boardingDraft.map((e, i) => (
          <div key={e.user_id} className="person">
            <span className="muted">{i + 1}.</span>
            <Link className="name person-link" to={`/people/${e.user_id}`}>{e.full_name}</Link>
            <LevelBadge level={e.level} />
            {e.is_guest && <span className="badge guest">אורח/ת{e.home_weekday_name ? ` · יום ${e.home_weekday_name}` : ""}</span>}
            <span className="badge">משמרת {SHIFT[e.shift]}</span>
            <span className="muted">{e.ambulance ? `אמבולנס ${e.ambulance}` : "ממתין/ה לשיבוץ לאמבולנס"}</span>
            <button className="btn small ghost" aria-label="הזזה למעלה" disabled={i === 0}
                    onClick={() => setBoardingDraft(moveRow(boardingDraft, i, i - 1))}>▲</button>
            <button className="btn small ghost" aria-label="הזזה למטה" disabled={i === boardingDraft.length - 1}
                    onClick={() => setBoardingDraft(moveRow(boardingDraft, i, i + 1))}>▼</button>
            <button className="btn small ghost" onClick={() => setBoardingDraft(boardingDraft.filter((_, j) => j !== i))}>הוצאה</button>
          </div>
        ))}
        {(() => {
          // Candidates: trainees waiting for a shift of this date. Evening first (priority order); reinforcement last.
          const listed = new Set(boardingDraft.map((e) => e.user_id));
          const candidates: { p: Person; shift: ShiftKind; shift_id: string }[] = [];
          for (const kind of ["evening", "reinforcement"] as ShiftKind[]) {
            for (const sh of board.shifts.filter((x) => x.kind === kind)) {
              for (const p of sh.waiting) {
                if (p.level === "trainee" && !listed.has(p.user_id) && !candidates.some((c) => c.p.user_id === p.user_id)) {
                  candidates.push({ p, shift: kind, shift_id: sh.id });
                }
              }
            }
          }
          if (candidates.length === 0) return <span className="muted">אין חניכים ממתינים שאפשר להוסיף</span>;
          return (
            <label className="field">
              הוספת חניך/ה לסדר העלייה
              <select value="" onChange={(ev) => {
                const c = candidates.find((x) => x.p.user_id === ev.target.value);
                if (c) setBoardingDraft([...boardingDraft, { position: 0, shift: c.shift, shift_id: c.shift_id,
                  user_id: c.p.user_id, full_name: c.p.full_name, level: c.p.level, is_guest: c.p.is_guest,
                  home_weekday_name: c.p.home_weekday_name, ambulance: null }]);
              }}>
                <option value="">בחירה…</option>
                {candidates.map((c) => <option key={c.p.user_id} value={c.p.user_id}>{c.p.full_name} · משמרת {SHIFT[c.shift]}</option>)}
              </select>
            </label>
          );
        })()}
        <div className="row">
          <button className="btn primary small" disabled={!boardingDirty} onClick={() => act(
            () => api.put(`/api/manage/days/${dayId}/boarding`, { entries: boardingDraft.map((e) => ({ user_id: e.user_id, shift_id: e.shift_id })) }),
            "סדר העלייה נשמר")}>שמירת סדר העלייה</button>
          {boardingDirty && <span className="muted">יש שינויים שלא נשמרו</span>}
        </div>
      </section>

      {board.shifts.map((sh) => (
        <div key={sh.id} className="card stack">
          <div className="row spread">
            <h3>{SHIFT[sh.kind]}</h3>
            <span className="muted"><TimeRange from={sh.starts_at} to={sh.ends_at} /> · הגעה {fmtTime(sh.arrival_at)}</span>
          </div>
          {sh.registration_toggle && (
            // Sunday–Friday morning: hidden from volunteer registration until opened here.
            <div className="row spread">
              <span className={sh.self_selectable ? "badge youth" : "badge"}>
                {sh.self_selectable ? "פתוחה להרשמת מתנדבים" : "לא מוצגת להרשמה (שיבוץ ידני בלבד)"}
              </span>
              {/* After publication, reinforcement registration cannot be newly opened (existing answers can be closed). */}
              <button className="btn small" disabled={!sh.self_selectable && !!board.published && sh.kind === "reinforcement"}
                      onClick={() => act(
                () => api.post(`/api/manage/shifts/${sh.id}/registration`, { open: !sh.self_selectable }),
                sh.self_selectable
                  ? `${sh.kind === "morning" ? "הבוקר" : "התגבור"} הוסתר מההרשמה. תשובות קיימות נשמרו`
                  : `${sh.kind === "morning" ? "הבוקר" : "התגבור"} נפתח להרשמה`)}>
                {sh.self_selectable ? "הסתרה מההרשמה" : "פתיחה להרשמה"}
              </button>
              {!sh.self_selectable && !!board.published && sh.kind === "reinforcement" && (
                <span className="muted">אחרי הפרסום אי אפשר לפתוח הרשמה חדשה לתגבור</span>
              )}
            </div>
          )}

          {sh.slots.length > 0 && sh.slots.every((sl) => sl.crew.length === 0) && (
            // Compact empty state: the shift stays open and its controls stay below.
            <span className="muted compact-empty">אין משובצים במשמרת זו עדיין</span>
          )}
          {sh.slots.map((sl) => (
            <div key={sl.slot_key} className={`slot ${sl.status === "cancelled" ? "cancelled" : ""}`}>
              {sl.operator && (
                <div className="operator-line">
                  <span className="badge">מפעיל/ה</span> <strong>{sl.operator.full_name}</strong>{" "}
                  <span className="time-range" dir="ltr">{sl.operator.start}{sl.operator.end ? `–${sl.operator.end}` : ""}</span>
                  {!sl.operator.end && <span className="muted"> עד סוף המשמרת</span>}
                </div>
              )}
              {sl.status === "active" && (
                <OperatorForm slotKey={sl.slot_key} current={sl.operator} candidates={allPeople} run={act} />
              )}
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

          {sh.cancelled.length > 0 && (
            <div className="stack">
              <strong>בוטלו ({sh.cancelled.length})</strong>
              <span className="muted">בוטל על ידי המתנדב עצמו. לא נספרים כמועמדים.</span>
              {sh.cancelled.map((p) => (
                <div key={p.user_id} className="person">
                  <PersonLine p={p} />
                  <span className="muted">בוטל · {fmtDateTime(p.cancelled_at)}</span>
                </div>
              ))}
            </div>
          )}
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
                  {/* Operational boarding priority: regular ambulances first; operated ones after, from their start time. */}
                  {[...allSlots(sh)].sort((a, b) => Number(!!a.operator) - Number(!!b.operator)).map((o) => (
                    <option key={o.slot_key} value={o.slot_key}>
                      {o.operator ? `אמבולנס ${o.ordinal} · מופעל/ת ${o.operator.full_name} מ-${o.operator.start}` : `אמבולנס ${o.ordinal}`}
                    </option>
                  ))}
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

// Operating volunteer of an additional ambulance: a name, a start time (until the end of the shift) and optionally
// an explicit end time. Kept apart from the youth role and the guest labels.
function OperatorForm({ slotKey, current, candidates, run }: {
  slotKey: string; current: Slot["operator"]; candidates: Person[];
  run: (fn: () => Promise<unknown>, okText?: string) => Promise<unknown>;
}) {
  const [uid, setUid] = useState(current?.user_id ?? "");
  const [start, setStart] = useState(current?.start ?? "");
  const [end, setEnd] = useState(current?.end ?? "");
  return (
    <details className="operator-form">
      <summary>מפעיל/ה לאמבולנס</summary>
      <div className="stack">
        <label className="field">
          מתנדב/ת מפעיל/ה
          <select value={uid} onChange={(e) => setUid(e.target.value)}>
            <option value="">ללא מפעיל/ה</option>
            {candidates.map((p) => <option key={p.user_id} value={p.user_id}>{p.full_name}</option>)}
          </select>
        </label>
        <label className="field">
          שעת התחלה
          <input type="time" dir="ltr" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label className="field">
          שעת סיום (לא חובה: בלי סיום, עד סוף המשמרת)
          <input type="time" dir="ltr" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
        <div className="row">
          <button className="btn small primary" disabled={!uid || !start}
                  onClick={() => run(() => api.put(`/api/manage/slots/${slotKey}/operator`,
                    { user_id: uid, start, end: end || null }), "המפעיל/ה נשמר/ה")}>שמירה</button>
          <button className="btn small ghost" disabled={!current}
                  onClick={() => run(() => api.put(`/api/manage/slots/${slotKey}/operator`,
                    { user_id: null, start: null, end: null }), "המפעיל/ה הוסר/ה")}>הסרה</button>
        </div>
      </div>
    </details>
  );
}

function moveRow(list: BoardingRow[], from: number, to: number): BoardingRow[] {
  const copy = [...list];
  const [item] = copy.splice(from, 1);
  copy.splice(to, 0, item);
  return copy;
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
      {/* The name opens the volunteer's profile (permissions are checked there). */}
      <Link className="name person-link" to={`/people/${p.user_id}`}>{p.full_name}</Link>
      <LevelBadge level={p.level} />
      {p.is_guest && <span className="badge guest">אורח/ת{p.home_weekday_name ? ` · יום ${p.home_weekday_name}` : ""}{p.confirmation === "pending" ? " · ממתין לאישור" : p.confirmation === "confirmed" ? " · אישר" : ""}</span>}
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
