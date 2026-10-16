import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, isUnreachable, type ShiftKind } from "../api";
import { useAuth } from "../auth";
import { SHIFT, dayTitle, fmtDateTime } from "../he";
import { GROUPS, fromFields, threeWayMerge, toFields, type Values } from "../merge";
import {
  drafts, edits as editStore, keys, newRideId, rememberJournal, resolveDraft, retryDraft, snapshots, syncDrafts,
  type Draft, type Edit, type Snapshot,
} from "../offline";
import { Loading, Shell, errorText, useLoad } from "../ui";

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
    // Only a local record: nothing is sent or counted until the person saves content (D-50).
    const id = newRideId();
    await drafts.put(me!.id, { key: keys.ride(id), kind: "ride", ride_id: id, trainee_id: data!.trainee.id, shift_id: shiftId,
                               payload: {}, base_version: 0, state: "local", updated_at: Date.now() });
    await d.refresh();
  }

  const creatableShifts = isSelf
    ? (data.documentable_shifts ?? [])
    : data.shifts.filter((s) => s.can_mentor);
  // Remount a card when its server versions or local drafts change, so its inputs show the right values.
  const stamp = (ds: Draft[]) => ds.map((x) => `${x.state}${x.updated_at}`).join("|");

  return (
    <Shell title={isSelf ? "יומן הנסיעות שלי" : `יומן · ${data.trainee.full_name}`} back={isSelf ? "/" : data.access.can_manage ? "/manage/trainees" : "/mentor"}>
      {fromDevice && (
        <div className="notice" role="status">
          עותק מהמכשיר מ־{fmtDateTime(new Date(copy.saved_at).toISOString())}. אפשר להמשיך לתעד; השינויים יישלחו לשרת כשהחיבור יחזור,
          והשרת יבדוק שוב את ההרשאות.
        </div>
      )}
      <div className="card row spread">
        <span><strong>{data.progress.ride_count}</strong> נסיעות{fromDevice ? " (לפי העותק במכשיר)" : ""}</span>
        <span className="muted">עלייה אחרונה: {data.progress.last_ride_date?.split("-").reverse().join(".") ?? "—"}</span>
      </div>
      {needsLogin && (
        <div className="error">פג תוקף ההתחברות. הטיוטות שמורות במכשיר — <Link to="/login">התחברות מחדש</Link> ואז הן יישלחו.</div>
      )}
      {unsavedCount > 0 && <div className="notice">יש {unsavedCount} שינויים שלא נשמרו — שמורים במכשיר בלבד. לחצו "שמירה" כדי לשלוח למערכת.</div>}
      {pendingCount > 0 && <div className="notice">טיוטה במכשיר — ממתינה לסנכרון ({pendingCount}). הנסיעות ייספרו רק אחרי אישור השרת.</div>}
      {syncMsg && <div className="ok">{syncMsg}</div>}

      {creatableShifts.length > 0 && (
        <div className="card stack">
          <strong>נסיעה חדשה</strong>
          {creatableShifts.slice(0, 4).map((s) => (
            <button key={s.shift_id} className="btn" onClick={() => addRide(s.shift_id)}>
              + נסיעה ב{dayTitle(s)} · {SHIFT[s.kind]}
            </button>
          ))}
        </div>
      )}

      {data.shifts.filter((s) => s.can_mentor || s.summary || (s.completeness.mentor_required && !isSelf)).map((s) => {
        const draft = myDrafts.find((x) => x.key === keys.summary(data.trainee.id, s.shift_id));
        return (
          <ShiftSummaryCard key={`${s.shift_id}:${s.summary?.version ?? 0}:${stamp(draft ? [draft] : [])}`}
                            s={s} traineeId={data.trainee.id} draft={draft} actions={actions} />
        );
      })}

      {rides.length === 0 && <div className="card muted">עוד אין נסיעות ביומן.</div>}
      {rides.map((r) => {
        const ds = myDrafts.filter((x) => x.ride_id === r.id);
        return (
          <RideCard key={`${r.id}:${r.description_version}:${r.assessment?.version ?? 0}:${r.cancelled ? "x" : ""}:${stamp(ds)}`}
                    r={r} traineeId={data.trainee.id} drafts={ds} actions={actions}
                    manage={data.access.can_manage && !fromDevice
                      ? { others: data.rides.filter((o) => o.id !== r.id && !o.cancelled), reload: j.reload } : undefined} />
        );
      })}

      {data.access.can_manage && !isSelf && !fromDevice && <ManagerTools j={data} reload={j.reload} />}
    </Shell>
  );
}

// ---- draft states: pending, denied (retry), conflict (choose per field) ----

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
              <div key={k} className="stack" style={{ gap: 4 }}>
                <span>{label}{markers[k] ? "" : " · לא נבחר"}</span>
                <div className="choice4" role="group" aria-label={label}>
                  {MARKER_VALUES.map(([v, vl]) => (
                    <button key={v} className="btn small" aria-pressed={markers[k] === v}
                            onClick={() => setMarkers({ ...markers, [k]: markers[k] === v ? null : v })}>{vl}</button>
                  ))}
                </div>
              </div>
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
export function ManagerTrainees() {
  const list = useLoad(() => api.get<{ id: string; full_name: string; ride_count: number; last_ride_date: string | null; journal_access: boolean }[]>("/api/journal/trainees"));
  return (
    <Shell title="חניכים" back="/manage">
      {!list.data && <Loading error={list.error} />}
      {list.data?.map((t) => {
        const body = (
          <>
            <span><strong>{t.full_name}</strong></span>
            <span className="muted">{t.ride_count} נסיעות · {t.last_ride_date?.split("-").reverse().join(".") ?? "—"}</span>
          </>
        );
        return t.journal_access
          ? <Link key={t.id} to={`/journal/${t.id}`} className="card list-link">{body}</Link>
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

export function NotificationsHelp() {
  const [perm, setPerm] = useState(typeof Notification === "undefined" ? "unsupported" : Notification.permission);
  return (
    <Shell title="הפעלת התראות" back="/">
      <div className="card stack">
        <strong>אייפון</strong>
        <ol style={{ margin: 0, paddingInlineStart: 20 }}>
          <li>פותחים את האתר ב־Safari.</li>
          <li>לוחצים על כפתור השיתוף ובוחרים ״הוספה למסך הבית״ (נדרש iOS 16.4 ומעלה).</li>
          <li>פותחים את האפליקציה מהמסך הראשי ומאשרים התראות.</li>
        </ol>
      </div>
      <div className="card stack">
        <strong>אנדרואיד</strong>
        <ol style={{ margin: 0, paddingInlineStart: 20 }}>
          <li>פותחים את האתר ב־Chrome.</li>
          <li>מאשרים ״התראות״ כשהדפדפן שואל (או בהגדרות האתר).</li>
          <li>מומלץ: תפריט ⋮ ← ״הוספה למסך הבית״.</li>
        </ol>
      </div>
      <div className="card stack">
        <span>הרשאת הדפדפן כרגע: {perm === "granted" ? "מאושרת" : perm === "denied" ? "חסומה (יש לשנות בהגדרות)" : perm === "unsupported" ? "לא נתמכת בדפדפן זה" : "טרם נבחרה"}</span>
        {perm === "default" && (
          <button className="btn" onClick={async () => setPerm(await Notification.requestPermission())}>אישור התראות בדפדפן</button>
        )}
        <div className="notice">
          שליחת התראות לטלפון עדיין לא הופעלה במערכת. עד אז כל ההודעות מופיעות במסך הבית באפליקציה. ההרשמה למשמרות זמינה גם בלי התראות.
        </div>
      </div>
    </Shell>
  );
}
