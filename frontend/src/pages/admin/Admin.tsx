import { useState } from "react";
import { Link, NavLink } from "react-router-dom";
import { api, type Level } from "../../api";
import { LEVEL, WEEKDAYS, fmtDateTime, phoneStatus } from "../../he";
import { LevelBadge, Loading, Shell, errorText, useLoad } from "../../ui";
import { PersonPicker } from "../manage/Manage";

interface AdminUser {
  id: string;
  full_name: string;
  email: string;
  phone: string | null;
  status: string;
  level: Level | null;
  home_weekday: number | null;
  requested_weekday: number | null;
  roles: string[];
  managed_weekdays: number[];
}

function AdminTabs() {
  const tabs: [string, string][] = [
    ["/admin", "משתמשים"],
    ["/admin/calendar", "לוח שנה"],
    ["/admin/outbox", "הודעות"],
    ["/admin/audit", "יומן שינויים"],
  ];
  return (
    <div className="tabs">
      {tabs.map(([to, label]) => (
        <NavLink key={to} to={to} end className={({ isActive }) => (isActive ? "active" : "")}>{label}</NavLink>
      ))}
    </div>
  );
}

export function AdminUsers() {
  const [q, setQ] = useState("");
  const pending = useLoad(() => api.get<AdminUser[]>("/api/admin/users?status=pending"));
  const users = useLoad(() => api.get<AdminUser[]>(`/api/admin/users?q=${encodeURIComponent(q)}`), [q]);
  return (
    <Shell title="ממשק אדמין">
      <AdminTabs />
      <div className="card stack">
        <h3>ממתינים לאישור ({pending.data?.length ?? 0})</h3>
        {pending.data?.length === 0 && <div className="muted">אין בקשות ממתינות.</div>}
        {pending.data?.map((u) => <Approve key={u.id} user={u} onDone={() => { pending.reload(); users.reload(); }} />)}
      </div>
      <input className="field" style={{ border: "1px solid var(--line)", borderRadius: 10, padding: 10 }}
             placeholder="חיפוש לפי שם או מייל" value={q} onChange={(e) => setQ(e.target.value)} />
      {!users.data && <Loading error={users.error} />}
      {users.data?.filter((u) => u.status !== "pending").map((u) => <UserCard key={u.id} user={u} onDone={users.reload} />)}
    </Shell>
  );
}

function Approve({ user, onDone }: { user: AdminUser; onDone: () => void }) {
  const [level, setLevel] = useState<Level>("trainee");
  const [day, setDay] = useState<string>(user.requested_weekday?.toString() ?? "");
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="slot">
      <strong>{user.full_name}</strong>
      <span className="muted" dir="ltr" style={{ textAlign: "end" }}>{user.email}</span>
      <div className="row">
        <select value={level} onChange={(e) => setLevel(e.target.value as Level)}>
          {(["trainee", "youth", "mentor"] as Level[]).map((l) => <option key={l} value={l}>{LEVEL[l]}</option>)}
        </select>
        <select value={day} onChange={(e) => setDay(e.target.value)}>
          <option value="">ללא יום קבוע</option>
          {WEEKDAYS.map((w, i) => <option key={i} value={i}>{w}</option>)}
        </select>
        <button className="btn small primary" onClick={async () => {
          try {
            await api.post(`/api/admin/users/${user.id}/approve`, { level, home_weekday: day === "" ? null : Number(day) });
            onDone();
          } catch (e) { setErr(errorText(e)); }
        }}>אישור</button>
      </div>
      {err && <div className="error">{err}</div>}
    </div>
  );
}

function UserCard({ user, onDone }: { user: AdminUser; onDone: () => void }) {
  const [open, setOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function patch(body: Record<string, unknown>) {
    try {
      await api.patch(`/api/admin/users/${user.id}`, body);
      setErr(null);
      onDone();
    } catch (e) { setErr(errorText(e)); }
  }
  return (
    <div className="card stack">
      <button className="list-link btn ghost" onClick={() => setOpen(!open)} style={{ border: 0, padding: 0, minHeight: 0 }}>
        <span className="row">
          <strong>{user.full_name}</strong>
          <LevelBadge level={user.level} />
          {user.status === "inactive" && <span className="badge">לא פעיל</span>}
          {user.roles.includes("admin") && <span className="badge red">אדמין</span>}
          {user.roles.includes("youth_manager") && <span className="badge">אחראי נוער</span>}
          {user.managed_weekdays.length > 0 && <span className="badge">אחראי {user.managed_weekdays.map((d) => WEEKDAYS[d]).join(", ")}</span>}
        </span>
        <span className="muted">{user.home_weekday !== null ? WEEKDAYS[user.home_weekday] : "—"}</span>
      </button>
      {open && (
        <div className="stack">
          <div className="muted" dir="ltr" style={{ textAlign: "end" }}>{user.email} · {user.phone ?? ""}</div>
          <label className="field">רמה
            <select value={user.level ?? ""} onChange={(e) => patch({ level: e.target.value })}>
              {(["trainee", "youth", "mentor"] as Level[]).map((l) => <option key={l} value={l}>{LEVEL[l]}</option>)}
            </select>
          </label>
          <label className="field">יום קבוע
            <select value={user.home_weekday ?? -1} onChange={(e) => patch({ home_weekday: Number(e.target.value) })}>
              <option value={-1}>ללא</option>
              {WEEKDAYS.map((w, i) => <option key={i} value={i}>{w}</option>)}
            </select>
          </label>
          <label className="row"><input type="checkbox" checked={user.roles.includes("youth_manager")}
                                        onChange={(e) => patch({ is_youth_manager: e.target.checked })} /> אחראי נוער</label>
          <label className="row"><input type="checkbox" checked={user.roles.includes("admin")}
                                        onChange={(e) => patch({ is_admin: e.target.checked })} /> אדמין</label>
          <button className="btn small" onClick={() => patch({ status: user.status === "active" ? "inactive" : "active" })}>
            {user.status === "active" ? "השבתת חשבון (ההיסטוריה נשמרת)" : "הפעלה מחדש"}
          </button>
          {err && <div className="error">{err}</div>}
        </div>
      )}
    </div>
  );
}

interface Template {
  weekday: number; weekday_name: string; open_days_before: number; open_time: string;
  close_days_before: number; close_time: string; target_days_before: number; target_time: string;
}

export function AdminCalendar() {
  const tpls = useLoad(() => api.get<Template[]>("/api/templates"));
  const [range, setRange] = useState({ start: "", end: "" });
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <Shell title="ממשק אדמין">
      <AdminTabs />
      <div className="card stack">
        <h3>תבניות הרשמה לפי יום</h3>
        <div className="muted">שינוי תבנית חל על תאריכים חדשים בלבד. תאריכים קיימים נשמרים עם המועדים שלהם.</div>
        {!tpls.data && <Loading error={tpls.error} />}
        {tpls.data?.map((t) => {
          const put = (body: object) => api.put(`/api/templates/${t.weekday}`, body).then(tpls.reload);
          return (
            <div key={t.weekday} className="slot">
              <strong>יום {t.weekday_name}</strong>
              <div className="row spread"><span>פתיחה · {t.open_days_before} ימים לפני</span>
                <TimeEdit value={t.open_time} onSave={(v) => put({ open_time: v })} /></div>
              <div className="row spread"><span>סגירה · {t.close_days_before} ימים לפני</span>
                <TimeEdit value={t.close_time} onSave={(v) => put({ close_time: v })} /></div>
              <div className="row spread"><span>יעד פרסום · {t.target_days_before} יום לפני</span>
                <TimeEdit value={t.target_time} onSave={(v) => put({ target_time: v })} /></div>
            </div>
          );
        })}
      </div>
      <div className="card stack">
        <h3>יצירת תאריכי פעילות</h3>
        <div className="muted">סוג היום נקבע לפי יום בשבוע (שישי/שבת). חגים מסומנים ידנית במסך היום — עדיין אין חיבור ללוח חגים.</div>
        <div className="row">
          <label className="field">מ־<input type="date" value={range.start} onChange={(e) => setRange({ ...range, start: e.target.value })} /></label>
          <label className="field">עד<input type="date" value={range.end} onChange={(e) => setRange({ ...range, end: e.target.value })} /></label>
        </div>
        <button className="btn primary" disabled={!range.start || !range.end} onClick={async () => {
          try {
            const r = await api.post<{ created: number }>("/api/admin/days/generate", range);
            setMsg(`נוצרו ${r.created} תאריכים`);
          } catch (e) { setMsg(errorText(e)); }
        }}>יצירה</button>
        {msg && <div>{msg}</div>}
      </div>
      <DayKindEditor />
    </Shell>
  );
}

function TimeEdit({ value, onSave }: { value: string; onSave: (v: string) => Promise<unknown> }) {
  // A select of quarter hours renders as a 24h wheel on phones (input type=time follows device AM/PM settings).
  const options: string[] = [];
  for (let h = 0; h < 24; h++) for (const m of ["00", "15", "30", "45"]) options.push(`${String(h).padStart(2, "0")}:${m}`);
  return (
    <select value={value} onChange={(e) => onSave(e.target.value)}>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

function DayKindEditor() {
  const days = useLoad(() => api.get<{ id: string; date: string; weekday: number; kind: string }[]>("/api/manage/days"));
  const [err, setErr] = useState<string | null>(null);
  return (
    <div className="card stack">
      <h3>סיווג תאריכים (חג / ערב חג)</h3>
      <div className="muted">אפשר לשנות סוג יום רק לפני שנפתחו בו הרשמות או סידור, כי מבנה המשמרות משתנה.</div>
      {days.data?.map((d) => (
        <div key={d.id} className="row spread">
          <span>{WEEKDAYS[d.weekday]} {d.date.split("-").reverse().join(".")}</span>
          <select value={d.kind} onChange={async (e) => {
            try { await api.patch(`/api/days/${d.id}/kind`, { kind: e.target.value }); setErr(null); days.reload(); }
            catch (x) { setErr(errorText(x)); }
          }}>
            <option value="regular">יום רגיל</option><option value="friday">שישי</option><option value="saturday">שבת</option>
            <option value="holiday">חג</option><option value="holiday_eve">ערב חג</option>
          </select>
        </div>
      ))}
      {err && <div className="error">{err}</div>}
    </div>
  );
}

export function AdminOutbox() {
  const rows = useLoad(() => api.get<{ id: string; event_type: string; recipient: string; status: string; attempts: number; created_at: string; last_error: string | null }[]>("/api/admin/outbox"));
  return (
    <Shell title="ממשק אדמין">
      <AdminTabs />
      <div className="notice">אין עדיין חיבור להתראות טלפון. הודעות מסומנות "מוצג באפליקציה בלבד" ואינן מדווחות כנשלחו.</div>
      <div className="card scroll-x">
        <table className="simple">
          <thead><tr><th>אירוע</th><th>נמען</th><th>מצב</th><th>זמן</th></tr></thead>
          <tbody>{rows.data?.map((m) => (
            <tr key={m.id}><td>{m.event_type}</td><td>{m.recipient}</td><td>{phoneStatus(m.status)}</td><td>{fmtDateTime(m.created_at)}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </Shell>
  );
}

export function AdminAudit() {
  const rows = useLoad(() => api.get<{ id: string; actor: string | null; action: string; object_type: string; created_at: string; meta: object }[]>("/api/admin/audit"));
  return (
    <Shell title="ממשק אדמין">
      <AdminTabs />
      <div className="card scroll-x">
        <table className="simple">
          <thead><tr><th>זמן</th><th>מבצע</th><th>פעולה</th><th>פרטים</th></tr></thead>
          <tbody>{rows.data?.map((e) => (
            <tr key={e.id}><td>{fmtDateTime(e.created_at)}</td><td>{e.actor ?? "מערכת"}</td><td>{e.action}</td>
              <td dir="ltr" style={{ fontSize: ".75rem" }}>{JSON.stringify(e.meta)}</td></tr>
          ))}</tbody>
        </table>
      </div>
    </Shell>
  );
}

// Temporary Day Manager appointments (decision of 2026-10-17): time-limited cover of one weekday by a mentor. Only
// the Youth Manager and Admin manage them. Times are Asia/Jerusalem wall clock. Permanent managers are unaffected.
interface TempAppointment {
  id: string; user_id: string; full_name: string; level: Level | null; weekday: number; weekday_name: string;
  starts: string; ends: string; status: "scheduled" | "active" | "expired" | "revoked";
  created_by: string; created_at: string; updated_by: string | null; updated_at: string | null;
  revoked_by: string | null; revoked_at: string | null;
}
const STATUS_TEXT: Record<TempAppointment["status"], string> = {
  scheduled: "מתוזמן", active: "פעיל", expired: "הסתיים", revoked: "בוטל",
};
const BLANK_FORM = { user: null as { id: string; full_name: string } | null, weekday: 0, starts: "", ends: "" };

function TemporaryAppointments() {
  const list = useLoad(() => api.get<TempAppointment[]>("/api/day-appointments/temporary"));
  const [form, setForm] = useState(BLANK_FORM);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function reset() { setForm(BLANK_FORM); setEditingId(null); }
  async function save() {
    setErr(null); setNotice(null);
    try {
      if (editingId) {
        await api.put(`/api/day-appointments/temporary/${editingId}`, { weekday: form.weekday, starts: form.starts, ends: form.ends });
        setNotice("המינוי עודכן");
      } else {
        if (!form.user) { setErr("יש לבחור מתנדב/ת"); return; }
        await api.post("/api/day-appointments/temporary", { user_id: form.user.id, weekday: form.weekday, starts: form.starts, ends: form.ends });
        setNotice("המינוי נשמר");
      }
      reset();
      list.reload();
    } catch (e) { setErr(errorText(e)); }
  }
  function startEdit(a: TempAppointment) {
    setEditingId(a.id);
    setForm({ user: { id: a.user_id, full_name: a.full_name }, weekday: a.weekday, starts: a.starts, ends: a.ends });
  }
  async function revoke(id: string) {
    setErr(null); setNotice(null);
    try {
      await api.post(`/api/day-appointments/temporary/${id}/revoke`, { confirm: true });
      setConfirmId(null);
      setNotice("המינוי בוטל. הגישה לניהול היום נסגרה מיד");
      list.reload();
    } catch (e) { setErr(errorText(e)); }
  }

  return (
    <section className="card stack" aria-labelledby="temp-title">
      <strong id="temp-title">אחראי/ת זמני/ת</strong>
      <span className="muted">מינוי זמני מאפשר לחונך/ת לנהל יום אחר לפרק זמן מוגדר, בלי לשנות את הרשאותיו/ה הקבועות. שעות לפי שעון ישראל.</span>
      {err && <div className="error" role="alert">{err}</div>}
      {notice && <div className="saved" role="status">{notice}</div>}

      {(list.data ?? []).map((a) => (
        <div key={a.id} className="person" style={{ flexWrap: "wrap" }}>
          <span className="name">{a.full_name}</span>
          <span className="badge">{STATUS_TEXT[a.status]}</span>
          <span className="muted">יום {a.weekday_name}</span>
          <span className="time-range" dir="ltr">{a.starts.replace("T", " ")} – {a.ends.replace("T", " ")}</span>
          <span className="muted">נוצר על ידי {a.created_by}{a.updated_by ? ` · עודכן על ידי ${a.updated_by}` : ""}{a.revoked_by ? ` · בוטל על ידי ${a.revoked_by}` : ""}</span>
          {(a.status === "scheduled" || a.status === "active") && (
            <span className="row">
              <button type="button" className="btn small ghost" onClick={() => startEdit(a)}>עריכה</button>
              {confirmId === a.id ? (
                <>
                  <button type="button" className="btn small primary" onClick={() => revoke(a.id)}>אישור ביטול</button>
                  <button type="button" className="btn small ghost" onClick={() => setConfirmId(null)}>חזרה</button>
                </>
              ) : (
                <button type="button" className="btn small ghost" onClick={() => setConfirmId(a.id)}>ביטול מינוי</button>
              )}
            </span>
          )}
        </div>
      ))}
      {list.data && list.data.length === 0 && <span className="muted">אין מינויים זמניים.</span>}

      <div className="stack">
        <strong>{editingId ? "עריכת מינוי" : "מינוי חדש"}</strong>
        {!editingId && <PersonPicker label="מתנדב/ת (חונך/ת פעיל/ה בלבד)" onPick={(u) => setForm({ ...form, user: { id: u.id, full_name: u.full_name } })} />}
        {form.user && <span className="muted">נבחר/ה: {form.user.full_name}</span>}
        <label className="field">
          יום לניהול
          <select value={form.weekday} onChange={(e) => setForm({ ...form, weekday: Number(e.target.value) })}>
            {WEEKDAYS.map((name, wd) => <option key={wd} value={wd}>{name}</option>)}
          </select>
        </label>
        <label className="field">
          התחלה (שעון ישראל)
          <input type="datetime-local" dir="ltr" value={form.starts} onChange={(e) => setForm({ ...form, starts: e.target.value })} />
        </label>
        <label className="field">
          סיום (שעון ישראל)
          <input type="datetime-local" dir="ltr" value={form.ends} onChange={(e) => setForm({ ...form, ends: e.target.value })} />
        </label>
        <div className="row">
          <button type="button" className="btn primary small" disabled={!form.starts || !form.ends || (!editingId && !form.user)} onClick={save}>
            {editingId ? "שמירת שינויים" : "שמירת מינוי זמני"}
          </button>
          {editingId && <button type="button" className="btn small ghost" onClick={reset}>ביטול עריכה</button>}
        </div>
      </div>
    </section>
  );
}

export function YouthManager() {
  const mgrs = useLoad(() => api.get<{ weekday: number; weekday_name: string; managers: { id: string; full_name: string }[] }[]>("/api/day-managers"));
  const [err, setErr] = useState<string | null>(null);
  async function save(weekday: number, ids: string[]) {
    try {
      await api.put(`/api/day-managers/${weekday}`, { user_ids: ids });
      setErr(null);
      mgrs.reload();
    } catch (e) { setErr(errorText(e)); }
  }
  return (
    <Shell title="אחראי יום" back="/manage">
      <Link className="btn" to="/manage/trainees">התקדמות חניכים (מספר נסיעות ותאריך אחרון)</Link>
      <div className="muted">מינוי, החלפה והסרה של אחראי יום. אחראי יום חייב להיות חונך. צפייה בסידורים מפורסמים דרך לשונית "סידורים".</div>
      {err && <div className="error">{err}</div>}
      <TemporaryAppointments />
      {mgrs.data?.map((d) => (
        <div key={d.weekday} className="card stack">
          <h3>יום {d.weekday_name}</h3>
          {d.managers.length === 0 && <span className="muted">אין אחראי</span>}
          {d.managers.map((m) => (
            <div key={m.id} className="person">
              <span className="name">{m.full_name}</span>
              <span className="badge">אחראי/ת קבוע/ה</span>
              <button className="btn small ghost" onClick={() => save(d.weekday, d.managers.filter((x) => x.id !== m.id).map((x) => x.id))}>הסרה</button>
            </div>
          ))}
          <PersonPicker label="הוספת אחראי" onPick={(u) => save(d.weekday, [...d.managers.map((x) => x.id), u.id])} />
        </div>
      ))}
    </Shell>
  );
}
