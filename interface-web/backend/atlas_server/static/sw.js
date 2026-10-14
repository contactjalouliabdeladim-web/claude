/* Service worker minimal : rend Atlas installable sur l'écran d'accueil.
 * Aucun cache : Atlas a toujours besoin du PC (conversation, voix, données en direct). */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
