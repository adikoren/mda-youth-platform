// Run with: npm test
import assert from "node:assert/strict";
import { test } from "node:test";
import { isIosDevice, pushState, safeAppPath, urlBase64ToUint8Array } from "../src/pushUtil.ts";

test("VAPID public key converts from base64url to bytes", () => {
  const bytes = urlBase64ToUint8Array("BAgQ-_"); // 6 chars = 4 whole bytes: 0x04 0x08 0x10 0xfb
  assert.deepEqual([...bytes], [0x04, 0x08, 0x10, 0xfb]);
});

test("notification destinations are same-origin application paths only", () => {
  assert.equal(safeAppPath("/notifications"), "/notifications");
  assert.equal(safeAppPath("/schedule/abc?x=1"), "/schedule/abc?x=1");
  for (const bad of ["//evil.test/x", "https://evil.test/", "javascript:alert(1)", "/a\\b", "", undefined, 5]) {
    assert.equal(safeAppPath(bad), "/", String(bad));
  }
});

test("iPhone and iPad are recognised, including iPadOS desktop mode", () => {
  assert.equal(isIosDevice("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", 5), true);
  assert.equal(isIosDevice("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", 5), true);   // iPadOS
  assert.equal(isIosDevice("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", 0), false);  // desktop Mac
  assert.equal(isIosDevice("Mozilla/5.0 (Linux; Android 14; Pixel 8)", 5), false);
});

test("state follows capability and permission, not the device name", () => {
  const base = { hasPushApi: true, ios: false, standalone: false, subscribed: false };
  assert.equal(pushState({ ...base, permission: "default" }), "not_enabled");
  assert.equal(pushState({ ...base, permission: "denied" }), "denied");
  assert.equal(pushState({ ...base, permission: "granted", subscribed: true }), "enabled");
  assert.equal(pushState({ ...base, permission: "granted", subscribed: false }), "not_enabled");
  assert.equal(pushState({ ...base, hasPushApi: false, permission: "unsupported" }), "unsupported");
  // iPhone in Safari tab: no push API until added to the home screen
  assert.equal(pushState({ ...base, ios: true, hasPushApi: false, permission: "unsupported" }), "needs_home_screen");
  assert.equal(pushState({ ...base, ios: true, standalone: true, permission: "default" }), "not_enabled");
});
