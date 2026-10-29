import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { WEEKDAYS } from "../he";
import { Shell, errorText } from "../ui";

// Hebrew text for the reasons the Google callback can report back to the login screen.
const GOOGLE_REASONS: Record<string, string> = {
  cancelled: "ההתחברות עם Google בוטלה.",
  not_linked: "החשבון הזה קיים, אבל לא מקושר ל-Google. התחברו עם מייל וסיסמה, ואז קשרו את Google מהפרופיל.",
  no_account: "אין חשבון מקושר לכתובת הזו. אפשר להירשם בטופס ההרשמה, והאחראי יאשר את הבקשה.",
  account_inactive: "החשבון אינו פעיל.",
  email_not_verified: "כתובת המייל ב-Google לא אומתה.",
  state_invalid: "ההתחברות פגה או כבר נוצלה. נסו שוב.",
  nonce_mismatch: "ההתחברות לא אומתה. נסו שוב.",
  invalid_token: "ההתחברות לא אומתה. נסו שוב.",
  token_exchange_failed: "ההתחברות עם Google נכשלה. נסו שוב.",
  provider_unavailable: "שירות Google אינו זמין כרגע. נסו שוב מאוחר יותר.",
  identity_in_use: "חשבון Google זה כבר מקושר לחשבון אחר.",
  link_email_mismatch: "כתובת המייל ב-Google שונה מכתובת החשבון.",
};

export function Login() {
  const { refresh } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [googleOn, setGoogleOn] = useState(false);
  const googleReason = params.get("google") === "error" ? params.get("reason") ?? "" : "";

  useEffect(() => {
    api.get<{ google: boolean }>("/api/auth/capabilities").then((c) => setGoogleOn(c.google), () => setGoogleOn(false));
  }, []);

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
        {googleReason && <div className="error" role="alert">{GOOGLE_REASONS[googleReason] ?? "ההתחברות עם Google נכשלה."}</div>}
        <button className="btn primary" disabled={busy}>כניסה</button>
        {googleOn && (
          <a className="btn block" href="/api/auth/google/start">התחברות עם Google</a>
        )}
        <div className="muted"><Link to="/forgot">שכחתי סיסמה</Link></div>
        <div className="muted">אין לך חשבון? <Link to="/signup">הרשמה</Link></div>
      </form>
    </Shell>
  );
}

// Forgot password: always the same neutral answer, whatever the address is.
export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ message: string }>("/api/auth/password/forgot", { email });
      setMessage(r.message);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="שכחתי סיסמה" back="/login">
      {message ? (
        <div className="card stack" role="status">
          <div>{message}</div>
          <Link to="/login">חזרה לכניסה</Link>
        </div>
      ) : (
        <form className="card stack" onSubmit={submit}>
          <label className="field">
            כתובת המייל של החשבון
            <input type="email" dir="ltr" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required />
          </label>
          {error && <div className="error">{error}</div>}
          <button className="btn primary" disabled={busy}>שליחת קישור לאיפוס</button>
        </form>
      )}
    </Shell>
  );
}

const RESET_STATUS: Record<string, string> = {
  used: "הקישור כבר נוצל. אפשר לבקש קישור חדש.",
  expired: "הקישור פג תוקף. אפשר לבקש קישור חדש.",
  invalid: "הקישור אינו תקין. אפשר לבקש קישור חדש.",
};

// Reset page: the token comes from the link. Success does not sign the person in.
export function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [status, setStatus] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!token) { setStatus("invalid"); return; }
    api.get<{ status: string }>(`/api/auth/password/token-status?token=${encodeURIComponent(token)}`)
      .then((r) => setStatus(r.status), () => setStatus("invalid"));
  }, [token]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) { setError("הסיסמאות אינן זהות."); return; }
    setBusy(true);
    setError(null);
    try {
      await api.post("/api/auth/password/reset", { token, password });
      setDone(true);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell title="איפוס סיסמה" back="/login">
      {done ? (
        <div className="card stack" role="status">
          <div>הסיסמה עודכנה. אפשר להתחבר עם הסיסמה החדשה.</div>
          <Link to="/login">כניסה</Link>
        </div>
      ) : status === null ? (
        <div className="muted">בודקים את הקישור…</div>
      ) : status !== "valid" ? (
        <div className="card stack">
          <div className="error">{RESET_STATUS[status] ?? RESET_STATUS.invalid}</div>
          <Link to="/forgot">בקשת קישור חדש</Link>
        </div>
      ) : (
        <form className="card stack" onSubmit={submit}>
          <label className="field">
            סיסמה חדשה (לפחות 8 תווים)
            <input type="password" dir="ltr" autoComplete="new-password" minLength={8} value={password}
                   onChange={(e) => setPassword(e.target.value)} required />
          </label>
          <label className="field">
            אימות סיסמה
            <input type="password" dir="ltr" autoComplete="new-password" minLength={8} value={confirm}
                   onChange={(e) => setConfirm(e.target.value)} required />
          </label>
          {error && <div className="error">{error}</div>}
          <button className="btn primary" disabled={busy}>עדכון סיסמה</button>
        </form>
      )}
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
