/* Dashboard notification click handling. Production Workbox imports this
   script; development registers it directly because it has no fetch handler. */
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const path =
    event.notification.data && typeof event.notification.data.path === "string"
      ? event.notification.data.path
      : "/";
  const scope = self.registration.scope;
  const target = new URL(path.replace(/^\/+/, ""), scope).href;
  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then(async (windowClients) => {
        const client = windowClients.find((item) => item.url.startsWith(scope));
        if (client) {
          await client.focus();
          client.postMessage({ type: "octop-notify-open", path });
          return;
        }
        if (self.clients.openWindow) await self.clients.openWindow(target);
      }),
  );
});

if ((self.location.pathname || "").endsWith("/notify-sw.js")) {
  self.addEventListener("install", () => self.skipWaiting());
  self.addEventListener("activate", (event) => {
    event.waitUntil(self.clients.claim());
  });
}
