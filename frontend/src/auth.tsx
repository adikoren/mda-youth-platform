import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, isUnreachable, type Me } from "./api";
import { snapshots } from "./offline";

interface AuthState {
  me: Me | null;
  loading: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

const Ctx = createContext<AuthState>({ me: null, loading: true, refresh: async () => {}, logout: async () => {} });

// Offline reopening (D-44): the last confirmed identity is remembered on this device so the app can
// open without a connection and show that person's local drafts. It grants nothing on the server;
// every sync is re-authorised there. It expires with the 30-day session inactivity limit and is
// removed on logout or when the server says the session is gone (401).
const LAST_ME = "mda-last-me";
const MAX_AGE_MS = 30 * 24 * 3600 * 1000;

function rememberMe(me: Me) {
  try { localStorage.setItem(LAST_ME, JSON.stringify({ me, saved_at: Date.now() })); } catch { /* storage unavailable */ }
}

function forgetMe() {
  try { localStorage.removeItem(LAST_ME); } catch { /* storage unavailable */ }
}

function lastMe(): Me | null {
  try {
    const raw = localStorage.getItem(LAST_ME);
    if (!raw) return null;
    const { me, saved_at } = JSON.parse(raw) as { me: Me; saved_at: number };
    if (Date.now() - saved_at > MAX_AGE_MS || me.status !== "active") return null;
    return { ...me, offline: true };
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const fresh = await api.get<Me>("/api/auth/me");
      setMe(fresh);
      rememberMe(fresh);
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        setMe(null);
        forgetMe();
      } else if (isUnreachable(e)) {
        setMe(lastMe());
      } else throw e;
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    // Needs the server: an offline "logout" could not revoke the session cookie.
    await api.post("/api/auth/logout");
    if (me) await snapshots.clear(me.id).catch(() => undefined); // drafts (unsent work) are kept
    forgetMe();
    setMe(null);
  }, [me]);

  useEffect(() => {
    refresh().catch(() => setLoading(false));
    const back = () => refresh().catch(() => undefined);
    window.addEventListener("online", back);
    return () => window.removeEventListener("online", back);
  }, [refresh]);

  // While running from the device copy, check the server again every 30 s.
  useEffect(() => {
    if (!me?.offline) return;
    const t = setInterval(() => refresh().catch(() => undefined), 30000);
    return () => clearInterval(t);
  }, [me?.offline, refresh]);

  return <Ctx.Provider value={{ me, loading, refresh, logout }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);

export function isManager(me: Me | null): boolean {
  return !!me && (me.is_admin || me.managed_weekdays.length > 0);
}
