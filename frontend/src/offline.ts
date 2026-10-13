// Offline journal drafts in IndexedDB, scoped per account (one database per user id, so switching
// accounts on a shared phone never exposes someone else's drafts).
//
// Every journal edit is written here first and then synchronised. A draft is deleted only after the
// server confirms it. Denied (window closed / no access / ride cancelled) and conflicting drafts are
// kept until the person resolves them. Local drafts never change progress.
//
// Conflicts (D-45): each draft remembers the server values it was edited from (`base`). On 409 the
// server's copy is merged field by field; only fields changed on both sides need a human choice.
// Nothing is ever overwritten blindly.
//
// Offline copies (D-44): the last journal view is kept per trainee so the app can be reopened without
// a connection, bounded by the viewer's access (mentors: until their window/extension ends).
import { api, ApiError, isUnreachable } from "./api";
import { GROUPS, fromFields, threeWayMerge, toFields, type Values } from "./merge";

export type DraftKind = "ride" | "description" | "assessment" | "summary";
// "local": a ride the person started ("+ נסיעה") but has not saved yet. Never synced or counted.
export type DraftState = "local" | "pending" | "denied" | "conflict";
type PartKind = Exclude<DraftKind, "ride">;

export interface Draft {
  key: string;
  kind: DraftKind;
  ride_id?: string;
  trainee_id: string;
  shift_id: string;
  payload: Record<string, unknown>;
  base_version: number;
  /** Server values this edit started from (payload form). null/undefined = unknown. */
  base?: Record<string, unknown> | null;
  updated_at: number;
  state: DraftState;
  message?: string;
  code?: string;
  server?: Record<string, unknown> | null;
  /** Field groups changed both here and on the server (state "conflict"). */
  conflicts?: string[];
}

export interface Snapshot<T = unknown> {
  key: string; // trainee id
  data: T;
  saved_at: number;
  valid_until: number;
}

const DRAFTS = "drafts";
const SNAPSHOTS = "snapshots";
const EDITS = "edits";

/** Text and choices typed but not saved yet (D-50). Kept on the device while typing; never sent to
 * the server until the person presses Save, which turns the edit into a pending draft. */
export interface Edit {
  key: string; // the draft key this edit belongs to (keys.description / assessment / summary)
  payload: Record<string, unknown>;
  updated_at: number;
}

function openDb(userId: string): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(`mda-journal-${userId}`, 3);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(DRAFTS)) db.createObjectStore(DRAFTS, { keyPath: "key" });
      if (!db.objectStoreNames.contains(SNAPSHOTS)) db.createObjectStore(SNAPSHOTS, { keyPath: "key" });
      if (!db.objectStoreNames.contains(EDITS)) db.createObjectStore(EDITS, { keyPath: "key" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx<T>(userId: string, store: string, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb(userId);
  return new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const req = fn(t.objectStore(store));
    t.oncomplete = () => { resolve(req.result); db.close(); };
    t.onerror = () => { reject(t.error); db.close(); };
  });
}

export const drafts = {
  all: (userId: string) => tx<Draft[]>(userId, DRAFTS, "readonly", (s) => s.getAll() as IDBRequest<Draft[]>),
  get: (userId: string, key: string) => tx<Draft | undefined>(userId, DRAFTS, "readonly", (s) => s.get(key) as IDBRequest<Draft | undefined>),
  put: (userId: string, d: Draft) => tx(userId, DRAFTS, "readwrite", (s) => s.put(d)),
  remove: (userId: string, key: string) => tx(userId, DRAFTS, "readwrite", (s) => s.delete(key)),
};

export const edits = {
  all: (userId: string) => tx<Edit[]>(userId, EDITS, "readonly", (s) => s.getAll() as IDBRequest<Edit[]>),
  put: (userId: string, e: Edit) => tx(userId, EDITS, "readwrite", (s) => s.put(e)),
  remove: (userId: string, key: string) => tx(userId, EDITS, "readwrite", (s) => s.delete(key)),
};

const MAX_SELF_COPY_MS = 30 * 24 * 3600 * 1000; // same as the session inactivity limit

export const snapshots = {
  async get<T>(userId: string, traineeId: string): Promise<Snapshot<T> | null> {
    const s = await tx<Snapshot<T> | undefined>(userId, SNAPSHOTS, "readonly", (st) => st.get(traineeId) as IDBRequest<Snapshot<T> | undefined>);
    if (!s) return null;
    if (s.valid_until <= Date.now()) {
      await snapshots.remove(userId, traineeId); // access ended: the copy is gone, drafts stay
      return null;
    }
    return s;
  },
  async list<T>(userId: string): Promise<Snapshot<T>[]> {
    const all = await tx<Snapshot<T>[]>(userId, SNAPSHOTS, "readonly", (st) => st.getAll() as IDBRequest<Snapshot<T>[]>);
    const now = Date.now();
    for (const s of all.filter((x) => x.valid_until <= now)) await snapshots.remove(userId, s.key);
    return all.filter((x) => x.valid_until > now);
  },
  put: (userId: string, s: Snapshot) => tx(userId, SNAPSHOTS, "readwrite", (st) => st.put(s)),
  remove: (userId: string, key: string) => tx(userId, SNAPSHOTS, "readwrite", (st) => st.delete(key)),
  clear: (userId: string) => tx(userId, SNAPSHOTS, "readwrite", (st) => st.clear()),
};

/** Minimal shape needed to decide what may be kept for offline use. */
interface JournalLike {
  trainee: { id: string };
  rides: { shift_id: string; cancelled?: unknown }[];
  shifts: { shift_id: string; summary?: unknown }[];
  access: { is_self: boolean; mentor_shift_ids: string[]; mentor_access_until?: Record<string, string> };
}

/** Keep an offline copy of a journal the viewer just loaded, limited to what they may document. */
export async function rememberJournal<T extends JournalLike>(userId: string, j: T): Promise<void> {
  const now = Date.now();
  if (j.access.is_self) {
    await snapshots.put(userId, { key: j.trainee.id, data: j, saved_at: now, valid_until: now + MAX_SELF_COPY_MS });
    return;
  }
  const mine = new Set(j.access.mentor_shift_ids);
  const ends = Object.values(j.access.mentor_access_until ?? {}).map((x) => Date.parse(x)).filter((x) => !Number.isNaN(x));
  if (mine.size === 0 || ends.length === 0) {
    await snapshots.remove(userId, j.trainee.id); // managers/admins do not keep offline copies
    return;
  }
  // Mentor: only the shifts in the current window (no history, no other summaries).
  const data = { ...j, rides: j.rides.filter((r) => mine.has(r.shift_id) && !r.cancelled),
                 shifts: j.shifts.filter((s) => mine.has(s.shift_id)) };
  await snapshots.put(userId, { key: j.trainee.id, data, saved_at: now, valid_until: Math.max(...ends) });
}

export const keys = {
  ride: (id: string) => `ride:${id}`,
  description: (id: string) => `desc:${id}`,
  assessment: (id: string) => `assess:${id}`,
  summary: (trainee: string, shift: string) => `summary:${trainee}:${shift}`,
};

const ORDER: Record<DraftKind, number> = { ride: 0, description: 1, assessment: 2, summary: 3 };
// Denied for reasons that can change later (access reopened, signed in again): retried on page open.
const RETRYABLE = new Set(["mentor_window_closed", "journal_not_found", "ride_not_found", "account_not_active", "csrf_failed"]);

export interface SyncResult {
  done: number;
  /** Draft keys the server confirmed in this run (only these may show "saved to the system"). */
  confirmed: string[];
  needsLogin: boolean;
  offline: boolean;
}

let running: Promise<SyncResult> | null = null;

/** Push pending drafts to the server. `retryDenied` also re-sends drafts refused for reasons that
 * may have changed (call on page open, not on every timer tick). */
export function syncDrafts(userId: string, retryDenied = false): Promise<SyncResult> {
  running = running ?? doSync(userId, retryDenied).finally(() => { running = null; });
  return running;
}

/** Person chose the values to keep for a conflicting or denied draft; send them on next sync. */
export async function resolveDraft(userId: string, key: string, payload: Record<string, unknown>) {
  const d = await drafts.get(userId, key);
  if (!d) return;
  const server = d.server ?? null;
  await drafts.put(userId, {
    ...d, payload, state: "pending", conflicts: undefined, message: undefined, code: undefined, server: undefined,
    base: server ? payloadOf(d.kind as PartKind, server) : d.base,
    base_version: server ? Number((server as { version?: number }).version ?? d.base_version) : d.base_version,
    updated_at: Date.now(),
  });
}

export async function retryDraft(userId: string, key: string) {
  const d = await drafts.get(userId, key);
  if (d && d.state === "denied") await drafts.put(userId, { ...d, state: "pending", message: undefined, code: undefined });
}

function payloadOf(kind: PartKind, v: Values): Record<string, unknown> {
  return fromFields(kind, toFields(kind, v) ?? {});
}

async function send(d: Draft): Promise<{ created?: boolean; description?: string | null; description_version?: number; cancelled?: boolean }> {
  if (d.kind === "ride") {
    return api.post("/api/journal/rides", { id: d.ride_id, trainee_id: d.trainee_id, shift_id: d.shift_id, ...d.payload });
  } else if (d.kind === "description") {
    return api.put(`/api/journal/rides/${d.ride_id}/description`, { ...d.payload, expected_version: d.base_version });
  } else if (d.kind === "assessment") {
    return api.put(`/api/journal/rides/${d.ride_id}/assessment`, { ...d.payload, expected_version: d.base_version });
  }
  return api.put(`/api/journal/summaries/${d.trainee_id}/${d.shift_id}`, { ...d.payload, expected_version: d.base_version });
}

/** 409 version_conflict: merge safely. Returns true when it could be merged without a human choice. */
async function handleConflict(userId: string, d: Draft, current: Values | null, message: string): Promise<boolean> {
  const kind = d.kind as PartKind;
  if (!current) {
    await drafts.put(userId, { ...d, state: "conflict", message, server: null, conflicts: Object.keys(GROUPS[kind]) });
    return false;
  }
  const { merged, conflicts } = threeWayMerge(toFields(kind, d.base), toFields(kind, d.payload)!, toFields(kind, current)!, GROUPS[kind]);
  const serverPayload = payloadOf(kind, current);
  const version = Number((current as { version?: number }).version ?? 0);
  if (conflicts.length === 0) {
    // Non-overlapping edits: combine them and rebase on the server version.
    await drafts.put(userId, { ...d, payload: fromFields(kind, merged), base: serverPayload, base_version: version,
                               state: "pending", updated_at: Date.now() });
    return true;
  }
  await drafts.put(userId, { ...d, state: "conflict", message, server: { ...current, version }, conflicts });
  return false;
}

async function doSync(userId: string, retryDenied: boolean, round = 0): Promise<SyncResult> {
  const all = await drafts.all(userId);
  if (retryDenied) {
    for (const d of all.filter((x) => x.state === "denied" && RETRYABLE.has(x.code ?? ""))) {
      d.state = "pending";
      await drafts.put(userId, d);
    }
  }
  const list = all.filter((d) => d.state === "pending").sort((a, b) => ORDER[a.kind] - ORDER[b.kind]);
  // Parts of a ride wait until the ride itself reached the server (pending, or still only local).
  const unsyncedRides = new Set(all.filter((d) => d.kind === "ride" && (d.state === "pending" || d.state === "local"))
    .map((d) => d.ride_id));
  const result: SyncResult = { done: 0, confirmed: [], needsLogin: false, offline: false };
  let rebased = false;
  for (const d of list) {
    if (d.kind !== "ride" && d.ride_id && unsyncedRides.has(d.ride_id)) continue; // wait for the ride itself
    try {
      const res = await send(d);
      if (d.kind === "ride") {
        unsyncedRides.delete(d.ride_id);
        const mine = (d.payload.description as string | undefined)?.trim() || null;
        if (res.cancelled) {
          await drafts.put(userId, { ...d, state: "denied", code: "ride_cancelled",
                                     message: "הנסיעה בוטלה על ידי האחראי (למשל ככפולה). הטיוטה נשמרה במכשיר" });
          continue;
        }
        if (!res.created && mine && !res.description) {
          // Retry of an already saved ride whose description is still empty: keep the text as an edit.
          await drafts.put(userId, { ...d, key: keys.description(d.ride_id!), kind: "description",
            payload: { description: mine }, base: { description: null }, base_version: res.description_version ?? 0 });
        } else if (!res.created && mine && res.description && res.description.trim() !== mine) {
          // The ride already exists with a different description: never drop either text.
          await drafts.put(userId, { ...d, key: keys.description(d.ride_id!), kind: "description",
            payload: { description: mine }, base: null, base_version: res.description_version ?? 0, state: "conflict",
            message: "לנסיעה הזו כבר נשמר תיאור אחר", conflicts: ["description"],
            server: { description: res.description, version: res.description_version } });
        }
      }
      await drafts.remove(userId, d.key);
      result.done++;
      result.confirmed.push(d.key);
    } catch (e) {
      if (!(e instanceof ApiError) || isUnreachable(e)) { result.offline = true; break; } // retry later
      if (e.status === 401) { result.needsLogin = true; break; } // keep everything pending; sign in again
      if (e.status === 409 && e.code === "version_conflict" && d.kind !== "ride") {
        rebased = (await handleConflict(userId, d, (e.extra.current as Values) ?? null, e.message)) || rebased;
        continue;
      }
      await drafts.put(userId, { ...d, state: "denied", code: e.code, message: e.message,
                                 server: (e.extra.current as Record<string, unknown>) ?? null });
    }
  }
  if (rebased && round < 2) {
    const next = await doSync(userId, false, round + 1); // send the merged drafts right away
    return { done: result.done + next.done, confirmed: [...result.confirmed, ...next.confirmed],
             needsLogin: next.needsLogin, offline: next.offline };
  }
  return result;
}

export function newRideId(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  // Fallback for non-secure contexts (e.g. testing over a LAN IP): RFC 4122 v4 from getRandomValues.
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((x) => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}
