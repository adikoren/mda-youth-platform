// "סידור": one date list for everyone. Volunteers see published schedules they may view (server rules, unchanged).
// Managers see their own dates, including unpublished ones, and get the management actions inside each row.
// Compact accordion rows, collapsed by default; upcoming week first, history below (collapsed).
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, type DaySummary } from "../api";
import { isManager, useAuth } from "../auth";
import { DAY_KIND, WEEKDAYS, WINDOW, dayTitle, fmtDateTime } from "../he";
import { Loading, Shell, useLoad } from "../ui";

export interface ScheduleRow extends DaySummary {
  section: "main" | "history";
  is_published: boolean;
  draft_dirty?: boolean;
  attention_count?: number;
}

function DayRow({ d, manager }: { d: ScheduleRow; manager: boolean }) {
  const [open, setOpen] = useState(false);
  const panelId = `day-panel-${d.id}`;
  return (
    <div className={`day-row${open ? " open" : ""}`}>
      <button type="button" className="day-row-head" aria-expanded={open} aria-controls={panelId}
              onClick={() => setOpen(!open)}>
        <strong className="day-row-title">{dayTitle(d)}</strong>
        <span className="day-row-status">
          <span className={`badge${d.is_published ? " pub" : ""}`}>{d.is_published ? "פורסם" : "טרם פורסם"}</span>
          {manager && d.draft_dirty && <span className="badge late">שינויים לא פורסמו</span>}
          {manager && !!d.attention_count && <span className="badge red">נדרש טיפול: {d.attention_count}</span>}
        </span>
        <span className="day-row-chev" aria-hidden>{open ? "▴" : "▾"}</span>
      </button>
      {open && (
        <div className="day-row-panel" id={panelId}>
          {d.kind !== "regular" && <div className="muted">{DAY_KIND[d.kind]}</div>}
          {manager && (
            <>
              <div className="muted">{WINDOW[d.window]}</div>
              <div className="muted">יעד פרסום: {fmtDateTime(d.publish_target_at)}</div>
            </>
          )}
          <div className="row">
            {d.is_published && <Link className="btn small" to={`/schedule/${d.id}`}>צפייה בסידור</Link>}
            {manager && <Link className="btn small primary" to={`/manage/${d.id}`}>ניהול הסידור</Link>}
          </div>
        </div>
      )}
    </div>
  );
}

export function ScheduleDaysPage() {
  const { me } = useAuth();
  const manager = isManager(me);
  // ?weekday=N scopes the list to one weekday (entered from a management hub or a youth manager's day page).
  const [params] = useSearchParams();
  const weekdayParam = params.get("weekday");
  const weekday = weekdayParam !== null && /^[0-6]$/.test(weekdayParam) ? Number(weekdayParam) : null;
  const days = useLoad(async (): Promise<ScheduleRow[]> => {
    if (manager) {
      return api.get<ScheduleRow[]>("/api/manage/days");
    }
    const r = await api.get<{ items: (DaySummary & { historical: boolean })[] }>("/api/schedule");
    return r.items.map((d) => ({ ...d, section: d.historical ? "history" : "main", is_published: true }));
  }, [manager]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const inScope = (d: ScheduleRow) => weekday === null || d.weekday === weekday;
  const main = (days.data ?? []).filter((d) => d.section === "main" && inScope(d)).sort((a, b) => a.date.localeCompare(b.date));
  const history = (days.data ?? []).filter((d) => d.section === "history" && inScope(d)).sort((a, b) => b.date.localeCompare(a.date));
  return (
    <Shell title={weekday === null ? "סידור" : `${weekday === 6 ? "שבת" : `יום ${WEEKDAYS[weekday]}`} · ניהול משמרת`}
           back={weekday === null ? undefined : (manager ? "/manage" : `/youth/day/${weekday}`)}>
      {!days.data && <Loading error={days.error} />}
      {days.data && main.length === 0 && history.length === 0 && (
        <div className="card muted">
          {manager ? "אין ימים בניהולך בשבעת הימים הקרובים." : "אין סידורים מפורסמים שזמינים לך כרגע."}
        </div>
      )}
      {main.length > 0 && <div className="day-list">{main.map((d) => <DayRow key={d.id} d={d} manager={manager} />)}</div>}
      {history.length > 0 && (
        <section className="section">
          <button type="button" className="day-row-head history-toggle" aria-expanded={historyOpen}
                  aria-controls="history-panel" onClick={() => setHistoryOpen(!historyOpen)}>
            <strong className="day-row-title">היסטוריית סידורים ({history.length})</strong>
            <span className="day-row-chev" aria-hidden>{historyOpen ? "▴" : "▾"}</span>
          </button>
          {historyOpen && (
            <div className="day-list" id="history-panel">
              {history.map((d) => <DayRow key={d.id} d={d} manager={manager} />)}
            </div>
          )}
        </section>
      )}
    </Shell>
  );
}
