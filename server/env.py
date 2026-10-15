"""Tiny .env loader — no third-party dependencies.

Loads server/.env into os.environ **without overriding** variables that
are already set in the real environment (real environment wins).
"""
from __future__ import annotations

import os
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent


def load(path: str | os.PathLike | None = None) -> Path:
    """Load a .env file. Returns the path that was (or would be) read."""
    env_path = Path(path) if path else SERVER_DIR / ".env"
    if not env_path.is_file():
        return env_path

    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
    return env_path


def get(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip()


def get_int(name: str, default: int) -> int:
    try:
        return int(get(name, str(default)))
    except (TypeError, ValueError):
        return default


def get_bool(name: str, default: bool = False) -> bool:
    raw = get(name, "").lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")
