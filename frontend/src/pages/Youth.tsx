// "אחראי נוער": the Youth Manager's area (decision of 2026-10-17). Weekday grid, a selected-day page, and the
// selected day's volunteer list with role filters. Destinations are scoped to the selected day.
import { Link, Navigate, useLocation, useParams, useSearchParams } from "react-router-dom";
import { api, type Level } from "../api";
import { useAuth } from "../auth";
import { WEEKDAYS, fmtDateTime } from "../he";
import { LevelBadge, Loading, Shell, useLoad } from "../ui";

const DAY_LABEL = (wd: number) => (wd === 6 ? "שבת" : `יום ${WEEKDAYS[wd]}`);
const FILTERS: { key: string; label: string; level: Level | null }[] = [
  { key: "all", label: "הכול", level: null },
  { key: "mentor", label: "חונך", level: "mentor" },
  { key: "youth", label: "נוער", level: "youth" },
  { key: "trainee", label: "חניך", level: "trainee" },
];

// Selected-day page: the day is the navigation context for everything below it. Youth managers and admin only.
export function YouthDayPage() {
  const { weekday } = useParams();
  const { me } = useAuth();
  const wd = Number(weekday);
  if (!(me?.is_admin || me?.is_youth_manager)) return <Navigate to="/manage" replace />;
  if (!Number.isInteger(wd) || wd < 0 || wd > 6) return <Shell title="ניהול" back="/manage"><div className="card muted">היום לא נמצא.</div></Shell>;
  return (
    <Shell title={DAY_LABEL(wd)} back="/manage" backDirect>
      <h2>{DAY_LABEL(wd)}</h2>
      <nav className="hub-grid" aria-label={DAY_LABEL(wd)}>
        <Link className="hub-card" to={`/manage/day/${wd}/volunteers`}>
          <strong>מתנדבים</strong>
          <span className="muted">רשימת המתנדבים של היום</span>
        </Link>
        <Link className="hub-card" to={`/schedule?weekday=${wd}`}>
          <strong>ניהול משמרת</strong>
          <span className="muted">הסידורים של היום</span>
        </Link>
      </nav>
    </Shell>
  );
}

interface VolunteerEntry {
  user_id: string;
  full_name: string;
  level: Level | null;
  home_member: boolean;        // a volunteer of this home day as well
  temporary_until?: string | null; // a temporary appointment that also covers this day (pinned manager)
}
interface DayVolunteers {
  weekday: number;
  weekday_name: string;
  day_managers: VolunteerEntry[];
  temporary_managers: (VolunteerEntry & { ends: string })[];
  volunteers: VolunteerEntry[];
}

// Each user ID once. The server already guarantees this; the page keeps the same rule so a repeated row can never render.
function uniqueById<T extends { user_id: string }>(rows: T[]): T[] {
  const seen = new Set<string>();
  return rows.filter((r) => (seen.has(r.user_id) ? false : (seen.add(r.user_id), true)));
}

// The selected day's volunteers. The day manager is pinned first and is never filtered out; the filter applies
// to the rest. The filter is kept in the address, so going back to this page restores it.
export function YouthVolunteersPage() {
  const { weekday } = useParams();
  const { me } = useAuth();
  const wd = Number(weekday);
  const [params, setParams] = useSearchParams();
  // Filter changes replace the current entry: "חזרה" never steps back through earlier filters.
  const role = params.get("role") ?? "all";
  const location = useLocation();
  const here = location.pathname + location.search;
  const data = useLoad(() => api.get<DayVolunteers>(`/api/youth/days/${wd}/volunteers`), [wd]);
  const filter = FILTERS.find((f) => f.key === role) ?? FILTERS[0];
  const title = `${DAY_LABEL(wd)} · מתנדבים`;
  return (
    <Shell title={title} back={`/manage/day/${wd}`} backDirect>
      <h2>{title}</h2>
      {!data.data && <Loading error={data.error} />}
      {data.data && (
        <>
          <section className="card stack" aria-labelledby="pinned-mgr">
            <div className="row spread">
              <strong id="pinned-mgr">אחראי/ת יום</strong>
              {(me?.is_admin || me?.is_youth_manager) && (
                <Link className="btn small" to={`/manage/appointments?weekday=${wd}`}>ניהול אחראי יום</Link>
              )}
            </div>
            {data.data.day_managers.length === 0 && <span className="muted">לא הוגדר אחראי/ת יום ליום זה.</span>}
            {uniqueById(data.data.day_managers).map((m) => (
              <div key={m.user_id} className="person">
                <Link className="name person-link" to={`/people/${m.user_id}`}>{m.full_name}</Link>
                <LevelBadge level={m.level} />
                <span className="badge">אחראי/ת יום</span>
                {m.temporary_until && <span className="badge">אחראי/ת זמני/ת · עד {fmtDateTime(m.temporary_until)}</span>}
                {m.home_member && <span className="badge">מתנדב/ת ביום הבית</span>}
              </div>
            ))}
            {uniqueById(data.data.temporary_managers).length > 0 && (
              <div className="stack">
                {uniqueById(data.data.temporary_managers).map((t) => (
                  <div key={t.user_id} className="person">
                    <Link className="name person-link" to={`/people/${t.user_id}`}>{t.full_name}</Link>
                    <LevelBadge level={t.level} />
                    <span className="badge">אחראי/ת זמני/ת · עד {fmtDateTime(t.ends)}</span>
                    {t.home_member && <span className="badge">מתנדב/ת ביום הבית</span>}
                  </div>
                ))}
              </div>
            )}
          </section>

          <div className="chip-row" role="group" aria-label="סינון לפי תפקיד">
            {FILTERS.map((f) => (
              <button key={f.key} type="button" className="chip" aria-pressed={f.key === filter.key}
                      onClick={() => setParams(f.key === "all" ? {} : { role: f.key }, { replace: true })}>
                {f.label}
              </button>
            ))}
          </div>

          <section className="stack" aria-label="מתנדבים">
            {uniqueById(data.data.volunteers)
              .filter((v) => filter.level === null || v.level === filter.level)
              .map((v) => (
                <div key={v.user_id} className="person">
                  {v.level === "trainee" ? (
                    // Trainees open the integrated trainee page; "חזרה" returns here with this filter.
                    <Link className="name person-link" to={`/journal/${v.user_id}?from=${encodeURIComponent(here)}`}>{v.full_name}</Link>
                  ) : (
                    <Link className="name person-link" to={`/people/${v.user_id}`}>{v.full_name}</Link>
                  )}
                  <LevelBadge level={v.level} />
                </div>
              ))}
            {uniqueById(data.data.volunteers).filter((v) => filter.level === null || v.level === filter.level).length === 0 && (
              <span className="muted">אין מתנדבים בסינון הזה.</span>
            )}
          </section>
        </>
      )}
    </Shell>
  );
}
