/* CrossInk Mail Bridge PWA SW v3 — network-only (no asset cache). */
const SW_VERSION = "v3-20261007";

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  // Required for Chromium installability. Always hit the network.
  event.respondWith(fetch(event.request));
});
