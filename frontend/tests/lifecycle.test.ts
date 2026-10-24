// Run with: npm test  (Node's built-in test runner)
import assert from "node:assert/strict";
import { test } from "node:test";
import { dayLifecycle, shiftLifecycle, type LifecycleShift } from "../src/lifecycle.ts";

const NAMES = { morning: "בוקר", reinforcement: "תגבור", evening: "ערב" };
const open = { window: "open", published: false };
const shift = (over: Partial<LifecycleShift> = {}): LifecycleShift =>
  ({ kind: "evening", state: "before", direct: true, request: false, available: null, cancelled_at: null, ...over });

test("unpublished, open, not started, unanswered: the only place 'עדיין לא נרשמת' appears", () => {
  assert.equal(shiftLifecycle(shift(), open).label, "עדיין לא נרשמת");
});

test("published and unregistered: no registration prompt, the schedule is shown instead", () => {
  const pub = { window: "open", published: true };
  const s = shift({ direct: false });
  assert.equal(shiftLifecycle(s, pub).label, "הסידור פורסם");
  assert.equal(dayLifecycle({ ...pub, shifts: [s] }, NAMES).label, "הסידור פורסם");
});

test("closed before publication: 'ההרשמה נסגרה'", () => {
  const closed = { window: "closed_late_allowed", published: false };
  assert.equal(shiftLifecycle(shift({ direct: false, request: true }), closed).label, "ההרשמה נסגרה");
});

test("started and ended shifts show the lifecycle status, never a prompt", () => {
  assert.equal(shiftLifecycle(shift({ state: "started", direct: false }), open).label, "המשמרת כבר התחילה");
  assert.equal(shiftLifecycle(shift({ state: "ended", direct: false }), open).label, "המשמרת הסתיימה");
});

test("an explicit cancellation stays 'בוטל' whatever the schedule says", () => {
  const s = shift({ available: true, cancelled_at: "2026-10-17T10:00:00Z", direct: false });
  assert.equal(shiftLifecycle(s, { window: "open", published: true }).label, "בוטל");
  assert.equal(dayLifecycle({ ...open, shifts: [s] }, NAMES).label, "בוטל");
});

test("a published date keeps the volunteer's own answer visible on the card", () => {
  const s = shift({ available: true, direct: false });
  assert.equal(shiftLifecycle(s, { window: "open", published: true }).label, "נרשמת");
  assert.equal(dayLifecycle({ window: "open", published: true, shifts: [s] }, NAMES).label, "נרשמת · ערב");
});

test("reinforcement and morning follow the same rules as evening", () => {
  for (const kind of ["morning", "reinforcement"]) {
    assert.equal(shiftLifecycle(shift({ kind }), open).label, "עדיין לא נרשמת", kind);
    assert.equal(shiftLifecycle(shift({ kind, direct: false }), { window: "open", published: true }).label, "הסידור פורסם", kind);
  }
});

import { countdownParts, DAY_MS, HOUR_MS } from "../src/lifecycle.ts";

test("countdown: 24 hours or more shows whole days, rounded up, never zero", () => {
  assert.deepEqual(countdownParts(DAY_MS), { text: "ההרשמה נסגרת בעוד יום אחד" });
  assert.equal(countdownParts(DAY_MS + 1).text, "ההרשמה נסגרת בעוד 2 ימים");
  assert.equal(countdownParts(3 * DAY_MS - 1).text, "ההרשמה נסגרת בעוד 3 ימים");
});

test("countdown: under 24 hours shows HH:MM hours, zero padded", () => {
  assert.deepEqual(countdownParts(5 * HOUR_MS + 12 * 60000), { text: "ההרשמה נסגרת בעוד", clock: "05:12", unit: "שעות" });
  assert.equal(countdownParts(DAY_MS - 1).clock, "23:59");
  assert.equal(countdownParts(0).clock, "00:00");
});

test("published precedence: a published date never shows the countdown or a registration prompt", () => {
  const s = shift({ direct: true }); // would be open if it were not published
  assert.equal(dayLifecycle({ window: "open", published: true, shifts: [s] }, NAMES).label, "הסידור פורסם");
  assert.equal(shiftLifecycle(s, { window: "open", published: true }).label, "הסידור פורסם");
});
