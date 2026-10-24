// MDA youth app — service worker for offline reopening (D-44).
// Caches only the application shell (HTML, hashed JS/CSS, icons). API responses are NEVER cached
// here: personal data offline lives only in the per-account IndexedDB copy with its access limit.
const CACHE = "mda-shell-v1";

// Cache the shell and the hashed assets it references, so the very first visit is enough.
async function cacheShell() {
  const cache = await caches.open(CACHE);
  const res = await fetch("/", { cache: "no-store" });
  if (!res.ok) return;
  const html = await res.clone().text();
  await cache.put("/", res);
  const assets = [...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]);
  await cache.addAll(assets);
}

self.addEventListener("install", (event) => {
  event.waitUntil(cacheShell().then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  if (req.mode === "navigate") {
    // Network first so a new deployment is picked up; the cached shell when offline.
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put("/", copy));
          }
          return res;
        })
        .catch(() => caches.match("/")),
    );
    return;
  }

  // Hashed build assets never change: cache first, fill the cache on first use.
  event.respondWith(
    caches.match(req).then((hit) => hit ?? fetch(req).then((res) => {
      if (res.ok && url.pathname.startsWith("/assets/")) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
      }
      return res;
    })),
  );
});

// ---- Web Push (initial flow, 2026-10-17). Same worker as the offline shell above. ----
// The push payload is generic and carries no personal data; a notification click opens a same-origin app path.
function safeAppPath(path) {
  return typeof path === "string" && /^\/(?!\/)[^\s\\]*$/.test(path) ? path : "/";
}

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = {}; }
  const title = typeof data.title === "string" ? data.title.slice(0, 80) : "מד״א";
  const body = typeof data.body === "string" ? data.body.slice(0, 200) : "";
  const tag = typeof data.tag === "string" ? data.tag.slice(0, 40) : undefined;
  event.waitUntil(self.registration.showNotification(title, {
    body, tag, dir: "rtl", lang: "he",
    icon: "/brand/mda-icon-192.png", badge: "/brand/mda-icon-192.png",
    data: { url: safeAppPath(data.url) },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(safeAppPath(event.notification.data && event.notification.data.url), self.location.origin).href;
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of windows) {
      if (new URL(client.url).origin === self.location.origin) {
        await client.focus();
        try { await client.navigate(target); } catch (e) { /* keep the window as it is */ }
        return;
      }
    }
    return self.clients.openWindow(target);
  })());
});
