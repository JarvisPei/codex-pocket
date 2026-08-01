self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const threadId = String(event.notification.data?.threadId || "");
  const targetUrl = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({
      type: "window",
      includeUncontrolled: true,
    });
    if (clients.length) {
      const client = clients[0];
      client.postMessage({ type: "open-thread", threadId });
      await client.focus();
      return;
    }
    await self.clients.openWindow(targetUrl);
  })());
});
