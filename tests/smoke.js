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
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });

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
    check("bundled walks are found", boot.walks === 6, boot.walks + " rows");
    check("distance is plausible", boot.km > 5 && boot.km < 40, boot.km + " km");
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

    // A shop in the fixture has to come out as a shop: flat felted roof,
    // shopfront onto the pavement, and its name on the map. Picking roof and
    // wall purely by hash (as an earlier version did) drew the whole town as
    // identical bungalows.
    const shops = await page.evaluate(() => {
      const s = window.game.scene.getScene("world");
      let shopWall = 0, flatRoof = 0, pitched = 0;
      s.layer.forEachTile((t) => {
        if (t.index >= 63) shopWall++;
        else if (t.index >= 46 && t.index < 51) flatRoof++;
        else if (t.index >= 26 && t.index < 46) pitched++;
      });
      return { shopWall, flatRoof, pitched,
               places: (s.worldRef.labels || []).filter((l) => l.kind === "place").length };
    });
    check("shops get a flat roof and a shopfront",
          shops.shopWall > 0 && shops.flatRoof > 0, JSON.stringify(shops));
    check("houses keep their pitched roofs", shops.pitched > shops.flatRoof, shops.pitched);
    check("named shops are labelled", shops.places > 0, shops.places);

    // No name may be printed through another. Labels are laid down park-first,
    // then shops, then streets, and anything that would collide is dropped.
    const overlap = await page.evaluate(() => {
      const s = window.game.scene.getScene("world");
      const ls = s.labelGroup.getChildren();
      const box = (t) => {
        const ca = Math.abs(Math.cos(t.rotation)), sa = Math.abs(Math.sin(t.rotation));
        const bw = t.width * ca + t.height * sa, bh = t.width * sa + t.height * ca;
        return { x0: t.x - bw / 2, x1: t.x + bw / 2, y0: t.y - bh / 2, y1: t.y + bh / 2 };
      };
      let over = 0, worst = null;
      for (let i = 0; i < ls.length; i++) for (let j = i + 1; j < ls.length; j++) {
        const a = box(ls[i]), b = box(ls[j]);
        if (a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0) {
          over++; if (!worst) worst = ls[i].text + " / " + ls[j].text;
        }
      }
      return { n: ls.length, over, worst };
    });
    check("no two labels overlap", overlap.over === 0,
          overlap.n + " labels, " + overlap.over + " clashes " + (overlap.worst || ""));

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

    // Weather animation. Open-Meteo is offline in this run, so the hourly rows
    // are injected directly and the scene is asked what it drew: rain streaks
    // and splashes in a shower, flakes and no splashes below freezing, litter
    // on the wind in a gale, drifting cloud shadows under broken cloud and none
    // under a clear sky.
    const wx = async (row) => {
      await page.evaluate((r) => {
        const d = window.dogwalk;
        d.state.wx = {};
        for (const p of d.state.points) {
          d.state.wx[new Date(p.t).toISOString().slice(0, 14) + "00"] = r;
        }
        window.game.scene.getScene("world").cloudDrift = { x: 0, y: 0 };
      }, row);
      await page.waitForTimeout(1800);
      return page.evaluate(() => {
        const s = window.game.scene.getScene("world");
        return { fall: s.rain ? s.rainKind : null,
                 drops: s.rain ? s.rain.getAliveParticleCount() : 0,
                 splash: s.splash ? s.splash.getAliveParticleCount() : 0,
                 gust: s.gust ? s.gust.getAliveParticleCount() : 0,
                 clouds: s.clouds.visible, drift: Math.abs(s.cloudDrift.x) };
      });
    };                                       // [mm, °C, cloud %, wind km/h]
    const shower = await wx([1.4, 12, 80, 14]);
    check("rain falls and splashes", shower.fall === "raindrop" &&
          shower.drops > 0 && shower.splash > 0, JSON.stringify(shower));
    const snow = await wx([1.2, 0.5, 85, 12]);
    check("below freezing it snows instead", snow.fall === "snowflake" &&
          snow.drops > 0 && snow.splash === 0, JSON.stringify(snow));
    const gale = await wx([3.0, 9, 70, 52]);
    check("a gale blows litter about", gale.gust > 0, JSON.stringify(gale));
    const broken = await wx([0, 14, 55, 18]);
    check("broken cloud casts drifting shadows",
          broken.clouds && broken.drift > 0 && broken.fall === null,
          JSON.stringify(broken));
    const clear = await wx([0, 17, 5, 6]);
    check("a clear sky is left alone",
          !clear.clouds && clear.fall === null && clear.gust === 0,
          JSON.stringify(clear));

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

    // walk list ordering
    await page.click('#walk-sort .chip[data-sort="longest"]');
    await page.waitForTimeout(300);
    const order = await page.$$eval(".walk .m", (els) =>
      els.map((e) => parseFloat(e.textContent)));
    const descending = order.every((v, i) => i === 0 || order[i - 1] >= v);
    check("longest-first ordering", descending, order.join(", "));
    await page.click('#walk-sort .chip[data-sort="recent"]');
    await page.waitForTimeout(300);

    // stats panel: charts drawn, hue applied, hover readout, table view present
    await page.keyboard.press("Escape");
    await page.click("#btn-stats");
    await page.waitForTimeout(700);
    const stats = await page.evaluate(() => ({
      open: getComputedStyle(document.getElementById("stats-panel")).display !== "none",
      charts: document.querySelectorAll("#stats-body .chart").length,
      bars: document.querySelectorAll("#stats-body .bar").length,
      fill: document.querySelector("#stats-body .bar")
        ? getComputedStyle(document.querySelector("#stats-body .bar")).fill : "",
      kpis: document.querySelectorAll("#stats-body .kpi").length,
      hero: (document.querySelector("#stats-body .hero b") || {}).textContent,
    }));
    check("stats panel opens", stats.open);
    check("three charts drawn", stats.charts === 3, stats.charts);
    check("bars are drawn and coloured", stats.bars > 0 && stats.fill !== "rgb(0, 0, 0)",
          stats.bars + " bars, fill " + stats.fill);
    check("kpi tiles present", stats.kpis >= 5, stats.kpis);
    check("hero figure is a number", /^[\d.]+$/.test(stats.hero || ""), stats.hero);
    const hit = await page.$$("#stats-body rect.hit");
    if (hit.length) {
      await hit[Math.floor(hit.length / 2)].hover();
      await page.waitForTimeout(300);
    }
    check("hover readout appears", await page.evaluate(
      () => getComputedStyle(document.getElementById("stats-tip")).opacity === "1"));
    await page.click("#btn-table");
    await page.waitForTimeout(300);
    check("table view lists every walk", await page.evaluate(
      () => document.querySelectorAll("#stats-table tbody tr").length) === 6);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(300);
    check("escape closes stats", await page.evaluate(
      () => getComputedStyle(document.getElementById("stats-panel")).display === "none"));

    // Free roam: you walk, he follows. Frame rate under a headless browser is
    // nothing like 60fps, so distances are checked as rates against the clock
    // actually elapsed rather than against the wall clock.
    await page.keyboard.press("Escape");
    await page.click("#btn-roam");
    await page.waitForTimeout(300);
    check("roam offers a choice of walker", await page.evaluate(() =>
      document.getElementById("roam-panel").classList.contains("open") &&
      document.querySelectorAll("#roam-who .who").length === 2));

    // Your dog: breed, colour, collar and name. The collar is a separate
    // tintable sprite rather than baked into the sheet, which is what keeps
    // this from being seven hundred frames to offer six colours.
    const pickers = await page.evaluate(() => ({
      types: document.querySelectorAll("#pick-type button").length,
      coats: document.querySelectorAll("#pick-coat button").length,
      collars: document.querySelectorAll("#pick-collar button").length,
      name: !!document.getElementById("roam-dog-name"),
    }));
    check("you can describe your own dog",
          pickers.types === 5 && pickers.coats === 6 && pickers.collars === 6 && pickers.name,
          JSON.stringify(pickers));
    await page.fill("#roam-dog-name", "Bramble");
    await page.click("#pick-type button:nth-child(3)");     // spaniel
    await page.click("#pick-coat button:nth-child(2)");     // golden
    await page.click("#pick-collar button:nth-child(2)");   // red
    await page.waitForTimeout(400);
    const picked = await page.evaluate(() => ({
      type: window.dogwalk.state.dogType, coat: window.dogwalk.state.dogCoat,
      collar: window.dogwalk.state.dogCollar, name: window.dogwalk.state.dogName,
      saved: !!localStorage.getItem("dogwalk.dog.v1"),
      title: document.title,
    }));
    check("the choice sticks and is remembered",
          picked.type === "spaniel" && picked.coat === "golden" &&
          picked.collar === "red" && picked.name === "Bramble" && picked.saved,
          JSON.stringify(picked));
    check("the page takes his name", /Bramble/.test(picked.title), picked.title);
    await page.click('#roam-who .who[data-who="him"]');
    await page.click("#btn-roam-go");
    await page.waitForTimeout(1200);

    const roamProbe = () => page.evaluate(() => {
      const s = window.game.scene.getScene("world"), r = window.dogwalk.roam;
      return { on: r.on, x: r.x, y: r.y, metres: r.metres,
               gap: Math.hypot(r.x - r.dog.x, r.y - r.dog.y),
               inWall: s.blocked(r.x, r.y), dogInWall: s.solidAt(r.dog.x, r.dog.y),
               follow: !!s.cameras.main._follow, anim: s.walker.anims.currentAnim.key,
               where: document.getElementById("roam-where").textContent };
    });
    const r0 = await roamProbe();
    check("roaming starts on open ground", r0.on && !r0.inWall, JSON.stringify(r0));
    check("camera follows the walker", r0.follow);

    // The dog on screen is the dog you described, and the collar rides on him.
    const look = await page.evaluate(() => {
      const s = window.game.scene.getScene("world"), d = window.dogwalk;
      return { anim: s.dog.anims.currentAnim.key, frame: s.dog.frame.name,
               want: d.dogFrame(d.state.dogType, d.state.dogCoat),
               collar: s.dogCollar.frame.name,
               wantCollar: d.collarFrame(d.state.dogType),
               tint: s.dogCollar.tintTopLeft,
               onDog: Math.abs(s.dogCollar.x - s.dog.x) < 0.01 &&
                      Math.abs(s.dogCollar.y - s.dog.y) < 0.01 };
    });
    check("the dog on screen is the one you picked",
          look.frame === look.want && /spaniel-golden/.test(look.anim), JSON.stringify(look));
    check("the collar is his colour and stays on him",
          look.collar === look.wantCollar && look.tint === 0xc4402f && look.onDog,
          JSON.stringify(look));

    // Whichever way is actually open — a real start point is on a pavement,
    // which may run north-south, so "east" is not guaranteed to be walkable.
    const way = await page.evaluate(() => {
      const s = window.game.scene.getScene("world"), r = window.dogwalk.roam;
      const dirs = [["ArrowRight", 1, 0], ["ArrowLeft", -1, 0],
                    ["ArrowDown", 0, 1], ["ArrowUp", 0, -1]];
      for (const [key, dx, dy] of dirs) {
        if (!s.blocked(r.x + dx * 4, r.y + dy * 4)) return { key, dx, dy };
      }
      return null;
    });
    check("there is somewhere to walk from the start", !!way, JSON.stringify(way));
    await page.keyboard.down(way.key);
    await page.waitForTimeout(2500);
    const rMid = await roamProbe();
    await page.keyboard.up(way.key);
    await page.waitForTimeout(400);
    const r1 = await roamProbe();
    check("the walk cycle plays while moving", rMid.anim === "walk-him", rMid.anim);
    check("standing still stops the cycle", r1.anim === "stand-him", r1.anim);
    const gone = Math.hypot(r1.x - r0.x, r1.y - r0.y);
    check("walking covers ground", r1.metres > 1 && gone > 1,
          r1.metres.toFixed(1) + "m walked, " + gone.toFixed(1) + "m from the start");
    check("the dog trails a few metres behind", r1.gap > 0.4 && r1.gap < 9,
          r1.gap.toFixed(1) + "m");
    check("neither of them is inside a building", !r1.inWall && !r1.dogInWall);
    check("the readout names the street", /\w/.test(r1.where), r1.where);

    // Walked straight into a wall: you stop at it, you do not pass through it.
    const wall = await page.evaluate(() => {
      const s = window.game.scene.getScene("world"), w = s.worldRef, r = window.dogwalk.roam;
      let best = null, bd = 1e9;
      for (let ty = 0; ty < w.rows; ty++) for (let tx = 0; tx < w.cols; tx++) {
        if (!w.solid[ty * w.cols + tx]) continue;
        const mx = (tx + 0.5) * w.mpt, my = (ty + 0.5) * w.mpt;
        const d = Math.hypot(mx - r.x, my - r.y);
        if (d < bd) { bd = d; best = [mx, my]; }
      }
      if (!best) return null;
      for (let d = 2; d < 30; d += 0.5) {
        const x = best[0] - d, y = best[1];
        if (!s.blocked(x, y)) { r.x = x; r.y = y; return { faced: true }; }
      }
      return null;
    });
    if (wall) {
      await page.keyboard.down("ArrowRight");
      await page.waitForTimeout(3000);
      await page.keyboard.up("ArrowRight");
      const r2 = await roamProbe();
      check("you cannot walk through a building", !r2.inWall, JSON.stringify({
        x: r2.x.toFixed(1), inWall: r2.inWall }));
    }

    // The lead. He wants things; you decide whether he gets them; the lead is
    // where those two meet. Everything below is that one mechanic.
    const spacing = await page.evaluate(() => {
      const r = window.dogwalk.roam;
      let worst = Infinity;
      for (let i = 0; i < Math.min(400, r.interests.length); i++) {
        for (let j = i + 1; j < Math.min(400, r.interests.length); j++) {
          const d = Math.hypot(r.interests[i].x - r.interests[j].x,
                               r.interests[i].y - r.interests[j].y);
          if (d < worst) worst = d;
        }
      }
      return { n: r.interests.length, closest: worst };
    });
    // Every tree in the town would be tens of thousands, he would want
    // something at every step, and the choice would stop being a choice.
    check("things to sniff are thinned out", spacing.n > 0 && spacing.closest >= 7.9,
          spacing.n + " interests, closest pair " + spacing.closest.toFixed(1) + "m");

    // Let him have one: stand beside it and wait.
    const target = await page.evaluate(() => {
      const s = window.game.scene.getScene("world"), r = window.dogwalk.roam;
      const it = s.nearestWant(r.x, r.y, 90);
      if (!it) return null;
      for (let a = 0; a < 16; a++) {
        const th = a * Math.PI / 8;
        const x = it.x + Math.cos(th) * 2.5, y = it.y + Math.sin(th) * 2.5;
        if (s.blocked(x, y)) continue;
        r.x = x; r.y = y; r.dog.x = x; r.dog.y = y; r.dog.want = null;
        return { kind: it.kind };
      }
      return null;
    });
    check("he finds something worth a sniff", !!target, JSON.stringify(target));
    await page.waitForTimeout(5000);
    const sniff = await page.evaluate(() => {
      const r = window.dogwalk.roam;
      return { joy: r.joy, sniffed: r.sniffed.length };
    });
    check("letting him sniff makes it a better walk",
          sniff.sniffed > 0 && sniff.joy > 0, JSON.stringify(sniff));

    // Refuse him one: walk away and the lead comes taut, then he drops it.
    const awayKey = await page.evaluate(() => {
      const s = window.game.scene.getScene("world"), r = window.dogwalk.roam;
      if (!r.dog.want) return null;
      const away = r.dog.want.x > r.x ? -1 : 1;
      if (s.blocked(r.x + away * 5, r.y)) return null;
      return away < 0 ? "ArrowLeft" : "ArrowRight";
    });
    if (awayKey) {
      // Tension rises and falls as he takes up wants and drops them, so the
      // peak over the stretch is the thing to measure — a single sample lands
      // wherever it lands.
      await page.evaluate(() => {
        window.__peak = { t: 0, gap: 0 };
        window.__peakTimer = setInterval(() => {
          const r = window.dogwalk.roam;
          window.__peak.t = Math.max(window.__peak.t, r.tension);
          window.__peak.gap = Math.max(window.__peak.gap,
            Math.hypot(r.x - r.dog.x, r.y - r.dog.y));
        }, 40);
      });
      await page.keyboard.down(awayKey);
      await page.waitForTimeout(5000);
      await page.keyboard.up(awayKey);
      const taut = await page.evaluate(() => {
        clearInterval(window.__peakTimer);
        return { peak: window.__peak, refused: window.dogwalk.roam.refused };
      });
      check("walking on pulls the lead taut", taut.peak.t > 0.5, JSON.stringify(taut.peak));
      check("the lead actually holds him", taut.peak.gap < 7, taut.peak.gap.toFixed(1) + "m");
      check("he gives up on what he cannot reach", taut.refused > 0, taut.refused);
    }

    // A squirrel. Not gripping means he is off after it.
    await page.evaluate(() => {
      const r = window.dogwalk.roam;
      r.metres = Math.max(r.metres, 30); r.nextSquirrel = 0; r.gripping = false;
    });
    await page.waitForTimeout(1200);
    check("a squirrel turns up", await page.evaluate(() =>
      !!window.dogwalk.roam.squirrel &&
      window.game.scene.getScene("world").critter.visible));
    check("the hold prompt appears", await page.evaluate(() =>
      !document.getElementById("btn-roam-hold").classList.contains("off")));
    await page.waitForTimeout(2200);
    check("not gripping and he slips the lead", await page.evaluate(() =>
      window.dogwalk.roam.slipped > 0 || window.dogwalk.roam.offLead),
      await page.evaluate(() => JSON.stringify({
        slipped: window.dogwalk.roam.slipped, off: window.dogwalk.roam.offLead })));
    await page.waitForTimeout(5000);
    check("he comes back and is caught", await page.evaluate(() => {
      const r = window.dogwalk.roam;
      return !r.squirrel && (!r.offLead || r.recalling);
    }), await page.evaluate(() => JSON.stringify({
      off: window.dogwalk.roam.offLead, recalling: window.dogwalk.roam.recalling })));

    // Off the lead is a park thing.
    await page.evaluate(() => {
      const r = window.dogwalk.roam;
      r.offLead = false; r.recalling = false; r.warn = "";
    });
    await page.waitForTimeout(400);
    const parked = await page.evaluate(() => {
      const s = window.game.scene.getScene("world"), w = s.worldRef, r = window.dogwalk.roam;
      const before = document.getElementById("btn-roam-lead").disabled;
      for (let ty = 0; ty < w.rows; ty++) for (let tx = 0; tx < w.cols; tx++) {
        if (!w.park[ty * w.cols + tx]) continue;
        const mx = (tx + 0.5) * w.mpt, my = (ty + 0.5) * w.mpt;
        if (s.blocked(mx, my)) continue;
        r.x = mx; r.y = my; r.dog.x = mx; r.dog.y = my;
        return { before, found: true };
      }
      return { before, found: false };
    });
    if (parked.found) {
      await page.waitForTimeout(500);
      check("the lead only comes off in a park",
            parked.before === true &&
            (await page.evaluate(() => document.getElementById("btn-roam-lead").disabled)) === false,
            "outside: " + parked.before);
      await page.click("#btn-roam-lead");
      await page.waitForTimeout(600);
      check("off the lead he ranges further", await page.evaluate(() =>
        window.dogwalk.roam.offLead && !window.game.scene.getScene("world").leadGfx.commandBuffer.length));
    }

    // Ending up inside geometry must not trap you.
    const escaped = await page.evaluate(() => {
      const s = window.game.scene.getScene("world"), w = s.worldRef, r = window.dogwalk.roam;
      for (let ty = 0; ty < w.rows; ty++) for (let tx = 0; tx < w.cols; tx++) {
        if (!w.solid[ty * w.cols + tx]) continue;
        r.x = (tx + 0.5) * w.mpt; r.y = (ty + 0.5) * w.mpt;
        return true;
      }
      return false;
    });
    if (escaped) {
      await page.waitForTimeout(600);
      check("you cannot get stuck inside a building", await page.evaluate(() => {
        const s = window.game.scene.getScene("world"), r = window.dogwalk.roam;
        return !s.blocked(r.x, r.y);
      }));
    }

    // Ground you covered yourself counts as explored, and survives a reload.
    await page.click("#btn-roam-stop");
    await page.waitForTimeout(500);
    const after = await page.evaluate(() => ({
      on: window.dogwalk.roam.on,
      barOff: document.getElementById("roam-bar").classList.contains("off"),
      roamed: window.dogwalk.state.roamed.length,
      saved: JSON.parse(localStorage.getItem("dogwalk.roamed.v1") || "[]").length,
    }));
    check("finishing puts the map back", !after.on && after.barOff, JSON.stringify(after));

    // And tells you how it went.
    const card = await page.evaluate(() => ({
      open: document.getElementById("roam-card").classList.contains("open"),
      verdict: document.getElementById("card-verdict").textContent,
      kpis: document.querySelectorAll("#card-kpis .kpi").length,
      log: document.getElementById("card-log").textContent,
    }));
    check("the walk ends with a verdict", card.open && /\w/.test(card.verdict) &&
          card.kpis >= 5 && /\w/.test(card.log), JSON.stringify(card));
    await page.click("#btn-card-close");
    await page.waitForTimeout(300);
    check("the card closes", await page.evaluate(() =>
      !document.getElementById("roam-card").classList.contains("open")));
    check("where you walked is remembered", after.roamed > 0 && after.saved === after.roamed,
          JSON.stringify(after));

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
    check("still playable without a map", (await off.$$(".walk")).length === 6);
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
    await m.tap("#btn-stats");
    await m.waitForTimeout(700);
    check("stats fit the phone", await m.evaluate(() => {
      const c = document.querySelector("#stats-panel .card");
      return c.scrollWidth <= window.innerWidth + 1;
    }));
    await m.keyboard.press("Escape");
    await m.waitForTimeout(300);

    // On a phone the only control is a thumbstick under your finger, so a drag
    // has to steer rather than pan the camera away from yourself.
    await m.tap("#btn-roam");
    await m.waitForTimeout(400);
    check("roam panel fits the phone", await m.evaluate(() => {
      const c = document.querySelector("#roam-panel .card");
      return c.scrollWidth <= window.innerWidth + 1;
    }));
    await m.tap("#btn-roam-go");
    await m.waitForTimeout(1200);
    const before = await m.evaluate(() => {
      const r = window.dogwalk.roam;
      return { x: r.x, y: r.y };
    });
    const box = await m.evaluate(() => ({ w: window.innerWidth, h: window.innerHeight }));
    const mway = await m.evaluate(() => {
      const s = window.game.scene.getScene("world"), r = window.dogwalk.roam;
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        if (!s.blocked(r.x + dx * 4, r.y + dy * 4)) return { dx, dy };
      }
      return { dx: 1, dy: 0 };
    });
    await m.mouse.move(box.w / 2, box.h / 2);
    await m.mouse.down();
    await m.mouse.move(box.w / 2 + mway.dx * 70, box.h / 2 + mway.dy * 70, { steps: 4 });
    await m.waitForTimeout(1800);
    const stickOn = await m.evaluate(() =>
      document.getElementById("stick").classList.contains("on"));
    await m.mouse.up();
    await m.waitForTimeout(300);
    const moved = await m.evaluate(() => {
      const r = window.dogwalk.roam; return { x: r.x, y: r.y };
    });
    const mgone = Math.hypot(moved.x - before.x, moved.y - before.y);
    check("the thumbstick appears under your finger", stickOn);
    check("dragging steers instead of panning", mgone > 0.5, mgone.toFixed(1) + "m");
    check("the stick clears when you let go", await m.evaluate(() =>
      !document.getElementById("stick").classList.contains("on") &&
      !window.dogwalk.roam.stick));
    await m.tap("#btn-roam-stop");
    await m.waitForTimeout(300);
    check("no page errors", mErrors.length === 0, mErrors.slice(0, 2).join(" | "));
  } finally {
    await browser.close();
    server.close();
  }

  console.log(failures ? "\n" + failures + " check(s) failed" : "\nall checks passed");
  process.exit(failures ? 1 : 0);
})();
