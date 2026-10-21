// Service worker mínimo: hace la PWA instalable.
// La cola offline de segmentos de audio vive en IndexedDB (Fase 2) y no depende de esta caché.
const VERSION = "v1";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});
