// PixSieve Service Worker — caches the app shell for instant loading
const CACHE_NAME = 'pixsieve-v4';
const APP_SHELL = [
    '/',
    '/static/css/app.css',
    '/static/js/app.js',
    '/static/js/editor.js',
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(APP_SHELL))
            .then(() => self.skipWaiting())
            .catch(err => {
                console.error('[SW] Install failed:', err);
                // Allow activation even if precache fails — network-first still works
            })
    );
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);

    // Never cache API calls or thumbnails
    if (url.pathname.startsWith('/api/')) return;

    // Network-first for app shell, fall back to cache
    event.respondWith(
        fetch(event.request)
            .then(response => {
                // Cache successful responses for app shell resources
                if (response.ok && APP_SHELL.includes(url.pathname)) {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
                }
                return response;
            })
            .catch(() => caches.match(event.request))
    );
});
