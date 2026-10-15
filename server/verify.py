"""Genuine YouTube subscription verification via the official Google APIs.

Flow (only runs when OAuth credentials are configured):

  1. User is redirected to Google's account/consent screen
     (`scope = youtube.readonly` only, `response_type=code`).
  2. Google redirects back to this server with a one-time `code`
     and our single-use `state`.
  3. Server exchanges the code for tokens **server-to-server**
     (`client_secret` never leaves this process).
  4. Server calls the YouTube Data API v3 `subscriptions.list`
     with `mine=true&forChannelId=<YOUR_CHANNEL_ID>` using the
     *user's* access token.  Only Google can authoritatively answer
     "is this account subscribed to this channel?" — and it answers
     with the user's own credentials.
  5. Only if the API reports an actual subscription is a verified
     session created.

If credentials are NOT configured the caller must fall back to the
explicit, honest manual-review mode — we never fake a verification.

Endpoint base URLs are overridable via environment variables purely so
the automated test-suite can point at a mock Google server.  In
production leave them at their defaults.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

import env as env_mod
import deploy as deploy_mod

YOUTUBE_READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"

DEFAULT_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
DEFAULT_TOKEN_URL = "https://oauth2.googleapis.com/token"
DEFAULT_API_BASE = "https://www.googleapis.com"


class VerifyError(Exception):
    """Verification could not be completed.  `code` is safe to show to users."""

    def __init__(self, code: str, public_message: str):
        super().__init__(public_message)
        self.code = code
        self.public_message = public_message


@dataclass
class VerifyConfig:
    client_id: str
    client_secret: str
    channel_id: str
    public_url: str
    auth_url: str
    token_url: str
    api_base: str

    @property
    def oauth_ready(self) -> bool:
        return bool(self.client_id and self.client_secret and self.channel_id)

    @property
    def missing(self) -> list[str]:
        needed = []
        if not self.client_id:
            needed.append("GOOGLE_CLIENT_ID")
        if not self.client_secret:
            needed.append("GOOGLE_CLIENT_SECRET")
        if not self.channel_id:
            needed.append("YOUTUBE_CHANNEL_ID (or set it in admin -> Site & footer)")
        return needed

    @property
    def redirect_uri(self) -> str:
        return self.public_url.rstrip("/") + "/api/oauth/callback"

    @classmethod
    def from_env(cls, settings_channel_id: str = "") -> "VerifyConfig":
        return cls(
            client_id=env_mod.get("GOOGLE_CLIENT_ID"),
            client_secret=env_mod.get("GOOGLE_CLIENT_SECRET"),
            channel_id=(env_mod.get("YOUTUBE_CHANNEL_ID") or (settings_channel_id or "").strip()),
            public_url=deploy_mod.default_public_url(),
            auth_url=env_mod.get("GOOGLE_AUTH_URL", DEFAULT_AUTH_URL),
            token_url=env_mod.get("GOOGLE_TOKEN_URL", DEFAULT_TOKEN_URL),
            api_base=env_mod.get("YOUTUBE_API_BASE", DEFAULT_API_BASE).rstrip("/"),
        )


def build_authorization_url(cfg: VerifyConfig, state: str) -> str:
    if not cfg.oauth_ready:
        raise VerifyError("not_configured", "Automatic verification is not configured.")
    params = {
        "client_id": cfg.client_id,
        "redirect_uri": cfg.redirect_uri,
        "response_type": "code",
        # Least privilege: only this single readonly scope is requested,
        # no offline access and no scope expansion.
        "scope": YOUTUBE_READONLY_SCOPE,
        # Always let the user pick the Google account they subscribed with
        # (silently auto-approved when the grant already exists).
        "prompt": "select_account",
        "state": state,
    }
    return cfg.auth_url + "?" + urllib.parse.urlencode(params)


def _post_form(url: str, fields: dict, timeout: int = 20) -> dict:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:  # Google returns JSON error bodies
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8", "replace")).get("error_description", "")
        except Exception:
            pass
        raise VerifyError(
            "exchange_failed",
            "Google rejected the verification response. Please try again.",
        ) from exc
    except urllib.error.URLError as exc:
        raise VerifyError(
            "api_unreachable",
            "Could not reach Google right now. Please try again shortly.",
        ) from exc
    try:
        payload = json.loads(body)
    except Exception as exc:
        raise VerifyError("api_error", "Unexpected response from Google.") from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise VerifyError("exchange_failed", "Google rejected the verification response.")
    if not isinstance(payload, dict):
        raise VerifyError("api_error", "Unexpected response from Google.")
    return payload


def _get_json(url: str, headers: dict, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = exc.code
        if status in (401,):
            raise VerifyError(
                "auth_expired",
                "Your Google sign-in has expired. Please verify again.",
            ) from exc
        if status == 403:
            raise VerifyError(
                "permission_denied",
                "Google did not grant permission to read your YouTube subscriptions. "
                "Please try signing in again and allow the requested permission.",
            ) from exc
        raise VerifyError(
            "api_error",
            "The YouTube API could not confirm your subscription right now. "
            "Please try again in a moment.",
        ) from exc
    except urllib.error.URLError as exc:
        raise VerifyError(
            "api_unreachable",
            "Could not reach the YouTube API right now. Please try again shortly.",
        ) from exc
    try:
        return json.loads(body)
    except Exception as exc:
        raise VerifyError("api_error", "Unexpected response from the YouTube API.") from exc


def exchange_code(cfg: VerifyConfig, code: str) -> str:
    """Authorization-code -> access token (server-to-server)."""
    payload = _post_form(
        cfg.token_url,
        {
            "code": code,
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "redirect_uri": cfg.redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    token = payload.get("access_token")
    if not token or not isinstance(token, str):
        raise VerifyError("exchange_failed", "Google did not return a sign-in token.")
    return token


def check_subscription(cfg: VerifyConfig, access_token: str) -> dict:
    """Authoritative check: is the signed-in Google account subscribed?

    Uses YouTube Data API v3 `subscriptions.list` with
    `mine=true&forChannelId=<channel>` — readable only with the
    *viewer's own* OAuth token, so it cannot be spoofed by the client.
    """
    params = urllib.parse.urlencode(
        {
            "part": "snippet",
            "mine": "true",
            "forChannelId": cfg.channel_id,
            "maxResults": "50",
        }
    )
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    data = _get_json(f"{cfg.api_base}/youtube/v3/subscriptions?{params}", headers)

    items = data.get("items") or []
    subscribed = bool(items)

    # Non-fatal: record which YouTube channel performed the verification.
    user_channel = ""
    try:
        who = _get_json(
            f"{cfg.api_base}/youtube/v3/channels?part=id&mine=true", headers
        )
        user_channel = ((who.get("items") or [{}])[0]).get("id", "") or ""
    except VerifyError:
        pass

    return {"subscribed": subscribed, "userChannel": user_channel}
