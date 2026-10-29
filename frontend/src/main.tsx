import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import { AuthProvider, isManager, useAuth } from "./auth";
import { ForgotPassword, Login, Pending, ResetPassword, Signup } from "./pages/Auth";
import { History, Home, RegisterDay, ScheduleView } from "./pages/Volunteer";
import { ScheduleDaysPage } from "./pages/ScheduleDays";
import { BoardPage, ManageHub, SignupApprovals, TraineeDay, TraineesEntry } from "./pages/manage/Manage";
import { ProfilePage } from "./pages/Profile";
import { AccountPage } from "./pages/AccountPage";
import { JournalPage, MentorHome, NotesPage, NotificationsHelp, OfflineHome } from "./pages/Journal";
import { AdminAudit, AdminCalendar, AdminOutbox, AdminUsers, YouthManager } from "./pages/admin/Admin";

// Old youth-manager addresses now live under the unified management area; day-specific links keep their day.
function RedirectDay({ to }: { to: (weekday: string) => string }) {
  const { weekday = "" } = useParams();
  return <Navigate to={to(weekday)} replace />;
}

function TraineeDayRoute() {
  const { weekday = "" } = useParams();
  const wd = Number(weekday);
  if (!Number.isInteger(wd) || wd < 0 || wd > 6) return <Navigate to="/manage/trainees" replace />;
  return <TraineeDay weekday={wd} />;
}
import { YouthDayPage, YouthVolunteersPage } from "./pages/Youth";
import "./styles.css";

// Route guards only shape navigation; every rule is enforced again by the API.
// Pages that work from data kept on this device when the server is unreachable.
const OFFLINE_PATHS = /^\/(journal|mentor)(\/|$)/;

function Guard({ children, need }: { children: ReactNode; need?: "manager" | "admin" | "youth" | "staff" }) {
  const { me, loading } = useAuth();
  const { pathname } = useLocation();
  if (loading) return <div className="page muted">טוען…</div>;
  if (!me) return <Navigate to="/login" replace />;
  if (me.offline && !OFFLINE_PATHS.test(pathname)) return <OfflineHome />;
  if (me.status !== "active") return <Navigate to="/pending" replace />;
  if (need === "manager" && !isManager(me)) return <Navigate to="/" replace />;
  if (need === "admin" && !me.is_admin) return <Navigate to="/" replace />;
  if (need === "youth" && !(me.is_youth_manager || me.is_admin)) return <Navigate to="/" replace />;
  if (need === "staff" && !(isManager(me) || me.is_youth_manager)) return <Navigate to="/" replace />;
  return <>{children}</>;
}

function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/forgot" element={<ForgotPassword />} />
      <Route path="/reset" element={<ResetPassword />} />
      <Route path="/pending" element={<Pending />} />
      <Route path="/" element={<Guard><Home /></Guard>} />
      <Route path="/register/:dayId" element={<Guard><RegisterDay /></Guard>} />
      <Route path="/schedule" element={<Guard><ScheduleDaysPage /></Guard>} />
      <Route path="/schedule/:dayId" element={<Guard><ScheduleView /></Guard>} />
      <Route path="/history" element={<Guard><History /></Guard>} />
      <Route path="/journal" element={<Guard><JournalPage /></Guard>} />
      <Route path="/journal/:traineeId" element={<Guard><JournalPage /></Guard>} />
      <Route path="/mentor" element={<Guard><MentorHome /></Guard>} />
      <Route path="/notes" element={<Guard><NotesPage /></Guard>} />
      <Route path="/notifications" element={<Guard><NotificationsHelp /></Guard>} />
      <Route path="/people/:userId" element={<Guard><ProfilePage /></Guard>} />
      <Route path="/profile" element={<Guard><AccountPage /></Guard>} />
      <Route path="/manage" element={<Guard need="staff"><ManageHub /></Guard>} />
      <Route path="/manage/day/:weekday" element={<Guard need="youth"><YouthDayPage /></Guard>} />
      <Route path="/manage/day/:weekday/volunteers" element={<Guard need="youth"><YouthVolunteersPage /></Guard>} />
      <Route path="/manage/appointments" element={<Guard need="youth"><YouthManager /></Guard>} />
      <Route path="/manage/trainees" element={<Guard need="staff"><TraineesEntry /></Guard>} />
      <Route path="/manage/trainees/day/:weekday" element={<Guard need="staff"><TraineeDayRoute /></Guard>} />
      <Route path="/manage/signups" element={<Guard need="manager"><SignupApprovals /></Guard>} />
      <Route path="/manage/:dayId" element={<Guard need="manager"><BoardPage /></Guard>} />
      <Route path="/youth" element={<Navigate to="/manage" replace />} />
      <Route path="/youth/managers" element={<Navigate to="/manage" replace />} />
      <Route path="/youth/day/:weekday" element={<RedirectDay to={(w) => `/manage/day/${w}`} />} />
      <Route path="/youth/day/:weekday/volunteers" element={<RedirectDay to={(w) => `/manage/day/${w}/volunteers`} />} />
      <Route path="/admin" element={<Guard need="admin"><AdminUsers /></Guard>} />
      <Route path="/admin/calendar" element={<Guard need="admin"><AdminCalendar /></Guard>} />
      <Route path="/admin/outbox" element={<Guard need="admin"><AdminOutbox /></Guard>} />
      <Route path="/admin/audit" element={<Guard need="admin"><AdminAudit /></Guard>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

// Offline reopening: a small service worker keeps the app shell (never API data) on the device.
// Production builds only; the Vite dev server serves source modules that should not be cached.
// Registered in every mode: Web Push needs the worker, including on localhost during development.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => undefined));
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
