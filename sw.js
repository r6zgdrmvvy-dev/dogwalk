/* The service worker: what makes this an app you can open on a train.
 *
 * The shell — the page, the engine, the art — is cached on install and served
 * from there afterwards, so the game opens with no network at all. A world you
 * have already built is in localStorage, so opening offline gets you your own
 * town and your own dog rather than an error page.
 *
 * What is deliberately NOT cached:
 *  - Overpass and the weather archive. Stale map data would mean a town that
 *    quietly stops matching the real one, and a cached forecast is a wrong
 *    forecast. Offline, the game already handles "no map" by saying so.
 *  - Anything under /api/. A cached answer to "who am I" is somebody else's
 *    account after a sign-out, which is the worst bug on this list.
 */
const VERSION = "dogwalk-v1";
const SHELL = [
  "./game.html",
  "./index.html",
  "./manifest.webmanifest",
  "./vendor/phaser.min.js",
  "./assets/city.png",
  "./assets/props.png",
  "./assets/dogs.png",
  "./assets/people.png",
  "./assets/critters.png",
  "./assets/city-index.json",
  "./assets/icon-192.png",
  "./assets/icon-512.png",
];

self.addEventListener("install", (e) => {
  // One missing file must not fail the whole install and leave the app with no
  // cache at all, so these are added one at a time and failures are tolerated.
  e.waitUntil(
    caches.open(VERSION).then((c) =>
      Promise.all(SHELL.map((u) => c.add(u).catch(() => null)))
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  const sameOrigin = url.origin === self.location.origin;

  // Accounts are never cached, and neither is anybody else's host.
  if (!sameOrigin || url.pathname.startsWith("/api/")) return;

  // The page itself: network first, so a deploy is picked up on the next load
  // rather than whenever the cache happens to turn over. Falls back to the
  // cached copy the moment there is no network, which is the whole point.
  const isPage = req.mode === "navigate" ||
                 url.pathname.endsWith(".html") ||
                 url.pathname === "/" ||
                 url.pathname.endsWith(".webmanifest");
  if (isPage) {
    e.respondWith(
      fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      }).catch(() => caches.match(req).then((hit) => hit ||
        caches.match("./game.html")))
    );
    return;
  }

  // The art and the engine: cache first. They are big, they do not change
  // without the page changing, and this is what makes a cold offline start
  // as quick as a warm online one.
  e.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((res) => {
      if (res && res.ok) {
        const copy = res.clone();
        caches.open(VERSION).then((c) => c.put(req, copy)).catch(() => {});
      }
      return res;
    }))
  );
});
