// Pure helpers for Web Push (no browser APIs here, so they can be unit-tested with Node).

// VAPID public key (base64url) to the byte array the Push API expects.
export function urlBase64ToUint8Array(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = base64url.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(base64url.length / 4) * 4, "=");
  const raw = atob(padded);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

// Notification click destinations: same-origin application paths only ("/x"; never "//host" or a full URL).
export function safeAppPath(path: unknown): string {
  return typeof path === "string" && /^\/(?!\/)[^\s\\]*$/.test(path) ? path : "/";
}

// iPhone and iPad (including iPadOS, which reports itself as a Mac with touch).
export function isIosDevice(userAgent: string, touchPoints: number): boolean {
  return /iPad|iPhone|iPod/.test(userAgent) || (/Macintosh/.test(userAgent) && touchPoints > 1);
}

export type PushState = "unsupported" | "needs_home_screen" | "denied" | "not_enabled" | "enabled";

// The state a user can act on. Capabilities are detected, not assumed from the device name alone.
export function pushState(input: {
  hasPushApi: boolean; ios: boolean; standalone: boolean; permission: NotificationPermission | "unsupported";
  subscribed: boolean;
}): PushState {
  if (input.permission === "unsupported") return input.ios ? "needs_home_screen" : "unsupported";
  if (!input.hasPushApi) return input.ios && !input.standalone ? "needs_home_screen" : "unsupported";
  if (input.permission === "denied") return "denied";
  if (input.permission === "granted" && input.subscribed) return "enabled";
  return "not_enabled";
}
