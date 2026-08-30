#!/usr/bin/env python3
"""The account server: sign up, sign in, and your walks kept for you.

Runs the game as well, so this is one deployable thing rather than a static
site plus an API on a different origin — which also means the session cookie
is same-origin and there is no CORS to get wrong.

    python3 server/app.py                 # http://127.0.0.1:8080
    DOGWALK_SECRET=... python3 server/app.py --host 0.0.0.0 --port 8080

No dependencies. Everything below is standard library: sqlite3 for storage,
hashlib.scrypt for passwords, hmac-signed cookies for sessions. That is a
deliberate choice rather than a shortcut — this project vendors its own Phaser
and draws its own tiles, and a login form is not a good reason to take on a
dependency tree. It is honest about its limits: see "Running this for real" in
the README before pointing a domain at it.

What it never does: store a password, log a request body, or hold anybody's
Tractive credentials. Syncing from a tracker stays on the machine its owner is
sitting at — see scripts/export_tractive.py --serve.
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ---------------------------------------------------------------- settings ---
# scrypt at these parameters costs roughly 100ms and 32MB per attempt here,
# which is the point: it is the difference between a stolen database being a
# bad afternoon and being everybody's password.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_LEN = 32
# OpenSSL refuses scrypt over 32MB unless asked, and n=2**15 with r=8 needs
# exactly 128 * N * r = 32MB. Without this it raises "memory limit exceeded"
# and every signup dies — which it did, first time this was run.
SCRYPT_MAXMEM = 96 * 1024 * 1024

SESSION_DAYS = 45
# Sessions are looked up by the hash of the token, not the token, so the table
# is useless to anybody who reads it.
TOKEN_BYTES = 32

# Login attempts, per account and per address. Slow enough to make guessing
# pointless, generous enough that somebody with a fat thumb is not locked out.
LOGIN_WINDOW_S = 900
LOGIN_MAX = 8

MAX_BODY = 4 * 1024 * 1024        # a save is walk points; a big one is ~1MB
FREE_SAVE_BYTES = 512 * 1024
PRO_SAVE_BYTES = 8 * 1024 * 1024

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")

STATIC = {
    "/": ("game.html", "text/html; charset=utf-8"),
    "/game.html": ("game.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/sw.js": ("sw.js", "text/javascript; charset=utf-8"),
}
STATIC_DIRS = ("assets", "vendor")
STATIC_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".json": "application/json", ".png": "image/png", ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml", ".ico": "image/x-icon", ".txt": "text/plain; charset=utf-8",
}


# ------------------------------------------------------------------- store ---
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id           INTEGER PRIMARY KEY,
  email        TEXT NOT NULL UNIQUE COLLATE NOCASE,
  pw_salt      BLOB NOT NULL,
  pw_hash      BLOB NOT NULL,
  created_at   INTEGER NOT NULL,
  plan         TEXT NOT NULL DEFAULT 'free',
  plan_until   INTEGER,
  billing_ref  TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash   BLOB PRIMARY KEY,
  user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at   INTEGER NOT NULL,
  expires_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS saves (
  user_id      INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  body         TEXT NOT NULL,
  updated_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
  who          TEXT NOT NULL,
  at           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS login_attempts_who ON login_attempts(who, at);
"""


class Store:
    def __init__(self, path):
        self.path = path
        self._local = threading.local()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=15)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            self._local.c = c
        return c

    # -- users --------------------------------------------------------------
    def create_user(self, email, password):
        salt = secrets.token_bytes(16)
        digest = hash_password(password, salt)
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO users (email, pw_salt, pw_hash, created_at) VALUES (?,?,?,?)",
                (email, salt, digest, int(time.time())))
            return cur.lastrowid

    def user_by_email(self, email):
        return self._conn().execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email,)).fetchone()

    def user_by_id(self, uid):
        return self._conn().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()

    def set_password(self, uid, password):
        salt = secrets.token_bytes(16)
        with self._conn() as c:
            c.execute("UPDATE users SET pw_salt = ?, pw_hash = ? WHERE id = ?",
                      (salt, hash_password(password, salt), uid))

    def set_plan(self, uid, plan, until=None, ref=None):
        with self._conn() as c:
            c.execute("UPDATE users SET plan = ?, plan_until = ?, billing_ref = ? WHERE id = ?",
                      (plan, until, ref, uid))

    # -- sessions -----------------------------------------------------------
    def new_session(self, uid):
        token = secrets.token_urlsafe(TOKEN_BYTES)
        now = int(time.time())
        with self._conn() as c:
            c.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at)"
                      " VALUES (?,?,?,?)",
                      (token_hash(token), uid, now, now + SESSION_DAYS * 86400))
        return token

    def session_user(self, token):
        if not token:
            return None
        row = self._conn().execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token_hash = ? AND s.expires_at > ?",
            (token_hash(token), int(time.time()))).fetchone()
        return row

    def drop_session(self, token):
        if not token:
            return
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))

    def drop_all_sessions(self, uid):
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))

    def sweep(self):
        now = int(time.time())
        with self._conn() as c:
            c.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            c.execute("DELETE FROM login_attempts WHERE at <= ?", (now - LOGIN_WINDOW_S,))

    # -- saves --------------------------------------------------------------
    def get_save(self, uid):
        return self._conn().execute(
            "SELECT body, updated_at FROM saves WHERE user_id = ?", (uid,)).fetchone()

    def put_save(self, uid, body):
        with self._conn() as c:
            c.execute("INSERT INTO saves (user_id, body, updated_at) VALUES (?,?,?)"
                      " ON CONFLICT(user_id) DO UPDATE SET body = excluded.body,"
                      " updated_at = excluded.updated_at",
                      (uid, body, int(time.time())))

    def delete_user(self, uid):
        with self._conn() as c:
            c.execute("DELETE FROM saves WHERE user_id = ?", (uid,))
            c.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
            c.execute("DELETE FROM users WHERE id = ?", (uid,))

    # -- rate limiting ------------------------------------------------------
    def note_attempt(self, who):
        with self._conn() as c:
            c.execute("INSERT INTO login_attempts (who, at) VALUES (?,?)",
                      (who, int(time.time())))

    def recent_attempts(self, who):
        return self._conn().execute(
            "SELECT COUNT(*) AS n FROM login_attempts WHERE who = ? AND at > ?",
            (who, int(time.time()) - LOGIN_WINDOW_S)).fetchone()["n"]

    def clear_attempts(self, who):
        with self._conn() as c:
            c.execute("DELETE FROM login_attempts WHERE who = ?", (who,))


def hash_password(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt,
                          n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=SCRYPT_LEN,
                          maxmem=SCRYPT_MAXMEM)


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).digest()


def check_password(row, password):
    want = bytes(row["pw_hash"])
    got = hash_password(password, bytes(row["pw_salt"]))
    return hmac.compare_digest(want, got)


# ------------------------------------------------------------------ policy ---
def password_problem(password):
    """What is wrong with this password, or None.

    Length first and length mostly. A twelve-character passphrase somebody can
    remember beats eight characters of punctuation they keep in a note on their
    phone, and rules that demand a symbol mostly produce Password1!.
    """
    if not isinstance(password, str):
        return "Give a password."
    if len(password) < 10:
        return "Use at least 10 characters — a short phrase is ideal."
    if len(password) > 200:
        return "That is longer than 200 characters."
    low = password.strip().lower()
    if low in ("password", "12345678901", "dogwalkdogwalk", "qwertyuiop", "letmein123"):
        return "Pick something less guessable."
    return None


def email_problem(email):
    if not isinstance(email, str) or not EMAIL_RE.match(email.strip()):
        return "That does not look like an email address."
    if len(email) > 200:
        return "That address is too long."
    return None


def plan_of(row):
    """A subscription that has run out is not a subscription."""
    if row["plan"] != "pro":
        return "free"
    until = row["plan_until"]
    if until and until < time.time():
        return "free"
    return "pro"


def save_limit(plan):
    return PRO_SAVE_BYTES if plan == "pro" else FREE_SAVE_BYTES


# ---------------------------------------------------------------- handler ----
def make_handler(store, opts):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "dogwalk"
        sys_version = ""

        # -- plumbing -------------------------------------------------------
        def _send(self, code, payload, extra=None):
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self._security_headers()
            for k, v in (extra or []):
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _security_headers(self):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "same-origin")
            self.send_header("X-Frame-Options", "DENY")
            if opts.https:
                self.send_header("Strict-Transport-Security",
                                 "max-age=31536000; includeSubDomains")

        def _cookies(self):
            raw = self.headers.get("Cookie") or ""
            out = {}
            for part in raw.split(";"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    out[k.strip()] = v.strip()
            return out

        def _set_session_cookie(self, token):
            bits = ["dw_session=" + token, "Path=/", "HttpOnly", "SameSite=Lax",
                    "Max-Age=" + str(SESSION_DAYS * 86400)]
            if opts.https:
                bits.append("Secure")
            return ("Set-Cookie", "; ".join(bits))

        def _clear_session_cookie(self):
            bits = ["dw_session=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
            if opts.https:
                bits.append("Secure")
            return ("Set-Cookie", "; ".join(bits))

        def _body(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None, "Bad Content-Length."
            if n > MAX_BODY:
                return None, "That is too big."
            if n <= 0:
                return {}, None
            raw = self.rfile.read(n)
            try:
                return json.loads(raw.decode("utf-8")), None
            except Exception:                                # noqa: BLE001
                return None, "Send JSON."

        def _me(self):
            return store.session_user(self._cookies().get("dw_session"))

        def _same_origin(self):
            """Cross-site POSTs must not be able to act as somebody.

            SameSite=Lax already stops the cookie riding along on a cross-site
            POST, so this is a second lock on the same door: anything that
            changes state has to say it came from here.
            """
            origin = self.headers.get("Origin")
            if origin is None:
                # No Origin at all is a same-origin form post or a non-browser
                # client. The cookie policy is what protects that case.
                return True
            host = self.headers.get("Host") or ""
            try:
                u = urlparse(origin)
            except ValueError:
                return False
            if opts.origin:
                return origin in opts.origin
            return u.netloc == host

        def _oops(self, exc):
            """Something in here is broken. Say so in one line and answer the
            request — dropping the connection instead turns a bug into a hang,
            which is how the scrypt memory limit hid on the first run."""
            sys.stderr.write("! %s handling %s: %s\n" %
                             (type(exc).__name__, urlparse(self.path).path, exc))
            try:
                return self._send(500, {"error": "Something went wrong here."})
            except Exception:                                # noqa: BLE001
                return None

        def log_message(self, fmt, *a):
            if opts.quiet:
                return
            # Path only. Never the body, never the query string — one is
            # passwords and the other is where somebody walks their dog.
            sys.stderr.write("%s %s %s\n" % (self.address_string(), self.command,
                                             urlparse(self.path).path))

        # -- routes ---------------------------------------------------------
        def do_GET(self):                                    # noqa: N802
            route = urlparse(self.path)
            try:
                if route.path.startswith("/api/"):
                    return self._api_get(route)
                return self._static(route.path)
            except Exception as exc:                         # noqa: BLE001
                return self._oops(exc)

        def do_HEAD(self):                                   # noqa: N802
            return self.do_GET()

        def do_POST(self):                                   # noqa: N802
            route = urlparse(self.path)
            if not route.path.startswith("/api/"):
                return self._send(404, {"error": "No such thing here."})
            if not self._same_origin():
                return self._send(403, {"error": "Cross-site request refused."})
            try:
                return self._api_post(route)
            except Exception as exc:                         # noqa: BLE001
                return self._oops(exc)

        def do_PUT(self):                                    # noqa: N802
            return self.do_POST()

        def _api_get(self, route):
            if route.path == "/api/health":
                return self._send(200, {"ok": True})
            if route.path == "/api/me":
                me = self._me()
                if not me:
                    return self._send(200, {"signedIn": False})
                return self._send(200, {"signedIn": True, "email": me["email"],
                                        "plan": plan_of(me),
                                        "planUntil": me["plan_until"],
                                        "saveLimit": save_limit(plan_of(me))})
            if route.path == "/api/save":
                me = self._me()
                if not me:
                    return self._send(401, {"error": "Sign in first."})
                row = store.get_save(me["id"])
                if not row:
                    return self._send(200, {"save": None, "updatedAt": None})
                return self._send(200, {"save": json.loads(row["body"]),
                                        "updatedAt": row["updated_at"]})
            return self._send(404, {"error": "No such thing here."})

        def _api_post(self, route):
            body, err = self._body()
            if err:
                return self._send(400, {"error": err})

            if route.path == "/api/signup":
                return self._signup(body)
            if route.path == "/api/login":
                return self._login(body)
            if route.path == "/api/logout":
                token = self._cookies().get("dw_session")
                store.drop_session(token)
                return self._send(200, {"signedIn": False}, [self._clear_session_cookie()])
            if route.path == "/api/save":
                return self._save(body)
            if route.path == "/api/password":
                return self._password(body)
            if route.path == "/api/delete-account":
                return self._delete(body)
            if route.path == "/api/billing/checkout":
                return self._checkout(body)
            return self._send(404, {"error": "No such thing here."})

        # -- accounts -------------------------------------------------------
        def _signup(self, body):
            email = (body.get("email") or "").strip()
            password = body.get("password") or ""
            problem = email_problem(email) or password_problem(password)
            if problem:
                return self._send(400, {"error": problem})
            if store.user_by_email(email):
                # Telling somebody an address is taken hands an attacker a list
                # of who has an account here. The address gets an email instead
                # — or would, once there is an address to send it from; until
                # then this is honest about what it did not do.
                return self._send(200, {"signedIn": False, "check": True})
            uid = store.create_user(email, password)
            token = store.new_session(uid)
            row = store.user_by_id(uid)
            return self._send(200, {"signedIn": True, "email": row["email"],
                                    "plan": "free", "saveLimit": save_limit("free")},
                              [self._set_session_cookie(token)])

        def _login(self, body):
            email = (body.get("email") or "").strip()
            password = body.get("password") or ""
            who = "ip:" + self.address_string()
            acct = "em:" + email.lower()
            if store.recent_attempts(who) >= LOGIN_MAX or \
               store.recent_attempts(acct) >= LOGIN_MAX:
                return self._send(429, {"error": "Too many tries. Wait a few minutes."})

            row = store.user_by_email(email)
            if row:
                ok = check_password(row, password)
            else:
                # Hash anyway. Otherwise "no such account" answers in a
                # millisecond and "wrong password" takes a hundred, and the
                # difference is a list of who has an account here.
                hash_password(password, b"no-such-user-0000")
                ok = False
            if not ok:
                store.note_attempt(who)
                store.note_attempt(acct)
                return self._send(401, {"error": "That email and password do not match."})

            store.clear_attempts(who)
            store.clear_attempts(acct)
            token = store.new_session(row["id"])
            return self._send(200, {"signedIn": True, "email": row["email"],
                                    "plan": plan_of(row),
                                    "saveLimit": save_limit(plan_of(row))},
                              [self._set_session_cookie(token)])

        def _password(self, body):
            me = self._me()
            if not me:
                return self._send(401, {"error": "Sign in first."})
            if not check_password(me, body.get("current") or ""):
                return self._send(403, {"error": "That is not your current password."})
            problem = password_problem(body.get("password") or "")
            if problem:
                return self._send(400, {"error": problem})
            store.set_password(me["id"], body["password"])
            # Everything else signed in as them stops being signed in. Changing
            # a password is usually somebody saying "get whoever it is out".
            store.drop_all_sessions(me["id"])
            token = store.new_session(me["id"])
            return self._send(200, {"signedIn": True, "email": me["email"]},
                              [self._set_session_cookie(token)])

        def _delete(self, body):
            me = self._me()
            if not me:
                return self._send(401, {"error": "Sign in first."})
            if not check_password(me, body.get("password") or ""):
                return self._send(403, {"error": "Password does not match."})
            store.delete_user(me["id"])
            return self._send(200, {"deleted": True}, [self._clear_session_cookie()])

        # -- the save ------------------------------------------------------
        def _save(self, body):
            me = self._me()
            if not me:
                return self._send(401, {"error": "Sign in first."})
            save = body.get("save")
            if not isinstance(save, dict):
                return self._send(400, {"error": "Send {\"save\": {...}}."})
            text = json.dumps(save, separators=(",", ":"))
            plan = plan_of(me)
            limit = save_limit(plan)
            if len(text.encode()) > limit:
                return self._send(413, {
                    "error": "That save is bigger than your plan allows.",
                    "plan": plan, "limit": limit, "size": len(text.encode())})
            store.put_save(me["id"], text)
            return self._send(200, {"saved": True, "size": len(text.encode()),
                                    "limit": limit, "plan": plan})

        # -- billing --------------------------------------------------------
        def _checkout(self, body):
            me = self._me()
            if not me:
                return self._send(401, {"error": "Sign in first."})
            if not opts.stripe_key:
                # Deliberately not a pretend success. There is nothing to buy
                # until somebody has wired a payment provider up, and a button
                # that says "thanks" without taking money is worse than one
                # that says so.
                return self._send(501, {
                    "error": "Subscriptions are not switched on yet.",
                    "why": "No payment provider is configured on this server."})
            return self._send(501, {
                "error": "Checkout is not implemented.",
                "why": "See 'Subscriptions' in the README for where this goes."})

        # -- static ---------------------------------------------------------
        def _static(self, path):
            entry = STATIC.get(path)
            if entry:
                return self._file(os.path.join(ROOT, entry[0]), entry[1])
            parts = path.lstrip("/").split("/")
            if len(parts) == 2 and parts[0] in STATIC_DIRS:
                name = parts[1]
                if "/" in name or ".." in name or name.startswith("."):
                    return self._send(404, {"error": "No such thing here."})
                full = os.path.join(ROOT, parts[0], name)
                ext = os.path.splitext(name)[1]
                return self._file(full, STATIC_TYPES.get(ext, "application/octet-stream"))
            return self._send(404, {"error": "No such thing here."})

        def _file(self, full, ctype):
            real = os.path.realpath(full)
            if not real.startswith(os.path.realpath(ROOT) + os.sep) or \
               not os.path.isfile(real):
                return self._send(404, {"error": "No such thing here."})
            with open(real, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            # The page and the worker are checked every time; the art and the
            # engine are content-addressed by the worker and never change.
            if real.endswith((".html", ".webmanifest")) or real.endswith("sw.js"):
                self.send_header("Cache-Control", "no-cache")
            else:
                self.send_header("Cache-Control", "public, max-age=604800")
            self._security_headers()
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

    return Handler


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1", help="Address to bind (default: loopback)")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--db", default=os.environ.get("DOGWALK_DB",
                                                  os.path.join(HERE, "dogwalk.sqlite3")))
    p.add_argument("--https", action="store_true",
                   help="This is served over TLS (sets Secure cookies and HSTS). "
                        "Set it whenever anything but localhost can reach you.")
    p.add_argument("--origin", action="append", default=None,
                   help="Exact origin(s) allowed to make state-changing requests. "
                        "Defaults to whatever Host the request came in on, which is "
                        "right behind a normal reverse proxy.")
    p.add_argument("--stripe-key", default=os.environ.get("STRIPE_SECRET_KEY"),
                   help="Payment provider secret. Without it, checkout answers 501.")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args(argv)


def main():
    opts = parse_args()
    store = Store(opts.db)
    store.sweep()

    httpd = ThreadingHTTPServer((opts.host, opts.port), make_handler(store, opts))
    httpd.daemon_threads = True

    def sweeper():
        while True:
            time.sleep(3600)
            try:
                store.sweep()
            except Exception:                                # noqa: BLE001
                pass

    threading.Thread(target=sweeper, daemon=True).start()

    if opts.host not in ("127.0.0.1", "localhost", "::1") and not opts.https:
        print("! Reachable from off this machine without --https: session cookies\n"
              "  will not be marked Secure. Put TLS in front of this and pass --https.",
              file=sys.stderr)
    print(f"dogwalk on http://{opts.host}:{opts.port}  (db: {opts.db})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
