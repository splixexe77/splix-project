"""Deployment / production helpers for the SPLIX unlock server.

Pure, side-effect-free functions (importing this module never reads
.env files, never touches the network and never mutates disk).  They
cover the four things a hosting platform needs from us:

  * host/port resolution  — platform ``PORT`` + public bind vs. a safe
    local loopback default,
  * public origin default — ``PUBLIC_URL`` (falls back to the platform's
    external URL, e.g. Render's ``RENDER_EXTERNAL_URL``),
  * secret-safe logging   — query parameters such as OAuth ``code`` and
    ``state`` are masked before a request line is printed,
  * HTTPS-aware cookies   — ``Secure`` is decided from ``PUBLIC_URL``
    and (only with ``TRUST_PROXY``) the proxy's ``X-Forwarded-Proto``,
  * first-boot seeding    — ``SEED_DIR`` copies data files into an
    *empty* data folder once, never overwriting existing state.

Everything here is unit-tested by ``server/tests/run_tests.py``.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# Environment variables that hosting platforms set automatically.
# Their presence means "this process runs on the platform", which is
# when we must bind 0.0.0.0 so the platform's proxy can reach us.
PLATFORM_HINTS = ("RENDER", "DYNO", "K_SERVICE", "RAILWAY_ENV_NAME", "VERCEL", "HEROKU_APP_NAME")

# The complete persistent state of the server.  These files must survive
# every deployment/restart: links (+destinations, stats), reviews,
# settings, sessions and the append-only audit log.
DATA_FILES = ("links.json", "reviews.json", "settings.json", "sessions.json", "events.jsonl")

DEFAULT_LOCAL_PORT = 8099

# Query parameters whose values must never appear in a log line.
_SENSITIVE_PARAMS = (
    "state", "code", "access_token", "refresh_token", "id_token",
    "client_secret", "admin_token", "api_key", "password", "secret",
    "token", "key",
)
_REDACT_RE = re.compile(
    r"(?i)([?&\s\"'(]" + "|".join(_SENSITIVE_PARAMS) + r")=[^&\s\"')]+"
)


# ============================================================== host/port
def is_deployed(environ: dict | None = None) -> bool:
    """True when well-known hosting-platform environment hints are present."""
    env = os.environ if environ is None else environ
    return any(k in env for k in PLATFORM_HINTS)


def resolve_host(environ: dict | None = None, platform_port: bool | None = None) -> str:
    """Bind address for the HTTP server.

    Precedence:
      1. explicit ``HOST`` (real environment or server/.env) — always wins,
      2. ``0.0.0.0`` when we are clearly deployed: the platform itself
         exported ``PORT`` (``platform_port=True``, captured by run.py
         *before* .env is loaded) or a platform hint variable exists,
      3. ``127.0.0.1`` — the safe local-development default.
    """
    env = os.environ if environ is None else environ
    host = (env.get("HOST") or "").strip()
    if host:
        return host
    if platform_port is None:
        platform_port = bool((env.get("PORT") or "").strip())
    if platform_port or is_deployed(env):
        return "0.0.0.0"
    return "127.0.0.1"


def resolve_port(environ: dict | None = None, default: int = DEFAULT_LOCAL_PORT) -> int:
    """Port: the platform's ``PORT`` when present, else the local fallback."""
    env = os.environ if environ is None else environ
    raw = (env.get("PORT") or "").strip()
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return default
    return port if 0 < port < 65536 else default


def default_public_url(environ: dict | None = None) -> str:
    """Public origin used for OAuth redirects and generated unlock URLs.

    ``PUBLIC_URL`` always wins.  When it is not configured yet, hosting
    platforms that expose the assigned URL (Render: RENDER_EXTERNAL_URL)
    provide a correct fallback, so the server never *invents* an origin.
    The final fallback is the local default.
    """
    env = os.environ if environ is None else environ
    explicit = (env.get("PUBLIC_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    external = (env.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    if external:
        return external
    return f"http://127.0.0.1:{resolve_port(env)}"


# ================================================================ logging
def redact(text: str) -> str:
    """Mask secret-bearing query parameters in a log fragment.

    Request lines such as ``GET /api/oauth/callback?state=…&code=…``
    contain single-use OAuth secrets; they must never reach a log file.
    Paths, status codes and harmless parameters (``?v=1``) stay readable.
    """
    return _REDACT_RE.sub(lambda m: f"{m.group(1)}=***", str(text))


# ========================================================== proxy / client
def forwarded_client_ip(forwarded: str, fallback: str) -> str:
    """Real client IP from a single trusted proxy's X-Forwarded-For.

    The RIGHTMOST entry is the hop our proxy observed when it accepted
    the connection, so a client that sends its own X-Forwarded-For
    header cannot spoof its IP for rate limiting.  Empty/missing header
    -> the direct socket address.  Only valid with TRUST_PROXY=true and
    exactly one proxy in front (e.g. Render's HTTPS edge).
    """
    hops = [h.strip() for h in str(forwarded or "").split(",") if h.strip()]
    return hops[-1] if hops else str(fallback or "")


# ================================================================= cookies
def cookie_secure(public_url: str, forwarded_proto: str = "",
                  trust_proxy: bool = False) -> bool:
    """Whether session/OAuth cookies should carry the ``Secure`` flag.

    * ``https://`` PUBLIC_URL  -> always Secure (production TLS),
    * plain HTTP origin        -> Secure only when a *trusted* proxy
      tells us the original client connection was HTTPS
      (``X-Forwarded-Proto: https`` with ``TRUST_PROXY=true``).
    The header is ignored unless TRUST_PROXY is enabled, so a client can
    never flip cookie behaviour by sending a forged header.
    """
    if str(public_url or "").lower().startswith("https://"):
        return True
    if not trust_proxy:
        return False
    proto = str(forwarded_proto or "").split(",")[0].strip().lower()
    return proto == "https"


# ================================================================ seeding
def _valid_json(raw: bytes) -> bool:
    import json
    try:
        json.loads(raw.decode("utf-8"))
        return True
    except Exception:
        return False


def _valid_jsonl(raw: bytes) -> bool:
    import json
    try:
        text = raw.decode("utf-8")
    except Exception:
        return False
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return True  # empty audit log is fine
    for line in lines:
        try:
            json.loads(line)
        except Exception:
            return False
    return True


def seed_data_dir(seed_dir, data_dir) -> list[str]:
    """First-boot migration: copy seed files into an EMPTY data folder.

    * only the known data files (``links.json``, ``reviews.json``,
      ``settings.json``, ``sessions.json``, ``events.jsonl``) are taken,
    * a file that already exists in the data folder is **never**
      overwritten — existing state always wins,
    * a file that is not valid JSON is skipped (and reported) instead of
      corrupting the store,
    * returns the list of file names actually copied (names only — the
      contents, which include destinations, are never printed).
    """
    copied: list[str] = []
    src = Path(seed_dir) if seed_dir else None
    if not src or not src.is_dir():
        return copied
    dst = Path(data_dir)
    dst.mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
        s = src / name
        if not s.is_file():
            continue
        d = dst / name
        if d.exists():
            continue  # live data always wins over seed data
        try:
            raw = s.read_bytes()
        except OSError:
            continue
        ok = _valid_jsonl(raw) if name.endswith(".jsonl") else _valid_json(raw)
        if not ok:
            print(f"[seed] skipped {name}: seed file is not valid JSON")
            continue
        try:
            tmp = d.with_name(d.name + ".tmp")
            tmp.write_bytes(raw)
            os.replace(tmp, d)
        except OSError:
            continue
        copied.append(name)
    return copied
