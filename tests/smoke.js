#!/usr/bin/env node
/**
 * Smoke test for game.html. Serves the repo, stubs Overpass with a tiny
 * hand-written fixture so the run is offline and fast, and checks the things
 * that have actually broken before: the splash never clearing, walks not being
 * found in a feed, playback not starting, the HUD panels trapping you on a
 * phone, and silent page errors.
 *
 *   npm install -D playwright && npx playwright install chromium
 *   node tests/smoke.js
 *
 * Exits non-zero on the first failure.
 */
const http = require("http");
const fs = require("fs");
const path = require("path");
const { chromium, devices } = require("playwright");

const ROOT = path.join(__dirname, "..");
const FIXTURE = fs.readFileSync(path.join(__dirname, "fixture-osm.json"), "utf8");
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".json": "application/json", ".png": "image/png" };

let failures = 0;
function check(label, ok, detail) {
  console.log((ok ? "  ok   " : "  FAIL ") + label + (detail === undefined ? "" : "  → " + detail));
  if (!ok) failures++;
}

function serve() {
  const server = http.createServer((req, res) => {
    const rel = decodeURIComponent(req.url.split("?")[0]).replace(/^\/+/, "") || "game.html";
    const file = path.join(ROOT, rel);
    if (!file.startsWith(ROOT) || !fs.existsSync(file) || fs.statSync(file).isDirectory()) {
      res.writeHead(404); res.end("not found"); return;
    }
    res.writeHead(200, { "content-type": TYPES[path.extname(file)] || "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((r) => server.listen(0, "127.0.0.1", () => r(server)));
}

async function stub(page) {
  for (const host of ["overpass-api.de", "overpass.kumi.systems", "overpass.openstreetmap.ru"]) {
    await page.route("https://" + host + "/api/interpreter", (r) =>
      r.fulfill({ status: 200, contentType: "application/json", body: FIXTURE }));
  }
  await page.route("https://fonts.googleapis.com/**", (r) => r.abort());
  await page.route("https://fonts.gstatic.com/**", (r) => r.abort());
}

// The splash only clears once the first map has arrived and the scene is up.
async function waitForReady(page) {
  await page.waitForFunction(
    () => getComputedStyle(document.getElementById("boot")).display === "none",
    null, { timeout: 45000 });
}

(async () => {
  const server = await serve();
  const base = "http://127.0.0.1:" + server.address().port + "/game.html";
  const browser = await chromium.launch();

  try {
    console.log("desktop");
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e.message)));
    await stub(page);
    await page.goto(base);
    await waitForReady(page);

    const boot = await page.evaluate(() => ({
      pct: document.getElementById("boot-pct").textContent,
      walks: document.querySelectorAll(".walk").length,
      km: parseFloat(document.getElementById("s-dist").textContent),
      canvas: !!document.querySelector("#game canvas"),
      fitted: Math.abs(window.game.scene.getScene("world").cameras.main.zoom -
                       window.game.scene.getScene("world").fitZoom) < 1e-6,
    }));
    check("splash reaches 100%", boot.pct === "100%", boot.pct);
    check("bundled walks are found", boot.walks === 2, boot.walks + " rows");
    check("distance is plausible", boot.km > 1 && boot.km < 40, boot.km + " km");
    check("canvas exists", boot.canvas);
    check("opens zoomed to fit", boot.fitted);

    // zoom buttons step and clamp
    const z0 = await page.evaluate(() => window.game.scene.getScene("world").targetZoom);
    await page.click("#btn-zoom-in");
    await page.waitForTimeout(400);
    const z1 = await page.evaluate(() => window.game.scene.getScene("world").targetZoom);
    check("zoom in increases zoom", Number.isFinite(z0) && Number.isFinite(z1) && z1 > z0,
          z0 + " → " + z1);
    for (let i = 0; i < 4; i++) { await page.click("#btn-zoom-out"); await page.waitForTimeout(250); }
    const clamped = await page.evaluate(() => {
      const s = window.game.scene.getScene("world");
      return Math.abs(s.targetZoom - s.fitZoom) < 1e-6;
    });
    check("zoom out clamps at fit", clamped);

    // The time-of-day grade is a world-space object: scrollFactor(0) pins it
    // against scrolling but not against zoom, so it used to shrink to a patch in
    // the middle of the screen when zoomed out and tint only that.
    const grade = await page.evaluate(() => {
      const s = window.game.scene.getScene("world");
      const v = s.cameras.main.worldView, g = s.grade;
      return { dx: Math.abs(g.x - v.x), dy: Math.abs(g.y - v.y),
               dw: Math.abs(g.width - v.width), dh: Math.abs(g.height - v.height),
               view: Math.round(v.width) };
    });
    check("grade covers the whole view when zoomed out",
          grade.dx < 12 && grade.dy < 12 && grade.dw < 12 && grade.dh < 12,
          JSON.stringify(grade));

    // playback
    await page.click(".walk:nth-child(1)");
    await page.waitForFunction(
      () => getComputedStyle(document.getElementById("loader")).display === "none" &&
            !document.getElementById("transport").classList.contains("off"),
      null, { timeout: 45000 });
    await page.waitForTimeout(4000);
    const play = await page.evaluate(() => ({
      clock: document.getElementById("clock").textContent,
      scrub: Number(document.getElementById("scrub").value),
      followed: !!window.game.scene.getScene("world").cameras.main._follow,
      turned: window.game.scene.getScene("world").dog.rotation,
    }));
    check("clock is running", /\d/.test(play.clock), JSON.stringify(play.clock));
    check("scrubber advances", play.scrub > 0, play.scrub);
    check("camera follows the dog", play.followed);
    check("dog is a finite heading", Number.isFinite(play.turned), play.turned);

    // keyboard: space pauses
    await page.keyboard.press("Space");
    await page.waitForTimeout(300);
    const paused = await page.evaluate(() => document.getElementById("btn-play").textContent);
    check("space toggles play", paused === "Play", paused);

    // Weather comes from Open-Meteo; the stub is offline so it should fail
    // gracefully and simply not claim any weather.
    check("clock survives no weather data", !/NaN|undefined/.test(play.clock), play.clock);

    // Dragging during playback takes the camera off the dog and offers it back.
    await page.mouse.move(640, 400);
    await page.mouse.down();
    await page.mouse.move(400, 260, { steps: 6 });
    await page.mouse.up();
    await page.waitForTimeout(400);
    const panned = await page.evaluate(() => ({
      free: window.game.scene.getScene("world").freeCam,
      following: !!window.game.scene.getScene("world").cameras.main._follow,
      btn: getComputedStyle(document.getElementById("btn-follow")).display,
    }));
    check("panning releases the camera", panned.free && !panned.following, JSON.stringify(panned));
    check("re-centre button appears", panned.btn !== "none", panned.btn);
    await page.click("#btn-follow");
    await page.waitForTimeout(400);
    check("re-centre picks the dog back up", await page.evaluate(
      () => !!window.game.scene.getScene("world").cameras.main._follow));

    check("no page errors", errors.length === 0, errors.slice(0, 2).join(" | "));
    await page.close();

    console.log("no map data available");
    const off = await browser.newPage({ viewport: { width: 1024, height: 720 } });
    const offErrors = [];
    off.on("pageerror", (e) => offErrors.push(String(e.message)));
    for (const host of ["overpass-api.de", "overpass.kumi.systems", "overpass.openstreetmap.ru"]) {
      await off.route("https://" + host + "/api/interpreter", (r) => r.abort());
    }
    await off.route("https://fonts.googleapis.com/**", (r) => r.abort());
    await off.goto(base);
    await waitForReady(off);
    check("still playable without a map", (await off.$$(".walk")).length === 2);
    check("no page errors", offErrors.length === 0, offErrors.slice(0, 2).join(" | "));
    await off.close();

    console.log("mobile");
    const ctx = await browser.newContext({ ...devices["iPhone 13"] });
    const m = await ctx.newPage();
    const mErrors = [];
    m.on("pageerror", (e) => mErrors.push(String(e.message)));
    await stub(m);
    await m.goto(base);
    await waitForReady(m);
    check("no horizontal overflow", !(await m.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth + 1)));
    check("walks panel starts closed", await m.evaluate(
      () => document.getElementById("walks").classList.contains("hidden")));
    await m.tap("#btn-walks");
    await m.waitForTimeout(400);
    await m.tap(".walk");
    await m.waitForFunction(
      () => !document.getElementById("transport").classList.contains("off"),
      null, { timeout: 45000 });
    await m.tap("#btn-walks-close");
    await m.waitForTimeout(300);
    check("walks panel closes again", await m.evaluate(
      () => document.getElementById("walks").classList.contains("hidden")));
    await m.tap("#btn-stop");
    await m.waitForTimeout(300);
    check("transport closes again", await m.evaluate(
      () => document.getElementById("transport").classList.contains("off")));
    check("no page errors", mErrors.length === 0, mErrors.slice(0, 2).join(" | "));
  } finally {
    await browser.close();
    server.close();
  }

  console.log(failures ? "\n" + failures + " check(s) failed" : "\nall checks passed");
  process.exit(failures ? 1 : 0);
})();
