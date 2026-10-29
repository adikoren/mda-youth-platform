// Time-dependent state for scenarios (windows that open or close, a shift that has started). The connection is
// fixed to the disposable e2e database (DB below); the development database is never addressed from here.
import { execFileSync } from "node:child_process";

const DB = "mda_e2e";
const URL = `postgresql://mda@127.0.0.1:5433/${DB}`;

export function sql(query: string): string {
  return execFileSync("psql", [URL, "-tA", "-v", "ON_ERROR_STOP=1", "-c", query], { encoding: "utf8" }).trim();
}

// Opens the registration window of a date on the e2e database: open now, or closed now.
export function setWindow(localDate: string, state: "open" | "closed") {
  const opens = state === "open" ? "now() - interval '2 hours'" : "now() - interval '5 days'";
  const closes = state === "open" ? "now() + interval '3 days'" : "now() - interval '1 hour'";
  sql(`update activity_days set registration_opens_at = ${opens}, registration_closes_at = ${closes} where local_date = '${localDate}'`);
}

export function firstTraineeEmailOnWeekday(weekday: number): string {
  return sql(`select u.email from users u join day_memberships d on d.user_id = u.id and d.valid_to is null
              join volunteer_level_history l on l.user_id = u.id and l.valid_to is null and l.level = 'trainee'
              where d.weekday = ${weekday} and u.status = 'active' order by u.email limit 1`);
}

export function traineeIdByEmail(email: string): string {
  return sql(`select id from users where email = '${email.replace(/'/g, "''")}'`);
}

export function dayIdByDate(localDate: string): string {
  return sql(`select id from activity_days where local_date = '${localDate}'`);
}

// The first future Thursday with registrations that is not one of the dates other scenarios use. The dataset's
// date window moves with the calendar, so a fixed date can end up without registrants.
export function thursdayWithRegistrations(excluded: string[]): string {
  const ex = excluded.map((d) => `'${d}'`).join(",");
  const date = sql(`select d.local_date from activity_days d join registrations r on r.activity_day_id = d.id
                     where extract(dow from d.local_date) = 4 and d.local_date > current_date
                       and d.local_date not in (${ex})
                     group by d.local_date order by d.local_date limit 1`);
  if (!date) throw new Error("no Thursday with registrations in the e2e database");
  return date;
}
