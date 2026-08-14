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

Points more than 90 minutes apart are automatically split into separate
walks.

## Pulling real data from Tractive

Tractive (the GPS tracker) has no official public API, so this uses the
reverse-engineered [`aiotractive`](https://github.com/zhulik/aiotractive)
client with your own Tractive account email/password. This must run
**locally** — never enter Tractive credentials into a browser page.

```bash
pip install -r scripts/requirements.txt
python scripts/export_tractive.py --days 30
```

This writes `guinness_walk.json` (override with `--output`) in the
`{lat, lng, t}` shape the game expects — load it into the page via the
"Load file…" button. See `python scripts/export_tractive.py --help` for all
options (specific tracker ID, clipboard copy, a `--raw` dump of the untouched
API response for debugging).

Because Tractive's history endpoint isn't publicly documented, the script
parses responses defensively rather than assuming one fixed schema — it
hasn't yet been verified against a live account, so if a run comes back
empty, re-run with `--raw` and check `<output>.raw.json` to see what the API
actually returned.

## Status / open items

- No backend, no persistence — reloading the page resets to demo data unless
  you reload your exported file.
- The Tractive export script's response parsing is best-effort and unverified
  against a real account.
- Multi-user "connect your own Tractive account" support (rather than a
  local per-person export script) is a bigger step — it needs a backend,
  since Tractive only supports raw email/password auth (no OAuth), which
  changes how account credentials would need to be handled.
