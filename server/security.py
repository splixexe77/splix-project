"""Security helpers — secrets, sessions, cookies, rate limiting.

Everything in this module runs **on the server only**.  No token, secret
or session value produced here is ever shipped to the browser except the
opaque session cookie, which cannot be forged because the authoritative
session records live in server memory/on disk keyed by their hash.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import parse_qsl

SESSION_COOKIE = "splix_unlock"


# ---------------------------------------------------------------- secrets
def new_token(nbytes: int = 32) -> str:
    """URL-safe random secret (default 256 bits)."""
    return secrets.token_urlsafe(nbytes)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest((a or "").encode("utf-8"), (b or "").encode("utf-8"))


# ---------------------------------------------------------------- cookies
def parse_cookie_header(header: str) -> dict[str, str]:
    if not header:
        return {}
    try:
        return dict(parse_qsl(header, keep_blank_values=True, separator=";"))
    except ValueError:
        return {}


def make_cookie(
    name: str,
    value: str,
    max_age: int,
    *,
    secure: bool = False,
    http_only: bool = True,
    same_site: str = "Lax",
    path: str = "/",
) -> str:
    parts = [f"{name}={value}", f"Path={path}", f"Max-Age={int(max_age)}", f"SameSite={same_site}"]
    if http_only:
        parts.append("HttpOnly")
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


# --------------------------------------------------------------- sessions
class SessionStore:
    """Verified-unlock sessions.

    Keyed by SHA-256 of the opaque cookie value, so even a full dump of
    the store cannot be replayed as a cookie.  Records expire (TTL) and
    are bound to one unlock slug.
    """

    def __init__(self, path: Path, ttl_seconds: int):
        self.path = Path(path)
        self.ttl = int(ttl_seconds)
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load()

    # -- persistence -------------------------------------------------
    def _load(self) -> None:
        try:
            if self.path.is_file():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = raw
        except Exception:
            self._data = {}

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:
            pass  # never break a request because of disk trouble

    def _prune_locked(self, now: int) -> None:
        dead = [k for k, v in self._data.items() if int(v.get("exp", 0)) <= now]
        for k in dead:
            self._data.pop(k, None)

    # -- API ---------------------------------------------------------
    def create(self, token: str, slug: str, method: str, channel: str = "",
               tasks: dict | None = None) -> dict:
        """Register a freshly verified session for `token` (plaintext cookie value)."""
        return self.create_with_hash(hash_token(token), slug, method, channel, tasks=tasks)

    def create_with_hash(self, key_hash: str, slug: str, method: str, channel: str = "",
                         tasks: dict | None = None) -> dict:
        """Register a session when only the hash of the cookie value is known.

        Used by the manual-review approval flow, where the user already
        holds the secret (their review token) server-side hash-matched.
        """
        now = int(time.time())
        record = {
            "slug": slug,
            "method": method,
            "channel": channel,
            "created": now,
            "exp": now + self.ttl,
        }
        if tasks is not None:
            # Per-task satisfaction map, e.g. {"subscribe": "oauth",
            # "like": "manual"} — truthy value = that requirement is met.
            record["tasks"] = dict(tasks)
        with self._lock:
            self._data[key_hash] = record
            self._prune_locked(now)
            self._save_locked()
        return dict(record)

    def set_tasks(self, token: str, tasks: dict) -> None:
        """Update the per-task satisfaction map of a live session."""
        if not token:
            return
        with self._lock:
            key = hash_token(token)
            record = self._data.get(key)
            if not record:
                return
            record["tasks"] = dict(tasks)
            self._save_locked()

    def get(self, token: str, slug: str) -> dict | None:
        """Return the live session record for this cookie value + slug."""
        if not token:
            return None
        now = int(time.time())
        with self._lock:
            record = self._data.get(hash_token(token))
            if not record:
                return None
            if int(record.get("exp", 0)) <= now:
                self._data.pop(hash_token(token), None)
                self._save_locked()
                return None
            if record.get("slug") != slug:
                return None
            return dict(record)

    def mark_claimed(self, token: str) -> None:
        """Flag a session so its first successful claim is counted once."""
        if not token:
            return
        key = hash_token(token)
        with self._lock:
            record = self._data.get(key)
            if record is not None:
                record["claimed"] = True
                self._save_locked()

    def count(self) -> int:
        now = int(time.time())
        with self._lock:
            self._prune_locked(now)
            return len(self._data)


# ------------------------------------------------------------ rate limits
class RateLimiter:
    """Sliding-window limiter keyed by arbitrary string (usually client IP)."""

    def __init__(self) -> None:
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: int) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds).  Consumes one slot when allowed."""
        now = time.monotonic()
        with self._lock:
            bucket = self._hits[key]
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= limit:
                retry = int(window - (now - bucket[0])) + 1
                return False, max(retry, 1)
            bucket.append(now)
            return True, 0
