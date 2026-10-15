"""HTTP application — serves the existing static site **and** the new
subscription-gated link (unlock) feature.

Design notes
------------
* The existing site files (index.html, plus.html, css/, js/, favicons)
  are served untouched from the project root — nothing was rewritten.
* Destination URLs only exist in server/data/links.json.  The public
  unlock page and its API never receive them until the server has
  confirmed the required subscription for a live session cookie.
* Verification state lives exclusively on the server (hashed session
  records), so query parameters, client-side JS edits or direct calls
  to the claim endpoint cannot unlock anything.
"""
from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
ROOT = SERVER_DIR.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import env as env_mod  # noqa: E402
import deploy as deploy_mod  # noqa: E402
import discord_publish  # noqa: E402
import security  # noqa: E402
import store as store_mod  # noqa: E402
import verify as verify_mod  # noqa: E402

TEMPLATE_PATH = SERVER_DIR / "templates" / "unlock.html"

CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
    "font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'self'; "
    "form-action 'self'"
)

EXTRA_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".png": "image/png",
    ".webp": "image/webp",
    ".md": "text/markdown; charset=utf-8",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}

SOCIAL_ICONS = {
    "youtube": "fa-brands fa-youtube",
    "discord": "fa-brands fa-discord",
    "tiktok": "fa-brands fa-tiktok",
    "x": "fa-brands fa-x-twitter",
    "facebook": "fa-brands fa-facebook-f",
    "instagram": "fa-brands fa-instagram",
    "linkedin": "fa-brands fa-linkedin-in",
}

SOCIAL_LABELS = {
    "youtube": "YouTube", "discord": "Discord", "tiktok": "TikTok", "x": "X",
    "facebook": "Facebook", "instagram": "Instagram", "linkedin": "LinkedIn",
}

ERR_MESSAGES = {
    "not_subscribed": (
        "YouTube says this Google account is <b>not subscribed</b> to the channel yet. "
        "Subscribe first, then press Verify again."
    ),
    "denied": "Google sign-in was cancelled. Nothing was unlocked — try again when ready.",
    "state_invalid": "That verification link expired or was already used. Please verify again.",
    "auth_expired": "Your Google sign-in expired. Please verify again.",
    "permission_denied": (
        "Google did not grant permission to read your YouTube subscriptions. "
        "Sign in again and approve the requested permission."
    ),
    "exchange_failed": "Google rejected the verification response. Please try again.",
    "api_unreachable": "The verification service is unreachable right now. Please try again shortly.",
    "api_error": "The YouTube API could not confirm your subscription right now. Please try again.",
    "not_configured": "Automatic verification is not configured on this server yet.",
}


# ============================================================== context
class Context:
    """Everything the request handlers need, built once at start-up."""

    def __init__(self) -> None:
        data_dir = Path(env_mod.get("DATA_DIR") or (SERVER_DIR / "data"))
        # Optional first-boot migration (e.g. Render persistent disk or a
        # secret-file seed): copies packed data files into an EMPTY data
        # folder only.  Existing files are never overwritten, so a restart
        # or redeploy can never clobber live links/sessions/audit history.
        seed_dir = env_mod.get("SEED_DIR")
        if seed_dir:
            seeded = deploy_mod.seed_data_dir(seed_dir, data_dir)
            if seeded:
                print(f"[seed] data folder initialized from SEED_DIR: {', '.join(seeded)}")
        self.data_dir = data_dir
        self.store = store_mod.Store(data_dir)

        session_ttl = env_mod.get_int("SESSION_TTL_MIN", 60) * 60
        self.sessions = security.SessionStore(data_dir / "sessions.json", session_ttl)
        self.limiter = security.RateLimiter()

        self.admin_token = env_mod.get("ADMIN_TOKEN")
        self.public_url = deploy_mod.default_public_url()
        self.secure_cookies = self.public_url.startswith("https")
        self.review_ttl = env_mod.get_int("REVIEW_TTL_MIN", 30) * 60
        self.state_ttl = env_mod.get_int("OAUTH_STATE_TTL_SEC", 600)
        self.discord_webhook = env_mod.get("DISCORD_WEBHOOK_URL")
        self.trust_proxy = env_mod.get_bool("TRUST_PROXY", False)

        self.rl = {
            "api": (env_mod.get_int("RL_API", 180), 60),
            "status": (env_mod.get_int("RL_STATUS", 120), 60),
            "oauth": (env_mod.get_int("RL_OAUTH", 8), 60),
            "review": (env_mod.get_int("RL_REVIEW", 5), 600),
            "like": (env_mod.get_int("RL_LIKE", 30), 60),
            "claim": (env_mod.get_int("RL_CLAIM", 30), 60),
            "admin": (env_mod.get_int("RL_ADMIN", 180), 60),
        }

        self._state_lock = threading.Lock()
        self._oauth_states: dict[str, dict] = {}
        self.started_at = time.time()

    # ---- verification config (reads live settings each time) --------
    def verify_config(self) -> verify_mod.VerifyConfig:
        settings = self.store.get_settings()
        return verify_mod.VerifyConfig.from_env(settings.get("youtubeChannelId", ""))

    def verification_mode(self) -> str:
        """Server-wide capability (shown in the admin panel)."""
        return "oauth" if self.verify_config().oauth_ready else "manual"

    def mode_for(self, link: dict) -> str:
        """Verification mode for ONE link.

        Automatic OAuth verification only applies when the required step
        IS a YouTube subscription and the Google credentials exist.  Any
        other action (custom step, missing credentials) uses the honest
        manual-review flow — a Google sign-in must never be used to
        "verify" an action it cannot actually confirm.
        """
        action_type = link.get("actionType") or "youtube_subscribe"
        if action_type == "youtube_subscribe" and self.verify_config().oauth_ready:
            return "oauth"
        return "manual"

    # ---- single-use OAuth state ------------------------------------
    def put_state(self, slug: str) -> tuple[str, str]:
        """Create a single-use OAuth state bound to the initiating browser.

        Returns (state, nonce).  The nonce is given to the browser as an
        HttpOnly cookie and only its hash is stored server-side, so a
        captured callback URL (state + code) cannot be replayed in a
        *different* browser to unlock a link for free.
        """
        state = security.new_token(18)
        nonce = security.new_token(24)
        now = time.time()
        with self._state_lock:
            dead = [k for k, v in self._oauth_states.items() if v["exp"] <= now]
            for k in dead:
                self._oauth_states.pop(k, None)
            self._oauth_states[state] = {
                "slug": slug,
                "keyHash": security.hash_token(nonce),
                "exp": now + self.state_ttl,
            }
        return state, nonce

    def pop_state(self, state: str) -> dict | None:
        with self._state_lock:
            record = self._oauth_states.pop(state, None)
        if not record:
            return None
        if record["exp"] <= time.time():
            return None
        return record

    def unlock_url(self, link: dict) -> str:
        return f"{self.public_url}/u/{link['slug']}"


CTX: Context | None = None


# ====================================================== page rendering
def _h(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _footer_contact(settings: dict) -> str:
    contact = []
    if settings.get("location"):
        contact.append(f'<p><i class="fa-solid fa-location-dot"></i> {_h(settings["location"])}</p>')
    if settings.get("email"):
        contact.append(
            f'<p><i class="fa-solid fa-envelope"></i> <b>Email:</b> '
            f'<a href="mailto:{_h(settings["email"])}">{_h(settings["email"])}</a></p>'
        )
    if settings.get("phone"):
        contact.append(f'<p><i class="fa-solid fa-phone"></i> {_h(settings["phone"])}</p>')
    return "".join(contact)


def _footer_socials(settings: dict) -> str:
    socials_html = []
    for key, url in (settings.get("socials") or {}).items():
        if not url:
            continue
        icon = SOCIAL_ICONS.get(key, "fa-solid fa-link")
        label = SOCIAL_LABELS.get(key, key)
        socials_html.append(
            f'<a href="{_h(url)}" class="social-btn" target="_blank" rel="noopener noreferrer" '
            f'aria-label="{_h(label)}" title="{_h(label)}"><i class="{icon}"></i></a>'
        )
    if not socials_html:
        socials_html.append(
            '<a href="/index.html#community" class="social-btn" aria-label="Community">'
            '<i class="fa-solid fa-users"></i></a>'
        )
    return "".join(socials_html)


# ------------------------------------------------- two-task policy helpers
def task_satisfied(record: dict | None, task_id: str) -> bool:
    """Server-side truth for one requirement of a visitor's session.

    Records written before the two-task model carry no ``tasks`` map: their
    single verified step counts as the subscribe (or only) task — exactly
    what that session was verified for.  Client input never reaches here.
    """
    if not record:
        return False
    tasks = record.get("tasks")
    if tasks is None:
        return task_id in ("subscribe", "custom")
    return bool(tasks.get(task_id))


def task_basis(record: dict | None, task_id: str) -> str:
    """How a requirement was met: 'oauth' | 'manual' | 'honor' | ''."""
    if not record:
        return ""
    tasks = record.get("tasks")
    if tasks is None:
        # Session created before the two-task model (and fresh OAuth
        # sessions): its single verified step was granted by OAuth
        # ('oauth') or by a moderator approval ('manual').
        if task_id == "subscribe":
            return "oauth" if record.get("method") == "oauth" else "manual"
        return ""
    value = tasks.get(task_id)
    if value is True:
        return "oauth" if task_id == "subscribe" else "auto"
    return str(value or "")


def link_state(record: dict | None, link: dict) -> str:
    """'none' | 'partial' | 'verified' — verified only when ALL tasks are met."""
    if not record:
        return "none"
    required = [t["id"] for t in store_mod.link_tasks(link)]
    if required and all(task_satisfied(record, tid) for tid in required):
        return "verified"
    return "partial"


def manual_review_tasks(link: dict, review: dict) -> dict:
    """Task map granted by an administrator's approval for this link."""
    out: dict = {}
    for task in store_mod.link_tasks(link):
        tid = task["id"]
        if tid == "subscribe":
            # Preserve how the subscribe step was already satisfied on this
            # server ('oauth' verified earlier, or 'manual' approval).
            out[tid] = review.get("subscribeBasis") or "manual"
        elif tid == "like":
            # A moderator review covers the like only under the 'manual'
            # policy.  Under the honor policy the visitor must record
            # their own confirmation afterwards (never granted silently).
            if (link.get("likeMethod") or "manual") == "manual":
                out[tid] = "manual"
        else:
            out[tid] = "manual"
    return out


def tasks_payload(link: dict, record: dict | None, mode: str) -> list[dict]:
    """Public per-task descriptors + honest states (never the destination)."""
    out: list[dict] = []
    for task in store_mod.link_tasks(link):
        ok = task_satisfied(record, task["id"])
        basis = task_basis(record, task["id"])
        if not ok:
            state = "unverified"
        elif basis == "manual":
            state = "manual_approved"
        elif basis == "honor":
            state = "confirmed"
        else:
            state = "verified"
        item = dict(task)
        item["state"] = state
        item["basis"] = basis
        item["auto"] = bool(task["id"] == "subscribe" and mode == "oauth")
        out.append(item)
    return out


def render_unlock_page(ctx: Context, link: dict) -> str:
    settings = ctx.store.get_settings()
    mode = ctx.mode_for(link)
    action_icon = (
        "fa-brands fa-youtube" if link.get("actionType") == "youtube_subscribe"
        else "fa-solid fa-bolt"
    )
    community_url = (settings.get("socials") or {}).get("discord") or "/index.html#community"

    tokens = {
        "TITLE": _h(f"{link['title']} — {settings.get('siteName', 'SPLIX X CHEAT')}"),
        "DESCRIPTION": _h(link.get("description") or "COMPLETE ALL THE STEPS BELOW"),
        "H1": _h(link.get("title") or "Complete Steps to Get Link"),
        "SLUG": _h(link["slug"]),
        "MODE": mode,
        "ACTION_LABEL": _h(link.get("actionLabel") or "Subscribe Channel"),
        "ACTION_URL": _h(link.get("channelUrl") or link.get("actionUrl") or "#"),
        "ACTION_ICON": action_icon,
        "STEPS_TOTAL": str(len(store_mod.link_tasks(link))),
        "COMMUNITY_URL": _h(community_url),
        "SITE_NAME": _h(settings.get("siteName", "SPLIX X CHEAT")),
        "SITE_DESCRIPTION": _h(settings.get("description", "")),
        "SITE_CONTACT": _footer_contact(settings),
        "SITE_SOCIALS": _footer_socials(settings),
        "SITE_DISCLAIMER": _h(settings.get("disclaimer", "")),
        "YEAR": time.strftime("%Y"),
    }

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    for key, value in tokens.items():
        template = template.replace("{{" + key + "}}", value)

    leftover = re.findall(r"\{\{([A-Z0-9_]+)\}\}", template)
    if leftover:
        raise RuntimeError(f"unreplaced template tokens: {sorted(set(leftover))}")
    return template


def render_simple_page(ctx: Context, title: str, message: str, status_ok: bool = True) -> str:
    accent = "var(--red-bright)" if status_ok else "var(--red)"
    settings = ctx.store.get_settings()
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_h(title)}</title>
<link rel="icon" href="/favicon.ico" sizes="any">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@600;800&family=Rajdhani:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css">
<link rel="stylesheet" href="/css/style.css"><link rel="stylesheet" href="/css/unlock.css">
</head><body class="unlock-body">
<div class="bg-layer"></div><div class="bg-grid"></div>
<main class="unlock-main"><div class="unlock-wrap">
<div class="unlock-card unlock-card--center">
<h1 class="unlock-title glow-text" style="font-size:1.6rem">{_h(title)}</h1>
<p class="unlock-notice unlock-notice--static" style="border-color:{accent}">{message}</p>
<a class="btn btn-primary" href="/index.html"><i class="fa-solid fa-house"></i> BACK TO HOME</a>
</div>
<p class="unlock-foot-note">{_h(settings.get('siteName', ''))}</p>
</div></main>
</body></html>"""


# ============================================================== handler
class Handler(BaseHTTPRequestHandler):
    server_version = "SplixUnlock/1.0"
    protocol_version = "HTTP/1.1"
    ctx: Context = None  # injected by run.py / tests

    # ----------------------------------------------------- plumbing
    def log_message(self, fmt: str, *args) -> None:
        try:
            stamp = time.strftime("%H:%M:%S")
            # Access-log line, secret-safe: OAuth state/code and any other
            # sensitive query values are masked before printing.  Paths,
            # statuses and client IPs stay readable for troubleshooting.
            message = deploy_mod.redact(fmt % args)
            print(f"[{stamp}] {self.address_string()} {message}")
        except Exception:
            pass

    def client_ip(self) -> str:
        if self.ctx and self.ctx.trust_proxy:
            forwarded = self.headers.get("X-Forwarded-For", "")
            if forwarded:
                # Single trusted proxy (e.g. Render's HTTPS edge): the
                # RIGHTMOST entry is the one our proxy observed, so a
                # client cannot spoof its IP by sending its own
                # X-Forwarded-For header.
                return deploy_mod.forwarded_client_ip(
                    forwarded, self.client_address[0])
        return self.client_address[0]

    def rate_limit(self, bucket: str, extra: str = "") -> bool:
        """Returns True when allowed; sends 429 + Retry-After when not."""
        limit, window = self.ctx.rl[bucket]
        key = f"{bucket}:{extra or self.client_ip()}"
        allowed, retry = self.ctx.limiter.check(key, limit, window)
        if not allowed:
            self.send_json(
                {"ok": False, "error": "rate_limited",
                 "message": f"Too many requests. Try again in {retry}s."},
                status=429,
                headers=[("Retry-After", str(retry))],
            )
        return allowed

    def send_bytes(self, body: bytes, ctype: str, status: int = 200,
                   headers: list[tuple[str, str]] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Content-Security-Policy", CSP)
        if self.ctx and self.ctx.secure_cookies:
            # HTTPS deployment (PUBLIC_URL starts with https://): tell
            # browsers to only ever use TLS for this origin.
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for name, value in (headers or []):
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_html(self, text: str, status: int = 200,
                  headers: list[tuple[str, str]] | None = None) -> None:
        base = [("Cache-Control", "no-store")]
        base.extend(headers or [])
        self.send_bytes(text.encode("utf-8"), "text/html; charset=utf-8", status, base)

    def send_json(self, obj, status: int = 200,
                  headers: list[tuple[str, str]] | None = None) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        base = [("Cache-Control", "no-store")]
        base.extend(headers or [])
        self.send_bytes(body, "application/json; charset=utf-8", status, base)

    def redirect(self, location: str, status: int = 302,
                 set_cookies: list[str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Content-Security-Policy", CSP)
        if self.ctx and self.ctx.secure_cookies:
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        for cookie in (set_cookies or []):
            self.send_header("Set-Cookie", cookie)
        self.end_headers()

    def read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 512 * 1024:
            raise store_mod.StoreError("request body too large")
        self._body_consumed = True
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise store_mod.StoreError("request body must be valid JSON") from exc
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise store_mod.StoreError("request body must be a JSON object")
        return data

    def cookies(self) -> dict[str, str]:
        return security.parse_cookie_header(self.headers.get("Cookie", ""))

    def origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = (self.headers.get("Host") or "").strip()
        return urllib.parse.urlsplit(origin).netloc == host

    def session_token(self) -> str:
        return self.cookies().get(security.SESSION_COOKIE, "")

    def cookie_secure(self) -> bool:
        """HTTPS-aware ``Secure`` flag for cookies issued on this request.

        True when PUBLIC_URL is https (normal production), or when a
        trusted proxy reports ``X-Forwarded-Proto: https`` while
        ``TRUST_PROXY=true`` (TLS terminated at the platform edge).
        The header is ignored without TRUST_PROXY, so clients cannot
        influence cookie flags.
        """
        return deploy_mod.cookie_secure(
            self.ctx.public_url if self.ctx else "",
            self.headers.get("X-Forwarded-Proto", ""),
            bool(self.ctx and self.ctx.trust_proxy),
        )

    def session_cookie(self, token: str) -> str:
        return security.make_cookie(
            security.SESSION_COOKIE,
            token,
            max_age=self.ctx.sessions.ttl,
            secure=self.cookie_secure(),
            http_only=True,
            same_site="Lax",
        )

    # -------------------------------------------------- admin auth
    def require_admin(self) -> bool:
        if not self.ctx.admin_token:
            self.send_json(
                {"ok": False, "error": "admin_disabled",
                 "message": "Admin API is disabled. Set ADMIN_TOKEN in server/.env and restart the server."},
                status=503,
            )
            return False
        header = self.headers.get("Authorization", "")
        provided = header[7:] if header.startswith("Bearer ") else ""
        if not provided or not security.constant_time_equal(provided, self.ctx.admin_token):
            self.send_json(
                {"ok": False, "error": "unauthorized", "message": "Invalid or missing admin token."},
                status=401,
            )
            return False
        if not self.rate_limit("admin"):
            return False
        return True

    # -------------------------------------------------- dispatch
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("HEAD")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        try:
            self._route(method)
        except store_mod.StoreError as exc:
            self.send_json({"ok": False, "error": "bad_request", "message": str(exc)}, status=400)
        except verify_mod.VerifyError as exc:
            self.send_json(
                {"ok": False, "error": exc.code, "message": exc.public_message}, status=502
            )
        except BrokenPipeError:
            pass
        except Exception:
            traceback.print_exc()
            try:
                self.send_json(
                    {"ok": False, "error": "server_error", "message": "Internal server error."},
                    status=500,
                )
            except Exception:
                pass
        # The connection stays alive under HTTP/1.1, so any request body
        # the handler never read (early 401/403/404/429 returns happen
        # before read_json) MUST be discarded here.  Otherwise those bytes
        # are parsed as the start of the next request on this socket and
        # clients see garbage like "Unsupported method ('{}GET') -> 501".
        self._drain_body()

    def handle_one_request(self) -> None:
        # ONE handler instance serves EVERY request on a keep-alive socket,
        # so per-request state must be cleared before each request line is
        # parsed — otherwise a body consumed by request N would make the
        # drain skip for request N+1 and corrupt request N+2.
        self._body_consumed = False
        try:
            super().handle_one_request()
        finally:
            # Also covers requests that never reach _dispatch (e.g. an
            # unknown method answered with 501 by the base class).
            self._drain_body()

    def _drain_body(self) -> None:
        if getattr(self, "_body_consumed", False):
            return
        hdrs = getattr(self, "headers", None)
        if hdrs is None:
            return  # request line never parsed — nothing to drain
        try:
            length = int(hdrs.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            self._body_consumed = True  # nothing left to consume this time
            return
        # Mark consumed FIRST: _dispatch and handle_one_request both call
        # this — it must never read the stream twice.
        self._body_consumed = True
        if length > 1024 * 1024:
            # Never buffer a huge body we were never asked to parse —
            # close instead; the client will simply reconnect.
            self.close_connection = True
            return
        try:
            remaining = length
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except Exception:
            self.close_connection = True

    # -------------------------------------------------- routing
    def _route(self, method: str) -> None:
        ctx = self.ctx
        split = urllib.parse.urlsplit(self.path)
        path = split.path
        query = dict(urllib.parse.parse_qsl(split.query, keep_blank_values=True))

        if method in ("GET", "HEAD"):
            # ---- platform health probe (liveness) ------------------
            # Deliberately handled first: never rate-limited (the host
            # polls it) and the payload contains no configuration,
            # tokens, counters or destinations.
            if path == "/health":
                return self.health()

            # ---- public unlock API --------------------------------
            m = re.fullmatch(r"/api/u/([a-z0-9-]{1,64})/status", path)
            if m:
                return self.api_status(method, m.group(1))
            m = re.fullmatch(r"/api/u/([a-z0-9-]{1,64})", path)
            if m:
                return self.api_unlock_config(method, m.group(1))
            if path == "/api/oauth/callback":
                return self.oauth_callback(query)

            # ---- admin API ----------------------------------------
            if path.startswith("/api/admin/"):
                return self.admin_route(method, path)

            # ---- unlock pages -------------------------------------
            m = re.fullmatch(r"/u/([a-z0-9-]{1,64})/?", path)
            if m:
                return self.unlock_page(m.group(1))

            # ---- oauth start (GET redirect) ----------------------
            m = re.fullmatch(r"/api/u/([a-z0-9-]{1,64})/oauth/start", path)
            if m:
                return self.oauth_start(m.group(1))

            if path.startswith("/api/"):
                return self.send_json(
                    {"ok": False, "error": "not_found", "message": "Unknown API endpoint."},
                    status=404,
                )

            if method == "GET":
                return self.serve_static(path)
            return self.serve_static(path)

        # ---- mutating routes --------------------------------------
        m = re.fullmatch(r"/api/u/([a-z0-9-]{1,64})/review/status", path)
        if m and method == "POST":
            return self.review_status(m.group(1))
        m = re.fullmatch(r"/api/u/([a-z0-9-]{1,64})/review", path)
        if m and method == "POST":
            return self.review_request(m.group(1))
        m = re.fullmatch(r"/api/u/([a-z0-9-]{1,64})/claim", path)
        if m and method == "POST":
            return self.claim(m.group(1))
        m = re.fullmatch(r"/api/u/([a-z0-9-]{1,64})/like", path)
        if m and method == "POST":
            return self.api_like_confirm(m.group(1))
        m = re.fullmatch(r"/api/u/([a-z0-9-]{1,64})/oauth/start", path)
        if m and method == "GET":
            return self.oauth_start(m.group(1))

        if path.startswith("/api/admin/"):
            return self.admin_route(method, path)

        if path.startswith("/api/"):
            return self.send_json(
                {"ok": False, "error": "not_found", "message": "Unknown API endpoint."},
                status=404,
            )
        if method in ("POST", "PUT", "DELETE"):
            return self.send_json(
                {"ok": False, "error": "method_not_allowed", "message": "Method not allowed."},
                status=405,
            )
        return self.serve_static(path)

    # -------------------------------------------------- health probe
    def health(self) -> None:
        """Liveness endpoint for hosting health checks (GET /health).

        Answers 200 as long as the process and its request loop are up.
        Intentionally exposes nothing but liveness timings — no link
        data, no counts of anything, no environment values.
        """
        self.send_json(
            {
                "ok": True,
                "status": "healthy",
                "uptimeSec": int(time.time() - self.ctx.started_at),
                "serverTime": int(time.time()),
            }
        )

    # -------------------------------------------------- static files
    def _safe_path(self, rel: str) -> Path | None:
        rel = urllib.parse.unquote(rel).replace("\\", "/")
        if "\x00" in rel:
            return None
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):
            return None
        candidate = ROOT.joinpath(*parts) if parts else ROOT
        try:
            resolved = candidate.resolve()
        except Exception:
            return None
        if not resolved.is_relative_to(ROOT):
            return None
        relative = resolved.relative_to(ROOT).as_posix()
        if relative == ".":
            relative = ""
        # never expose the backend itself or its data
        if relative == "server" or relative.startswith("server/"):
            return None
        if resolved.name.startswith("."):
            return None
        if resolved.suffix.lower() in (".py", ".pyc", ".env", ".bak", ".md", ".log"):
            return None
        if resolved.is_dir():
            index = resolved / "index.html"
            if index.is_file():
                return index
            return None
        return resolved if resolved.is_file() else None

    def serve_static(self, path: str) -> None:
        if path in ("", "/"):
            path = "/index.html"
        target = self._safe_path(path)
        if not target:
            return self.send_json(
                {"ok": False, "error": "not_found", "message": "Not found"}, status=404
            )
        ctype = EXTRA_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(str(target))[0]
        if not ctype:
            ctype = "application/octet-stream"
        try:
            body = target.read_bytes()
        except OSError:
            return self.send_json(
                {"ok": False, "error": "not_found", "message": "Not found"}, status=404
            )
        self.send_bytes(body, ctype, 200, [("Cache-Control", "no-cache")])

    # -------------------------------------------------- unlock pages
    def unlock_page(self, slug: str) -> None:
        link = self.ctx.store.get_by_slug(slug)
        if not link or not link.get("enabled", True):
            page = render_simple_page(
                self.ctx,
                "Link unavailable",
                "This unlock page does not exist or has been disabled by an administrator.",
                status_ok=False,
            )
            return self.send_html(page, status=404)
        if self.command == "GET":
            # Count real page loads only (not HEAD preflights).
            self.ctx.store.bump_stat(slug, "visits")
        try:
            page = render_unlock_page(self.ctx, link)
        except Exception:
            traceback.print_exc()
            return self.send_json(
                {"ok": False, "error": "server_error", "message": "Could not render page."},
                status=500,
            )
        self.send_html(page)

    def _session_record(self, slug: str) -> dict | None:
        token = self.session_token()
        record = token and self.ctx.sessions.get(token, slug)
        return record or None

    def _session_info(self, slug: str, link: dict | None = None) -> tuple[str, str]:
        record = self._session_record(slug)
        if record:
            if link is None:
                link = self.ctx.store.get_by_slug(slug)
            state = link_state(record, link) if link else "verified"
            return state, str(record.get("method", "") or "")
        return "none", ""

    def _state_for(self, slug: str) -> str:
        return self._session_info(slug)[0]

    def api_unlock_config(self, method: str, slug: str) -> None:
        if not self.rate_limit("api"):
            return
        link = self.ctx.store.get_by_slug(slug)
        if not link:
            return self.send_json(
                {"ok": False, "error": "not_found", "message": "Unknown unlock page."},
                status=404,
            )
        if not link.get("enabled", True):
            return self.send_json(
                {"ok": False, "error": "disabled", "message": "This link has been disabled."},
                status=410,
            )
        state = self._state_for(slug)
        state_method = self._session_info(slug)[1]
        mode = self.ctx.mode_for(link)
        cfg = self.ctx.verify_config()
        record = self._session_record(slug)
        tasks = tasks_payload(link, record, mode)
        self.send_json(
            {
                "ok": True,
                "slug": slug,
                "title": link.get("title"),
                "description": link.get("description"),
                "action": {
                    "label": link.get("actionLabel"),
                    "url": link.get("actionUrl"),
                    "type": link.get("actionType"),
                },
                "mode": mode,
                "verification": {
                    # "ready" means: THIS link is verified automatically.
                    "ready": mode == "oauth",
                    # Only meaningful for YouTube-subscription steps; a
                    # custom step can never be auto-verified, so no
                    # credentials message is shown for it.
                    "missing": cfg.missing if link.get("actionType") == "youtube_subscribe" else [],
                },
                "state": state,
                "stateMethod": state_method,
                "step": {
                    "total": len(tasks),
                    "done": sum(1 for t in tasks if t["state"] != "unverified"),
                },
                "tasks": tasks,
                "likeMethod": (link.get("likeMethod") or "manual") if link.get("videoUrl") else None,
                "serverTime": int(time.time()),
            }
        )

    def api_status(self, method: str, slug: str) -> None:
        if not self.rate_limit("status"):
            return
        link = self.ctx.store.get_by_slug(slug)
        if not link:
            return self.send_json(
                {"ok": False, "error": "not_found", "message": "Unknown unlock page."},
                status=404,
            )
        state = self._state_for(slug)
        state_method = self._session_info(slug)[1]
        record = self._session_record(slug)
        total = len(store_mod.link_tasks(link))
        done = 0
        if record:
            done = sum(1 for t in store_mod.link_tasks(link)
                       if task_satisfied(record, t["id"]))
        self.send_json(
            {
                "ok": True,
                "slug": slug,
                "state": state,
                "stateMethod": state_method,
                "enabled": bool(link.get("enabled", True)),
                "step": {"total": total, "done": done},
            }
        )

    # -------------------------------------------------- manual review
    def review_request(self, slug: str) -> None:
        if not self.origin_ok():
            return self.send_json({"ok": False, "error": "forbidden", "message": "Bad origin."}, status=403)
        if not self.rate_limit("review"):
            return
        link = self.ctx.store.get_by_slug(slug)
        if not link or not link.get("enabled", True):
            return self.send_json(
                {"ok": False, "error": "not_found", "message": "Unknown unlock page."},
                status=404,
            )

        record = self._session_record(slug)
        subscribe_basis = (
            task_basis(record, "subscribe") if task_satisfied(record, "subscribe") else ""
        )

        if self.ctx.mode_for(link) == "oauth":
            # Automatic verification is active for the subscribe step.
            # A moderator review is only accepted when it exists to cover
            # a manual LIKE step — and even then the visitor must already
            # hold the server-verified subscribe session first, so the
            # review can never substitute for automatic verification.
            needs_like_review = (
                link.get("actionType") == "youtube_subscribe"
                and bool(link.get("videoUrl"))
                and (link.get("likeMethod") or "manual") == "manual"
            )
            if not needs_like_review:
                return self.send_json(
                    {"ok": False, "error": "automatic_verification_required",
                     "message": "Automatic verification is active — use the Verify button instead."},
                    status=403,
                )
            if not subscribe_basis:
                return self.send_json(
                    {"ok": False, "error": "subscribe_verification_required",
                     "message": "Verify your subscription with Google first — the moderator "
                                "review only covers the video-like step."},
                    status=403,
                )

        body = self.read_json()
        provided = str(body.get("token") or "")
        if provided:
            existing = self.ctx.store.find_review(security.hash_token(provided), slug)
            if existing and existing["state"] in ("pending", "approved") and existing["exp"] > time.time():
                return self.send_json(
                    {"ok": True, "token": provided, "state": existing["state"],
                     "expiresAt": existing["exp"]}
                )

        token = security.new_token(24)
        review = self.ctx.store.create_review(
            slug, security.hash_token(token), self.ctx.review_ttl,
            subscribe_basis=subscribe_basis,
        )
        self.send_json(
            {"ok": True, "token": token, "state": "pending", "expiresAt": review["exp"]}
        )

    def review_status(self, slug: str) -> None:
        if not self.rate_limit("status"):
            return
        body = self.read_json()
        token = str(body.get("token") or "")
        if not token:
            return self.send_json(
                {"ok": False, "error": "bad_request", "message": "Missing review token."},
                status=400,
            )
        self.ctx.store.expire_due_reviews()
        review = self.ctx.store.find_review(security.hash_token(token), slug)
        if not review:
            return self.send_json(
                {"ok": False, "error": "unknown", "state": "unknown",
                 "message": "No review request found for this link."}
            )

        state = review["state"]
        if state == "pending" and review["exp"] <= time.time():
            state = "expired"

        cookies: list[str] = []
        verified = False
        tasks_out: list[dict] = []
        if state == "approved":
            # Hand the user their own (already known) secret back as the
            # verified session cookie — the session record was created
            # by the administrator's approval action.
            link = self.ctx.store.get_by_slug(slug)
            tasks_map = manual_review_tasks(link, review) if link else None
            if not self.ctx.sessions.get(token, slug):
                self.ctx.sessions.create(token, slug, "manual", tasks=tasks_map)
            cookies.append(self.session_cookie(token))
            record = self.ctx.sessions.get(token, slug)
            if link:
                tasks_out = tasks_payload(link, record, self.ctx.mode_for(link))
                verified = link_state(record, link) == "verified"
            else:
                verified = True

        payload: dict = {"ok": True, "state": state, "verified": verified}
        if tasks_out:
            payload["tasks"] = tasks_out
        self.send_json(
            payload,
            headers=[("Set-Cookie", c) for c in cookies],
        )

    # -------------------------------------------------- claim
    def claim(self, slug: str) -> None:
        if not self.origin_ok():
            return self.send_json({"ok": False, "error": "forbidden", "message": "Bad origin."}, status=403)
        if not self.rate_limit("claim"):
            return
        link = self.ctx.store.get_by_slug(slug)
        if not link:
            return self.send_json(
                {"ok": False, "error": "not_found", "message": "Unknown unlock page."},
                status=404,
            )
        if not link.get("enabled", True):
            return self.send_json(
                {"ok": False, "error": "disabled", "message": "This link has been disabled."},
                status=410,
            )
        token = self.session_token()
        record = token and self.ctx.sessions.get(token, slug)
        if not record:
            return self.send_json(
                {"ok": False, "error": "not_verified",
                 "message": "Verification has not been completed. Complete the required step first."},
                status=403,
            )
        # EVERY configured requirement must be satisfied server-side before
        # the destination leaves the server (never trust the page/JS).
        required = [t["id"] for t in store_mod.link_tasks(link)]
        missing = [tid for tid in required if not task_satisfied(record, tid)]
        if missing:
            return self.send_json(
                {"ok": False, "error": "tasks_incomplete", "missing": missing,
                 "message": "Complete every required step first — the link is still locked."},
                status=403,
            )
        if not record.get("claimed"):
            # Count a successful unlock once per verified session.
            self.ctx.sessions.mark_claimed(token)
            self.ctx.store.bump_stat(slug, "unlocks")
            self.ctx.store.record_event(
                "unlock.claimed", slug=slug, method=record.get("method", "")
            )
        self.send_json(
            {
                "ok": True,
                "url": link["destination"],
                "expiresAt": record["exp"],
                "method": record.get("method", "oauth"),
            }
        )

    # -------------------------------------------------- like (honor system)
    def api_like_confirm(self, slug: str) -> None:
        """Visitor self-confirms the video-like step (honest honor system).

        YouTube's API does not expose whether a specific viewer liked a
        video, so this can NEVER be a verified task — it is recorded as a
        self-confirmation ('honor') and is only available when the link's
        likeMethod says so.  The server still requires a live, already
        subscribe-verified session: a click alone proves nothing.
        """
        if not self.origin_ok():
            return self.send_json(
                {"ok": False, "error": "forbidden", "message": "Bad origin."}, status=403
            )
        if not self.rate_limit("like"):
            return
        link = self.ctx.store.get_by_slug(slug)
        if not link:
            return self.send_json(
                {"ok": False, "error": "not_found", "message": "Unknown unlock page."},
                status=404,
            )
        if not link.get("enabled", True):
            return self.send_json(
                {"ok": False, "error": "disabled", "message": "This link has been disabled."},
                status=410,
            )
        if not link.get("videoUrl"):
            return self.send_json(
                {"ok": False, "error": "not_found",
                 "message": "This unlock page has no video step."},
                status=404,
            )
        if (link.get("likeMethod") or "manual") != "honor":
            return self.send_json(
                {"ok": False, "error": "honor_unavailable",
                 "message": "This link's video step is confirmed by a moderator — "
                            "use the review button instead."},
                status=403,
            )

        token = self.session_token()
        record = token and self.ctx.sessions.get(token, slug)
        if not record or not task_satisfied(record, "subscribe"):
            return self.send_json(
                {"ok": False, "error": "subscribe_required",
                 "message": "Verify the subscribe step first — the video step can only "
                            "be confirmed after that."},
                status=403,
            )

        mode = self.ctx.mode_for(link)
        tasks = dict(record.get("tasks") or {"subscribe": True})
        if not tasks.get("like"):
            tasks["like"] = "honor"
            self.ctx.sessions.set_tasks(token, tasks)
            self.ctx.store.record_event(
                "task.confirmed", slug=slug, task="like", basis="honor"
            )
        fresh = self.ctx.sessions.get(token, slug)
        out = tasks_payload(link, fresh, mode)
        self.send_json(
            {"ok": True, "state": "confirmed",
             "tasks": out,
             "step": {
                 "total": len(out),
                 "done": sum(1 for t in out if t["state"] != "unverified"),
             }}
        )

    # -------------------------------------------------- OAuth flow
    OAUTH_COOKIE = "splix_oauth"

    def _clear_oauth_cookie(self) -> str:
        return security.make_cookie(
            self.OAUTH_COOKIE,
            "",
            max_age=0,
            secure=self.cookie_secure(),
            http_only=True,
            same_site="Lax",
        )

    def oauth_start(self, slug: str) -> None:
        if not self.rate_limit("oauth"):
            return
        link = self.ctx.store.get_by_slug(slug)
        if not link or not link.get("enabled", True):
            return self.send_json(
                {"ok": False, "error": "not_found", "message": "Unknown unlock page."},
                status=404,
            )
        if self.ctx.mode_for(link) != "oauth":
            # A custom step cannot be confirmed by the YouTube API —
            # never send the visitor through a Google sign-in that
            # could not verify their action anyway.
            return self.send_json(
                {"ok": False, "error": "manual_verification",
                 "message": "This link uses moderator verification, not Google sign-in."},
                status=403,
            )
        cfg = self.ctx.verify_config()
        if not cfg.oauth_ready:
            return self.send_json(
                {"ok": False, "error": "not_configured",
                 "message": "Automatic verification is not configured on this server.",
                 "missing": cfg.missing},
                status=503,
            )
        state, nonce = self.ctx.put_state(slug)
        location = verify_mod.build_authorization_url(cfg, state)
        # Bind this OAuth attempt to THIS browser: the callback below is
        # only accepted when the same browser returns the nonce cookie,
        # so a captured callback URL cannot be replayed elsewhere.
        cookie = security.make_cookie(
            self.OAUTH_COOKIE,
            nonce,
            max_age=self.ctx.state_ttl,
            secure=self.cookie_secure(),
            http_only=True,
            same_site="Lax",
        )
        self.redirect(location, set_cookies=[cookie])

    def oauth_callback(self, query: dict) -> None:
        if not self.rate_limit("api"):
            return
        state = str(query.get("state") or "")
        provider_error = str(query.get("error") or "")
        clear_cookie = self._clear_oauth_cookie()

        record = self.ctx.pop_state(state) if state else None
        slug = record["slug"] if record else None

        # Browser binding: the nonce cookie issued by oauth_start must
        # match the hash stored with this state.  The state is single-use
        # and is burned (already popped above) on any mismatch.
        nonce = self.cookies().get(self.OAUTH_COOKIE, "")
        bound = bool(
            record
            and nonce
            and security.constant_time_equal(security.hash_token(nonce), record.get("keyHash", ""))
        )

        if provider_error:
            code = "denied" if provider_error in ("access_denied", "consent_denied") else "denied"
            if slug and bound:
                return self.redirect(f"/u/{slug}?err={code}", set_cookies=[clear_cookie])
            return self.send_html(
                render_simple_page(self.ctx, "Verification cancelled",
                                   ERR_MESSAGES["denied"], status_ok=False),
                headers=[("Set-Cookie", clear_cookie)],
            )

        if not record or not slug or not bound:
            return self.send_html(
                render_simple_page(
                    self.ctx,
                    "Verification link expired",
                    ERR_MESSAGES["state_invalid"],
                    status_ok=False,
                ),
                status=400,
                headers=[("Set-Cookie", clear_cookie)],
            )

        code = str(query.get("code") or "")
        if not code:
            return self.redirect(f"/u/{slug}?err=exchange_failed", set_cookies=[clear_cookie])

        cfg = self.ctx.verify_config()
        if not cfg.oauth_ready:
            return self.redirect(f"/u/{slug}?err=not_configured", set_cookies=[clear_cookie])

        try:
            access_token = verify_mod.exchange_code(cfg, code)
            result = verify_mod.check_subscription(cfg, access_token)
        except verify_mod.VerifyError as exc:
            self.ctx.store.record_event("verify.failed", slug=slug, code=exc.code)
            return self.redirect(f"/u/{slug}?err={exc.code}", set_cookies=[clear_cookie])

        if not result.get("subscribed"):
            # Not subscribed → no session, nothing unlocks.
            self.ctx.store.record_event("verify.failed", slug=slug, code="not_subscribed")
            return self.redirect(f"/u/{slug}?err=not_subscribed", set_cookies=[clear_cookie])

        session_token = security.new_token(32)
        self.ctx.sessions.create(
            session_token, slug, "oauth", channel=result.get("userChannel", "")
        )
        self.ctx.store.bump_stat(slug, "verified")
        self.ctx.store.record_event("verify.ok", slug=slug, method="oauth")
        self.redirect(
            f"/u/{slug}?v=1",
            # Session cookie first so dict-based consumers (which keep
            # only the first Set-Cookie) see it; browsers apply both
            # headers regardless of order.
            set_cookies=[self.session_cookie(session_token), clear_cookie],
        )

    # -------------------------------------------------- admin API
    def admin_route(self, method: str, path: str) -> None:
        ctx = self.ctx

        # ---- read-only admin endpoints ---------------------------
        if method in ("GET", "HEAD"):
            if not self.require_admin():
                return
            if path == "/api/admin/overview":
                cfg = ctx.verify_config()
                links = ctx.store.list_links()
                totals = {"visits": 0, "verified": 0, "unlocks": 0}
                for lk in links:
                    stats = lk.get("stats") or {}
                    for k in totals:
                        totals[k] += int(stats.get(k, 0) or 0)
                return self.send_json({
                    "ok": True,
                    "publicUrl": ctx.public_url,
                    "verification": {
                        "mode": ctx.verification_mode(),
                        "ready": cfg.oauth_ready,
                        "missing": cfg.missing,
                        "channelId": cfg.channel_id,
                        "redirectUri": cfg.redirect_uri,
                    },
                    "discord": {"configured": bool(ctx.discord_webhook)},
                    "counts": {
                        "links": len(links),
                        "pending": len(ctx.store.list_reviews("pending")),
                        "sessions": ctx.sessions.count(),
                        "totals": totals,
                    },
                    "adminEnabled": bool(ctx.admin_token),
                    "serverTime": int(time.time()),
                })
            if path == "/api/admin/events":
                return self.send_json({"ok": True, "events": ctx.store.list_events(150)})
            if path == "/api/admin/links":
                links = ctx.store.list_links()
                return self.send_json({
                    "ok": True,
                    "links": [
                        {**link, "unlockUrl": ctx.unlock_url(link)} for link in links
                    ],
                })
            if path == "/api/admin/reviews":
                return self.send_json({"ok": True, "reviews": ctx.store.list_reviews()})
            if path == "/api/admin/settings":
                return self.send_json({"ok": True, "settings": ctx.store.get_settings()})
            return self.send_json(
                {"ok": False, "error": "not_found", "message": "Unknown admin endpoint."},
                status=404,
            )

        # ---- mutating admin endpoints ----------------------------
        if not self.origin_ok():
            return self.send_json(
                {"ok": False, "error": "forbidden", "message": "Bad origin."}, status=403
            )
        if not self.require_admin():
            return

        if method == "POST" and path == "/api/admin/links":
            link = ctx.store.create_link(self.read_json())
            ctx.store.record_event("link.created", slug=link.get("slug"), id=link.get("id"))
            return self.send_json(
                {"ok": True, "link": {**link, "unlockUrl": ctx.unlock_url(link)}}, status=201
            )

        m = re.fullmatch(r"/api/admin/links/([0-9a-f]+)", path)
        if m and method == "PUT":
            link = ctx.store.update_link(m.group(1), self.read_json())
            ctx.store.record_event("link.updated", slug=link.get("slug"), id=link.get("id"))
            return self.send_json(
                {"ok": True, "link": {**link, "unlockUrl": ctx.unlock_url(link)}}
            )
        if m and method == "DELETE":
            ctx.store.delete_link(m.group(1))
            ctx.store.record_event("link.deleted", id=m.group(1))
            return self.send_json({"ok": True})

        m = re.fullmatch(r"/api/admin/reviews/([0-9a-f]+)/(approve|reject)", path)
        if m and method == "POST":
            review_id, action = m.group(1), m.group(2)
            review = ctx.store.get_review(review_id)
            if not review:
                return self.send_json(
                    {"ok": False, "error": "not_found", "message": "Review not found."},
                    status=404,
                )
            new_state = "approved" if action == "approve" else "rejected"
            updated = ctx.store.set_review_state(review_id, new_state)
            if new_state == "approved":
                # Authoritative side effect: grant the matching session its
                # per-task map (subscribe credited as 'oauth' only when the
                # server itself captured that fact at request time).
                tasks_map = None
                link = ctx.store.get_by_slug(updated["slug"])
                if link:
                    tasks_map = manual_review_tasks(link, updated)
                ctx.sessions.create_with_hash(
                    updated["tokenHash"], updated["slug"], "manual", tasks=tasks_map
                )
                ctx.store.bump_stat(updated["slug"], "verified")
            ctx.store.record_event(
                f"review.{new_state}", slug=updated.get("slug"), id=review_id
            )
            return self.send_json({"ok": True, "review": updated})

        if method == "POST" and path == "/api/admin/settings":
            settings = ctx.store.update_settings(self.read_json().get("settings", {}))
            ctx.store.record_event("settings.updated")
            return self.send_json({"ok": True, "settings": settings})

        if method == "PUT" and path == "/api/admin/settings":
            settings = ctx.store.update_settings(self.read_json())
            ctx.store.record_event("settings.updated")
            return self.send_json({"ok": True, "settings": settings})

        if method == "POST" and path == "/api/admin/publish":
            body = self.read_json()
            link = ctx.store.get_link(str(body.get("id") or ""))
            if not link:
                return self.send_json(
                    {"ok": False, "error": "not_found", "message": "Link not found."},
                    status=404,
                )
            if not ctx.discord_webhook:
                return self.send_json(
                    {"ok": False, "error": "not_configured",
                     "message": "DISCORD_WEBHOOK_URL is not set in server/.env."},
                    status=503,
                )
            try:
                # Do NOT leak the destination host into a (potentially
                # public) Discord channel — only the unlock page URL goes out.
                discord_publish.publish_unlock_link(
                    ctx.discord_webhook,
                    title=link["title"],
                    description=link.get("description", ""),
                    unlock_url=ctx.unlock_url(link),
                    destination_label="a locked resource",
                )
            except RuntimeError as exc:
                ctx.store.record_event("publish.failed", slug=link.get("slug"), id=link.get("id"))
                return self.send_json(
                    {"ok": False, "error": "discord_failed", "message": str(exc)},
                    status=502,
                )
            ctx.store.record_event("link.published", slug=link.get("slug"), id=link.get("id"))
            return self.send_json({"ok": True})

        return self.send_json(
            {"ok": False, "error": "not_found", "message": "Unknown admin endpoint."},
            status=404,
        )


# ============================================================== builder
class SplixHTTPServer(ThreadingHTTPServer):
    """Threading server that stays quiet when a client merely hangs up."""

    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError)):
            return  # tab closed / connection dropped — completely normal
        super().handle_error(request, client_address)


def build_context() -> Context:
    return Context()


def make_server(host: str, port: int, ctx: Context | None = None) -> ThreadingHTTPServer:
    global CTX
    CTX = ctx or build_context()
    Handler.ctx = CTX
    server = SplixHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
