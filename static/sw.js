self.addEventListener('install', (e) => {
    e.waitUntil(
        caches.open('shift-app').then((cache) => {
            return cache.addAll([
                '/',
                '/login',
                '/dashboard'
            ]);
        })
    );
});

self.addEventListener('fetch', (e) => {
    e.respondWith(
        caches.match(e.request).then((response) => {
            return response || fetch(e.request);
        })
    );
});