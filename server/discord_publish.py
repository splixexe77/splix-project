"""Optional Discord publishing via a channel **webhook**.

Why a webhook and not a bot: the task requires the *minimum* necessary
Discord permissions.  A webhook posts to one channel you own and needs
no bot token, no gateway connection and definitely no Administrator
permission.  It is disabled entirely until DISCORD_WEBHOOK_URL is set
in server/.env (never exposed to the browser).
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

EMBED_COLOR = 0xFF2D3A  # brand red


def publish_unlock_link(
    webhook_url: str,
    *,
    title: str,
    description: str,
    unlock_url: str,
    destination_label: str = "a locked resource",
) -> dict:
    if not webhook_url:
        raise RuntimeError("DISCORD_WEBHOOK_URL is not configured")

    embed = {
        "title": title[:256] or "Locked link",
        "description": (description or "Complete the required step to unlock the link.")[:500],
        "url": unlock_url,
        "color": EMBED_COLOR,
        "fields": [
            {"name": "Unlock link", "value": f"[Open the unlock page]({unlock_url})", "inline": False},
            {"name": "Unlocks", "value": destination_label[:256], "inline": True},
        ],
        "footer": {"text": "Link gated by the SPLIX unlock system"},
    }
    payload = json.dumps({"embeds": [embed], "allowed_mentions": {"parse": []}}).encode("utf-8")

    req = urllib.request.Request(webhook_url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status = resp.status
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Discord rejected the post (HTTP {exc.code})") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Could not reach Discord") from exc
    if status not in (200, 204):
        raise RuntimeError(f"Discord returned HTTP {status}")
    return {"ok": True}
