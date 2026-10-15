# SPLIX Unlock Server

The subscription-gated link ("unlock link") feature.  It is a **small,
zero-dependency Python server** that:

* serves your existing website **byte-for-byte unchanged**
  (`index.html`, `plus.html`, `css/`, `js/`, favicons),
* renders one unlock page per destination: `/u/<slug>`, with a
  **two-task Sub4Unlock-style experience** (Subscribe to the YouTube
  channel + Like a YouTube video),
* exposes the admin API behind a bearer token (used by
  `/unlock-admin.html`),
* verifies what can genuinely be verified **on the server** using the
  official Google / YouTube API, uses an explicitly configured honest
  fallback for what cannot, and only then releases the destination
  URL.

No pip packages, no Node, no database — plain Python 3 standard library
(JSON files under `server/data/`).

---

## 1. Quick start

```powershell
cd SPLIX_X_CHEAT_WEBSITE
python server\run.py
```

| What                | Where                                              |
|---------------------|----------------------------------------------------|
| Website             | http://127.0.0.1:8099/index.html                   |
| Unlock pages        | http://127.0.0.1:8099/u/`<slug>`                   |
| Admin panel         | http://127.0.0.1:8099/unlock-admin.html            |
| Configuration       | `server\.env`  (copy from `server\.env.example`)   |
| Data (links, etc.)  | `server\data\`                                     |

> Opening `index.html` directly from disk still works exactly as before —
> the server is only required for unlock pages and the admin panel.

---

## 2. Required environment variables (`server/.env`)

| Variable | Required | Purpose |
|---|---|---|
| `PORT` / `HOST` | no (8099 / 127.0.0.1) | Bind address. Local defaults shown. On a hosting platform the platform-provided `PORT` is used, and when `HOST` is unset the server binds `0.0.0.0` (platform/proxy mode) — an explicit `HOST` always wins. |
| `PUBLIC_URL` | **yes for OAuth** | The URL users browse, e.g. `https://your-domain.com`. Used as the OAuth redirect origin (`PUBLIC_URL + /api/oauth/callback`). Until it is set, hosting platforms that expose their assigned URL (Render: `RENDER_EXTERNAL_URL`) are used automatically — no origin is ever invented. |
| `DATA_DIR` | no (`server/data`) | Folder holding `links.json` / `reviews.json` / `settings.json` / `sessions.json` / `events.jsonl`. On a persistent disk, e.g. Render: `DATA_DIR=/var/data`. |
| `SEED_DIR` | no | Optional first-boot seed folder: its data files are copied into an **empty** `DATA_DIR` once at startup (existing files are never overwritten). Used to migrate state onto a fresh disk — see §10. |
| `ADMIN_TOKEN` | **yes for admin** | Long random string protecting the admin panel + API. Without it the admin API returns `503 admin_disabled`. Generate: `python -c "import secrets;print(secrets.token_urlsafe(32))"` |
| `GOOGLE_CLIENT_ID` | for auto-verification | OAuth client ID (Google Cloud). |
| `GOOGLE_CLIENT_SECRET` | for auto-verification | OAuth client secret — **server-side only, never sent to the browser**. |
| `YOUTUBE_CHANNEL_ID` | for auto-verification | Your channel ID (`UC…`). Env overrides the value saved in admin → *Site & footer*. |
| `DISCORD_WEBHOOK_URL` | no | Channel webhook used by the admin **PUBLISH** button. |
| `SESSION_TTL_MIN` | no (60) | How long a verified unlock stays valid. |
| `REVIEW_TTL_MIN` | no (30) | Lifetime of a manual review request. |
| `OAUTH_STATE_TTL_SEC` | no (600) | Single-use OAuth `state` lifetime. |
| `TRUST_PROXY` | no (false) | **Enable behind the hosting platform's HTTPS proxy** (required on Render). The client IP is then taken from the *rightmost* `X-Forwarded-For` entry (added by the single trusted proxy — clients cannot spoof it) and `X-Forwarded-Proto: https` makes cookies `Secure`. Only enable with exactly one trusted proxy in front. |
| `RL_API` … `RL_ADMIN` (incl. `RL_LIKE`) | no | Rate limits (requests per window, window = 60 s except `RL_REVIEW` = 600 s). |

Secrets live **only** in `server/.env` (not committed, not served — the
static handler blocks the whole `server/` folder, `.env` and `*.py`).

---

## 3. Official YouTube verification — Google Cloud setup

Verification uses **YouTube Data API v3 → `subscriptions.list`** with
`mine=true&forChannelId=<your channel>`.  That call can only be answered
with the *viewer's own* OAuth token, which is why the flow signs the user
in with Google.  A click on the subscribe button, a page visit, a query
parameter or edited JavaScript can never satisfy it.

1. **Create a project** — <https://console.cloud.google.com> → *New
   Project* (or use an existing one).
2. **Enable the API** — *APIs & Services → Library* → search
   **YouTube Data API v3** → *Enable*.
3. **OAuth consent screen** — *APIs & Services → OAuth consent screen*:
   * User Type: **External** (unless you have a Google Workspace org),
   * App name / contact details as you like,
   * **Scopes → add** `https://www.googleapis.com/auth/youtube.readonly`
     (this is the only scope the app requests),
   * **Test users → add** every Google account you will test with
     (required while the app is in *Testing* mode).
4. **Credentials** — *APIs & Services → Credentials → Create Credentials →
   OAuth client ID → Web application*, then add **one** Authorized
   redirect URI, exactly:

   ```
   http://127.0.0.1:8099/api/oauth/callback        (local testing)
   https://your-domain.com/api/oauth/callback       (production)
   ```

   Copy the *Client ID* and *Client secret*.
5. **Channel ID** — YouTube Studio → *Settings → Advanced → Channel
   settings → Advanced settings → Channel ID* (`UC…`).  Put it in
   `YOUTUBE_CHANNEL_ID` or in admin → *Site & footer*.
6. **Fill `server/.env`** and restart `python server/run.py`.  The admin
   banner now reads *Automatic verification active*.

**What a user experiences:** Subscribe button opens your channel →
*Verify with Google* → Google sign-in for the account they subscribed
with → server asks YouTube → subscribed? session granted → *Get Your
Link* enables.

**Cost / quota:** `subscriptions.list` = 1 quota unit (default 10,000
units/day).  No API key is needed for this flow — the user's access token
authorizes the call.

### The two-task model (Subscribe + Like) and honest limitations

Each link can configure **two tasks** (both optional, shown as separate
cards on the unlock page):

| Task | Configured with | How it is satisfied |
|---|---|---|
| **A — Subscribe** | YouTube channel URL (or the step URL) | *Automatically* via the YouTube API (`subscriptions.list`) when OAuth is configured — otherwise by moderator review. |
| **B — Like** | YouTube video URL | **Never automatically.** YouTube's API does not expose whether a specific viewer liked a video, so this task uses the link's explicitly configured honest fallback (see below). |

The Like policy is chosen **per link** in the admin form:

* **Moderator review (default)** — after subscribing (verified with
  Google in auto mode, or by you in manual mode), the visitor requests a
  review and you approve the like step by hand.  In automatic mode a
  review request is only accepted *after* the subscribe step was
  verified by the API — a review can never substitute for automatic
  verification.
* **Honor system** — after the subscribe step is satisfied, the visitor
  records "I liked the video" themselves.  The server stores this as a
  **confirmation (`honor`)**, and the page labels it `CONFIRMED
  (HONOR)` — it is never shown as "verified".

Progress is displayed as `0/2 → 1/2 → 2/2` with per-task states (NOT
STARTED / PENDING / MANUAL REVIEW / VERIFIED / CONFIRMED (HONOR) /
FAILED), and **every requirement is enforced again on the server**
before the destination URL is returned.  Links with only one configured
task keep working exactly as before (`x/1`).

`YOUTUBE_CHANNEL_ID` (env or admin settings) is the channel the API
subscription check runs against — make sure the per-link channel URL
points at that same channel.

**If you do not configure OAuth** the feature still works with the
explicit *manual review* mode: the user subscribes, presses
*I've subscribed — request review*, and **you** approve the request in
the admin panel after checking yourself.  The page states this clearly —
it never claims a verification that did not happen.

---

## 4. Admin panel (`/unlock-admin.html`)

1. Paste your `ADMIN_TOKEN` → *Unlock panel* (kept in `sessionStorage`
   for that tab only).
2. **Links tab**
   * *NEW UNLOCK LINK* — page title, slug (optional: auto-generated from
     the title), destination URL (kept secret on the server), step label,
     step URL (the subscribe link), step type, active toggle, plus the
     two-task options: **YouTube channel URL** (Subscribe task card),
     **YouTube video URL** (adds the Like task card) and the **Like
     verification policy** (moderator review / honor system — see §3).
   * Each row: copy the unlock URL, edit, **disable/enable**, delete (with
     confirmation), and **PUBLISH** to Discord when a webhook is configured.
     The *Step* column shows the configured tasks
     (`subscribe`, `like · review`, `like · honor`, or `custom`).
   * Each row also shows live **stats**: page **visits**, server-confirmed
     **verifications** and successful **unlocks**.
3. **Manual reviews tab** — approve/reject pending requests.  In manual
   mode a review covers every step of the link; when automatic YouTube
   verification is active a review only ever covers the **LIKE** step
   (the subscribe step must already be API-verified).  Approving grants
   exactly those requirements — nothing more.
4. **Activity tab** — an audit trail of link creation/updates/deletions,
   verification attempts (success *and* failure), successful unlocks,
   review decisions, publishing and settings changes.  Destination URLs
   and tokens are **never** written to the log.
5. **Site & footer tab** — site name, description, contact details,
   disclaimer, social URLs, and the YouTube channel ID.  These power the
   footer of every unlock page.

Everything is stored in `server/data/links.json` / `settings.json` /
`reviews.json` / `events.jsonl` — no source-code edits needed to add links.

### Statistics & audit log

* `visits` — counted once per real `GET` of the unlock page (not `HEAD`).
* `verified` — counted only when the **server** records a verified
  session (successful OAuth check, or an admin approving a manual review).
* `unlocks` — counted once per verified session on the first successful
  claim (repeat claims with the same session are not double-counted).
* `events.jsonl` — append-only JSONL audit log, kept in memory + on disk
  (last 500 events loaded on start).  Exposed read-only at
  `/api/admin/events` (admin token required).

---

## 5. Discord publishing (optional)

No bot token and **no Administrator permission** are requested.  The
optional PUBLISH button posts an embed through a **channel webhook**:

1. Discord → channel settings → *Integrations → Webhooks → New Webhook*
   → *Copy Webhook URL*.
2. Put it in `DISCORD_WEBHOOK_URL` in `server/.env` and restart.
3. The webhook URL stays on the server; it is never sent to browsers.

The feature is disabled (button hidden) until the variable is set.  If
you later add a real bot, the same endpoint can be reused — nothing else
needs to change.

---

## 6. Security model (what was tested)

* Destination URLs exist only in `server/data/links.json`; the public
  page and `/api/u/<slug>` never contain them.
* Unlock state = server-side session record keyed by the **SHA-256** of a
  random cookie value (`HttpOnly`, `SameSite=Lax`, `Secure` under HTTPS),
  bound to one slug with an expiry (`SESSION_TTL_MIN`).
* Claim endpoint re-validates everything on the backend: missing/forged
  cookies, `?verified=1`, `{"verified":true}` bodies and page-forged
  `?v=1` all return **403**.  With two-task links the claim is additionally
  refused (`403 tasks_incomplete`) until **every** configured requirement
  of the visitor's session is satisfied — client-side state, hidden
  fields and button clicks are never trusted.
* The honor-system like endpoint (`POST /api/u/<slug>/like`) requires a
  live, already subscribe-verified session, is refused when the link's
  policy is moderator review, and records only a *self-confirmation* —
  it can never fabricate a verified state.
* OAuth `state` is single-use with a TTL; tokens and secrets never reach
  the frontend; admin API uses a constant-time bearer check + rate limit.
* Sliding-window rate limits on status, OAuth start, review requests,
  claims and admin calls (HTTP 429 + `Retry-After`).
* Static serving blocks `server/`, dotfiles, `*.py`, `*.env`, and `..`
  traversal; a strict `Content-Security-Policy` (incl. `frame-ancestors
  'self'`) is sent on every response.
* Manual review requests expire; approving a rejected/expired request is
  refused server-side.

---

## 7. Tests

```powershell
python server\tests\run_tests.py
```

Spins up a **mock Google/YouTube API** plus isolated server
instances and runs 264 checks: original files byte-identical, admin CRUD +
validation (incl. slug auto-generation), no destination leakage, all
bypass attempts, the full OAuth success/failure matrix (unsubscribed,
denied, API 403, exchange failure, state replay, slug binding, cookie
flags), **OAuth states bound to the initiating browser** (a captured
callback URL cannot be replayed in another browser), **per-link modes**
(a custom step never uses Google verification, even when OAuth is
configured), manual review approve/reject/expire, **stats + audit-log
accounting** (visits/verified/unlocks counted once, no destinations in the
log), **Discord webhook publishing** (a mock channel webhook receives the
embed; it carries only the unlock URL — never the destination — and a
webhook failure answers 502 with an audit entry), **HTTP keep-alive
integrity** (early responses — 401/403/429 — drain any request body they
never read, so a reused socket can never parse leftover bytes as the next
request), **review-queue hygiene** (deleting a link purges its review
rows; manual review is refused while automatic verification is active),
rate limits, disabled-admin mode, and responsive UI wiring.

**Two-task (Subscribe + Like) coverage:** link creation with channel/
video URLs + like-policy validation (non-YouTube URLs and unknown
policies rejected), `0/2 → 1/2 → 2/2` progress, subscribe verified by
the (mock) YouTube API while the like stays `unverified`, claim refused
at 1/2 with the missing task listed, honor confirmation recorded and
**never shown as verified**, moderator review covering the like step
only after subscribe verification (review requests before that are
refused), manual-mode approval granting exactly the configured
requirements (an honor-policy like is left for the visitor to confirm),
ordering (like confirmation before subscribe verification → 403),
policy guards (honor endpoint refuses review-mode links and links
without a video step), destination secrecy across config/review/like
responses and the audit log, repeated claims counted once, and
deleted two-task links purging their review rows and pages.

**Deployment-readiness coverage (§10):** host/port resolution (explicit
`HOST` wins, platform `PORT`/hints → `0.0.0.0`, local fallback
`127.0.0.1:8099`), `PUBLIC_URL`/platform-URL public-origin resolution,
secret-safe access logs (OAuth `state`/`code` and token query values
masked — proven against a *running* instance's captured stdout),
HTTPS-aware cookies (`Secure` under an https origin and via a trusted
`X-Forwarded-Proto`; forged headers ignored without `TRUST_PROXY`),
`/health` (200, no config/secrets/destinations in the payload, not
rate-limited), HSTS present exactly when the origin is https,
`SEED_DIR` first-boot migration (valid files copied into an empty data
folder, invalid JSON skipped, existing data never overwritten), the
`migrate_data.py` CLI (backup + copy re-verification, `verify` failing
on corruption, `pack` refusing corrupt data, `restore` refused without
`--yes` and snapshotting current data first), git hygiene (`.gitignore`
covers `.env`/`data/`/`backups/`/seed packs; `render.yaml` carries the
real build/start/health commands with **no** stored secrets), secrets
and source never served statically, and all state living inside
`DATA_DIR`. **All 264 pass.**

> The mock validates *our* code against Google-shaped responses.  Live
> YouTube verification additionally requires your real credentials (§3)
> and should be confirmed once with a subscribed test account — until
> then the admin banner honestly reports *Automatic verification not
> configured*.

---

## 8. Keeping secrets out of version control

A `.gitignore` at the project root excludes:

* `server/.env` (the real `ADMIN_TOKEN`, Google client secret and Discord
  webhook URL) — only `server/.env.example` with **empty** placeholders
  may be committed,
* `server/data/` (links, sessions, reviews, the audit log),
* `server/backups/`, `__pycache__/` and OS noise.

If you use Git, confirm with `git status --ignored` that `server/.env`
never appears as a staged file before the first commit.

---

## 9. Files added by this feature

```
server/
├── run.py               Entry point: python server/run.py
├── app.py               Router, static serving, unlock + admin APIs, pages
├── store.py             Links / reviews / settings / stats / audit JSON storage
├── verify.py            Google OAuth + YouTube API verification
├── security.py          Sessions, cookies, secrets, rate limiting
├── discord_publish.py   Optional webhook publishing
├── env.py               .env loader (no dependencies)
├── deploy.py            Deployment helpers: host/port/origin resolution,
│                        secret-safe log redaction, HTTPS cookie flags,
│                        X-Forwarded-For parsing, SEED_DIR migration
├── migrate_data.py      CLI: backup / verify / pack / restore the data folder
├── requirements.txt     Intentionally empty (stdlib only) — build-command target
├── .env.example         Configuration template
├── templates/unlock.html  Unlock page template
├── data/                Runtime data (links, reviews, sessions, settings, events)
├── backups/             Timestamped data backups (never in git)
└── tests/run_tests.py   264-check test-suite (+ mock Google API + mock Discord webhook)

render.yaml              Render blueprint: build/start/health + persistent disk
unlock-admin.html        Admin panel UI
css/unlock.css           Unlock + admin styles (uses existing theme tokens)
js/unlock.js             Unlock page controller
js/admin.js              Admin panel controller
.gitignore               Excludes .env, data/, backups/ and seed packs from version control
```

Existing files (`index.html`, `plus.html`, `css/style.css`, `js/config.js`,
`js/main.js`, `js/plus.js`, `js/particles.js`) were **not modified**.

---

## 10. Production deployment (Render or any HTTPS host)

Nothing in this section has been executed for you — no deployment was
performed, no repository was created or pushed, and no credentials were
generated. The steps in §10.4–10.7 are yours to do in the hosting
dashboard.

### 10.1 What the server expects from the host

| | |
|---|---|
| **Build command** | `pip install -r server/requirements.txt` (the file is intentionally empty of packages — the server is pure Python 3 standard library) |
| **Start command** | `python server/run.py` |
| **Health check path** | `/health` → `200 {"ok": true, "status": "healthy", …}` (never rate-limited; carries only liveness timings — no config, counters, tokens or destinations) |
| **Bind address** | explicit `HOST` wins → otherwise `0.0.0.0` when the platform itself exported `PORT` (captured *before* `.env` is merged) or a platform hint (`RENDER`, `DYNO`, …) exists → otherwise the local fallback `127.0.0.1` |
| **Port** | platform `PORT` when present, otherwise the local fallback `8099` |
| **Public origin** | `PUBLIC_URL` when set → otherwise the platform's assigned URL (Render: `RENDER_EXTERNAL_URL`) → otherwise `http://127.0.0.1:8099`. An origin is never invented. |

`render.yaml` at the project root already encodes the build command,
start command, health path, `TRUST_PROXY=true`, `DATA_DIR=/var/data`, a
generated `ADMIN_TOKEN` and the persistent disk (§10.2).

### 10.2 Storage strategy — read before deploying

The server's entire state is **five files in `DATA_DIR`**:

| File | Holds |
|---|---|
| `links.json` | unlock links incl. **destination URLs** + per-link stats |
| `reviews.json` | manual review requests |
| `settings.json` | site/footer settings |
| `sessions.json` | hashed verified-session records |
| `events.jsonl` | append-only audit log |

**Storage risk:** on Render (and most PaaS) the instance filesystem is
**ephemeral** — every redeploy/restart of a disk-less instance starts
from a clean checkout, which would silently erase all links, statistics,
settings and audit history, and invalidate every session. **Never run
real data without persistent storage.**

**Chosen strategy (compatible with the current file-based store, no code
rewrite):** a Render **persistent disk** mounted at `/var/data` with
`DATA_DIR=/var/data` (declared in `render.yaml`; a disk requires a paid
instance). Local development keeps using `server/data/` exactly as
before.

**Migration procedure — before the first deploy:**

```powershell
# 1. Back up (copies into server/backups/<ts>_<note> and re-verifies
#    every copy; prints record counts only, never file contents)
python server\migrate_data.py backup --note pre_deploy

# 2. Verify the live data parses
python server\migrate_data.py verify

# 3. Pack a seed folder for first boot on the empty disk
#    (deploy-seed/ is git-ignored — it contains destination URLs)
python server\migrate_data.py pack --out deploy-seed
```

Then on Render: create the disk (the blueprint already declares it),
upload the five seed files as **Secret Files** (or any private mount)
and set `SEED_DIR` to their directory. On first boot the server copies
those files into the **empty** `DATA_DIR`; every later boot leaves
existing data untouched — a seed can never overwrite live state, and a
file that is not valid JSON is skipped with a warning instead of
corrupting the store.

**Rollback if anything looks wrong:**

```powershell
python server\migrate_data.py restore server\backups\<backup-dir> --yes
```

`restore` refuses to run without `--yes`, verifies the backup first, and
snapshots the current data (`…_pre_restore`) before overwriting anything.

Keep off-machine copies of `server/backups/` as well: those files contain
destination URLs, so store them privately (they are git-ignored).

### 10.3 Secrets — hosting environment only, never in the repo

The root `.gitignore` excludes `server/.env`, `server/data/`,
`server/backups/` and `deploy-seed/`; `render.yaml` contains **no**
secret values (`ADMIN_TOKEN` uses Render's `generateValue`). No git
repository has been created or pushed as part of this preparation.

| Variable | Where to set it | Notes |
|---|---|---|
| `ADMIN_TOKEN` | dashboard (blueprint generates one) | Or generate your own: `python -c "import secrets;print(secrets.token_urlsafe(32))"` |
| `TRUST_PROXY` | `true` (blueprint) | Enables real per-visitor rate limiting + `X-Forwarded-Proto` cookie awareness behind the platform proxy |
| `DATA_DIR` | `/var/data` (blueprint) | Must match the disk mount path |
| `PUBLIC_URL` | dashboard, after the URL exists | see §10.4 |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | dashboard (secret) | see §10.4 |
| `YOUTUBE_CHANNEL_ID` | dashboard or admin → *Site & footer* | `UC…` |
| `DISCORD_WEBHOOK_URL` | dashboard (secret) | enables the PUBLISH button |

### 10.4 Configure after the real public URL is assigned

1. **`PUBLIC_URL=https://<your-service-url>`** (no trailing slash;
   replace with your custom domain if you attach one). This drives the
   OAuth redirect origin, generated unlock URLs, `Secure` cookies and
   HSTS.
2. **Google Cloud Console → APIs & Services → Credentials → your OAuth
   client → Authorized redirect URIs** — add exactly:

   ```
   https://<your-service-url>/api/oauth/callback
   ```

   Keep the local `http://127.0.0.1:8099/api/oauth/callback` entry for
   development. The redirect URI must match `PUBLIC_URL` **character for
   character** or Google rejects the callback.
3. **`GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET`** → dashboard secrets
   (server-side only; never in frontend code).
4. **`YOUTUBE_CHANNEL_ID`** (or admin → *Site & footer*) — the channel
   the subscription check runs against; per-link channel URLs must point
   at it.
5. **`DISCORD_WEBHOOK_URL`** → dashboard secret (channel webhook from
   Discord → Integrations → Webhooks).
6. **Redeploy / restart**, then confirm: `/health` answers 200, the
   admin overview shows *Automatic verification active* (when OAuth is
   configured) and *Discord publishing configured*, and a real test
   account completes one unlock end-to-end.

### 10.5 HTTPS, proxy handling, cookies and logging (already in the code)

* **HSTS** (`Strict-Transport-Security: max-age=31536000`) is sent on
  every response when `PUBLIC_URL` is https.
* **Cookies** (`splix_unlock` session, OAuth binding nonce) are
  `HttpOnly`, `SameSite=Lax` and `Secure` when the origin is https — or
  when `TRUST_PROXY=true` and the proxy reports `X-Forwarded-Proto:
  https`. Without `TRUST_PROXY` that header is ignored, so clients
  cannot influence cookie flags.
* **Client IP / rate limiting** with `TRUST_PROXY=true` uses the
  *rightmost* `X-Forwarded-For` entry — the hop the single trusted proxy
  observed — so IPs cannot be spoofed to dodge rate limits. Enable it
  only with exactly one proxy in front (Render's edge). Without it, all
  visitors share the proxy IP's rate-limit bucket.
* **Safe logging:** access-log query values for `state`, `code`,
  `token`, `client_secret`, `password`, `key`, etc. are masked
  (`GET /api/oauth/callback?state=***&code=*** …`) — proven by the test
  suite against a running instance. Destinations and secrets are never
  logged, and stdout is line-buffered so platform log streams receive
  each line immediately.
* The **health endpoint** is intentionally exempt from rate limiting
  (the platform polls it) and returns nothing but liveness timings.
* All existing security headers (CSP, nosniff, Referrer-Policy,
  Permissions-Policy) and server-side authorization checks are
  unchanged; the Subscribe + Like task policies still verify exactly
  what they did before — nothing was made "automatic" for deployment.

### 10.6 Honest storage risks & limitations

* **Free instances have no persistent disk** → data would be ephemeral.
  The blueprint uses a paid `starter` instance for that reason; if you
  run without a disk, seed on every deploy and accept that live stats,
  sessions and audit history reset.
* **A deleted disk is gone** — Render disk backups are platform-level,
  not ours. Keep `server/backups/` copies off-machine.
* **`events.jsonl` grows unbounded** (append-only audit log; only the
  last 500 events are loaded into memory). Rotate it manually if the
  site runs for years.
* **No managed-database migration** is provided on purpose: the current
  implementation is a file-based store, and moving to SQL would require
  rewriting `store.py`/`security.py` — out of scope for deployment
  preparation. The persistent-disk strategy preserves the current
  implementation exactly.
* Sessions survive restarts (`sessions.json`) but expire per
  `SESSION_TTL_MIN`; a redeploy with a fresh disk invalidates everyone.

### 10.7 Before you press Deploy

```powershell
python server\tests\run_tests.py     # must end with: 264/264 checks passed
python server\migrate_data.py verify # must list every file as ok
```

Both must pass **before** any deployment; this preparation stopped short
of creating accounts, repositories, disks or deployments — those require
your authorization in the hosting dashboard.
