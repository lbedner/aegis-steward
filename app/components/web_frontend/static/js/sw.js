/* The service worker: notifications only. No fetch handler, so every
   request still goes to the network exactly as without it.

   A push carries {title, body, url} (app/services/comms/push.py);
   tapping the notification opens ``url`` in the app. */

self.addEventListener('push', (event) => {
  const { title, body, url } = event.data.json();
  event.waitUntil(self.registration.showNotification(title, { body, data: { url } }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(self.clients.openWindow(event.notification.data.url));
});
