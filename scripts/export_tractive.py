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

Credentials are read from (in order): --email/--password flags, then the
TRACTIVE_EMAIL / TRACTIVE_PASSWORD environment variables, then an
interactive prompt (password entry is hidden).
"""

import argparse
import asyncio
import getpass
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

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
    return p.parse_args()


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

    # Dedup + sort
    seen = set()
    points = []
    for fx in fixes:
        key = (round(fx["lat"], 6), round(fx["lng"], 6), fx["t"])
        if key in seen:
            continue
        seen.add(key)
        points.append({"lat": fx["lat"], "lng": fx["lng"], "t": ts_to_iso(fx["t"])})
    points.sort(key=lambda p: p["t"])

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


def main():
    args = parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
