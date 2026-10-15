# SPLIX X CHEAT — Website

A complete, responsive, premium esports-style community website built with
**plain HTML, CSS and vanilla JavaScript**. No Node.js, npm, React or build step
required.

```
SPLIX_X_CHEAT_WEBSITE/
├── index.html          Home · Features · Tutorials · Community · FAQ
├── plus.html           SPLIX PLUS membership page
├── unlock-admin.html   🔒 Admin panel for subscription-gated links
├── css/
│   ├── style.css       All styling + the theme tokens (:root)
│   └── unlock.css      🔒 Unlock page + admin panel styling
├── js/
│   ├── config.js       ★ EDIT THIS ONE — all text, links, stats, plans
│   ├── main.js         Shared behaviour (nav, FAQ, reveal, toast, counters)
│   ├── plus.js         Renders membership cards + comparison table
│   ├── particles.js    Ambient canvas background
│   ├── unlock.js       🔒 Unlock page controller
│   └── admin.js        🔒 Admin panel controller
└── server/             🔒 Small Python backend for gated links (optional)
    ├── run.py          python server/run.py  — start everything
    ├── README.md       ★ Setup, Google/YouTube API config, deployment guide
    ├── app.py          Router, static serving, unlock + admin APIs
    ├── store.py        Links / reviews / settings / stats / audit storage
    ├── security.py     Sessions, cookies, secrets, rate limiting
    ├── verify.py       Google OAuth + YouTube API verification
    ├── discord_publish.py  Optional webhook publishing
    ├── env.py          .env loader (no dependencies)
    ├── deploy.py       🔒 Production helpers (bind/port, HTTPS, seed migration)
    ├── migrate_data.py 🔒 Backup / verify / pack / restore the data folder
    ├── requirements.txt  Empty on purpose (stdlib only) — build target
    ├── .env.example    Configuration template — copy to server/.env
    ├── templates/unlock.html   Unlock page template
    ├── data/           Runtime JSON (links, reviews, sessions, events)
    ├── backups/        Timestamped data backups (git-ignored)
    └── tests/run_tests.py      python server/tests/run_tests.py
├── render.yaml         🔒 Render blueprint (build/start/health + disk)
├── .gitignore          Keeps .env / runtime data out of version control
└── README.md           This file
```

---

## ▶️ How to run it locally

### Easiest — just open the file
Double-click **`index.html`**. It opens straight in your browser.

### Recommended — a tiny local server
Some browsers restrict features on `file://`. A local server avoids that.

**Python (already installed on this PC):**
```
cd SPLIX_X_CHEAT_WEBSITE
python -m http.server 8099
```
Then open <http://127.0.0.1:8099/index.html>

> 🔒 **Subscription-gated links:** to use unlock pages and the admin
> panel, start `python server\run.py` instead — it serves this exact
> site **plus** the gated-link feature on the same port. Setup and the
> official YouTube verification guide live in **`server/README.md`**.

**Or just double-click `index.html`** — everything still works.

> Internet is needed only for the Google Fonts and Font Awesome icons.
> If you are offline the layout still works, but icons/fonts fall back to system ones.

---

## ✏️ How to customise (only edit `js/config.js`)

Everything — branding, links, stats, features, tutorials, FAQ and membership
plans — lives in **`js/config.js`**. You never need to touch the HTML or CSS.

### 1. Add your real Discord / YouTube links
```js
const links = {
  discord: 'https://discord.com/invite/YourInviteCode',
  youtube: 'https://www.youtube.com/@YourChannel',
};
```
Until you do, those buttons stay fully working and show a friendly
*"link is not configured yet"* notice instead of breaking.

### 2. Community statistics
```js
const stats = [
  { value: '1250', label: 'Community Members', icon: 'fa-solid fa-users' },
  ...
];
```
- Write a **number** (e.g. `'1250'`) → it counts up automatically.
- Write a **string** (e.g. `'TBA'`, `'24/7'`) → shown as-is.
- Defaults are honest placeholders (`TBA`). No numbers are invented.

### 3. Membership plans (`plus.html`)
Each plan supports a status:

| `status`    | Behaviour                                                        |
|-------------|------------------------------------------------------------------|
| `'current'` | Button disabled, shown as the visitor's current plan              |
| `'soon'`    | Button clearly says **COMING SOON** and explains on click         |
| `'live'`    | Reserved for a future payment provider (see below)                |

Prices, features, button labels and the "MOST POPULAR" badge are all editable
in the `plans` array.

### 4. Future payments (currently disabled)
Each plan has a `payment` block and there is a top-level `payments` object.
To enable payments later:
```js
payment: { enabled: true, provider: 'stripe', checkoutUrl: 'https://your-secure-endpoint' }
```
and set `status: 'live'`.

**Nothing here collects, stores or transmits payment information.**
There is no payment form anywhere on the site, no fake confirmation screens,
and no card fields. All checkout logic must live on a secure backend you control.

---

## 🎨 Theme / colours
Edit the `:root` block at the top of `css/style.css`:
```css
--blue:   #00a8ff;
--cyan:   #26e8ff;
--violet: #7c5cff;
```

---

## ✅ Tested
- Zero JavaScript console errors on both pages.
- Navigation, smooth scroll and mobile menu toggle work.
- FAQ accordions open, auto-close siblings, and close correctly (both pages).
- Stats, features, tutorials, plans and the comparison table render from config.
- "COMING SOON" buttons are honest — they explain themselves and never fake a payment.
- Scroll-reveal has a failsafe so content can never stay invisible.
- Fully responsive at 1080px / 900px / 760px / 420px breakpoints.
- Respects `prefers-reduced-motion`; particles pause when the tab is hidden.

---

## 📌 Notes
- This is an **independent** community site and is **not affiliated with,
  endorsed by, or connected to Garena Free Fire or Garena** — stated clearly in
  the footer of both pages.
- Tutorial thumbnails are generated with CSS gradients + icons, so there are
  **no external images that can ever break**.
- No testimonials or fabricated reviews are included.

---

## 🔒 Subscription-gated unlock links (new)

Publish **one link per resource**: users open `/u/<slug>`, complete the
required steps — **Subscribe to the YouTube channel and Like a YouTube
video** — and only the server releases the destination.

- **Design** — recreates the reference layout (title, pill, red task
  buttons, `Steps Completed 0/2` + progress bar, locked *Get Your Link*
  button, configurable footer) in the site's existing dark esports theme,
  with separate **Subscribe** and **Like** task cards, per-task state
  chips (NOT STARTED / PENDING / MANUAL REVIEW / VERIFIED / CONFIRMED
  (HONOR) / FAILED) and honest loading/success/failure messages;
  responsive at the same 900/760/420 breakpoints.
- **Admin** — `unlock-admin.html`: create / edit / disable / enable /
  delete links, copy the generated unlock URL, configure the step action,
  the **YouTube channel URL** and **video URL**, the **like verification
  policy** (moderator review or honor system) and the title/description,
  view per-link **statistics** (page visits,
  server-confirmed verifications, successful unlocks) and an **activity
  audit log**, manage footer/site info, approve manual reviews,
  optionally publish a link to Discord. No source-code edits needed.
- **Verification** — genuine server-side checks, nothing faked:
  * Subscribe (with credentials): official **Google OAuth + YouTube Data
    API v3** (`subscriptions.list?mine=true&forChannelId=…`) using the
    visitor's own token — a click or a URL parameter can never unlock
    anything;
  * Subscribe (without credentials): an explicit **manual review** flow
    where *you* approve each request;
  * Like: YouTube's API cannot show whether a specific viewer liked a
    video, so this task is **never auto-verified** — it uses the link's
    configured honest fallback: moderator review, or a clearly labelled
    **honor-system confirmation** (shown as CONFIRMED, never as
    "verified");
  * every requirement is enforced again on the server before the
    destination is released.
- **Delivery** — destinations stay in `server/data/links.json`, are
  released only by a validated backend endpoint, with session expiry,
  per-slug binding, rate limiting and a strict CSP. No secret is ever
  shipped to the browser.
- **Statistics & audit** — the server counts page **visits**,
  server-confirmed **verifications** and successful **unlocks** per link
  (unlocks are counted once per session), and keeps a JSONL **activity
  log** of link/verification/review/publish events. Destinations and
  tokens are never written to the log.
- **Discord** — optional channel **webhook** publishing (minimum
  permissions, no bot token, no Administrator right) via `DISCORD_WEBHOOK_URL`.
  The published embed never exposes the destination URL — only the unlock
  page link.
- **Tests** — `python server\tests\run_tests.py` → **264/264 checks pass**
  (original files byte-identical, CRUD + slug auto-generation, leakage,
  bypass attempts, OAuth success/failure matrix against a mock Google API,
  callback URLs bound to the initiating browser, per-link verification
  modes, review approve/reject/expire, stats + audit accounting, rate
  limits, and the full two-task matrix: Subscribe + Like task states in
  both automatic and manual mode, server-enforced 2/2 unlocking, honest
  honor-system confirmations, like-review policy gates and
  destination secrecy — plus deployment readiness: platform bind/port
  resolution, secret-safe access logs, `/health`, first-boot seed
  migration and the backup/restore tool).
- **Secrets** — a root `.gitignore` keeps `server/.env`, `server/data/`
  and `server/backups/` out of version control; only the empty
  `server/.env.example` template is committable.

Full configuration guide: **`server/README.md`**.

---

## 🚀 Deploying publicly (Render-ready, not deployed yet)

The project is prepared for HTTPS hosting — **no deployment has been
performed and no repository has been pushed**; the dashboard steps below
are yours to run.

**Ready-made blueprint:** `render.yaml`

| | |
|---|---|
| Build command | `pip install -r server/requirements.txt` (no packages — stdlib only) |
| Start command | `python server/run.py` |
| Health check | `GET /health` → `200 {"ok": true, …}` |
| Bind behaviour | platform `PORT` + `0.0.0.0` automatically; local fallback stays `127.0.0.1:8099` |
| Storage | persistent disk at `/var/data` (`DATA_DIR=/var/data`) — **required**, the instance filesystem is otherwise ephemeral |
| Secrets | `ADMIN_TOKEN` generated by Render; Google/Discord values added in the dashboard only — never in the repo |

**Storage migration (before deploying):**

```powershell
python server\migrate_data.py backup --note pre_deploy   # back up + verify
python server\migrate_data.py pack --out deploy-seed     # seed for first boot
```

Upload the seed files as a private platform secret, set `SEED_DIR` to
their path, and the first boot copies them into the empty disk (existing
data is never overwritten). Details, risks and rollback:
**`server/README.md` → §10 Production deployment**.

**After your real URL exists:** set `PUBLIC_URL=https://…`, add
`https://…/api/oauth/callback` as the Google OAuth redirect URI, then
add `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `YOUTUBE_CHANNEL_ID` and
`DISCORD_WEBHOOK_URL` in the hosting dashboard and redeploy.
