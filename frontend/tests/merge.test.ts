// Run with: npm test  (Node's built-in test runner; Node >= 23 strips TypeScript types natively)
import assert from "node:assert/strict";
import { test } from "node:test";
import { GROUPS, fromFields, threeWayMerge, toFields } from "../src/merge.ts";

const A = (markers: Record<string, string | null>, summary: string | null = null) =>
  toFields("assessment", { markers, summary })!;

test("another mentor's marker survives my edit of a different marker", () => {
  const base = A({ pulse: "success" });
  const mine = A({ pulse: "success", breathing: "failure" });
  const server = A({ pulse: "success", blood_pressure: "partial" });
  const r = threeWayMerge(base, mine, server, GROUPS.assessment);
  assert.deepEqual(r.conflicts, []);
  assert.deepEqual(fromFields("assessment", r.merged).markers,
    { blood_pressure: "partial", pulse: "success", breathing: "failure", forms: null });
});

test("both changed the same marker differently -> must choose, never silent", () => {
  const base = A({ pulse: "success" });
  const r = threeWayMerge(base, A({ pulse: "failure" }), A({ pulse: "partial" }), GROUPS.assessment);
  assert.deepEqual(r.conflicts, ["marker.pulse"]);
});

test("same change on both sides is not a conflict", () => {
  const base = A({});
  const r = threeWayMerge(base, A({ forms: "not_performed" }, "ok"), A({ forms: "not_performed" }, " ok "), GROUPS.assessment);
  assert.deepEqual(r.conflicts, []);
});

test("unknown base: every differing field is a conflict", () => {
  const r = threeWayMerge(null, A({ pulse: "success" }), A({ pulse: "success", forms: "failure" }), GROUPS.assessment);
  assert.deepEqual(r.conflicts, ["marker.forms"]);
});

test("description edited on two devices", () => {
  const base = { description: "סחרחורת" };
  assert.deepEqual(threeWayMerge(base, { description: "סחרחורת" }, { description: "סחרחורת קלה" }, GROUPS.description),
    { merged: { description: "סחרחורת קלה" }, conflicts: [] });
  assert.deepEqual(threeWayMerge(base, { description: "נפילה" }, { description: "סחרחורת קלה" }, GROUPS.description).conflicts,
    ["description"]);
});

test("summary text and its 'no comments' flag move together", () => {
  const S = (v: Record<string, unknown>) => toFields("summary", v)!;
  const base = S({});
  const mine = S({ strengths: "יוזמה", improvements_none: true });
  const server = S({ improvements: "קשר עין" });
  const r = threeWayMerge(base, mine, server, GROUPS.summary);
  assert.deepEqual(r.conflicts, ["improvements"]);
  assert.equal(r.merged.strengths, "יוזמה");
});
