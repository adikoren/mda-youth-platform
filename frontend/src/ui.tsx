import { useCallback, useEffect, useState, type ReactNode } from "react";
import { NavLink, useNavigate, type NavigateFunction } from "react-router-dom";
import { ApiError } from "./api";
import { isManager, useAuth } from "./auth";
import { LEVEL, fmtTime } from "./he";
import type { Level } from "./api";

export function useLoad<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [failure, setFailure] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const load = useCallback(loader, deps);
  const reload = useCallback(async () => {
    setLoading(true);
    try {
      setData(await load());
      setError(null);
      setFailure(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "אירעה שגיאה");
      setFailure(e);
    } finally {
      setLoading(false);
    }
  }, [load]);
  useEffect(() => {
    reload();
  }, [reload]);
  return { data, error, failure, loading, reload, setData };
}

export function errorText(e: unknown): string {
  return e instanceof ApiError ? e.message : "אירעה שגיאה";
}

// Official MDA logo, supplied by Adi as frontend/public/brand/mda-logo.svg (or .png). Nothing is
// drawn in its place when the file is missing: no substitute logo is ever generated.
const LOGO_SOURCES = ["/brand/mda-logo.svg", "/brand/mda-logo.png"];

export function BrandLogo() {
  const [i, setI] = useState(0);
  if (i >= LOGO_SOURCES.length) return null;
  return <img className="brand-logo" src={LOGO_SOURCES[i]} alt="מגן דוד אדום בישראל" onError={() => setI(i + 1)} />;
}

// Back returns to the previous in-app page when there is one (keeps the date or profile the user came from).
// Opened directly (no previous page in this tab), it goes to the fallback route.
export function goBack(navigate: NavigateFunction, fallback: string) {
  const idx = (window.history.state as { idx?: number } | null)?.idx ?? 0;
  if (idx > 0) navigate(-1);
  else navigate(internalRoute(fallback));
}

// Return destinations are application routes only: "/x" with no protocol or host.
export function internalRoute(path: string): string {
  return /^\/(?!\/)[^\s\\]*$/.test(path) ? path : "/";
}

export function BackButton({ fallback, direct = false }: { fallback: string; direct?: boolean }) {
  const navigate = useNavigate();
  // direct: always go to the fallback route, never by history (used where history would step through filter states)
  return (
    <button type="button" className="back-btn" onClick={() => (direct ? navigate(internalRoute(fallback)) : goBack(navigate, fallback))}>
      <span aria-hidden>→</span> חזרה
    </button>
  );
}

// A time range is always read left to right (07:00–15:00), even inside Hebrew text.
export function TimeRange({ from, to }: { from: string; to: string }) {
  return <span className="time-range" dir="ltr">{fmtTime(from)}–{fmtTime(to)}</span>;
}

// WhatsApp logo in its own green. The only accent outside the red-and-white palette (explicit decision).
export function WhatsAppLink({ phone, name }: { phone: string; name: string }) {
  const digits = phone.replace(/\D/g, "").replace(/^0/, "972");
  return (
    <a className="wa-link" href={`https://wa.me/${digits}`} target="_blank" rel="noreferrer"
       aria-label={`פתיחת שיחת וואטסאפ עם ${name}`} title="וואטסאפ">
      <svg viewBox="0 0 448 512" width="22" height="22" aria-hidden="true" focusable="false">
        <path fill="#25D366" d="M380.9 97.1C339 55.1 283.2 32 223.9 32c-122.4 0-222 99.6-222 222 0 39.1 10.2 77.3 29.6 111L0 480l117.7-30.9c32.4 17.7 68.9 27 106.1 27h.1c122.3 0 224.1-99.6 224.1-222 0-59.3-25.2-115-67.1-157zm-157 341.6c-33.2 0-65.7-8.9-94-25.7l-6.7-4-69.8 18.3L72 359.2l-4.4-7c-18.5-29.4-28.2-63.3-28.2-98.2 0-101.7 82.8-184.5 184.6-184.5 49.3 0 95.6 19.2 130.4 54.1 34.8 34.9 56.2 81.2 56.1 130.5 0 101.8-84.9 184.6-186.6 184.6zm101.2-138.2c-5.5-2.8-32.8-16.2-37.9-18-5.1-1.9-8.8-2.8-12.5 2.8-3.7 5.6-14.3 18-17.6 21.8-3.2 3.7-6.5 4.2-12 1.4-32.6-16.3-54-29.1-75.5-66-5.7-9.8 5.7-9.1 16.3-30.3 1.8-3.7.9-6.9-.5-9.7-1.4-2.8-12.5-30.1-17.1-41.2-4.5-10.8-9.1-9.3-12.5-9.5-3.2-.2-6.9-.2-10.6-.2-3.7 0-9.7 1.4-14.8 6.9-5.1 5.6-19.4 19-19.4 46.3 0 27.3 19.9 53.7 22.6 57.4 2.8 3.7 39.1 59.7 94.8 83.8 35.2 15.2 49 16.5 66.6 13.9 10.7-1.6 32.8-13.4 37.4-26.4 4.6-13 4.6-24.1 3.2-26.4-1.3-2.5-5-3.9-10.5-6.6z"/>
      </svg>
    </a>
  );
}

export function Shell({ title, back, backDirect = false, brand = false, children }: { title: string; back?: string; backDirect?: boolean; brand?: boolean; children: ReactNode }) {
  const { me, logout } = useAuth();
  const out = () => logout().then(() => (location.href = "/login"), (e) => alert(errorText(e)));
  return (
    <div className="app">
      {brand ? (
        <header className="brandbar">
          <BrandLogo />
          <div className="brand-text">
            <strong>מתנדבי נוער</strong>
            <span>מד״א · ראשון לציון</span>
          </div>
          {me && <button className="brand-out" onClick={out}>יציאה</button>}
        </header>
      ) : (
        <header className="topbar">
          {back && <BackButton fallback={back} direct={backDirect} />}
          <h1>{title}</h1>
          {me && <button onClick={out}>יציאה</button>}
        </header>
      )}
      <main className="page">
        {me?.offline && <div className="notice" role="status">אין חיבור לשרת — מוצגים רק נתונים שנשמרו במכשיר. השינויים יסונכרנו כשהחיבור יחזור.</div>}
        {children}
      </main>
      {me?.status === "active" && <BottomNav />}
    </div>
  );
}

function BottomNav() {
  const { me } = useAuth();
  const items: [string, string, string][] = [
    ["/", "בית", "⌂"],
    ["/schedule", "סידור", "▤"],
  ];
  if (me?.level === "trainee") items.push(["/journal", "יומן", "✚"]);
  if (me?.level === "mentor") items.push(["/mentor", "חניכה", "✚"]);
  if (isManager(me) || me?.is_youth_manager) items.push(["/manage", "ניהול", "✎"]);
  if (me?.is_admin) items.push(["/admin", "אדמין", "⚙"]);
  return (
    <div className="bottomnav">
      <nav>
        {items.map(([to, label, ico]) => (
          <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
            <span className="ico" aria-hidden>{ico}</span>
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}

export function LevelBadge({ level }: { level: Level | null }) {
  if (!level) return null;
  return <span className={`badge ${level}`}>{LEVEL[level]}</span>;
}

export function Loading({ error }: { error?: string | null }) {
  return error ? <div className="error">{error}</div> : <div className="muted">טוען…</div>;
}
