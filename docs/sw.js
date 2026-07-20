/* Service worker מינימלי — מטרתו העיקרית: לאפשר התקנה כאפליקציה אמיתית (WebAPK) באנדרואיד Chrome.
   אסטרטגיה: network-first. כשיש רשת → תמיד מהשרת (בלי cache תקוע, מנגנון הגרסה ממשיך לעבוד).
   שומר עותק רק כגיבוי לאופליין. */
const CACHE = 'gb-dash-shell-v1';

self.addEventListener('install', e => { self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(self.clients.claim()); });

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;                     // רק GET
  if (new URL(req.url).origin !== self.location.origin) return; // רק אותו דומיין
  e.respondWith(
    fetch(req)
      .then(res => {
        try { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); } catch (_) {}
        return res;                                      // רשת תמיד גוברת כשאונליין
      })
      .catch(() => caches.match(req))                    // אופליין → הגיבוי האחרון
  );
});
