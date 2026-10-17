// "אחראי נוער": the Youth Manager's area (decision of 2026-10-17). Weekday grid, a selected-day page, and the
// selected day's volunteer list with role filters. Destinations are scoped to the selected day.
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, type Level } from "../api";
import { WEEKDAYS } from "../he";
import { LevelBadge, Loading, Shell, useLoad } from "../ui";

const DAY_LABEL = (wd: number) => (wd === 6 ? "שבת" : `יום ${WEEKDAYS[wd]}`);
const FILTERS: { key: string; label: string; level: Level | null }[] = [
  { key: "all", label: "הכול", level: null },
  { key: "mentor", label: "חונך", level: "mentor" },
  { key: "youth", label: "נוער", level: "youth" },
  { key: "trainee", label: "חניך", level: "trainee" },
];

// Week grid: Sunday to Friday in two columns; Saturday and the future modules span the full width.
export function YouthHub() {
  return (
    <Shell title="אחראי נוער">
      <nav className="youth-grid" aria-label="ימים">
        {[0, 1, 2, 3, 4, 5].map((wd) => (
          <Link key={wd} className="hub-card" to={`/youth/day/${wd}`}>
            <strong>{WEEKDAYS[wd]}</strong>
          </Link>
        ))}
        <Link className="hub-card wide" to="/youth/day/6">
          <strong>שבת</strong>
        </Link>
        {["אבטחות", "אט״ן", "קורס 60"].map((label) => (
          <button key={label} type="button" className="hub-card wide" disabled>
            <strong>{label}</strong>
            <span className="muted">בקרוב</span>
          </button>
        ))}
        <Link className="hub-card wide" to="/youth/managers">
          <strong>אחראי יום</strong>
          <span className="muted">מינוי והחלפה של אחראי יום</span>
        </Link>
        <Link className="hub-card wide" to="/manage/trainees">
          <strong>התקדמות חניכים</strong>
        </Link>
      </nav>
    </Shell>
  );
}

// Selected-day page: the day is the navigation context for everything below it.
export function YouthDayPage() {
  const { weekday } = useParams();
  const wd = Number(weekday);
  if (!Number.isInteger(wd) || wd < 0 || wd > 6) return <Shell title="אחראי נוער" back="/youth"><div className="card muted">היום לא נמצא.</div></Shell>;
  return (
    <Shell title={DAY_LABEL(wd)} back="/youth">
      <h2>{DAY_LABEL(wd)}</h2>
      <nav className="hub-grid" aria-label={DAY_LABEL(wd)}>
        <Link className="hub-card" to={`/youth/day/${wd}/volunteers`}>
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

interface DayVolunteers {
  weekday: number;
  weekday_name: string;
  day_managers: { user_id: string; full_name: string; level: Level | null }[];
  volunteers: { user_id: string; full_name: string; level: Level | null }[];
}

// The selected day's volunteers. The day manager is pinned first and is never filtered out; the filter applies
// to the rest. The filter is kept in the address, so going back to this page restores it.
export function YouthVolunteersPage() {
  const { weekday } = useParams();
  const wd = Number(weekday);
  const [params, setParams] = useSearchParams();
  const role = params.get("role") ?? "all";
  const data = useLoad(() => api.get<DayVolunteers>(`/api/youth/days/${wd}/volunteers`), [wd]);
  const filter = FILTERS.find((f) => f.key === role) ?? FILTERS[0];
  const title = `${DAY_LABEL(wd)} · מתנדבים`;
  return (
    <Shell title={title} back={`/youth/day/${wd}`}>
      <h2>{title}</h2>
      {!data.data && <Loading error={data.error} />}
      {data.data && (
        <>
          <section className="card stack" aria-labelledby="pinned-mgr">
            <strong id="pinned-mgr">אחראי/ת יום</strong>
            {data.data.day_managers.length === 0 && <span className="muted">לא הוגדר אחראי/ת יום ליום זה.</span>}
            {data.data.day_managers.map((m) => (
              <div key={m.user_id} className="person">
                <Link className="name person-link" to={`/people/${m.user_id}`}>{m.full_name}</Link>
                <LevelBadge level={m.level} />
                <span className="badge">אחראי/ת יום</span>
              </div>
            ))}
          </section>

          <div className="chip-row" role="group" aria-label="סינון לפי תפקיד">
            {FILTERS.map((f) => (
              <button key={f.key} type="button" className="chip" aria-pressed={f.key === filter.key}
                      onClick={() => setParams(f.key === "all" ? {} : { role: f.key })}>
                {f.label}
              </button>
            ))}
          </div>

          <section className="stack" aria-label="מתנדבים">
            {data.data.volunteers
              .filter((v) => filter.level === null || v.level === filter.level)
              .map((v) => (
                <div key={v.user_id} className="person">
                  <Link className="name person-link" to={`/people/${v.user_id}`}>{v.full_name}</Link>
                  <LevelBadge level={v.level} />
                </div>
              ))}
            {data.data.volunteers.filter((v) => filter.level === null || v.level === filter.level).length === 0 && (
              <span className="muted">אין מתנדבים בסינון הזה.</span>
            )}
          </section>
        </>
      )}
    </Shell>
  );
}
