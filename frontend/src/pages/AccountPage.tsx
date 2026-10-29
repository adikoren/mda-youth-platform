import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { Shell, errorText } from "../ui";

// My account: contact details and the Google link. Linking asks for the password again (re-authentication).
export function AccountPage() {
  const { me } = useAuth();
  const [params] = useSearchParams();
  const [googleOn, setGoogleOn] = useState(false);
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.get<{ google: boolean }>("/api/auth/capabilities").then((c) => setGoogleOn(c.google), () => setGoogleOn(false));
  }, []);

  async function linkGoogle(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post<{ url: string }>("/api/auth/google/link/start", { password });
      window.location.href = r.url;
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  return (
    <Shell title="החשבון שלי" back="/">
      <section className="card stack">
        <strong>{me?.full_name}</strong>
        <span dir="ltr" className="muted">{me?.email}</span>
      </section>
      {params.get("google") === "linked" && <div className="ok" role="status">חשבון Google קושר בהצלחה.</div>}
      {googleOn && (
        <form className="card stack" onSubmit={linkGoogle}>
          <strong>קישור חשבון Google</strong>
          <span className="muted">כדי לקשר את החשבון, הזינו את הסיסמה שלכם שוב. כתובת המייל ב-Google חייבת להיות זהה לכתובת החשבון.</span>
          <label className="field">
            סיסמה
            <input type="password" dir="ltr" autoComplete="current-password" value={password} minLength={1}
                   onChange={(e) => setPassword(e.target.value)} required />
          </label>
          {error && <div className="error">{error}</div>}
          <button className="btn primary" disabled={busy}>קישור Google</button>
        </form>
      )}
    </Shell>
  );
}
