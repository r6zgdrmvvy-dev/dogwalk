#!/usr/bin/env python3
"""Check the --serve bridge in scripts/export_tractive.py.

Runs the real HTTP layer against a stubbed Tractive client, so it exercises the
thing the game actually talks to — the origin policy, the CORS headers, the
shape of what comes back — without needing an account or a network.

    python3 tests/bridge_test.py
"""

import importlib.util
import json
import os
import sys
import threading
import time
import types
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PASSED = []
FAILED = []

MAX_DAYS = 14        # the ceiling this test runs the bridge with


def check(label, ok, detail=""):
    (PASSED if ok else FAILED).append(label)
    mark = "ok  " if ok else "FAIL"
    print(f"  {mark} {label}" + (f"  -> {detail}" if detail else ""))


# A tracker that returns two fixes, in the shape the real one does: a nest of
# whatever, with latlong pairs and unix timestamps somewhere inside it.
FIXES = [
    {"latlong": [55.7952, -4.2955], "time": 1755000000},
    {"latlong": [55.7961, -4.2941], "time": 1755000120},
    {"latlong": [55.7952, -4.2955], "time": 1755000000},   # a duplicate
]


class FakeTracker:
    _id = "TESTTRACKER"

    async def positions(self, a, b, fmt):
        return {"segments": [FIXES]}

    async def pos_report(self):
        return {}

    async def details(self):
        return {"_id": "TESTTRACKER"}


class FakeTractive:
    def __init__(self, email, password):
        self.email, self.password = email, password

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def trackers(self):
        return [FakeTracker()]


def load_script():
    sys.modules["aiotractive"] = types.SimpleNamespace(Tractive=FakeTractive)
    spec = importlib.util.spec_from_file_location(
        "export_tractive", os.path.join(ROOT, "scripts", "export_tractive.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["export_tractive.py"]
    spec.loader.exec_module(mod)
    return mod


def get(url, origin, method="GET"):
    req = urllib.request.Request(url, method=method)
    if origin is not None:
        req.add_header("Origin", origin)
    if method == "OPTIONS":
        req.add_header("Access-Control-Request-Method", "GET")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def main():
    m = load_script()

    # The origin policy on its own, before anything is listening.
    for origin, extra, want in [
        (m.PUBLISHED_ORIGIN, [], True),
        ("http://localhost:8000", [], True),
        ("http://127.0.0.1:5500", [], True),
        ("https://evil.example.com", [], False),
        ("https://someone-else.github.io", [], False),
        ("null", [], False),
        (None, [], False),
        ("https://mine.github.io", ["https://mine.github.io"], True),
    ]:
        got = m.origin_allowed(origin, extra)
        check(f"origin {origin!r} {'allowed' if want else 'refused'}", got == want)

    bridge = m.Bridge("someone@example.invalid", "hunter2", None, 7)
    tracker_id = bridge.start()
    check("the bridge signs in and picks a tracker", tracker_id == "TESTTRACKER", tracker_id)

    port = 8766
    idle = {"at": time.time()}
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", port),
        # A small ceiling on purpose: the real one is 365, and a year is 53
        # sequential windows with a politeness sleep between each. The clamp is
        # what is under test, not how long a year takes.
        m.make_handler(bridge, ["https://mine.github.io"],
                       lambda: idle.update(at=time.time()), MAX_DAYS))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    good = "http://localhost:8000"

    try:
        # It listens on loopback only. Anything else on the network must not
        # be able to see where the dog has been.
        check("it binds to loopback and nothing else",
              httpd.server_address[0] == "127.0.0.1", httpd.server_address[0])

        status, headers, body = get(base + "/health", good)
        health = json.loads(body)
        check("health says it is up", status == 200 and health.get("ok") is True,
              json.dumps(health))
        check("health names the tracker", health.get("tracker") == "TESTTRACKER")
        check("health allows the asking origin",
              headers.get("Access-Control-Allow-Origin") == good,
              headers.get("Access-Control-Allow-Origin"))
        # Without this the browser may cache one origin's answer for another.
        check("and varies on origin, so nothing caches it wrong",
              headers.get("Vary") == "Origin", headers.get("Vary"))

        status, headers, _ = get(base + "/health", good, method="OPTIONS")
        check("the preflight passes", status == 204 and
              headers.get("Access-Control-Allow-Origin") == good, str(status))
        check("and names the methods it will take",
              "GET" in (headers.get("Access-Control-Allow-Methods") or ""),
              headers.get("Access-Control-Allow-Methods"))

        status, headers, body = get(base + "/walks?days=7", good)
        pts = json.loads(body)
        check("walks come back as the game's own point shape",
              status == 200 and isinstance(pts, list) and pts and
              set(pts[0]) == {"lat", "lng", "t"}, json.dumps(pts[:1]))
        check("duplicates are dropped", len(pts) == 2, f"{len(pts)} points")
        check("and they are in time order", [p["t"] for p in pts] ==
              sorted(p["t"] for p in pts), " ".join(p["t"] for p in pts))
        check("timestamps are the ISO the loader parses",
              pts[0]["t"].endswith("Z") and "T" in pts[0]["t"], pts[0]["t"])

        # The one that matters: while this is up, only the origins named may
        # read it. A walk trail starts at your front door.
        status, headers, _ = get(base + "/walks?days=7", "https://evil.example.com")
        check("a stranger's page is refused", status == 403, str(status))
        check("and is told nothing it could use",
              headers.get("Access-Control-Allow-Origin") is None,
              headers.get("Access-Control-Allow-Origin"))
        status, _, _ = get(base + "/walks?days=7", None)
        check("so is a request with no origin at all", status == 403, str(status))
        status, _, _ = get(base + "/health", "https://mine.github.io")
        check("but an origin you passed in by hand is let through", status == 200,
              str(status))

        # A number out of range is clamped rather than handed to the API.
        # Measured by what came back: past the ceiling the window stops growing.
        _, _, at_max = get(base + f"/walks?days={MAX_DAYS}", good)
        _, _, absurd = get(base + "/walks?days=99999", good)
        check("an absurd number of days is clamped to the ceiling",
              json.loads(absurd) == json.loads(at_max),
              f"{len(json.loads(absurd))} points either way")
        status, _, body = get(base + "/walks?days=nonsense", good)
        check("and a nonsense one is a clean error",
              status == 400 and "error" in json.loads(body), str(status))

        status, _, _ = get(base + "/nope", good)
        check("an unknown path is a 404", status == 404, str(status))

        # Every request has to keep the idle timer alive, or the bridge closes
        # under someone who is still using it.
        idle["at"] = 0
        get(base + "/health", good)
        check("a request keeps it from timing out", idle["at"] > 0)
    finally:
        httpd.shutdown()
        httpd.server_close()
        bridge.stop()

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        sys.exit(1)
    print(f"all {len(PASSED)} checks passed")


if __name__ == "__main__":
    main()
