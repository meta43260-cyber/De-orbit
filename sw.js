const CACHE_NAME = 'reentry-v2.0';
const urlsToCache = [
    '/',
    '/index.html',
    '/script.js',
    '/algorithms.js',
    '/learning.js',
    '/api.js',
    '/manifest.json'
];

// Install
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(urlsToCache))
    );
    self.skipWaiting();
});

// Activate
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys => 
            Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        )
    );
    self.clients.claim();
});

// Fetch
self.addEventListener('fetch', event => {
    event.respondWith(
        caches.match(event.request)
            .then(response => {
                if (response) {
                    // Fetch from network and update cache (stale-while-revalidate)
                    fetch(event.request).then(fetchRes => {
                        caches.open(CACHE_NAME).then(cache => {
                            cache.put(event.request, fetchRes);
                        });
                    }).catch(() => {});
                    return response;
                }
                
                return fetch(event.request).then(response => {
                    if (!response || response.status !== 200) {
                        return response;
                    }
                    
                    const responseClone = response.clone();
                    caches.open(CACHE_NAME).then(cache => {
                        cache.put(event.request, responseClone);
                    });
                    
                    return response;
                });
            })
            .catch(() => caches.match('/index.html'))
    );
});

// Background Sync for recording actual re-entries
self.addEventListener('sync', event => {
    if (event.tag === 'sync-reentry') {
        event.waitUntil(syncReentryData());
    }
});

async function syncReentryData() {
    // Sync learning data to cloud (if you add backend later)
    console.log('Syncing reentry data...');
}
