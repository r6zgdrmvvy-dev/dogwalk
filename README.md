# dogwalk

**A Walk With Your Dog** — turn your dog's GPS walk data into a cozy pixel-art
exploration game. Your home area is divided into a grid of "screens"; every
GPS point from a walk lights up the cell it falls in, occasionally turning up
a scattered treasure along the way.

## Play it

Open `dog-walk-game.html` in a browser. No build step, no server — it's a
single self-contained file. It loads with seven generated demo walks so it's
playable immediately.

Set your dog's name at the top, then load real data either by pasting JSON
into the "Load walk data" panel or by loading an exported `.json` file
directly. Each walk in the "Walks" panel can be watched back with the ▶
button — an animated playthrough of that day's route.

Expected data shape — an array of GPS points:

```json
[{"lat": 55.792, "lng": -4.293, "t": "2026-08-10T07:12:00Z"}, ...]
```

### Finding the walks in a tracker feed

A GPS collar reports all day, including while the dog is asleep on the couch,
so there are no gaps to split on — on a real month of Tractive data the fixes
arrive every ~150 seconds around the clock, and 44% of them are the dog at
home. What separates a walk from the rest of the day is leaving the house, so
the game:

- finds home as the ~40m cell the dog spends the most *time* in (dwell, not fix
  count, so a tracker that reports more often when moving doesn't put "home" on
  the busiest street);
- takes each contiguous run spent more than 75m from home, allowing a brief dip
  back inside so one walk doesn't become two;
- breaks a run where the collar goes quiet for over 30 minutes, or where the dog
  covered ground faster than 15 km/h — being driven to the park is part of the
  day, but drawing it as a straight line through everyone's gardens is not;
- drops anything under 5 minutes or 250m.

On a month of real data that turns 2,074 raw fixes into 62 walks averaging
2 km and 43 minutes. Summing every raw fix instead gives 237 km, most of it
GPS jitter and car journeys.

## Pulling real data from Tractive

Tractive (the GPS tracker) has no official public API, so this uses the
reverse-engineered [`aiotractive`](https://github.com/zhulik/aiotractive)
client with your own Tractive account email/password. This must run
**locally** — never enter Tractive credentials into a browser page.

```bash
pip install -r scripts/requirements.txt
python scripts/export_tractive.py --days 30
```

This writes `walks.json` (override with `--output`) in the
`{lat, lng, t}` shape the game expects — load it into the page via the
"Load file…" button. See `python scripts/export_tractive.py --help` for all
options (specific tracker ID, clipboard copy, a `--raw` dump of the untouched
API response for debugging).

Because Tractive's history endpoint isn't publicly documented, the script
parses responses defensively rather than assuming one fixed schema. It has now
been run against a live account and produced a usable month of history; if a
run comes back empty, re-run with `--raw` and check `<output>.raw.json` to see
what the API actually returned.

## Status / open items

- No backend, no persistence — reloading the page resets to demo data unless
  you reload your exported file.
- The Tractive export script's response parsing is best-effort. It works on a
  real account, but the endpoint is undocumented and could change shape.
- The world is built around one walk at a time. A single day trip whose route
  spans several kilometres still has to be drawn coarsely, because the tile
  count is capped; local walks, which are the overwhelming majority, come out
  at about two metres per tile.
- Multi-user "connect your own Tractive account" support (rather than a
  local per-person export script) is a bigger step — it needs a backend,
  since Tractive only supports raw email/password auth (no OAuth), which
  changes how account credentials would need to be handled.

## Art

The tileset is generated, not hand-pixelled by mouse — `scripts/make_tiles.py`
draws every tile and prop from one palette and writes `assets/city.png`,
`assets/props.png` and `assets/city-index.json` (the index map `game.html`
reads its tile numbers from).

```bash
pip install pillow
python3 scripts/make_tiles.py
```

The palette gives each material five values, hue-shifted so shadows drift blue
and highlights drift warm, and texture comes from ordered dithering between
neighbouring steps rather than random noise — which is what keeps a small
palette from going muddy when tiles repeat across a whole town. Light is fixed
to the north-west everywhere, so every cast shadow falls south-east.

Ground tiles are 16px; props (parked cars, lamp posts, bins, pillar boxes,
benches) are authored on 32px frames — two tiles across — so a car comes out
about 4m long at native scale with its pixels still landing exactly on the
ground's pixel grid. Nothing is drawn at a fractional scale, because that
would put sprite pixels off the tile grid and break the pixel-art look.

The older `dog-walk-game.html` build still uses Kenney's
[Tiny Town](https://kenney.nl/assets/tiny-town) pack, released under Creative
Commons Zero (CC0). Its licence text is in `ASSETS-LICENSE.txt`.
