const CACHE = "reticulum-admin-offline-v2";
const OFFLINE = "/static/offline.html";
self.addEventListener("install", event => event.waitUntil(caches.open(CACHE).then(cache => cache.add(OFFLINE))));
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("push", event => {
  let payload = {};
  try { payload = event.data ? event.data.json() : {}; } catch (_) { payload = {}; }
  const notification = payload.notification || {};
  let navigate = "/messaging";
  try {
    const candidate = new URL(notification.navigate, self.location.origin);
    if (candidate.origin === self.location.origin && candidate.pathname.startsWith("/messaging/")) navigate = candidate.pathname + candidate.search;
  } catch (_) {}
  const badge = Number(payload.app_badge);
  // Declarative-capable browsers use this call as a replacement notification,
  // never alongside the proposed declarative one. Older browsers use it as
  // their normal imperative fallback, so both paths present exactly one alert.
  const tasks = [self.registration.showNotification(notification.title || "Reticulum Admin", {body: notification.body || "New LXMF message", data: {navigate}, tag: notification.tag || undefined, silent: notification.silent === true})];
  if (typeof self.navigator?.setAppBadge === "function" && Number.isFinite(badge)) tasks.push(self.navigator.setAppBadge(badge));
  event.waitUntil(Promise.all(tasks));
});
self.addEventListener("message", event => {
  // This is a foreground page badge refresh only. It is deliberately not
  // treated as evidence that a background push event reached the device.
  const unread = event.data && event.data.type === "foreground-unread" && event.data.unread;
  if (typeof self.navigator?.setAppBadge !== "function") return;
  if (Number.isFinite(unread) && unread > 0) self.navigator.setAppBadge(unread);
  else self.navigator.clearAppBadge();
});
self.addEventListener("notificationclick", event => {
  event.notification.close();
  const target = event.notification.data.navigate || "/messaging";
  const offline = `${OFFLINE}?target=${encodeURIComponent(target)}`;
  event.waitUntil(fetch(target, {credentials: "include", cache: "no-store"}).then(() => clients.matchAll({type: "window", includeUncontrolled: true})).then(windows => windows[0] ? windows[0].focus().then(() => windows[0].navigate(target)) : clients.openWindow(target)).catch(() => clients.openWindow(offline)));
});
self.addEventListener("fetch", event => {if(event.request.mode === "navigate") event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE)));});
