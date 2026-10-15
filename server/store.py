"""Persistent storage for unlock links, manual-review requests and settings.

Plain JSON files under server/data/ (atomic writes, thread-safe).  No
third-party database is required.  Destination URLs only ever live here,
on the server — they are never embedded in public pages.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,62}[a-z0-9])$")

RESERVED_SLUGS = {
    "api", "u", "admin", "static", "assets", "css", "js", "img", "images",
    "server", "data", "index", "plus", "unlock-admin", "favicon", "robots",
}

MAX_TITLE = 120
MAX_DESCRIPTION = 200
MAX_LABEL = 60
MAX_URL = 2048

# Per-link usage counters (persisted with each link record).
STAT_KEYS = ("visits", "verified", "unlocks")

# Rolling in-memory + append-only JSONL audit log bounds.
MAX_EVENTS = 500

DEFAULT_SETTINGS = {
    "siteName": "SPLIX X CHEAT",
    "tagline": "YOUR ULTIMATE GAMING COMMUNITY",
    "description": (
        "An independent Free Fire gaming community for tutorials, news "
        "and players who want to improve."
    ),
    "email": "",
    "location": "",
    "phone": "",
    "disclaimer": (
        "This website is an independent gaming community. It is not "
        "affiliated with, endorsed by, or connected to Garena Free Fire "
        "or Garena in any way."
    ),
    "youtubeChannelId": "",
    "socials": {
        "youtube": "https://youtube.com/@nxsplix1363?si=AYSnGGuAYatkUOz_",
        "discord": "https://discord.gg/fUbAwWTYC",
        "tiktok": "https://www.tiktok.com/@ghost_hex67?is_from_webapp=1&sender_device=pc",
        "x": "",
        "facebook": "",
        "instagram": "",
        "linkedin": "",
    },
}


class StoreError(ValueError):
    """Validation or not-found error raised to the API layer."""


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def validate_url(value: str, field: str = "url") -> str:
    value = (value or "").strip()
    if not value:
        raise StoreError(f"{field} is required")
    if len(value) > MAX_URL:
        raise StoreError(f"{field} is too long")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise StoreError(f"{field} must be a full http(s) URL, e.g. https://…")
    return value


# Hosts that count as YouTube for the per-link channel/video task URLs.
_YOUTUBE_HOSTS = (
    "youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com", "youtube-nocookie.com",
)


def validate_optional_youtube_url(value: str, field: str) -> str:
    """Optional YouTube-only URL ('' allowed). Empty means the task is not configured."""
    value = (value or "").strip()
    if not value:
        return ""
    url = validate_url(value, field)
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _YOUTUBE_HOSTS:
        raise StoreError(f"{field} must be a YouTube URL")
    return url


def link_tasks(link: dict) -> list[dict]:
    """Public task descriptors for an unlock link.

    Contains ONLY shareable information (never the destination).  The
    returned ids are what the server enforces before claiming:
      youtube_subscribe link -> ["subscribe"] (+ ["like"] when a video is set)
      custom link           -> ["custom"]
    """
    action_type = link.get("actionType") or "youtube_subscribe"
    if action_type == "custom":
        return [{
            "id": "custom",
            "title": link.get("actionLabel") or "Complete this step",
            "label": link.get("actionLabel") or "Complete this step",
            "url": link.get("actionUrl") or "",
            "method": "manual",
        }]
    tasks: list[dict] = [{
        "id": "subscribe",
        "title": "SUBSCRIBE TO MY YOUTUBE CHANNEL",
        "label": link.get("actionLabel") or "Subscribe Channel",
        "url": link.get("channelUrl") or link.get("actionUrl") or "",
        "method": "oauth",
    }]
    if link.get("videoUrl"):
        tasks.append({
            "id": "like",
            "title": "LIKE MY YOUTUBE VIDEO",
            "label": "Open the video on YouTube",
            "url": link.get("videoUrl") or "",
            "method": link.get("likeMethod") or "manual",
        })
    return tasks


def normalize_slug(value: str) -> str:
    slug = (value or "").strip().lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    return slug[:64]


def validate_slug(slug: str) -> str:
    if not SLUG_RE.match(slug):
        raise StoreError(
            "slug must be 3-64 characters: lowercase letters, numbers and hyphens"
        )
    if slug in RESERVED_SLUGS:
        raise StoreError(f"'{slug}' is a reserved word, pick another slug")
    return slug


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, fallback):
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, type(fallback)):
                return raw
    except Exception:
        pass
    return fallback


class Store:
    """Thread-safe JSON store for links + reviews + settings."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.links_path = self.data_dir / "links.json"
        self.reviews_path = self.data_dir / "reviews.json"
        self.settings_path = self.data_dir / "settings.json"
        self.events_path = self.data_dir / "events.jsonl"
        self._lock = threading.RLock()

        self.links: list[dict] = _read_json(self.links_path, [])
        if not isinstance(self.links, list):
            self.links = []
        self.reviews: list[dict] = _read_json(self.reviews_path, [])
        if not isinstance(self.reviews, list):
            self.reviews = []
        stored = _read_json(self.settings_path, {})
        self.settings: dict = json.loads(json.dumps(DEFAULT_SETTINGS))
        self._merge_settings(self.settings, stored)
        self._prune_reviews()
        self.events: list[dict] = self._load_events()
        self._migrate_stats()

    # ------------------------------------------------------------- events
    def _load_events(self) -> list[dict]:
        """Load the tail of the append-only JSONL audit log."""
        events: list[dict] = []
        try:
            if self.events_path.is_file():
                with self.events_path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        if isinstance(rec, dict) and "event" in rec:
                            events.append(rec)
        except OSError:
            pass
        return events[-MAX_EVENTS:]

    def _migrate_stats(self) -> None:
        """Ensure every stored link has a zero-filled stats block."""
        changed = False
        for link in self.links:
            if not isinstance(link, dict):
                continue
            stats = link.get("stats")
            if not isinstance(stats, dict):
                stats = {}
            migrated = {k: int(stats.get(k, 0) or 0) for k in STAT_KEYS}
            if migrated != link.get("stats"):
                link["stats"] = migrated
                changed = True
        if changed:
            try:
                _atomic_write(self.links_path, self.links)
            except OSError:
                pass

    # ------------------------------------------------------------ settings
    def _merge_settings(self, base: dict, stored: dict) -> None:
        if not isinstance(stored, dict):
            return
        for key, value in stored.items():
            if key == "socials" and isinstance(value, dict):
                for s_key, s_val in value.items():
                    if s_key in base["socials"] and isinstance(s_val, str):
                        base["socials"][s_key] = s_val[:MAX_URL]
            elif key in base and isinstance(base[key], str) and isinstance(value, str):
                base[key] = value[:MAX_DESCRIPTION * 3]

    def get_settings(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self.settings))

    def update_settings(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise StoreError("settings must be an object")
        with self._lock:
            for key, value in patch.items():
                if key == "socials" and isinstance(value, dict):
                    for s_key, s_val in value.items():
                        if s_key in self.settings["socials"] and isinstance(s_val, str):
                            parsed = urlparse(s_val.strip())
                            if s_val.strip() and (parsed.scheme not in ("http", "https") or not parsed.netloc):
                                raise StoreError(f"social '{s_key}' must be a full http(s) URL")
                            self.settings["socials"][s_key] = s_val.strip()[:MAX_URL]
                elif key in self.settings and isinstance(self.settings[key], str):
                    if not isinstance(value, str):
                        raise StoreError(f"'{key}' must be text")
                    self.settings[key] = value.strip()[: MAX_DESCRIPTION * 3]
            _atomic_write(self.settings_path, self.settings)
            return json.loads(json.dumps(self.settings))

    # --------------------------------------------------------------- links
    def list_links(self) -> list[dict]:
        with self._lock:
            return [dict(link) for link in self.links]

    def get_link(self, link_id: str) -> dict:
        with self._lock:
            for link in self.links:
                if link["id"] == link_id:
                    return dict(link)
        raise StoreError("link not found")

    def get_by_slug(self, slug: str) -> dict | None:
        with self._lock:
            for link in self.links:
                if link["slug"] == slug:
                    return dict(link)
        return None

    def _slug_taken(self, slug: str, ignore_id: str | None = None) -> bool:
        return any(
            link["slug"] == slug and link["id"] != ignore_id for link in self.links
        )

    @staticmethod
    def _clean_payload(payload: dict, partial: bool = False, ignore_id: str | None = None,
                       slug_taken=None) -> dict:
        if not isinstance(payload, dict):
            raise StoreError("request body must be a JSON object")
        out: dict = {}

        def need(key: str) -> object:
            if key not in payload:
                if partial:
                    return None
                raise StoreError(f"'{key}' is required")
            return payload[key]

        if not partial or "slug" in payload:
            raw_slug = payload.get("slug") or ""
            slug = normalize_slug(str(raw_slug)) if raw_slug else ""
            if not slug:
                if partial:
                    raise StoreError("slug cannot be empty")
                # On create a blank slug is allowed: create_link() will
                # auto-generate one from the title.
            else:
                slug = validate_slug(slug)
                if slug_taken and slug_taken(slug):
                    raise StoreError(f"slug '/u/{slug}' is already in use")
                out["slug"] = slug

        if not partial or "title" in payload:
            title = str(need("title") or "").strip()
            if not title:
                raise StoreError("title is required")
            out["title"] = title[:MAX_TITLE]

        if not partial or "description" in payload:
            desc = str(payload.get("description") or "").strip()
            out["description"] = desc[:MAX_DESCRIPTION] or "COMPLETE ALL THE STEPS BELOW"

        if not partial or "destination" in payload:
            out["destination"] = validate_url(str(need("destination") or ""), "destination URL")

        if not partial or "actionUrl" in payload:
            out["actionUrl"] = validate_url(str(need("actionUrl") or ""), "step (subscription) URL")

        if not partial or "actionLabel" in payload:
            label = str(payload.get("actionLabel") or "Subscribe Channel").strip()
            out["actionLabel"] = label[:MAX_LABEL] or "Subscribe Channel"

        if not partial or "actionType" in payload:
            a_type = str(payload.get("actionType") or "youtube_subscribe")
            if a_type not in ("youtube_subscribe", "custom"):
                raise StoreError("actionType must be 'youtube_subscribe' or 'custom'")
            out["actionType"] = a_type

        # Optional two-task YouTube configuration (Sub4Unlock style).
        if not partial or "channelUrl" in payload:
            out["channelUrl"] = validate_optional_youtube_url(
                str(payload.get("channelUrl") or ""), "channel URL")
        if not partial or "videoUrl" in payload:
            out["videoUrl"] = validate_optional_youtube_url(
                str(payload.get("videoUrl") or ""), "video URL")
        if not partial or "likeMethod" in payload:
            like_method = str(payload.get("likeMethod") or "manual")
            if like_method not in ("manual", "honor"):
                raise StoreError("likeMethod must be 'manual' or 'honor'")
            out["likeMethod"] = like_method

        if not partial or "enabled" in payload:
            out["enabled"] = bool(payload.get("enabled", True))

        return out

    def create_link(self, payload: dict) -> dict:
        with self._lock:
            data = self._clean_payload(
                payload,
                slug_taken=lambda s: self._slug_taken(s),
            )
            if not data.get("slug"):
                base = normalize_slug(str(payload.get("slug") or payload.get("title") or "link"))
                base = base or "link"
                candidate, n = base, 1
                while self._slug_taken(candidate) or candidate in RESERVED_SLUGS:
                    n += 1
                    candidate = f"{base[:58]}-{n}"
                data["slug"] = validate_slug(candidate)
            link = {
                "id": secrets.token_hex(6),
                **data,
                "stats": {k: 0 for k in STAT_KEYS},
                "createdAt": _now_iso(),
                "updatedAt": _now_iso(),
            }
            self.links.append(link)
            _atomic_write(self.links_path, self.links)
            return dict(link)

    # -------------------------------------------------------------- stats
    def bump_stat(self, key: str, stat: str) -> None:
        """Increment a per-link usage counter (keyed by id or slug)."""
        if stat not in STAT_KEYS:
            raise StoreError("unknown stat")
        with self._lock:
            for link in self.links:
                if link.get("id") == key or link.get("slug") == key:
                    stats = link.get("stats")
                    if not isinstance(stats, dict):
                        stats = {}
                    for k in STAT_KEYS:
                        stats.setdefault(k, 0)
                    stats[stat] = int(stats.get(stat, 0) or 0) + 1
                    link["stats"] = stats
                    _atomic_write(self.links_path, self.links)
                    return

    # ------------------------------------------------------------- events
    def record_event(self, event: str, **fields) -> dict:
        """Append an audit event. Never pass secrets/destinations here."""
        rec: dict = {"ts": _now_iso(), "event": str(event)[:80]}
        for k, v in fields.items():
            if v is None or v == "":
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                rec[k] = v
            else:
                rec[k] = str(v)[:300]
        with self._lock:
            self.events.append(rec)
            if len(self.events) > MAX_EVENTS:
                self.events = self.events[-MAX_EVENTS:]
            try:
                self.events_path.parent.mkdir(parents=True, exist_ok=True)
                with self.events_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except OSError:
                pass  # never break a request because of audit-log disk trouble
        return rec

    def list_events(self, limit: int = 100) -> list[dict]:
        with self._lock:
            limit = max(1, min(int(limit or 100), MAX_EVENTS))
            return [dict(e) for e in reversed(self.events[-limit:])]

    def update_link(self, link_id: str, payload: dict) -> dict:
        with self._lock:
            for index, link in enumerate(self.links):
                if link["id"] == link_id:
                    patch = self._clean_payload(
                        payload,
                        partial=True,
                        ignore_id=link_id,
                        slug_taken=lambda s: self._slug_taken(s, ignore_id=link_id),
                    )
                    merged = dict(link)
                    merged.update(patch)
                    merged["updatedAt"] = _now_iso()
                    self.links[index] = merged
                    _atomic_write(self.links_path, self.links)
                    return dict(merged)
        raise StoreError("link not found")

    def delete_link(self, link_id: str) -> None:
        with self._lock:
            before = len(self.links)
            slug = ""
            for link in self.links:
                if link["id"] == link_id:
                    slug = link.get("slug", "")
                    break
            self.links = [l for l in self.links if l["id"] != link_id]
            if len(self.links) == before:
                raise StoreError("link not found")
            _atomic_write(self.links_path, self.links)
            # Review rows for this slug are meaningless once the link is
            # gone — without this the admin review tab would keep showing
            # approve/reject buttons for pages that no longer exist.
            if slug:
                remaining = [r for r in self.reviews if r.get("slug") != slug]
                if len(remaining) != len(self.reviews):
                    self.reviews = remaining
                    _atomic_write(self.reviews_path, self.reviews)

    # ------------------------------------------------------------- reviews
    def _prune_reviews(self, max_age_days: int = 7) -> None:
        cutoff = time.time() - max_age_days * 86400
        alive = []
        for review in self.reviews:
            try:
                created = calendar.timegm(time.strptime(review["created"], "%Y-%m-%dT%H:%M:%SZ"))
            except Exception:
                created = cutoff
            if created >= cutoff:
                alive.append(review)
        if len(alive) != len(self.reviews):
            self.reviews = alive
            _atomic_write(self.reviews_path, self.reviews)

    def create_review(self, slug: str, token_hash: str, ttl_seconds: int,
                      subscribe_basis: str = "") -> dict:
        now = time.time()
        with self._lock:
            # Re-use a live pending/approved review for the same token when possible.
            for review in self.reviews:
                if review["tokenHash"] == token_hash and review["slug"] == slug:
                    if review["state"] in ("pending", "approved") and review["exp"] > now:
                        return dict(review)
            review = {
                "id": secrets.token_hex(5),
                "slug": slug,
                "tokenHash": token_hash,
                "state": "pending",
                # Server-side fact captured at REQUEST time: how (and if)
                # the visitor's subscribe step was already satisfied on
                # this server.  Approval carries it into the granted
                # session; it is never taken from client input.
                "subscribeBasis": subscribe_basis,
                "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
                "expiresAt": int(now + ttl_seconds),
                "exp": int(now + ttl_seconds),
            }
            self.reviews.append(review)
            _atomic_write(self.reviews_path, self.reviews)
            return dict(review)

    def get_review(self, review_id: str) -> dict | None:
        with self._lock:
            for review in self.reviews:
                if review["id"] == review_id:
                    return dict(review)
        return None

    def find_review(self, token_hash: str, slug: str | None = None) -> dict | None:
        with self._lock:
            for review in reversed(self.reviews):
                if review["tokenHash"] == token_hash and (slug is None or review["slug"] == slug):
                    return dict(review)
        return None

    def list_reviews(self, state: str | None = None) -> list[dict]:
        with self._lock:
            out = []
            for review in self.reviews:
                if state and review["state"] != state:
                    continue
                item = dict(review)
                item.pop("tokenHash", None)
                out.append(item)
            return list(reversed(out))

    def set_review_state(self, review_id: str, state: str) -> dict:
        if state not in ("approved", "rejected"):
            raise StoreError("invalid review state")
        with self._lock:
            for review in self.reviews:
                if review["id"] == review_id:
                    if review["state"] != "pending":
                        raise StoreError(f"review already {review['state']}")
                    if review["exp"] <= time.time():
                        review["state"] = "expired"
                        _atomic_write(self.reviews_path, self.reviews)
                        raise StoreError("review request has expired")
                    review["state"] = state
                    review[f"{state}At"] = _now_iso()
                    _atomic_write(self.reviews_path, self.reviews)
                    return dict(review)
        raise StoreError("review not found")

    def expire_due_reviews(self) -> None:
        with self._lock:
            changed = False
            for review in self.reviews:
                if review["state"] == "pending" and review["exp"] <= time.time():
                    review["state"] = "expired"
                    changed = True
            if changed:
                _atomic_write(self.reviews_path, self.reviews)
