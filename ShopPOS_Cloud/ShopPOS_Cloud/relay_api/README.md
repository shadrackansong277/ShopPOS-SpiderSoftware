# ShopPOS Cloud Relay

The hosted counterpart to ShopPOS's built-in **Settings → Remote Access**
page. Your Electron app already has everything it needs to talk to this —
`python_api/remote/sync_agent.py` pushes sales/stock every 30 seconds, and
`renderer/js/pages/remote.js` is the setup screen. This folder is the piece
that was missing: the server those talk to, plus a live owner dashboard you
can open from any browser or phone.

**No changes needed on the desktop app side** — the endpoints below match
what it already calls, exactly.

## What it does

- `POST /api/register` — one-time signup from ShopPOS's Remote Access screen
- `POST /api/push` — receives sales, stock movements, low-stock alerts and a
  daily snapshot every 30 seconds, authenticated with the shop's key
- `GET /login` + `/dashboard` — a small web app the shop owner signs into
  (same email/password used at registration) to see revenue, cashier
  performance, recent transactions and low-stock alerts from anywhere

## 1. Deploy

### Option A — Render Blueprint (easiest)

1. Push this `relay_api/` folder to a GitHub repo (or the whole ShopPOS
   repo — Render lets you point a Blueprint at a subfolder).
2. In Render: **New → Blueprint**, select the repo. `render.yaml` here
   provisions a free web service *and* a free Postgres database, and wires
   `DATABASE_URL`/`SECRET_KEY` automatically.
3. Wait for the build — Render gives you a URL like
   `https://shoppos-relay-xxxx.onrender.com`.

### Option B — Manual Render Web Service (matches the in-app guide)

1. **New → Web Service**, connect the repo, set the root directory to
   `ShopPOS_Cloud/relay_api`.
2. Build command: `pip install -r requirements.txt`
3. Start command: `gunicorn app:app --bind 0.0.0.0:$PORT`
4. Add an environment variable `SECRET_KEY` = any random 32+ character
   string.
5. **Storage matters**: Render's free plan has an *ephemeral* disk — it
   wipes on every deploy/restart. Either:
   - add a free **Render Postgres** instance and set `DATABASE_URL` to its
     connection string (the relay auto-detects and uses it), **or**
   - attach a Render **Persistent Disk** at `/data` and set
     `RELAY_DB_PATH=/data/relay.db`.
   Without one of these, a redeploy loses all synced data (fine for
   testing, not for real use).

### Option C — Anywhere else

It's a plain Flask app behind gunicorn — Railway, Fly.io, a `$5` VPS, or
Docker all work the same way. Set `SECRET_KEY` and, ideally, `DATABASE_URL`.

## 2. Connect ShopPOS to it

1. Open ShopPOS → **Settings → Remote Access**.
2. Paste the relay URL (e.g. `https://shoppos-relay-xxxx.onrender.com`).
3. Enter an owner email + password (this becomes the dashboard login).
4. Click **Enable Remote Access**. The sync agent starts pushing every 30s.
5. Click **Open Owner Dashboard**, or just visit `<relay-url>/dashboard`
   from any browser and sign in.

## Notes on the free tier

Render's free web services sleep after 15 minutes idle. The sync agent's
next push wakes it up, but that first push takes ~30 seconds. The $7/month
plan keeps it always on — worth it once you're relying on this for live
monitoring rather than checking in occasionally.

## Security notes

- Passwords are SHA-256 hashed **before they leave the browser or the
  desktop app** — the relay never sees or stores a plaintext password. This
  matches the hashing scheme already used by `sync_agent.py`. It's adequate
  for this use case but not bank-grade; if you want stronger protection,
  swap in `werkzeug.security.generate_password_hash` (bcrypt/scrypt) on
  both sides.
- Each shop's `shop_key` (used to authenticate `/api/push`) is stored
  hashed, never in plaintext.
- One known quirk inherited from the existing desktop UI: the "Update
  Settings" button sends the literal string `__keep__` when the password
  field is left blank, which this relay will hash and store as-is (it
  doesn't special-case it into "leave unchanged"). If you want true
  "leave password unchanged" behavior, that needs a small tweak in
  `renderer/js/pages/remote.js`'s `rmUpdate()`.

## Multi-shop hosting

One relay deployment can host multiple independent shops — each
registration gets its own `shop_id`, `shop_key`, and login, and dashboard
data is scoped per shop via the session. You can either deploy one relay
per shop (simplest, matches the in-app guide) or run a single relay for
several shops/branches you own.
