// Three-way merge for journal drafts (safe conflict resolution, D-45).
//
// Every draft remembers the server values it was edited from ("base"). When the server answers
// 409 version_conflict with its current copy, each field is compared:
//   mine == base            -> I did not touch it: take the server value
//   server == base          -> only I changed it: keep mine
//   mine == server          -> same result on both sides: nothing to decide
//   otherwise               -> real conflict: the person must choose (never resolved silently)
// Without a known base (old drafts) every differing field is a conflict.
// Fields are compared as groups, so text + its "no comments" flag move together.

export type Values = Record<string, unknown>;

export interface MergeResult {
  merged: Values;
  conflicts: string[]; // group names that need a human decision
}

const same = (a: unknown, b: unknown) => JSON.stringify(norm(a)) === JSON.stringify(norm(b));
// "", null and undefined all mean "empty"; text is compared trimmed.
function norm(v: unknown): unknown {
  if (v === undefined || v === null) return null;
  if (typeof v === "string") return v.trim() === "" ? null : v.trim();
  return v;
}

function pick(values: Values | null | undefined, fields: string[]): Values {
  const out: Values = {};
  for (const f of fields) out[f] = values ? norm(values[f]) : null;
  return out;
}

export function threeWayMerge(
  base: Values | null | undefined,
  mine: Values,
  server: Values,
  groups: Record<string, string[]>,
): MergeResult {
  const merged: Values = {};
  const conflicts: string[] = [];
  for (const [name, fields] of Object.entries(groups)) {
    const m = pick(mine, fields);
    const s = pick(server, fields);
    const b = base ? pick(base, fields) : null;
    let take: Values;
    if (same(m, s)) take = m;
    else if (b && same(m, b)) take = s;
    else if (b && same(s, b)) take = m;
    else {
      conflicts.push(name);
      take = m; // provisional; the UI shows both sides until the person chooses
    }
    Object.assign(merged, take);
  }
  return { merged, conflicts };
}

// Field groups per draft kind. Assessment markers are flattened to "marker.<key>".
export const MARKER_KEYS = ["blood_pressure", "pulse", "breathing", "forms"] as const;

export const GROUPS: Record<"description" | "assessment" | "summary", Record<string, string[]>> = {
  description: { description: ["description"] },
  assessment: {
    ...Object.fromEntries(MARKER_KEYS.map((k) => [`marker.${k}`, [`marker.${k}`]])),
    summary: ["summary"],
  },
  summary: { strengths: ["strengths", "strengths_none"], improvements: ["improvements", "improvements_none"] },
};

/** Flatten an assessment {markers:{k:v}, summary} to merge fields and back. */
export function flattenAssessment(a: { markers?: Record<string, unknown> | null; summary?: unknown } | null | undefined): Values {
  const out: Values = { summary: a?.summary ?? null };
  for (const k of MARKER_KEYS) out[`marker.${k}`] = a?.markers?.[k] ?? null;
  return out;
}

export function unflattenAssessment(v: Values): { markers: Record<string, string | null>; summary: string | null } {
  const markers: Record<string, string | null> = {};
  for (const k of MARKER_KEYS) markers[k] = (v[`marker.${k}`] as string | null) ?? null;
  return { markers, summary: (v.summary as string | null) ?? null };
}

/** Values of a draft payload / server copy in merge-field form, per kind. */
export function toFields(kind: "description" | "assessment" | "summary", v: Values | null | undefined): Values | null {
  if (!v) return null;
  if (kind === "assessment") return flattenAssessment(v as { markers?: Record<string, unknown>; summary?: unknown });
  if (kind === "description") return { description: v.description ?? null };
  return {
    strengths: v.strengths ?? null, strengths_none: !!v.strengths_none,
    improvements: v.improvements ?? null, improvements_none: !!v.improvements_none,
  };
}

export function fromFields(kind: "description" | "assessment" | "summary", v: Values): Values {
  if (kind === "assessment") return unflattenAssessment(v);
  if (kind === "description") return { description: v.description ?? null };
  return {
    strengths: v.strengths ?? null, strengths_none: !!v.strengths_none,
    improvements: v.improvements ?? null, improvements_none: !!v.improvements_none,
  };
}
