# Running this for real

`server/app.py` is the account server. It also serves the game, so it is one
process rather than a static site plus an API — which is why the session cookie
is same-origin and there is no CORS anywhere in it.

```bash
python3 server/app.py                    # http://127.0.0.1:8080
python3 server/app.py --host 0.0.0.0 --port 8080 --https
python3 tests/server_test.py             # 42 checks, no browser, no network
```

No dependencies. sqlite3, `hashlib.scrypt` and `hmac` are all standard library.
That is deliberate rather than lazy: this project vendors its own Phaser and
draws its own tiles, and a login form is not a good reason to take on a
dependency tree that has to be watched for the next decade.

## What it does with a password

Never stores one. Sign-up runs it through scrypt (N=2¹⁵, r=8) with sixteen
random bytes of salt, and keeps the 32-byte result. That costs about a tenth of
a second and 32MB per attempt, which is the point: it is the difference between
a stolen database being a bad afternoon and being everybody's password. A login
against an address that does not exist hashes anyway, so "no such account" and
"wrong password" take the same time and the reply cannot be used to work out
who has an account here.

Sessions are 32 random bytes in an `HttpOnly; SameSite=Lax` cookie (`Secure`
too, with `--https`). The table stores the *hash* of the token, so a leaked
database is not a set of keys to everybody's account. Changing a password drops
every other session, because changing a password is usually somebody saying
"get whoever it is out".

Request bodies are never logged, and neither are query strings — one of those
is passwords and the other is where somebody walks their dog. Only the method
and the path go to stderr.

## Before you point a domain at it

- **Put TLS in front of it and pass `--https`.** It speaks plain HTTP by
  design; terminate TLS at nginx, Caddy, or your host's proxy. Without
  `--https` the session cookie is not marked `Secure`, and it will warn you at
  startup if it is reachable from off the machine without it.
- **`ThreadingHTTPServer` is not a production web server.** It is fine for the
  hundreds of users this will realistically have, behind a proxy that handles
  TLS, timeouts and slow clients. If it grows past that, the handler is small
  and portable — move it to gunicorn or uvicorn rather than hardening this.
- **Back the database up.** It is one sqlite file (`--db`). People's walks are
  in it.
- **Email is not wired up.** There is no verification and no password reset, so
  a forgotten password is currently an unrecoverable account. That is the first
  thing to add if this gets real users; it needs an address to send from, which
  is a decision rather than a line of code.

## Subscriptions

The free plan is the whole game. Everything — the town, the roaming, the bond,
the weather, racing a ghost — works signed out and always will, because it all
runs in the browser and costs nothing to run.

What the paid plan buys is the part that actually costs money: keeping your
walks on a server so they are on your phone as well as your laptop. Free
accounts get 512KB of save (a few months of walks); paid gets 8MB. The limit is
enforced server-side in `_save`, not in the page.

`POST /api/billing/checkout` answers **501** and says so. That is on purpose: a
button that says "thanks" without taking money is worse than one that admits it
is not switched on. Wiring it up means:

1. Create a product and a recurring price at your payment provider.
2. Set `STRIPE_SECRET_KEY` (or pass `--stripe-key`). Without it, checkout
   refuses before doing anything else.
3. In `_checkout`, create a Checkout Session with `client_reference_id` set to
   the user's id, and return `{"url": ...}` — the game already redirects to a
   `url` if it gets one.
4. Add a webhook endpoint that verifies the provider's signature and calls
   `store.set_plan(uid, "pro", until)`. **Verify the signature.** An unverified
   billing webhook is an endpoint that lets anybody grant themselves a
   subscription.
5. `plan_of()` already treats a lapsed `plan_until` as free, so an expiry needs
   no extra code — only the webhook that sets the date.

Nothing about billing is implemented beyond the plan column, the entitlement
check and the honest 501. It is scaffolding with the load-bearing parts named,
not a half-finished integration.

## What this server never touches

Tractive credentials. Syncing from a tracker runs on the machine its owner is
sitting at — `scripts/export_tractive.py --serve` — and this server has no idea
it exists. Tractive has no OAuth, so a hosted sync would mean holding a reusable
secret to a live GPS tracker on somebody's animal, and this is not the project
to hold that. See the main README for the longer version of that argument.
