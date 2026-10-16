import { useCallback, useEffect, useState, type ReactNode } from "react";
import { NavLink, Link } from "react-router-dom";
import { ApiError } from "./api";
import { isManager, useAuth } from "./auth";
import { LEVEL } from "./he";
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

export function Shell({ title, back, brand = false, children }: { title: string; back?: string; brand?: boolean; children: ReactNode }) {
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
          {back && <Link to={back} aria-label="חזרה">→</Link>}
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
  if (isManager(me)) items.push(["/manage", "ניהול", "✎"]);
  if (me?.is_youth_manager || me?.is_admin) items.push(["/youth", "אחראי נוער", "★"]);
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
