/* pm service worker: caches static assets only. Never HTML, never /events, never POST. */
'use strict';
const CACHE = 'pm-static-v1';
const STATIC_PREFIX = '/static/';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (!url.pathname.startsWith(STATIC_PREFIX)) return; // pages, /events, /a/*, API: network only
  if (req.headers.get('accept') === 'text/event-stream') return;
  event.respondWith(
    fetch(req).then((resp) => {
      const type = resp.headers.get('content-type') || '';
      if (resp.ok && !type.startsWith('text/html')) {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
      }
      return resp;
    }).catch(() => caches.match(req))
  );
});
