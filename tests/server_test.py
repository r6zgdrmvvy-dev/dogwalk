#!/usr/bin/env python3
"""Check the account server: server/app.py.

Runs the real thing against a throwaway database on a loopback port — no
browser, no network, no fixtures to keep in step. Most of what is checked here
is the security behaviour, because that is the part where being wrong is
expensive and being wrong quietly is normal.

    python3 tests/server_test.py
"""

import http.client
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

PASSED, FAILED = [], []
PORT = 8771
HOST = "127.0.0.1"
ORIGIN = f"http://{HOST}:{PORT}"

GOOD_PW = "a short phrase will do"
OTHER_PW = "another perfectly fine one"


def check(label, ok, detail=""):
    (PASSED if ok else FAILED).append(label)
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  -> {detail}" if detail else ""))


def load():
    spec = importlib.util.spec_from_file_location(
        "dogwalk_server", os.path.join(ROOT, "server", "app.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Client:
    """One browser: keeps its own cookie jar so two of them are two people."""

    def __init__(self):
        self.cookie = None

    def req(self, method, path, body=None, origin=ORIGIN, cookie=True):
        conn = http.client.HTTPConnection(HOST, PORT, timeout=20)
        headers = {"Host": f"{HOST}:{PORT}"}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(payload))
        if origin is not None:
            headers["Origin"] = origin
        if cookie and self.cookie:
            headers["Cookie"] = self.cookie
        conn.request(method, path, payload, headers)
        r = conn.getresponse()
        raw = r.read()
        setc = r.getheader("Set-Cookie")
        if setc:
            first = setc.split(";")[0]
            self.cookie = None if first.endswith("=") else first
        try:
            data = json.loads(raw) if raw else None
        except Exception:                                    # noqa: BLE001
            data = raw
        out = (r.status, data, dict(r.getheaders()))
        conn.close()
        return out


def main():
    m = load()
    tmp = tempfile.mkdtemp(prefix="dogwalk-test-")
    db = os.path.join(tmp, "test.sqlite3")
    store = m.Store(db)
    opts = m.parse_args(["--port", str(PORT), "--quiet"])
    from http.server import ThreadingHTTPServer
    httpd = ThreadingHTTPServer((HOST, PORT), m.make_handler(store, opts))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    try:
        a, b = Client(), Client()

        # -- what it will and will not accept -------------------------------
        st, body, _ = a.req("POST", "/api/signup",
                            {"email": "not-an-email", "password": GOOD_PW})
        check("a bad address is refused", st == 400, str(body))
        st, body, _ = a.req("POST", "/api/signup",
                            {"email": "someone@example.com", "password": "short"})
        check("a short password is refused", st == 400, (body or {}).get("error"))
        st, body, _ = a.req("POST", "/api/signup",
                            {"email": "someone@example.com", "password": "qwertyuiop"})
        check("and a long but obvious one is too", st == 400, (body or {}).get("error"))

        # -- signing up -----------------------------------------------------
        st, body, hdr = a.req("POST", "/api/signup",
                              {"email": "ann@example.com", "password": GOOD_PW})
        check("you can make an account", st == 200 and body.get("signedIn") is True,
              json.dumps(body))
        cookie = hdr.get("Set-Cookie", "")
        check("the session cookie is HttpOnly", "HttpOnly" in cookie, cookie)
        check("and SameSite=Lax, so it does not ride along on other sites' posts",
              "SameSite=Lax" in cookie, cookie)
        check("it is not Secure on plain http, and says so honestly",
              "Secure" not in cookie, cookie)
        st, body, _ = a.req("GET", "/api/me")
        check("and you are signed in afterwards",
              st == 200 and body.get("email") == "ann@example.com", json.dumps(body))
        check("a new account is on the free plan", body.get("plan") == "free")

        # The password must not be anywhere near the database in the clear.
        with open(db, "rb") as f:
            blob = f.read()
        check("the password is not in the database", GOOD_PW.encode() not in blob)
        row = store.user_by_email("ann@example.com")
        check("what is stored is a scrypt hash, with a salt of its own",
              len(bytes(row["pw_hash"])) == m.SCRYPT_LEN and
              len(bytes(row["pw_salt"])) == 16)

        # Nor may the session token be, so a leaked table is not a set of keys.
        token = a.cookie.split("=", 1)[1]
        check("the session token is not stored either, only its hash",
              token.encode() not in blob)

        # -- who is who -----------------------------------------------------
        st, body, _ = b.req("GET", "/api/me")
        check("somebody with no cookie is nobody", st == 200 and
              body.get("signedIn") is False, json.dumps(body))
        st, _, _ = b.req("GET", "/api/save")
        check("and cannot read a save", st == 401, str(st))

        # -- signing up twice -----------------------------------------------
        c = Client()
        st, body, _ = c.req("POST", "/api/signup",
                            {"email": "ANN@example.com", "password": OTHER_PW})
        check("signing up with a taken address does not say it is taken",
              st == 200 and body.get("signedIn") is False and "error" not in body,
              json.dumps(body))
        st, body, _ = c.req("POST", "/api/login",
                            {"email": "ann@example.com", "password": OTHER_PW})
        check("and definitely does not let you in with a new password",
              st == 401, str(st))

        # -- saving ---------------------------------------------------------
        save = {"name": "Rusty", "bond": 42,
                "points": [{"lat": 51.5, "lng": -0.14, "t": "2026-08-20T07:00:00Z"}]}
        st, body, _ = a.req("POST", "/api/save", {"save": save})
        check("you can put your walks away", st == 200 and body.get("saved") is True,
              json.dumps(body))
        st, body, _ = a.req("GET", "/api/save")
        check("and get them back", st == 200 and body["save"] == save, json.dumps(body))

        # The one that matters most: one person's save is not another's.
        st, body, _ = b.req("POST", "/api/login",
                            {"email": "ann@example.com", "password": "wrong password here"})
        check("a wrong password does not get in", st == 401, str(st))
        d = Client()
        d.req("POST", "/api/signup", {"email": "bob@example.com", "password": OTHER_PW})
        st, body, _ = d.req("GET", "/api/save")
        check("a different account sees its own empty shelf, not yours",
              st == 200 and body.get("save") is None, json.dumps(body))
        d.req("POST", "/api/save", {"save": {"name": "Pepper"}})
        st, body, _ = a.req("GET", "/api/save")
        check("and writing to it does not touch yours",
              body["save"]["name"] == "Rusty", json.dumps(body["save"])[:80])

        # -- size limits ----------------------------------------------------
        big = {"blob": "x" * (m.FREE_SAVE_BYTES + 5000)}
        st, body, _ = a.req("POST", "/api/save", {"save": big})
        check("a save past the free limit is refused, with the numbers",
              st == 413 and body.get("limit") == m.FREE_SAVE_BYTES, json.dumps(body)[:120])
        store.set_plan(row["id"], "pro", int(time.time()) + 86400)
        st, body, _ = a.req("POST", "/api/save", {"save": big})
        check("and accepted on the paid plan", st == 200 and body.get("plan") == "pro",
              json.dumps(body)[:120])
        # A subscription that has run out is not a subscription.
        store.set_plan(row["id"], "pro", int(time.time()) - 10)
        st, body, _ = a.req("GET", "/api/me")
        check("a lapsed subscription is not a subscription",
              body.get("plan") == "free", json.dumps(body))
        store.set_plan(row["id"], "free", None)
        a.req("POST", "/api/save", {"save": save})

        # -- cross-site -----------------------------------------------------
        st, body, _ = a.req("POST", "/api/save", {"save": {"name": "Hacked"}},
                            origin="https://evil.example.com")
        check("a post from another site is refused outright", st == 403, str(st))
        st, body, _ = a.req("GET", "/api/save")
        check("and changed nothing", body["save"]["name"] == "Rusty",
              json.dumps(body["save"])[:60])

        # -- changing a password --------------------------------------------
        old_cookie = a.cookie
        st, body, _ = a.req("POST", "/api/password",
                            {"current": "not it", "password": "a brand new phrase"})
        check("you cannot change a password without the old one", st == 403, str(st))
        st, body, _ = a.req("POST", "/api/password",
                            {"current": GOOD_PW, "password": "a brand new phrase"})
        check("but you can with it", st == 200, json.dumps(body))
        stale = Client()
        stale.cookie = old_cookie
        st, body, _ = stale.req("GET", "/api/me")
        check("changing it signs out everything else that was signed in",
              body.get("signedIn") is False, json.dumps(body))
        st, _, _ = a.req("GET", "/api/me")
        check("but not the browser that changed it", st == 200)

        # -- rate limiting --------------------------------------------------
        e = Client()
        codes = []
        for _ in range(m.LOGIN_MAX + 2):
            st, _, _ = e.req("POST", "/api/login",
                             {"email": "bob@example.com", "password": "guess guess guess"})
            codes.append(st)
        check("guessing gets you locked out rather than more guesses",
              429 in codes, " ".join(str(x) for x in codes))

        # -- signing out ----------------------------------------------------
        st, _, _ = d.req("POST", "/api/logout")
        check("you can sign out", st == 200)
        st, body, _ = d.req("GET", "/api/me")
        check("and are then nobody again", body.get("signedIn") is False)

        # -- deleting -------------------------------------------------------
        f = Client()
        f.req("POST", "/api/signup", {"email": "carla@example.com", "password": OTHER_PW})
        f.req("POST", "/api/save", {"save": {"name": "Gone"}})
        st, _, _ = f.req("POST", "/api/delete-account", {"password": "wrong"})
        check("deleting an account needs the password", st == 403, str(st))
        st, _, _ = f.req("POST", "/api/delete-account", {"password": OTHER_PW})
        check("and then actually deletes it", st == 200, str(st))
        check("with the save gone too, not just the login",
              store.user_by_email("carla@example.com") is None and
              b"Gone" not in open(db, "rb").read())

        # -- billing --------------------------------------------------------
        st, body, _ = a.req("POST", "/api/billing/checkout", {})
        check("checkout says it is not switched on rather than pretending",
              st == 501 and "error" in body, json.dumps(body))

        # -- the game itself ------------------------------------------------
        st, body, hdr = a.req("GET", "/")
        check("the server hands out the game", st == 200 and b"<title>" in bytes(body),
              hdr.get("Content-Type"))
        check("with nosniff on it", hdr.get("X-Content-Type-Options") == "nosniff")
        st, _, _ = a.req("GET", "/assets/city.png")
        check("and its art", st == 200, str(st))
        st, _, _ = a.req("GET", "/assets/../server/app.py")
        check("but not whatever else is on the disk", st in (400, 403, 404), str(st))
        st, _, _ = a.req("GET", "/server/app.py")
        check("nor its own source", st == 404, str(st))
    finally:
        httpd.shutdown()
        httpd.server_close()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILED:
        print(f"{len(FAILED)} check(s) failed")
        sys.exit(1)
    print(f"all {len(PASSED)} checks passed")


if __name__ == "__main__":
    main()
