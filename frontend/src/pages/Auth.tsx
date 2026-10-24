import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { WEEKDAYS } from "../he";
import { Shell, errorText } from "../ui";

export function Login() {
  const { refresh } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/api/auth/login", { email, password });
      await refresh();
      nav("/");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="כניסה">
      <form className="card stack" onSubmit={submit}>
        <label className="field">
          מייל
          <input type="email" dir="ltr" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
        </label>
        <label className="field">
          סיסמה
          <input type="password" dir="ltr" autoComplete="current-password" value={password}
                 onChange={(e) => setPassword(e.target.value)} required />
        </label>
        {error && <div className="error">{error}</div>}
        <button className="btn primary" disabled={busy}>כניסה</button>
        <div className="muted">אין לך חשבון? <Link to="/signup">הרשמה</Link></div>
      </form>
    </Shell>
  );
}

export function Signup() {
  const { refresh } = useAuth();
  const nav = useNavigate();
  const [form, setForm] = useState({ full_name: "", email: "", phone: "", password: "", requested_weekday: "" });
  const [error, setError] = useState<string | null>(null);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  async function submit(e: FormEvent) {
    e.preventDefault();
    try {
      await api.post("/api/auth/signup", {
        full_name: form.full_name,
        email: form.email,
        phone: form.phone || null,
        password: form.password,
        requested_weekday: form.requested_weekday === "" ? null : Number(form.requested_weekday),
      });
      await refresh();
      nav("/pending");
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <Shell title="הרשמה למערכת" back="/login">
      <form className="card stack" onSubmit={submit}>
        <label className="field">שם מלא<input value={form.full_name} onChange={set("full_name")} required minLength={2} /></label>
        <label className="field">מייל<input type="email" dir="ltr" value={form.email} onChange={set("email")} required /></label>
        <label className="field">טלפון<input type="tel" dir="ltr" value={form.phone} onChange={set("phone")} /></label>
        <label className="field">
          סיסמה (לפחות 8 תווים)
          <input type="password" dir="ltr" autoComplete="new-password" value={form.password} onChange={set("password")}
                 required minLength={8} />
        </label>
        <label className="field">
          יום התנדבות מבוקש
          <select value={form.requested_weekday} onChange={set("requested_weekday")}>
            <option value="">לא ידוע</option>
            {WEEKDAYS.slice(0, 6).map((w, i) => <option key={i} value={i}>{w}</option>)}
          </select>
        </label>
        <div className="muted">החשבון ייפתח לאחר אישור מנהל. היום והתפקיד ייקבעו בעת האישור.</div>
        {error && <div className="error">{error}</div>}
        <button className="btn primary">שליחה</button>
      </form>
    </Shell>
  );
}

export function Pending() {
  const { me, refresh } = useAuth();
  return (
    <Shell title="ממתין לאישור">
      <div className="card stack">
        <h3>שלום {me?.full_name}</h3>
        <div>הבקשה התקבלה. לאחר שמנהל יאשר את החשבון ויקבע יום ותפקיד, תוכלו להירשם למשמרות.</div>
        <div className="muted">{me?.email}</div>
        <button className="btn" onClick={() => refresh()}>בדיקה מחדש</button>
      </div>
    </Shell>
  );
}
