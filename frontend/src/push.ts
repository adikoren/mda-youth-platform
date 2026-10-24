// Browser side of Web Push: explicit opt-in, self-test, turning off on this device, and detaching on sign-out or
// account change. The server decides the recipient from the signed-in account; the browser only names its device.
import { api } from "./api";
import { urlBase64ToUint8Array, isIosDevice, pushState, type PushState } from "./pushUtil";

interface PushConfig { public_key: string | null; configured: boolean }

function toBase64Url(buf: ArrayBuffer | null): string {
  if (!buf) return "";
  let s = "";
  for (const b of new Uint8Array(buf)) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function iosDevice(): boolean {
  return isIosDevice(navigator.userAgent, navigator.maxTouchPoints);
}

export function standaloneApp(): boolean {
  return window.matchMedia("(display-mode: standalone)").matches || (navigator as { standalone?: boolean }).standalone === true;
}

async function currentSubscription(): Promise<PushSubscription | null> {
  if (!("serviceWorker" in navigator)) return null;
  const reg = await navigator.serviceWorker.ready;
  return reg.pushManager.getSubscription();
}

export async function currentPushState(): Promise<PushState> {
  const hasPush = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const sub = hasPush ? await currentSubscription().catch(() => null) : null;
  return pushState({
    hasPushApi: hasPush, ios: iosDevice(), standalone: standaloneApp(),
    permission: "Notification" in window ? Notification.permission : "unsupported",
    subscribed: !!sub,
  });
}

// Called only from a button click: the browser asks for permission only then.
export async function enablePush(): Promise<string> {
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return "ההתראות לא אושרו. אפשר לאשר בהגדרות הדפדפן או המכשיר.";
  const cfg = await api.get<PushConfig>("/api/push/config");
  if (!cfg.public_key) return "שירות ההתראות אינו מוגדר בשרת עדיין.";
  const reg = await navigator.serviceWorker.ready;
  let sub = await reg.pushManager.getSubscription();
  if (!sub) {
    sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: urlBase64ToUint8Array(cfg.public_key) });
  }
  await api.post("/api/push/subscriptions", {
    endpoint: sub.endpoint,
    keys: { p256dh: toBase64Url(sub.getKey("p256dh")), auth: toBase64Url(sub.getKey("auth")) },
  });
  return "ההתראות הופעלו במכשיר הזה.";
}

export async function disablePush(): Promise<void> {
  const sub = await currentSubscription();
  if (!sub) return;
  await api.post("/api/push/subscriptions/remove", { endpoint: sub.endpoint }).catch(() => undefined);
  await sub.unsubscribe();
}

const RESULTS: Record<string, string> = {
  accepted_by_push_service: "הבקשה נשלחה לשירות ההתראות של הדפדפן. זו אינה אישור שההתראה הופיעה במכשיר. אם לא הופיעה, בדקו הגדרות התראות של המכשיר.",
  subscription_expired: "ההתראות במכשיר הזה כבר לא תקפות. יש להפעיל אותן שוב.",
  temporarily_unavailable: "שירות ההתראות אינו זמין כרגע. נסו שוב מאוחר יותר.",
  rejected_by_push_service: "שירות ההתראות דחה את הבקשה.",
};

export async function sendSelfTest(): Promise<string> {
  const sub = await currentSubscription();
  if (!sub) return "ההתראות לא פעילות במכשיר הזה.";
  try {
    const r = await api.post<{ result: string }>("/api/push/test", { endpoint: sub.endpoint });
    return RESULTS[r.result] ?? "לא ניתן לאשר את התוצאה.";
  } catch (e) {
    const code = (e as { code?: string }).code;
    if (code === "test_rate_limited") return "אפשר לשלוח התראת בדיקה פעם בכמה שניות. נסו שוב מאוחר יותר.";
    if (code === "push_not_configured") return "שירות ההתראות אינו מוגדר בשרת עדיין.";
    return "שליחת התראת הבדיקה נכשלה.";
  }
}

// Sign-out: detach this device from the account and stop its push subscription here. Never blocks sign-out.
export async function detachPushForSignOut(): Promise<void> {
  try {
    const sub = await currentSubscription();
    if (!sub) return;
    await api.post("/api/push/subscriptions/remove", { endpoint: sub.endpoint }).catch(() => undefined);
    await sub.unsubscribe();
  } catch {
    /* no push support or no service worker: nothing to detach */
  }
}

// After a sign-in: if this device's subscription belongs to another account (or to nobody), stop it here, so the
// previous account's notifications do not keep arriving. No permission prompt is shown.
export async function syncPushForAccount(): Promise<void> {
  try {
    const sub = await currentSubscription();
    if (!sub) return;
    const r = await api.post<{ registered_to_me: boolean }>("/api/push/subscriptions/check", { endpoint: sub.endpoint });
    if (!r.registered_to_me) await sub.unsubscribe();
  } catch {
    /* offline or not signed in: try again on the next load */
  }
}
