// Spare service worker: fast repeat visits and an offline page.
// Pages always come from the network first (prices and availability change constantly);
// static files are served from cache and refreshed in the background.
const VERSION = "spare-v7";
// Static file URLs carry ?v=<hash>, so a changed file is a new URL and is always fetched fresh.
const PRECACHE = ["/offline", "/static/favicon.svg", "/static/icons/icon-192.png"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(VERSION).then((c) => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);

  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("/offline")));
    return;
  }
  // Unversioned copies of the stylesheet/script are never served from cache.
  if (url.pathname.startsWith("/static/") && /\.(css|js)$/.test(url.pathname) && !url.searchParams.has("v")) return;
  const cacheable = (url.origin === location.origin && url.pathname.startsWith("/static/")) ||
                    url.hostname.endsWith("fonts.gstatic.com") || url.hostname.endsWith("fonts.googleapis.com");
  if (!cacheable) return;
  event.respondWith(
    caches.open(VERSION).then((cache) =>
      cache.match(req).then((hit) => {
        const fresh = fetch(req).then((res) => {
          if (res.ok || res.type === "opaque") cache.put(req, res.clone());
          return res;
        }).catch(() => hit);
        return hit || fresh;
      })
    )
  );
});

// Push notifications: sales, ticket alerts, event-day reminders.
self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { title: "Spare", body: event.data && event.data.text() }; }
  event.waitUntil(self.registration.showNotification(data.title || "Spare", {
    body: data.body || "",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    tag: data.tag || undefined,
    data: { url: data.url || "/" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data && event.notification.data.url || "/", self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url.startsWith(self.location.origin) && "focus" in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
