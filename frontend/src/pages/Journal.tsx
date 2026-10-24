import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { api, isUnreachable, type ShiftKind } from "../api";
import { useAuth } from "../auth";
import { SHIFT, WEEKDAYS, dayTitle, fmtDateTime } from "../he";
import { GROUPS, fromFields, threeWayMerge, toFields, type Values } from "../merge";
import { currentPushState, disablePush, enablePush, sendSelfTest } from "../push";
import type { PushState } from "../pushUtil";
import {
  drafts, edits as editStore, keys, newRideId, rememberJournal, resolveDraft, retryDraft, snapshots, syncDrafts,
  type Draft, type Edit, type Snapshot,
} from "../offline";
import { Loading, Shell, errorText, internalRoute, useLoad } from "../ui";

// Where "חזרה" goes from a trainee page: the list the person came from, with its day, search and filter in the URL.
// Only the management list routes are accepted; anything else falls back to the default list.
export function originReturn(from: string | null, fallback: string): string {
  if (!from) return internalRoute(fallback);
  const path = from; // already decoded once by the URL parameter reader
  const allowed = /^\/manage\/(trainees(\/day\/[0-6])?|day\/[0-6]\/volunteers)(\?[^#]*)?$/.test(path);
  return allowed ? path : internalRoute(fallback);
}

// Latest first: occurrence date, then shift (evening before morning on the same day), then ride number.
// A ride not yet numbered on the server (a new local record) counts as the newest of its shift.
const KIND_RANK: Record<string, number> = { evening: 0, reinforcement: 1, morning: 2 };
function sortRidesLatestFirst<T extends { date: string; seq: number | null; kind: string }>(rs: T[]): T[] {
  return [...rs].sort((a, b) =>
    b.date.localeCompare(a.date) || (KIND_RANK[a.kind] ?? 9) - (KIND_RANK[b.kind] ?? 9) || (b.seq ?? 1e15) - (a.seq ?? 1e15));
}
function shortDate(d: string): string {
  if (!d) return "—";
  const [y, m, day] = d.split("-");
  return `${day}/${m}/${y.slice(2)}`;
}
function groupByShift<T extends { shift_id: string; kind: string; date: string }>(rs: T[]) {
  const groups: { key: string; label: string; rides: T[] }[] = [];
  for (const r of rs) {
    let g = groups.find((x) => x.key === r.shift_id);
    if (!g) {
      g = { key: r.shift_id, label: `משמרת ${shortDate(r.date)} · ${SHIFT[r.kind as ShiftKind]}`, rides: [] };
      groups.push(g);
    }
    g.rides.push(r);
  }
  return groups;
}

// ---- labels ----
export const MARKERS: [string, string][] = [
  ["blood_pressure", "לחץ דם"],
  ["pulse", "דופק"],
  ["breathing", "נשימות"],
  ["forms", "טפסים"],
];
export const MARKER_VALUES: [string, string][] = [
  ["success", "✓ הצלחה"],
  ["failure", "✗ כישלון"],
  ["not_performed", "חסר (לא בוצע)"],
  ["partial", "ויקס (הצלחה חלקית)"],
];
const VALUE_LABEL = Object.fromEntries(MARKER_VALUES);
const MARKER_LABEL = Object.fromEntries(MARKERS);

interface Assessment { markers: Record<string, string | null>; summary: string | null; version: number }
interface RideRow {
  id: string;
  seq: number | null;
  date: string;
  weekday: number;
  kind: ShiftKind;
  shift_id: string;
  description: string | null;
  description_version: number;
  awaiting_description: boolean;
  assessment: Assessment | null;
  assessment_complete: boolean;
  contributors: string[];
  can_assess: boolean;
  can_edit_description: boolean;
  cancelled?: { at: string; reason: string; duplicate_of_seq: number | null } | null;
  counted?: boolean;
  localOnly?: boolean;
}
interface Summary { strengths: string | null; strengths_none: boolean; improvements: string | null; improvements_none: boolean; version: number }
interface ShiftRow {
  shift_id: string;
  kind: ShiftKind;
  date: string;
  weekday: number;
  summary: Summary | null;
  can_mentor: boolean;
  mentors: { id: string; full_name: string }[];
  completeness: { mentor_required: boolean; trainee_missing: number; mentor_missing_rides: number; summary_missing: boolean; complete: boolean };
}
interface Journal {
  trainee: { id: string; full_name: string };
  progress: { ride_count: number; last_ride_date: string | null };
  rides: RideRow[];
  shifts: ShiftRow[];
  access: { is_self: boolean; is_admin: boolean; is_home_manager: boolean; mentor_shift_ids: string[]; can_manage: boolean;
            mentor_access_until?: Record<string, string> };
  documentable_shifts?: { shift_id: string; kind: ShiftKind; date: string; weekday: number }[];
}

type SaveFn = (d: Omit<Draft, "updated_at" | "state">) => Promise<void>;
interface DraftActions {
  /** Keys being sent right now / confirmed by the server in this page session. */
  sending: Set<string>;
  confirmed: Set<string>;
  onSave: SaveFn;
  onDiscard: (key: string) => void;
  onResolve: (key: string, payload: Record<string, unknown>) => void;
  onRetry: (key: string) => void;
  /** Local, unsaved edits (kept on the device while typing; sent only on Save). */
  edits: Map<string, Edit>;
  onEdit: (key: string, payload: Record<string, unknown> | null) => void;
  onDiscardRide: (rideId: string) => void;
}

/** Keeps what is typed on the device (debounced) until Save; `null` when it equals the saved value. */
function useLocalEdit(actions: DraftActions, key: string, value: unknown, saved: unknown, enabled = true) {
  const first = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (first.current) { first.current = false; return; } // nothing typed yet on mount
    if (!enabled) return;
    timer.current = setTimeout(() => {
      const same = JSON.stringify(value) === JSON.stringify(saved);
      actions.onEdit(key, same ? null : (value as Record<string, unknown>));
    }, 400);
    return () => { if (timer.current) clearTimeout(timer.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(value)]);
  // Save calls this first, so a pending local write can never land after the save.
  return () => { if (timer.current) clearTimeout(timer.current); };
}

function UnsavedBadge({ k, actions, onRevert }: { k: string; actions: DraftActions; onRevert: () => void }) {
  if (!actions.edits.has(k)) return null;
  return (
    <span className="row">
      <span className="badge late" role="status">שינויים שלא נשמרו — שמורים במכשיר בלבד</span>
      <button className="btn small ghost" onClick={() => { actions.onEdit(k, null); onRevert(); }}>ביטול השינויים</button>
    </span>
  );
}

function useDrafts(userId: string | undefined) {
  const [list, setList] = useState<Draft[]>([]);
  const [edits, setEdits] = useState<Map<string, Edit>>(new Map());
  const refresh = useCallback(async () => {
    if (!userId) return;
    setList(await drafts.all(userId));
    setEdits(new Map((await editStore.all(userId)).map((e) => [e.key, e])));
  }, [userId]);
  useEffect(() => {
    refresh();
  }, [refresh]);
  return { list, edits, refresh };
}

export function JournalPage() {
  const { traineeId } = useParams();
  const { me } = useAuth();
  const url = traineeId ? `/api/journal/trainees/${traineeId}` : "/api/journal/me";
  const j = useLoad(() => api.get<Journal>(url), [url]);
  const d = useDrafts(me?.id);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [needsLogin, setNeedsLogin] = useState(false);
  const [sending, setSending] = useState<Set<string>>(new Set());
  const [confirmed, setConfirmed] = useState<Set<string>>(new Set());
  const [copy, setCopy] = useState<Snapshot<Journal> | null | undefined>(undefined);
  // One ride at a time: the selected ride id (null = latest on entry). Adding a ride is guarded against repeats.
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  const [params] = useSearchParams();
  const unreachable = isUnreachable(j.failure);
  const copyKey = traineeId ?? me?.id;

  // Keep a bounded offline copy of what this person may document (D-44).
  useEffect(() => {
    if (me && j.data) rememberJournal(me.id, j.data).catch(() => undefined);
  }, [me, j.data]);
  useEffect(() => {
    if (me && unreachable && copyKey) snapshots.get<Journal>(me.id, copyKey).then(setCopy, () => setCopy(null));
  }, [me, unreachable, copyKey]);

  const sync = useCallback(async (retryDenied = false) => {
    if (!me) return;
    const r = await syncDrafts(me.id, retryDenied);
    await d.refresh();
    setNeedsLogin(r.needsLogin);
    if (r.confirmed.length) setConfirmed((prev) => new Set([...prev, ...r.confirmed]));
    if (r.done > 0) setSyncMsg(`סונכרנו ${r.done} טיוטות לשרת`);
    if (r.done > 0 || (unreachable && !r.offline)) j.reload();
  }, [me, d, j, unreachable]);

  useEffect(() => {
    sync(true); // on open, also retry drafts refused for reasons that may have changed (access reopened)
    const onOnline = () => sync(true);
    window.addEventListener("online", onOnline);
    const timer = setInterval(() => sync(), 30000);
    return () => { window.removeEventListener("online", onOnline); clearInterval(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me?.id]);

  const data = j.data ?? (unreachable ? copy?.data : undefined) ?? null;
  const traineeKey = data?.trainee.id ?? traineeId ?? me?.id ?? "";
  const myDrafts = useMemo(() => d.list.filter((x) => x.trainee_id === traineeKey), [d.list, traineeKey]);

  async function save(draft: Omit<Draft, "updated_at" | "state">) {
    if (!me) return;
    setConfirmed((prev) => { const n = new Set(prev); n.delete(draft.key); return n; });
    setSending((prev) => new Set([...prev, draft.key]));
    // A ride not yet on the server (still local, or queued): the description travels with the ride
    // itself; saving any other part queues the ride too. Only Save ever queues anything.
    const rideDraft = d.list.find((x) => x.key === keys.ride(draft.ride_id ?? ""));
    const unsentRide = rideDraft && (rideDraft.state === "pending" || rideDraft.state === "local");
    if (draft.kind === "description" && unsentRide) {
      await drafts.put(me.id, { ...rideDraft, payload: { ...rideDraft.payload, ...draft.payload }, state: "pending", updated_at: Date.now() });
    } else {
      if (unsentRide && rideDraft.state === "local") await drafts.put(me.id, { ...rideDraft, state: "pending", updated_at: Date.now() });
      await drafts.put(me.id, { ...draft, updated_at: Date.now(), state: "pending" });
    }
    await editStore.remove(me.id, draft.key);
    await d.refresh();
    try { await sync(); } finally {
      setSending((prev) => { const n = new Set(prev); n.delete(draft.key); return n; });
    }
  }
  const actions: DraftActions = {
    sending, confirmed,
    onSave: save,
    onDiscard: async (k) => { await drafts.remove(me!.id, k); d.refresh(); },
    onResolve: async (k, payload) => { await resolveDraft(me!.id, k, payload); await d.refresh(); await sync(); },
    onRetry: async (k) => { await retryDraft(me!.id, k); await d.refresh(); await sync(); },
    edits: d.edits,
    onEdit: async (k, payload) => {
      if (!me) return;
      if (payload) await editStore.put(me.id, { key: k, payload, updated_at: Date.now() });
      else await editStore.remove(me.id, k);
      await d.refresh();
    },
    onDiscardRide: async (rideId) => {
      if (!me) return;
      for (const x of d.list.filter((y) => y.ride_id === rideId)) await drafts.remove(me.id, x.key);
      for (const k of [keys.description(rideId), keys.assessment(rideId)]) await editStore.remove(me.id, k);
      await d.refresh();
    },
  };

  if (!data) {
    const pending = d.list.filter((x) => x.state === "pending").length;
    return (
      <Shell title="יומן נסיעות" back={traineeId ? "/mentor" : "/"}>
        {unreachable && copy === null ? (
          <div className="notice">אין חיבור לשרת, ואין במכשיר עותק של היומן הזה (או שהגישה אליו הסתיימה).
            {pending > 0 && ` ${pending} טיוטות שמורות במכשיר ויסונכרנו כשהחיבור יחזור.`}</div>
        ) : <Loading error={unreachable ? null : j.error} />}
      </Shell>
    );
  }

  // Rides created on this device that the server has not confirmed yet.
  const localRides: RideRow[] = myDrafts.filter((x) => x.kind === "ride" && !data.rides.some((r) => r.id === x.ride_id))
    .map((x) => {
      const sh = data.shifts.find((s) => s.shift_id === x.shift_id) ?? data.documentable_shifts?.find((s) => s.shift_id === x.shift_id);
      return {
        id: x.ride_id!, seq: null, date: sh?.date ?? "", weekday: sh?.weekday ?? 0, kind: (sh?.kind ?? "evening") as ShiftKind,
        shift_id: x.shift_id, description: (x.payload.description as string) ?? null, description_version: 0,
        awaiting_description: !x.payload.description, assessment: null, assessment_complete: false, contributors: [],
        can_assess: data.access.mentor_shift_ids.includes(x.shift_id) || data.access.is_admin,
        can_edit_description: data.access.is_self || data.access.is_admin, localOnly: true,
      };
    });
  const rides = [...localRides, ...data.rides];
  const pendingCount = myDrafts.filter((x) => x.state === "pending").length;
  const unsavedCount = myDrafts.filter((x) => x.state === "local").length
    + [...d.edits.keys()].filter((k) => rides.some((r) => k.includes(r.id)) || k.includes(data.trainee.id)).length;
  const isSelf = data.access.is_self;
  const fromDevice = !j.data && copy;

  async function addRide(shiftId: string) {
    if (adding) return; // a repeated click must not create a second record
    setAdding(true);
    try {
      // Only a local record: nothing is sent or counted until the person saves content (D-50).
      const id = newRideId();
      await drafts.put(me!.id, { key: keys.ride(id), kind: "ride", ride_id: id, trainee_id: data!.trainee.id, shift_id: shiftId,
                                 payload: {}, base_version: 0, state: "local", updated_at: Date.now() });
      await d.refresh();
      setSelectedId(id); // the new ride is shown straight away
      setAddOpen(false);
    } finally {
      setAdding(false);
    }
  }

  const ridesSorted = sortRidesLatestFirst(rides);
  const selected = ridesSorted.find((r) => r.id === selectedId) ?? ridesSorted[0] ?? null;
  const groups = groupByShift(ridesSorted);
  const selectedShift = selected ? data.shifts.find((s) => s.shift_id === selected.shift_id) : undefined;
  const lastDate = data.progress.last_ride_date ? shortDate(data.progress.last_ride_date) : "—";

  const creatableShifts = isSelf
    ? (data.documentable_shifts ?? [])
    : data.shifts.filter((s) => s.can_mentor);
  // Remount a card when its server versions or local drafts change, so its inputs show the right values.
  const stamp = (ds: Draft[]) => ds.map((x) => `${x.state}${x.updated_at}`).join("|");

  return (
    <Shell title={isSelf ? "יומן הנסיעות שלי" : `יומן · ${data.trainee.full_name}`} back={isSelf ? "/" : originReturn(params.get("from"), data.access.can_manage ? "/manage/trainees" : "/mentor")} backDirect>
      {fromDevice && (
        <div className="notice" role="status">
          עותק מהמכשיר מ־{fmtDateTime(new Date(copy.saved_at).toISOString())}. אפשר להמשיך לתעד; השינויים יישלחו לשרת כשהחיבור יחזור,
          והשרת יבדוק שוב את ההרשאות.
        </div>
      )}
      <section className="card stack" aria-label="סיכום החניך">
        <strong>{data.trainee.full_name}</strong>
        <div className="row spread">
          <span><strong>{data.progress.ride_count}</strong> נסיעות{fromDevice ? " (לפי העותק במכשיר)" : ""}</span>
          <span className="muted">תאריך עלייה אחרון: {lastDate}</span>
        </div>
        {creatableShifts.length > 0 && !fromDevice && (addOpen ? (
          <div className="stack">
            <span className="muted">לאיזו משמרת שייכת הנסיעה?</span>
            {creatableShifts.map((s) => (
              <button key={s.shift_id} className="btn" disabled={adding} onClick={() => addRide(s.shift_id)}>
                {dayTitle(s)} · {SHIFT[s.kind]}
              </button>
            ))}
            <button className="btn ghost small" disabled={adding} onClick={() => setAddOpen(false)}>ביטול</button>
          </div>
        ) : (
          <button className="btn primary" onClick={() => setAddOpen(true)}>+ הוספת נסיעה</button>
        ))}
      </section>
      {needsLogin && (
        <div className="error">פג תוקף ההתחברות. הטיוטות שמורות במכשיר — <Link to="/login">התחברות מחדש</Link> ואז הן יישלחו.</div>
      )}
      {unsavedCount > 0 && <div className="notice">יש {unsavedCount} שינויים שלא נשמרו — שמורים במכשיר בלבד. לחצו "שמירה" כדי לשלוח למערכת.</div>}
      {pendingCount > 0 && <div className="notice">טיוטה במכשיר — ממתינה לסנכרון ({pendingCount}). הנסיעות ייספרו רק אחרי אישור השרת.</div>}
      {syncMsg && <div className="ok">{syncMsg}</div>}


      {data.access.can_manage && !isSelf && !fromDevice && (
        // Management tools stay out of the way (collapsed) so the ride itself is what a phone shows first.
        <details className="card stack">
          <summary><strong>כלי ניהול החניך</strong></summary>
          <ManagerTools j={data} reload={j.reload} />
        </details>
      )}

      {rides.length === 0 && <div className="card muted">עוד אין נסיעות ביומן. אפשר להוסיף נסיעה בכפתור "+ הוספת נסיעה".</div>}
      {selected && (
        <>
          <label className="field">
            בחירת נסיעה
            <select value={selected.id} onChange={(e) => setSelectedId(e.target.value)}>
              {groups.map((g) => (
                <optgroup key={g.key} label={g.label}>
                  {g.rides.map((r) => (
                    <option key={r.id} value={r.id}>
                      {rideOptionLabel(r)}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          <RideCard key={`${selected.id}:${selected.description_version}:${selected.assessment?.version ?? 0}:${selected.cancelled ? "x" : ""}:${stamp(myDrafts.filter((x) => x.ride_id === selected.id))}`}
                    r={selected} traineeId={data.trainee.id} drafts={myDrafts.filter((x) => x.ride_id === selected.id)} actions={actions}
                    manage={data.access.can_manage && !fromDevice
                      ? { others: data.rides.filter((o) => o.id !== selected.id && !o.cancelled), reload: j.reload } : undefined} />
          {selectedShift && (selectedShift.can_mentor || selectedShift.summary || (selectedShift.completeness.mentor_required && !isSelf)) && (
            <ShiftSummaryCard key={`${selectedShift.shift_id}:${selectedShift.summary?.version ?? 0}:${stamp(myDrafts.filter((x) => x.key === keys.summary(data.trainee.id, selectedShift.shift_id)))}`}
                              s={selectedShift} traineeId={data.trainee.id}
                              draft={myDrafts.find((x) => x.key === keys.summary(data.trainee.id, selectedShift.shift_id))}
                              actions={actions} />
          )}
        </>
      )}
    </Shell>
  );
}

// ---- draft states: pending, denied (retry), conflict (choose per field) ----

function rideOptionLabel(r: RideRow): string {
  const number = r.seq ? `נסיעה ${r.seq}` : "נסיעה חדשה (במכשיר)";
  const status = r.cancelled ? " · בוטלה" : "";
  return `${number} · ${shortDate(r.date)} · ${r.description ?? "ללא תיאור"}${status}`;
}

function groupLabel(group: string): string {
  if (group.startsWith("marker.")) return MARKER_LABEL[group.slice(7)] ?? group;
  return { description: "תיאור המקרה", summary: "סיכום המקרה", strengths: "נקודות לשימור", improvements: "נקודות לשיפור" }[group] ?? group;
}

function groupValue(group: string, v: Values): string {
  if (group.startsWith("marker.")) {
    const x = v[group] as string | null;
    return x ? VALUE_LABEL[x] : "לא נבחר";
  }
  if (group === "strengths" || group === "improvements") {
    if (v[`${group}_none`]) return "אין הערות";
    return (v[group] as string | null) || "ריק";
  }
  return (v[group] as string | null) || "ריק";
}

function ConflictChooser({ draft, onResolve, onDiscard }: { draft: Draft; onResolve: DraftActions["onResolve"]; onDiscard: () => void }) {
  const kind = draft.kind as "description" | "assessment" | "summary";
  const mine = toFields(kind, draft.payload)!;
  const server = toFields(kind, draft.server ?? null);
  const groups = draft.conflicts ?? Object.keys(GROUPS[kind]);
  const [choice, setChoice] = useState<Record<string, "mine" | "server">>(() => Object.fromEntries(groups.map((g) => [g, "mine"])));

  function apply() {
    // Non-conflicting fields are combined automatically; conflicting ones follow the person's choice.
    const merged = server ? threeWayMerge(toFields(kind, draft.base), mine, server, GROUPS[kind]).merged : { ...mine };
    for (const g of groups) {
      const src = choice[g] === "server" && server ? server : mine;
      for (const f of GROUPS[kind][g]) merged[f] = src[f];
    }
    onResolve(draft.key, fromFields(kind, merged));
  }

  return (
    <div className="notice stack" role="alert">
      <strong>{draft.message ?? "הנתונים עודכנו במקביל"}</strong>
      <span>שינויים שלא מתנגשים שולבו. יש לבחור מה לשמור בשדות האלה:</span>
      {groups.map((g) => (
        <fieldset key={g} className="stack" style={{ gap: 4, border: 0, padding: 0, margin: 0 }}>
          <legend><strong>{groupLabel(g)}</strong></legend>
          <label className="row"><input type="radio" name={`${draft.key}:${g}`} checked={choice[g] === "mine"}
                                        onChange={() => setChoice({ ...choice, [g]: "mine" })} /> שלי: {groupValue(g, mine)}</label>
          <label className="row"><input type="radio" name={`${draft.key}:${g}`} checked={choice[g] === "server"} disabled={!server}
                                        onChange={() => setChoice({ ...choice, [g]: "server" })} /> שמור בשרת: {server ? groupValue(g, server) : "—"}</label>
        </fieldset>
      ))}
      <span className="row">
        <button className="btn small primary" onClick={apply}>שמירת הבחירה</button>
        <button className="btn small ghost" onClick={onDiscard}>לבטל את הטיוטה שלי</button>
      </span>
    </div>
  );
}

/** Save state of one part. "נשמר במערכת" appears only after the server confirmed this exact draft. */
function DraftStatus({ draft, actions, k }: { draft?: Draft; actions: DraftActions; k?: string }) {
  const key = draft?.key ?? k;
  if (key && actions.sending.has(key) && (!draft || draft.state === "pending")) {
    return <span className="badge" role="status">שומר…</span>;
  }
  if (!draft) return key && actions.confirmed.has(key) ? <span className="badge youth" role="status">✓ נשמר במערכת</span> : null;
  if (draft.state === "local") return null; // not saved yet: shown by the ride's own "not saved" badge
  if (draft.state === "pending") return <span className="badge late" role="status">טיוטה במכשיר — ממתינה לסנכרון</span>;
  if (draft.state === "conflict" && draft.kind !== "ride") {
    return <ConflictChooser draft={draft} onResolve={actions.onResolve} onDiscard={() => actions.onDiscard(draft.key)} />;
  }
  return (
    <div className="error stack" role="alert">
      <strong>השמירה במערכת נכשלה — הטיוטה נשמרה במכשיר</strong>
      <span>{draft.message}</span>
      <span className="row">
        {draft.code !== "ride_cancelled" && <button className="btn small" onClick={() => actions.onRetry(draft.key)}>לנסות שוב</button>}
        <button className="btn small ghost" onClick={() => actions.onDiscard(draft.key)}>לבטל את הטיוטה שלי</button>
      </span>
    </div>
  );
}

function CancelRide({ r, others, reload }: { r: RideRow; others: RideRow[]; reload: () => void }) {
  const [open, setOpen] = useState(false);
  const [original, setOriginal] = useState(others.find((o) => o.shift_id === r.shift_id)?.id ?? "");
  const [reason, setReason] = useState("נסיעה כפולה");
  const [msg, setMsg] = useState<string | null>(null);
  if (r.cancelled) {
    return (
      <button className="btn small ghost" onClick={async () => {
        const why = prompt("סיבת השחזור");
        if (!why || why.trim().length < 2) return;
        try { await api.post(`/api/journal/rides/${r.id}/restore`, { reason: why }); reload(); } catch (e) { setMsg(errorText(e)); }
      }}>שחזור הנסיעה{msg ? ` · ${msg}` : ""}</button>
    );
  }
  if (!open) return <button className="btn small ghost" onClick={() => setOpen(true)}>ביטול נסיעה כפולה</button>;
  return (
    <div className="stack">
      <label className="field">כפולה של
        <select value={original} onChange={(e) => setOriginal(e.target.value)}>
          <option value="">ללא (נפתחה בטעות)</option>
          {others.map((o) => <option key={o.id} value={o.id}>נסיעה {o.seq} · {dayTitle(o)} · {o.description ?? "ללא תיאור"}</option>)}
        </select>
      </label>
      <label className="field">סיבה<input value={reason} maxLength={300} onChange={(e) => setReason(e.target.value)} /></label>
      <span className="muted">הנסיעה תישמר ותתועד, אבל לא תיספר בהתקדמות ולא תופיע לחונכים.</span>
      <span className="row">
        <button className="btn small primary" disabled={reason.trim().length < 2} onClick={async () => {
          try {
            await api.post(`/api/journal/rides/${r.id}/cancel`, { reason, duplicate_of_ride_id: original || null });
            setOpen(false); reload();
          } catch (e) { setMsg(errorText(e)); }
        }}>ביטול הנסיעה</button>
        <button className="btn small ghost" onClick={() => setOpen(false)}>חזרה</button>
      </span>
      {msg && <div className="error">{msg}</div>}
    </div>
  );
}

function RideCard({ r, traineeId, drafts: ds, actions, manage }: {
  r: RideRow; traineeId: string; drafts: Draft[]; actions: DraftActions;
  manage?: { others: RideRow[]; reload: () => void };
}) {
  const rideDraft = r.localOnly ? ds.find((x) => x.kind === "ride") : undefined;
  const descDraft = ds.find((x) => x.kind === "description") ?? rideDraft;
  const assessDraft = ds.find((x) => x.kind === "assessment");
  const descKey = keys.description(r.id), assessKey = keys.assessment(r.id);
  // Last SAVED values (queued draft, else server). Typed-but-unsaved values come from the edit store.
  const savedDesc = (descDraft?.payload.description as string) ?? r.description ?? "";
  const savedAssess = {
    markers: (assessDraft?.payload.markers as Record<string, string | null>) ?? r.assessment?.markers ?? {},
    summary: (assessDraft?.payload.summary as string) ?? r.assessment?.summary ?? "",
  };
  const descEdit = actions.edits.get(descKey)?.payload;
  const assessEdit = actions.edits.get(assessKey)?.payload as typeof savedAssess | undefined;
  const [desc, setDesc] = useState<string>((descEdit?.description as string) ?? savedDesc);
  const [markers, setMarkers] = useState<Record<string, string | null>>(assessEdit?.markers ?? savedAssess.markers);
  const [summary, setSummary] = useState<string>(assessEdit?.summary ?? savedAssess.summary);
  const stopDesc = useLocalEdit(actions, descKey, { description: desc }, { description: savedDesc }, r.can_edit_description);
  const stopAssess = useLocalEdit(actions, assessKey, { markers, summary }, savedAssess, r.can_assess);
  const hasAssessContent = Object.values(markers).some(Boolean) || summary.trim() !== "";

  const base = { trainee_id: traineeId, shift_id: r.shift_id, ride_id: r.id };
  // The server values an edit starts from; kept with the draft for a safe merge on conflict.
  const saveDesc = () => stopDesc() ?? actions.onSave({
    ...base, key: keys.description(r.id), kind: "description", payload: { description: desc },
    base_version: descDraft?.base_version ?? r.description_version,
    base: descDraft ? descDraft.base ?? null : { description: r.description },
  });
  const saveAssessment = () => stopAssess() ?? actions.onSave({
    ...base, key: keys.assessment(r.id), kind: "assessment", payload: { markers, summary },
    base_version: assessDraft?.base_version ?? r.assessment?.version ?? 0,
    base: assessDraft ? assessDraft.base ?? null : { markers: r.assessment?.markers ?? {}, summary: r.assessment?.summary ?? null },
  });

  if (r.cancelled) {
    return (
      <div className="card stack" style={{ opacity: 0.75 }}>
        <div className="row spread">
          <strong>נסיעה {r.seq} · בוטלה</strong>
          <span className="muted">{r.date && dayTitle(r)} · {SHIFT[r.kind]}</span>
        </div>
        <span>{r.cancelled.duplicate_of_seq ? `כפולה של נסיעה ${r.cancelled.duplicate_of_seq}. ` : ""}סיבה: {r.cancelled.reason}</span>
        <span className="muted">לא נספרת בהתקדמות. בוטלה ב־{fmtDateTime(r.cancelled.at)}</span>
        {ds.map((x) => <DraftStatus key={x.key} draft={x} actions={actions} />)}
        {manage && <CancelRide r={r} others={manage.others} reload={manage.reload} />}
      </div>
    );
  }

  return (
    <div className="card stack">
      <div className="row spread">
        <strong>{r.seq ? `נסיעה ${r.seq}` : "נסיעה חדשה"}</strong>
        <span className="muted">{r.date && dayTitle(r)} · {SHIFT[r.kind]}</span>
      </div>
      {rideDraft?.state === "local" && (
        <span className="row">
          <span className="badge late" role="status">נסיעה חדשה במכשיר — עוד לא נשמרה ולא נספרת</span>
          <button className="btn small ghost" onClick={() => actions.onDiscardRide(r.id)}>מחיקת הטיוטה</button>
        </span>
      )}
      {r.localOnly && descDraft?.state === "pending" && <span className="badge late">טיוטה במכשיר — ממתינה לסנכרון</span>}
      {!r.localOnly && r.counted === false && <span className="badge">רשומה ריקה — לא נספרת עד שיתווסף תוכן</span>}
      {r.localOnly && descDraft && (descDraft.state === "denied" || descDraft.state === "conflict") && <DraftStatus draft={descDraft} actions={actions} />}

      <label className="field">
        תיאור המקרה {r.can_edit_description ? "" : "(ממלא/ת החניך/ה)"}
        {r.can_edit_description ? (
          <input value={desc} maxLength={200} placeholder="לדוגמה: סחרחורת" onChange={(e) => setDesc(e.target.value)} />
        ) : (
          <span>{r.description ?? <span className="muted">ממתין לתיאור מהחניך/ה</span>}</span>
        )}
      </label>
      {r.can_edit_description && (
        <div className="row">
          <button className="btn small" disabled={rideDraft?.state === "local" ? !desc.trim() : desc === savedDesc && !actions.edits.has(descKey)}
                  onClick={saveDesc}>שמירת תיאור</button>
          <UnsavedBadge k={descKey} actions={actions} onRevert={() => setDesc(savedDesc)} />
          {r.awaiting_description && !r.localOnly && <span className="badge late">ממתין לתיאור שלך</span>}
          {!r.localOnly && <DraftStatus draft={descDraft} actions={actions}
                                        k={actions.confirmed.has(keys.ride(r.id)) ? keys.ride(r.id) : keys.description(r.id)} />}
        </div>
      )}

      <div className="stack">
        <strong>הערכת החונך</strong>
        {r.can_assess ? (
          <>
            {MARKERS.map(([k, label]) => (
              <label key={k} className="field">
                {label}
                <select value={markers[k] ?? ""} onChange={(e) => setMarkers({ ...markers, [k]: e.target.value || null })}>
                  <option value="">בחירת הערכה</option>
                  {MARKER_VALUES.map(([v, vl]) => <option key={v} value={v}>{vl}</option>)}
                </select>
              </label>
            ))}
            <label className="field">סיכום המקרה
              <textarea rows={3} value={summary} onChange={(e) => setSummary(e.target.value)} />
            </label>
            <div className="row">
              <button className="btn small primary" disabled={rideDraft?.state === "local" && !hasAssessContent}
                      onClick={saveAssessment}>שמירת הערכה</button>
              <UnsavedBadge k={assessKey} actions={actions}
                            onRevert={() => { setMarkers(savedAssess.markers); setSummary(savedAssess.summary); }} />
              <DraftStatus draft={assessDraft} actions={actions} k={keys.assessment(r.id)} />
            </div>
          </>
        ) : r.assessment ? (
          <div className="stack" style={{ gap: 2 }}>
            {MARKERS.map(([k, label]) => (
              <span key={k}>{label}: {r.assessment!.markers[k] ? VALUE_LABEL[r.assessment!.markers[k]!] : <span className="muted">לא נבחר</span>}</span>
            ))}
            {r.assessment.summary && <span>סיכום: {r.assessment.summary}</span>}
          </div>
        ) : (
          <span className="muted">אין הערכה</span>
        )}
      </div>
      {r.contributors.length > 0 && <span className="muted">תיעדו: {r.contributors.join(", ")}</span>}
      {manage && !r.localOnly && <CancelRide r={r} others={manage.others} reload={manage.reload} />}
    </div>
  );
}

function SummaryField({ label, text, none, setText, setNone, editable }: {
  label: string; text: string; none: boolean; setText: (v: string) => void; setNone: (v: boolean) => void; editable: boolean;
}) {
  if (!editable) return <span>{label}: {none ? "אין הערות" : text || <span className="muted">לא מולא</span>}</span>;
  return (
    <div className="stack" style={{ gap: 4 }}>
      <span>{label}</span>
      <textarea rows={2} value={text} disabled={none} onChange={(e) => setText(e.target.value)} />
      <button className="btn small" aria-pressed={none} onClick={() => { setNone(!none); if (!none) setText(""); }}>אין הערות</button>
    </div>
  );
}

function ShiftSummaryCard({ s, traineeId, draft, actions }: {
  s: ShiftRow; traineeId: string; draft?: Draft; actions: DraftActions;
}) {
  const key = keys.summary(traineeId, s.shift_id);
  const src = (draft?.payload as unknown as Summary | undefined) ?? s.summary;
  const saved = { strengths: src?.strengths ?? "", strengths_none: src?.strengths_none ?? false,
                  improvements: src?.improvements ?? "", improvements_none: src?.improvements_none ?? false };
  const typed = actions.edits.get(key)?.payload as typeof saved | undefined;
  const start = typed ?? saved;
  const [strengths, setStrengths] = useState(start.strengths);
  const [strengthsNone, setStrengthsNone] = useState(start.strengths_none);
  const [improvements, setImprovements] = useState(start.improvements);
  const [improvementsNone, setImprovementsNone] = useState(start.improvements_none);
  const stopEdit = useLocalEdit(actions, key, { strengths, strengths_none: strengthsNone, improvements, improvements_none: improvementsNone },
                               saved, s.can_mentor);
  const c = s.completeness;
  const saveIt = () => stopEdit() ?? actions.onSave({
    key: keys.summary(traineeId, s.shift_id), kind: "summary", trainee_id: traineeId, shift_id: s.shift_id,
    payload: { strengths, strengths_none: strengthsNone, improvements, improvements_none: improvementsNone },
    base_version: draft?.base_version ?? s.summary?.version ?? 0,
    base: draft ? draft.base ?? null : { ...(s.summary ?? { strengths: null, strengths_none: false, improvements: null, improvements_none: false }) },
  });
  return (
    <div className="card stack">
      <div className="row spread">
        <strong>סיכום חניכה למשמרת</strong>
        <span className="muted">{dayTitle(s)} · {SHIFT[s.kind]}</span>
      </div>
      {s.mentors.length > 0 && <span className="muted">חונכים: {s.mentors.map((m) => m.full_name).join(", ")}</span>}
      <span className={c.complete ? "badge youth" : "badge late"}>
        {c.complete ? "התיעוד הושלם" : [
          c.trainee_missing ? `חסר תיאור ב־${c.trainee_missing} נסיעות` : "",
          c.mentor_missing_rides ? `חסרה הערכה ב־${c.mentor_missing_rides} נסיעות` : "",
          c.summary_missing ? "חסר סיכום חניכה" : "",
        ].filter(Boolean).join(" · ")}
      </span>
      <SummaryField label="נקודות לשימור" text={strengths} none={strengthsNone} setText={setStrengths} setNone={setStrengthsNone} editable={s.can_mentor} />
      <SummaryField label="נקודות לשיפור" text={improvements} none={improvementsNone} setText={setImprovements} setNone={setImprovementsNone} editable={s.can_mentor} />
      {s.can_mentor && (
        <div className="row">
          <button className="btn small primary" onClick={saveIt}>שמירת סיכום</button>
          <UnsavedBadge k={key} actions={actions} onRevert={() => {
            setStrengths(saved.strengths); setStrengthsNone(saved.strengths_none);
            setImprovements(saved.improvements); setImprovementsNone(saved.improvements_none);
          }} />
          <DraftStatus draft={draft} actions={actions} k={keys.summary(traineeId, s.shift_id)} />
        </div>
      )}
    </div>
  );
}

function ManagerTools({ j, reload }: { j: Journal; reload: () => void }) {
  const [msg, setMsg] = useState<string | null>(null);
  const [delta, setDelta] = useState("0");
  const [lastDate, setLastDate] = useState("");
  const [reason, setReason] = useState("");
  const [shiftId, setShiftId] = useState(j.shifts[0]?.shift_id ?? "");
  const mentors = j.shifts.find((s) => s.shift_id === shiftId)?.mentors ?? [];
  const [mentorId, setMentorId] = useState("");
  const notes = useLoad(() => api.get<{ id: string; content: string; created_at: string }[]>(`/api/journal/trainees/${j.trainee.id}/notes`));

  return (
    <>
      <div className="card stack">
        <strong>פתיחת גישה מחדש לחונך</strong>
        <select value={shiftId} onChange={(e) => { setShiftId(e.target.value); setMentorId(""); }}>
          {j.shifts.map((s) => <option key={s.shift_id} value={s.shift_id}>{dayTitle(s)} · {SHIFT[s.kind]}</option>)}
        </select>
        <select value={mentorId} onChange={(e) => setMentorId(e.target.value)}>
          <option value="">בחירת חונך…</option>
          {mentors.map((m) => <option key={m.id} value={m.id}>{m.full_name}</option>)}
        </select>
        <button className="btn small" disabled={!mentorId} onClick={async () => {
          try {
            const r = await api.post<{ ends_at: string }>(`/api/journal/trainees/${j.trainee.id}/extensions`, { mentor_id: mentorId, shift_id: shiftId, hours: 12 });
            setMsg(`הגישה נפתחה עד ${fmtDateTime(r.ends_at)}`);
          } catch (e) { setMsg(errorText(e)); }
        }}>פתיחה ל־12 שעות</button>
      </div>
      <div className="card stack">
        <strong>תיקון התקדמות</strong>
        <div className="row">
          <label className="field" style={{ flex: 1 }}>שינוי במספר הנסיעות<input type="number" value={delta} onChange={(e) => setDelta(e.target.value)} /></label>
          <label className="field" style={{ flex: 1 }}>תאריך עלייה אחרון<input type="date" value={lastDate} onChange={(e) => setLastDate(e.target.value)} /></label>
        </div>
        <label className="field">סיבה<input value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        <button className="btn small" disabled={reason.trim().length < 2} onClick={async () => {
          try {
            await api.post(`/api/journal/trainees/${j.trainee.id}/corrections`, { count_delta: Number(delta), last_ride_date: lastDate || null, reason });
            setMsg("התיקון נשמר ותועד"); setReason(""); setDelta("0"); setLastDate(""); reload();
          } catch (e) { setMsg(errorText(e)); }
        }}>שמירת תיקון</button>
      </div>
      <div className="card stack">
        <strong>סיכומים אישיים ששותפו איתך</strong>
        {(notes.data ?? []).length === 0 && <span className="muted">אין סיכומים משותפים.</span>}
        {notes.data?.map((n) => <div key={n.id}><span className="muted">{fmtDateTime(n.created_at)}</span><div>{n.content}</div></div>)}
      </div>
      {msg && <div className="notice">{msg}</div>}
    </>
  );
}

// ---- mentor home ----
export function MentorHome() {
  const { me } = useAuth();
  const list = useLoad(() => api.get<{ trainee_id: string; full_name: string; shift_id: string; kind: ShiftKind; date: string; weekday: number;
    completeness: ShiftRow["completeness"] }[]>("/api/journal/mentor"));
  return (
    <Shell title="חניכה">
      <div className="muted">חניכים מהאמבולנס שלך במשמרת ערב. הגישה פתוחה מ־13:30 ועד 10:00 למחרת.</div>
      {/* Admin override: the journals of every trainee are reachable at any time, not only in a window. */}
      {me?.is_admin && <Link className="card list-link" to="/manage/trainees">
        <span><strong>כל היומנים</strong><br /><span className="muted">אדמין: גישה לכל החניכים, ללא חלון זמן</span></span>
        <span>›</span>
      </Link>}
      {isUnreachable(list.failure) ? <OfflineJournals mentorOnly /> : !list.data && <Loading error={list.error} />}
      {list.data?.length === 0 && <div className="card muted">אין כרגע חניכים בחלון החניכה שלך.</div>}
      {list.data?.map((t) => (
        <Link key={t.trainee_id + t.shift_id} to={`/journal/${t.trainee_id}`} className="card list-link">
          <span><strong>{t.full_name}</strong><br /><span className="muted">{dayTitle(t)} · {SHIFT[t.kind]}</span></span>
          <span className={t.completeness.complete ? "badge youth" : "badge late"}>{t.completeness.complete ? "הושלם" : "חסר תיעוד"}</span>
        </Link>
      ))}
    </Shell>
  );
}

/** Journals this person may still open from the device copy (expired copies are removed). */
function OfflineJournals({ mentorOnly = false }: { mentorOnly?: boolean }) {
  const { me } = useAuth();
  const [list, setList] = useState<Snapshot<Journal>[] | null>(null);
  const [pending, setPending] = useState(0);
  useEffect(() => {
    if (!me) return;
    snapshots.list<Journal>(me.id).then(setList, () => setList([]));
    drafts.all(me.id).then((ds) => setPending(ds.filter((x) => x.state === "pending").length), () => undefined);
  }, [me]);
  if (!me || list === null) return <div className="muted">טוען…</div>;
  const items = list.filter((x) => !mentorOnly || !x.data.access.is_self);
  return (
    <>
      {pending > 0 && <div className="notice">{pending} טיוטות שמורות במכשיר ויישלחו כשהחיבור יחזור.</div>}
      {items.length === 0 && <div className="card muted">אין במכשיר יומנים זמינים ללא חיבור.</div>}
      {items.map((x) => (
        <Link key={x.key} to={x.data.access.is_self ? "/journal" : `/journal/${x.key}`} className="card list-link">
          <span><strong>{x.data.access.is_self ? "יומן הנסיעות שלי" : x.data.trainee.full_name}</strong><br />
            <span className="muted">נשמר במכשיר {fmtDateTime(new Date(x.saved_at).toISOString())}
              {x.data.access.is_self ? "" : ` · זמין עד ${fmtDateTime(new Date(x.valid_until).toISOString())}`}</span></span>
          <span>›</span>
        </Link>
      ))}
    </>
  );
}

/** Shown for every other page while the server is unreachable and the app runs from the device. */
export function OfflineHome() {
  return (
    <Shell title="אין חיבור">
      <div className="card stack">
        <span>אין חיבור לשרת. אפשר לפתוח את היומנים שנשמרו במכשיר ולהמשיך לתעד; כל השאר יחזור כשהחיבור יחזור.</span>
      </div>
      <OfflineJournals />
    </Shell>
  );
}

// ---- manager trainees ----
// Trainee progress list, optionally limited to one home day (decision of 2026-10-17). Shared by the management
// screens; the detailed progress content is unchanged.
export function TraineeList({ weekday, back }: { weekday: number | null; back: string }) {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const search = params.get("q") ?? "";
  const [typed, setTyped] = useState(search);
  // The typed text reaches the URL after a short pause, so back navigation restores it.
  useEffect(() => {
    const t = setTimeout(() => {
      if (typed.trim() !== search.trim()) setParams(typed.trim() ? { q: typed } : {}, { replace: true });
    }, 250);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [typed]);
  const base = weekday === null ? "/api/journal/trainees" : `/api/journal/trainees?weekday=${weekday}`;
  const query = search.trim() ? `${base}${base.includes("?") ? "&" : "?"}search=${encodeURIComponent(search.trim())}` : base;
  const list = useLoad(() => api.get<{ id: string; full_name: string; ride_count: number; last_ride_date: string | null; journal_access: boolean }[]>(query), [query]);
  const rows = useMemo(() => {
    const seen = new Set<string>();
    return (list.data ?? []).filter((t) => (seen.has(t.id) ? false : (seen.add(t.id), true)));
  }, [list.data]);
  const here = location.pathname + location.search;
  const title = weekday === null ? "חניכים" : `${weekday === 6 ? "שבת" : `יום ${WEEKDAYS[weekday]}`} · חניכים`;
  return (
    <Shell title={title} back={back}>
      <h2>{title}</h2>
      <label className="field">
        חיפוש לפי שם
        <input type="search" value={typed} placeholder="הקלידו חלק משם" onChange={(e) => setTyped(e.target.value)} />
      </label>
      {!list.data && <Loading error={list.error} />}
      {list.data && rows.length === 0 && (
        <div className="card stack">
          <span className="muted">{search.trim() ? `לא נמצאו חניכים שהשם שלהם מכיל „${search.trim()}”.` : "אין חניכים ליום הזה."}</span>
          {search.trim() && <button className="btn small" onClick={() => setTyped("")}>איפוס החיפוש</button>}
        </div>
      )}
      {rows.map((t) => {
        const body = (
          <>
            <span><strong>{t.full_name}</strong></span>
            <span className="muted">{t.ride_count} נסיעות · {t.last_ride_date?.split("-").reverse().join(".") ?? "—"}</span>
          </>
        );
        return t.journal_access
          ? <Link key={t.id} to={`/journal/${t.id}?from=${encodeURIComponent(here)}`} className="card list-link">{body}</Link>
          : <div key={t.id} className="card list-link">{body}</div>;
      })}
    </Shell>
  );
}

// ---- personal notes ----
interface Note { id: string; content: string; shared: boolean; version: number; created_at: string }
export function NotesPage() {
  const notes = useLoad(() => api.get<Note[]>("/api/notes"));
  const [text, setText] = useState("");
  const [shared, setShared] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  return (
    <Shell title="סיכומים אישיים" back="/journal">
      <div className="notice">
        סיכום שלא שותף גלוי לך ולמנהלי המערכת (אדמין). אם תבחר/י לשתף, יראה אותו גם אחראי היום הקבוע שלך. אפשר לבטל שיתוף בכל עת.
        הסיכומים האישיים נפרדים מסיכומי החניכה שהחונכים כותבים.
      </div>
      <div className="card stack">
        <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} placeholder="מה למדתי, מה הרגשתי…" />
        <label className="row"><input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} /> לשתף עם אחראי היום שלי</label>
        <button className="btn primary" disabled={!text.trim()} onClick={async () => {
          try { await api.post("/api/notes", { content: text, shared }); setText(""); setShared(false); notes.reload(); }
          catch (e) { setErr(errorText(e)); }
        }}>שמירה</button>
        {err && <div className="error">{err}</div>}
      </div>
      {notes.data?.map((n) => (
        <div key={n.id} className="card stack">
          <span className="muted">{fmtDateTime(n.created_at)}</span>
          <div>{n.content}</div>
          <label className="row">
            <input type="checkbox" checked={n.shared} onChange={async (e) => {
              try { await api.put(`/api/notes/${n.id}`, { shared: e.target.checked, expected_version: n.version }); notes.reload(); }
              catch (x) { setErr(errorText(x)); notes.reload(); }
            }} />
            {n.shared ? "משותף עם אחראי היום" : "לא משותף"}
          </label>
        </div>
      ))}
    </Shell>
  );
}

// ---- notifications notice ----
export function NotificationNotice() {
  const s = useLoad(() => api.get<{ phone_channel_connected: boolean }>("/api/me/notification-status", true));
  const permission = typeof Notification === "undefined" ? "unsupported" : Notification.permission;
  const enabled = permission === "granted" && !!s.data?.phone_channel_connected;
  if (!s.data || enabled) return null;
  return (
    <Link to="/notifications" className="card alert list-link" role="status">
      <span>ההתראות כבויות — יש להפעיל כדי לקבל עדכוני משמרות</span>
      <span>›</span>
    </Link>
  );
}

// Notification settings for this device (Web Push). The current state is always shown in words.
const STATE_TEXT: Record<PushState, string> = {
  enabled: "מופעלות במכשיר הזה",
  not_enabled: "לא מופעלות במכשיר הזה",
  denied: "חסומות בהגדרות (הרשאה נדחתה)",
  needs_home_screen: "לא זמינות עדיין בדפדפן הזה",
  unsupported: "הדפדפן הזה אינו תומך בהתראות",
};

export function NotificationsHelp() {
  const [state, setState] = useState<PushState | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(() => { currentPushState().then(setState, () => setState("unsupported")); }, []);
  useEffect(() => { refresh(); }, [refresh]);

  async function run(fn: () => Promise<string | void>) {
    setBusy(true);
    setMessage(null);
    try {
      const text = await fn();
      if (text) setMessage(text);
    } catch (e) {
      setMessage(errorText(e));
    } finally {
      setBusy(false);
      refresh();
    }
  }

  return (
    <Shell title="הפעלת התראות" back="/">
      <section className="card stack" aria-labelledby="push-title">
        <strong id="push-title">התראות במכשיר הזה</strong>
        <span role="status">מצב: {state ? STATE_TEXT[state] : "בודק…"}</span>

        {state === "needs_home_screen" && (
          <div className="notice">
            באייפון ובאייפד ההתראות פועלות רק כשהאתר נפתח מהמסך הראשי: לחצו על כפתור השיתוף ובחרו ״הוספה למסך הבית״, ואז פתחו את האפליקציה מהאייקון שנוצר ואשרו התראות. נדרש iOS 16.4 ומעלה.
          </div>
        )}
        {state === "unsupported" && (
          <div className="notice">הדפדפן הזה לא תומך בהתראות. אפשר לנסות Chrome באנדרואיד או Safari באייפון (מהמסך הראשי).</div>
        )}
        {state === "denied" && (
          <div className="notice">
            ההתראות חסומות. כדי לאפשר: באנדרואיד/Chrome — נעילת האתר בשורת הכתובת ← הרשאות ← התראות ← אפשרו. באייפון — הגדרות ← התראות ← מד״א. אחר כך חזרו לכאן ולחצו ״הפעלת התראות״.
          </div>
        )}

        {state === "not_enabled" && (
          <button className="btn primary" disabled={busy} onClick={() => run(enablePush)}>הפעלת התראות</button>
        )}
        {state === "enabled" && (
          <>
            <button className="btn" disabled={busy} onClick={() => run(sendSelfTest)}>שליחת התראת בדיקה</button>
            <button className="btn ghost" disabled={busy} onClick={() => run(async () => { await disablePush(); return "ההתראות כובו במכשיר הזה."; })}>כיבוי התראות במכשיר הזה</button>
          </>
        )}
        {message && <div className="notice" role="status">{message}</div>}
      </section>

      <div className="card stack">
        <strong>איך זה עובד</strong>
        <span className="muted">ההתראה נשלחת למכשיר הזה בלבד, ורק לחשבון שמחובר. ״שליחת התראת בדיקה״ בודקת את החיבור; אישור שהשירות קיבל את הבקשה אינו אישור שההתראה הופיעה במכשיר.</span>
        <span className="muted">בהתראות לטלפון אין פרטים אישיים או פרטי משמרת. ההודעות במסך הבית באפליקציה לא משתנות.</span>
      </div>
    </Shell>
  );
}
