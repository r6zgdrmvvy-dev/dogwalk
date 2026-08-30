#!/usr/bin/env python3
"""
Export walk/position history from a Tractive GPS tracker into the
{"lat", "lng", "t"} JSON shape the game reads.

Tractive has no official public API. This uses the reverse-engineered
`aiotractive` client (https://github.com/zhulik/aiotractive) and talks to
Tractive with YOUR OWN account email/password. Run this locally — never
paste your Tractive credentials into a browser page or client-side code.

The exact JSON shape Tractive's history endpoint returns is not publicly
documented, so this script parses the response defensively: it walks the
whole response looking for anything that looks like a GPS fix (a "latlong"
pair plus a timestamp field) instead of assuming one fixed schema. Run once
with --raw to dump the untouched API response for a quick sanity check if
the point count looks wrong.

Install:
    pip install -r scripts/requirements.txt

Usage:
    python scripts/export_tractive.py --days 14
    python scripts/export_tractive.py --days 30 --output walks.json
    python scripts/export_tractive.py --tracker-id abc123 --raw
    python scripts/export_tractive.py --serve          # sync button in the game

Credentials are read from (in order): --email/--password flags, then the
TRACTIVE_EMAIL / TRACTIVE_PASSWORD environment variables, then an
interactive prompt (password entry is hidden).

--serve is the same export with a door on it. It signs in here, in your
terminal, and then answers the game's "Sync from Tractive" button over
loopback so you do not have to save a file and load it by hand. Your
credentials stay in this process: the page asks this script for points and
never learns anything about your Tractive account. Nothing listens on
anything but 127.0.0.1, only the origins in --allow-origin may read it, and
it shuts itself down once you stop using it.

Why a local bridge and not the page itself: Tractive's API sends no CORS
headers at all (its preflight comes back 200 with no Access-Control-Allow-*
of any kind), so a browser cannot talk to it from any origin — measured, not
assumed. That leaves a local helper, or a server that holds your Tractive
password on your behalf. This is the first one.
"""

import argparse
import asyncio
import getpass
import json
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

try:
    from aiotractive import Tractive
except ImportError:
    print(
        "Missing dependency: aiotractive.\n"
        "Install it with: pip install -r scripts/requirements.txt",
        file=sys.stderr,
    )
    sys.exit(1)

# Time-ish keys observed/plausible in Tractive API responses, in priority order.
TIME_KEYS = ("time", "time_rcvd", "timestamp", "recorded_at")


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--email", help="Tractive account email (else $TRACTIVE_EMAIL or prompt)")
    p.add_argument("--password", help="Tractive account password (else $TRACTIVE_PASSWORD or prompt)")
    p.add_argument("--tracker-id", help="Specific tracker ID to export. If omitted and there's only one tracker on the account, it's used automatically.")
    p.add_argument("--days", type=int, default=30, help="How many days of history to pull (default: 30)")
    p.add_argument("--chunk-days", type=int, default=7, help="Request the history in windows of this many days at a time (default: 7)")
    p.add_argument("--output", default="walks.json", help="Output JSON file (default: walks.json)")
    p.add_argument("--raw", action="store_true", help="Also dump the untouched API responses to <output>.raw.json for inspection")
    p.add_argument("--clipboard", action="store_true", help="Copy the resulting JSON to the clipboard (requires pyperclip)")
    p.add_argument("--serve", action="store_true", help="Stay running and answer the game's Sync button over loopback instead of writing a file")
    p.add_argument("--port", type=int, default=8765, help="Port for --serve (default: 8765)")
    p.add_argument("--idle-minutes", type=float, default=15.0, help="Shut --serve down after this long with nothing asking for anything (default: 15, 0 to stay up)")
    p.add_argument("--allow-origin", action="append", default=[], help="Extra web origin allowed to read from --serve. Repeatable. Your own fork's Pages URL goes here.")
    return p.parse_args()


# Who may read from the bridge. Deliberately an exact list rather than a
# wildcard: while it is up, anything this allows can read where your dog has
# been, and a walk trail starts at your front door. localhost is here so the
# game works when you serve the repo yourself; the Pages URL is the published
# copy. Anything else is --allow-origin, opted into by hand.
PUBLISHED_ORIGIN = "https://r6zgdrmvvy-dev.github.io"


def origin_allowed(origin, extra):
    if not origin:
        return False
    if origin == PUBLISHED_ORIGIN or origin in extra:
        return True
    try:
        u = urlparse(origin)
    except ValueError:
        return False
    return u.scheme in ("http", "https") and u.hostname in ("localhost", "127.0.0.1", "::1")


def resolve_credentials(args):
    email = args.email or os.environ.get("TRACTIVE_EMAIL") or input("Tractive email: ").strip()
    password = args.password or os.environ.get("TRACTIVE_PASSWORD") or getpass.getpass("Tractive password: ")
    return email, password


def find_fixes(node, out):
    """Recursively walk an arbitrary JSON structure, collecting anything
    that looks like a GPS fix: a dict with a 'latlong'/'latitude'+'longitude'
    pair and a plausible timestamp field."""
    if isinstance(node, dict):
        latlong = node.get("latlong")
        lat = lng = None
        if isinstance(latlong, (list, tuple)) and len(latlong) == 2:
            lat, lng = latlong[0], latlong[1]
        elif "latitude" in node and "longitude" in node:
            lat, lng = node.get("latitude"), node.get("longitude")

        if isinstance(lat, (int, float)) and isinstance(lng, (int, float)):
            ts = None
            for key in TIME_KEYS:
                if key in node and isinstance(node[key], (int, float)):
                    ts = node[key]
                    break
            if ts is not None:
                out.append({"lat": float(lat), "lng": float(lng), "t": ts})

        for value in node.values():
            find_fixes(value, out)
    elif isinstance(node, (list, tuple)):
        for item in node:
            find_fixes(item, out)


def ts_to_iso(ts):
    # Tractive timestamps observed in the wild are unix seconds; guard
    # against millisecond-resolution values just in case.
    if ts > 1e12:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def pick_tracker(client, requested_id):
    trackers = await client.trackers()
    if not trackers:
        print("No trackers found on this Tractive account.", file=sys.stderr)
        sys.exit(1)

    if requested_id:
        for t in trackers:
            if t._id == requested_id:
                return t
        print(f"Tracker id {requested_id!r} not found on this account.", file=sys.stderr)
        sys.exit(1)

    if len(trackers) == 1:
        return trackers[0]

    print("Multiple trackers found on this account:")
    for i, t in enumerate(trackers):
        print(f"  [{i}] {t._id}")
    choice = input("Pick a tracker index: ").strip()
    return trackers[int(choice)]


async def fetch_history(tracker, days, chunk_days, raw_dump):
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    all_fixes = []
    window_start = start
    while window_start < now:
        window_end = min(window_start + timedelta(days=chunk_days), now)
        time_from = window_start.timestamp()
        time_to = window_end.timestamp()

        try:
            resp = await tracker.positions(time_from, time_to, "json_segments")
        except Exception as exc:  # noqa: BLE001 - surface whatever the API/client raises
            print(f"  ! request for {window_start.date()} to {window_end.date()} failed: {exc}", file=sys.stderr)
            window_start = window_end
            continue

        if raw_dump is not None:
            raw_dump.append({"time_from": time_from, "time_to": time_to, "response": resp})

        before = len(all_fixes)
        find_fixes(resp, all_fixes)
        print(f"  {window_start.date()} to {window_end.date()}: {len(all_fixes) - before} points")

        window_start = window_end
        await asyncio.sleep(0.3)  # be polite to an unofficial API

    return all_fixes


async def fetch_current_position(tracker, raw_dump):
    try:
        resp = await tracker.pos_report()
    except Exception as exc:  # noqa: BLE001
        print(f"  ! current position request failed: {exc}", file=sys.stderr)
        return []
    if raw_dump is not None:
        raw_dump.append({"pos_report": resp})
    fixes = []
    find_fixes(resp, fixes)
    return fixes


def tidy(fixes):
    """Dedup, convert to the game's {lat,lng,t} shape, and sort by time."""
    seen = set()
    points = []
    for fx in fixes:
        key = (round(fx["lat"], 6), round(fx["lng"], 6), fx["t"])
        if key in seen:
            continue
        seen.add(key)
        points.append({"lat": fx["lat"], "lng": fx["lng"], "t": ts_to_iso(fx["t"])})
    points.sort(key=lambda p: p["t"])
    return points


async def collect(tracker, days, chunk_days, raw_dump=None):
    """Everything the tracker has for the window, already tidied."""
    print(f"Fetching {days} day(s) of history in {chunk_days}-day windows...")
    fixes = await fetch_history(tracker, days, chunk_days, raw_dump)
    if not fixes:
        print("No history points found; falling back to current position report.")
        fixes = await fetch_current_position(tracker, raw_dump)
    return tidy(fixes)


async def run(args):
    email, password = resolve_credentials(args)
    raw_dump = [] if args.raw else None

    async with Tractive(email, password) as client:
        tracker = await pick_tracker(client, args.tracker_id)
        print(f"Using tracker {tracker._id}")

        print(f"Fetching {args.days} day(s) of history in {args.chunk_days}-day windows...")
        fixes = await fetch_history(tracker, args.days, args.chunk_days, raw_dump)

        if not fixes:
            print("No history points found; falling back to current position report.")
            fixes = await fetch_current_position(tracker, raw_dump)

    if not fixes:
        print("Got nothing back from Tractive. Re-run with --raw and inspect the dump — "
              "the response shape may not match what this script expects yet.", file=sys.stderr)
        if raw_dump is not None:
            raw_path = args.output + ".raw.json"
            with open(raw_path, "w") as f:
                json.dump(raw_dump, f, indent=2, default=str)
            print(f"Raw API responses written to {raw_path}")
        sys.exit(1)

    points = tidy(fixes)

    with open(args.output, "w") as f:
        json.dump(points, f, indent=2)

    print(f"\nWrote {len(points)} points to {args.output}")
    if points:
        print(f"Range: {points[0]['t']} to {points[-1]['t']}")

    if raw_dump is not None:
        raw_path = args.output + ".raw.json"
        with open(raw_path, "w") as f:
            json.dump(raw_dump, f, indent=2, default=str)
        print(f"Raw API responses written to {raw_path} (inspect this if the field-name "
              "guesses in find_fixes() need adjusting)")

    if args.clipboard:
        try:
            import pyperclip
            pyperclip.copy(json.dumps(points))
            print("Copied JSON to clipboard.")
        except ImportError:
            print("--clipboard requires pyperclip: pip install pyperclip", file=sys.stderr)


# ----------------------------------------------------------------- bridge ---
# The signed-in client, kept alive on its own event loop in its own thread so
# the blocking HTTP server can hand it work. aiohttp's session belongs to the
# loop that made it, so everything that touches Tractive is submitted to that
# loop rather than run wherever the request happened to land.
class Bridge:
    def __init__(self, email, password, tracker_id, chunk_days):
        self._email, self._password = email, password
        self._tracker_id, self._chunk_days = tracker_id, chunk_days
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._lock = threading.Lock()
        self._client = self._tracker = None
        self.tracker_name = None
        # A short cache, so leaning on the button does not lean on an
        # undocumented API somebody else is paying for.
        self._cache = {}

    def _pump(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro, timeout):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def start(self):
        """Sign in now, so a wrong password is an error in this terminal
        rather than a shrug in the browser five minutes later."""
        self._thread.start()

        async def _open():
            client = Tractive(self._email, self._password)
            await client.__aenter__()
            tracker = await pick_tracker(client, self._tracker_id)
            return client, tracker

        self._client, self._tracker = self._submit(_open(), 60)
        try:
            details = self._submit(self._tracker.details(), 30)
            self.tracker_name = (details or {}).get("_id") or self._tracker._id
        except Exception:                                    # noqa: BLE001
            self.tracker_name = self._tracker._id
        return self._tracker._id

    def points(self, days):
        # One request at a time. Two browser tabs both syncing would otherwise
        # interleave on one aiohttp session for no benefit to anybody.
        with self._lock:
            hit = self._cache.get(days)
            if hit and time.time() - hit[0] < 90:
                print(f"  serving {len(hit[1])} points for {days}d from cache")
                return hit[1]
            pts = self._submit(collect(self._tracker, days, self._chunk_days), 600)
            self._cache[days] = (time.time(), pts)
            return pts

    def stop(self):
        try:
            self._submit(self._client.__aexit__(None, None, None), 15)
        except Exception:                                    # noqa: BLE001
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)


def make_handler(bridge, extra_origins, on_activity, max_days):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _cors(self, origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            # Chrome's Private Network Access check is not enforced on
            # loopback in every build yet, but it costs one header to be
            # ready for the one where it is.
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Cache-Control", "no-store")

        def _send(self, code, payload, origin):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self._cors(origin)
            self.end_headers()
            self.wfile.write(body)

        def _deny(self, origin):
            body = b'{"error":"origin not allowed"}'
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if origin:
                print(f"  ! refused a request from {origin} — "
                      f"pass --allow-origin {origin} if that was you")
            else:
                print("  ! refused a request that named no origin — browsers "
                      "always send one, so that was not the game")

        def do_OPTIONS(self):                                # noqa: N802
            origin = self.headers.get("Origin")
            if not origin_allowed(origin, extra_origins):
                self._deny(origin)
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "content-type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self._cors(origin)
            self.end_headers()

        def do_GET(self):                                    # noqa: N802
            origin = self.headers.get("Origin")
            if not origin_allowed(origin, extra_origins):
                self._deny(origin)
                return
            on_activity()
            route = urlparse(self.path)
            query = parse_qs(route.query)

            if route.path == "/health":
                self._send(200, {"ok": True, "tracker": bridge.tracker_name,
                                 "maxDays": max_days}, origin)
                return

            if route.path == "/walks":
                try:
                    days = int(query.get("days", ["30"])[0])
                except ValueError:
                    self._send(400, {"error": "days must be a whole number"}, origin)
                    return
                days = max(1, min(max_days, days))
                try:
                    self._send(200, bridge.points(days), origin)
                except Exception as exc:                     # noqa: BLE001
                    print(f"  ! sync failed: {exc}", file=sys.stderr)
                    self._send(502, {"error": str(exc)}, origin)
                return

            self._send(404, {"error": "no such thing here"}, origin)

        def log_message(self, fmt, *a):
            # The default logger writes to stderr on every request, which
            # buries the two lines that actually matter.
            pass

    return Handler


def serve(args):
    email, password = resolve_credentials(args)
    extra = list(args.allow_origin)

    bridge = Bridge(email, password, args.tracker_id, args.chunk_days)
    print("Signing in to Tractive…")
    tracker_id = bridge.start()
    print(f"Signed in. Using tracker {tracker_id}")

    idle = {"at": time.time()}
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        make_handler(bridge, extra, lambda: idle.update(at=time.time()), 365))
    httpd.daemon_threads = True

    # Bound to 127.0.0.1 only — nothing else on the network can see it — and it
    # closes itself once you have stopped using it, because a bridge left
    # running for a week is a door left open for a week.
    print()
    print(f"  Sync bridge on http://127.0.0.1:{args.port}")
    print(f"  Open the game and press \"Sync from Tractive\".")
    print(f"  Allowed origins: {PUBLISHED_ORIGIN}, localhost" +
          ("".join(", " + o for o in extra) if extra else ""))
    if args.idle_minutes > 0:
        print(f"  Shuts down after {args.idle_minutes:g} idle minute(s). Ctrl-C to stop now.")
    else:
        print("  Ctrl-C to stop.")
    print()

    stop = threading.Event()

    def watchdog():
        while not stop.wait(5):
            if args.idle_minutes > 0 and time.time() - idle["at"] > args.idle_minutes * 60:
                print("Idle. Shutting the bridge down.")
                httpd.shutdown()
                return

    threading.Thread(target=watchdog, daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        stop.set()
        httpd.server_close()
        bridge.stop()


def main():
    args = parse_args()
    if args.serve:
        serve(args)
    else:
        asyncio.run(run(args))


if __name__ == "__main__":
    main()
