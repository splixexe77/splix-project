"""End-to-end test-suite for the SPLIX unlock feature.

    python server/tests/run_tests.py

What it does
------------
1. Starts a **mock Google/YouTube API server** (same endpoints the real
   integration uses: OAuth consent redirect, token exchange,
   subscriptions.list, channels.list).
2. Starts isolated instances of the unlock server with temporary
   data folders:
     * app1  -> official-verification mode (OAuth creds present)
     * app2  -> manual-review mode (no creds, normal TTLs)
     * app3  -> manual-review mode with 0-minute review TTL + a tiny
                OAuth rate limit + admin API disabled
     * app4  -> deployment-readiness instance (https PUBLIC_URL,
                TRUST_PROXY, SEED_DIR first-boot migration)
3. Asserts 200+ behaviours, including:
     * every original site file still serves byte-identical,
     * admin CRUD (create/read/update/delete + validation),
     * destination URLs are never exposed on public endpoints/pages,
     * clients cannot unlock via query params, forged cookies or bare
       claim requests,
     * the OAuth flow only unlocks when the *API* reports subscribed,
     * state single-use, session-per-slug binding, rate limiting,
     * manual review approve/reject/expiry behaviour,
     * deployment readiness (host/port resolution, secret-safe access
       logs, /health, first-boot seed migration, backup tool).

The mock proves *our* code behaves correctly against a Google-shaped
API.  Live YouTube verification still requires real credentials — see
server/README.md.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
SERVER_DIR = TESTS_DIR.parent
ROOT = SERVER_DIR.parent

if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import deploy as deploy_mod  # noqa: E402  (pure helpers, no side effects)

APP1_PORT = 8301
APP2_PORT = 8302
APP3_PORT = 8303
APP4_PORT = 8304
MOCK_PORT = 8398

ADMIN_TOKEN = "test-admin-token-1234567890abcdef"

RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((bool(condition), name, detail))
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"   -> {detail}" if detail and not condition else ""))


# ===================================================== mock Google API
class MockState:
    scenario = "subscribed"
    # Discord webhook simulation: "ok" answers 204 like a real channel
    # webhook, "fail" answers 400 so the failure path is testable too.
    discord_mode = "ok"
    discord_payloads: list = []


MOCK = MockState()


class MockGoogleHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet
        pass

    def _send(self, status: int, payload: dict | str, ctype="application/json"):
        body = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _query(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8", "replace") if length else ""
        if not raw and "?" in self.path:
            raw = self.path.split("?", 1)[1]
        return dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/o/oauth2/v2/auth":
            q = self._query()
            if not q.get("state") or not q.get("redirect_uri"):
                return self._send(400, {"error": "missing params"})
            if "youtube.readonly" not in q.get("scope", ""):
                return self._send(400, {"error": "wrong scope"})
            redirect = q["redirect_uri"]
            sep = "&" if "?" in redirect else "?"
            if MOCK.scenario == "user_denied":
                return self._redirect(f"{redirect}{sep}error=access_denied&state={q['state']}")
            return self._redirect(
                f"{redirect}{sep}code=code_{MOCK.scenario}&state={q['state']}"
            )

        if path == "/youtube/v3/subscriptions":
            auth = self.headers.get("Authorization", "")
            token = auth.replace("Bearer ", "")
            if token == "tok_code_subscribed":
                return self._send(200, {"items": [{"snippet": {"resourceId": {"channelId": "UC_TEST"}}}]})
            if token == "tok_code_not_subscribed":
                return self._send(200, {"items": []})
            if token == "tok_code_denied403":
                return self._send(403, {"error": {"code": 403, "message": "The user does not have permission.",
                                                  "errors": [{"reason": "insufficientPermissions"}]}})
            return self._send(401, {"error": {"code": 401, "message": "Invalid Credentials"}})

        if path == "/youtube/v3/channels":
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer tok_code_"):
                return self._send(200, {"items": [{"id": "UC_TEST_USER_CHANNEL"}]})
            return self._send(401, {"error": {"code": 401, "message": "Invalid Credentials"}})

        self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/token":
            q = self._query()
            if q.get("code") == "code_exchange_fail":
                return self._send(400, {"error": "invalid_grant"})
            return self._send(200, {"access_token": "tok_" + q.get("code", ""), "token_type": "Bearer"})
        if path == "/discord-webhook":
            # Stands in for a Discord channel webhook: capture the embed so
            # the test can assert exactly what we would post (and what we
            # would never post). The real webhook answers 204 on success.
            # Always drain the request body first — with HTTP/1.1 keep-alive
            # an unread body would be parsed as the next request and wedge
            # this connection's handler thread.
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            if MOCK.discord_mode == "fail":
                return self._send(400, {"error": "invalid webhook"})
            try:
                MOCK.discord_payloads.append(json.loads(raw.decode("utf-8")))
            except Exception:
                MOCK.discord_payloads.append({"_raw": raw.decode("utf-8", "replace")})
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self._send(404, {"error": "not found"})


# ===================================================== HTTP helpers
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


OPENER = urllib.request.build_opener(_NoRedirect)


def http(url: str, method: str = "GET", headers: dict | None = None, body: bytes | None = None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with OPENER.open(req, timeout=12) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def json_call(url: str, method: str = "GET", payload: dict | None = None,
              token: str | None = None, cookie: str | None = None):
    headers: dict[str, str] = {}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    status, resp_headers, raw = http(url, method, headers, body)
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        data = {"__raw": raw.decode("utf-8", "replace")}
    return status, data, resp_headers


def first_cookie(header_value: str) -> str | None:
    if not header_value:
        return None
    main = header_value.split(";", 1)[0]
    return main if "=" in main else None


def follow(url: str, method: str = "GET", cookie: str | None = None,
           headers: dict | None = None, max_hops: int = 8):
    """Manual redirect follower that keeps cookies and records the chain."""
    chain = []
    for _ in range(max_hops):
        req_headers = dict(headers or {})
        if cookie:
            req_headers["Cookie"] = cookie
        status, resp_headers, raw = http(url, method, req_headers, None)
        set_cookie = first_cookie(resp_headers.get("Set-Cookie", ""))
        if set_cookie:
            cookie = set_cookie
        if status in (301, 302, 303, 307, 308) and resp_headers.get("Location"):
            location = urllib.parse.urljoin(url, resp_headers["Location"])
            chain.append((status, location))
            method = "GET"
            url = location
            continue
        return status, resp_headers, raw, cookie, chain
    return 0, {}, b"", cookie, chain


# ===================================================== app instances
def app_env(port: int, data_dir: Path, **overrides) -> dict:
    env = dict(os.environ)
    env.update({
        "PORT": str(port),
        "HOST": "127.0.0.1",
        "PUBLIC_URL": f"http://127.0.0.1:{port}",
        "ADMIN_TOKEN": ADMIN_TOKEN,
        "DATA_DIR": str(data_dir),
        "GOOGLE_CLIENT_ID": "test-client-id.apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "test-client-secret",
        "YOUTUBE_CHANNEL_ID": "UC_TEST_CHANNEL_123",
        "GOOGLE_AUTH_URL": f"http://127.0.0.1:{MOCK_PORT}/o/oauth2/v2/auth",
        "GOOGLE_TOKEN_URL": f"http://127.0.0.1:{MOCK_PORT}/token",
        "YOUTUBE_API_BASE": f"http://127.0.0.1:{MOCK_PORT}",
        "RL_API": "5000", "RL_STATUS": "5000", "RL_OAUTH": "5000",
        "RL_REVIEW": "5000", "RL_LIKE": "5000", "RL_CLAIM": "5000", "RL_ADMIN": "5000",
        "SESSION_TTL_MIN": "60",
        "REVIEW_TTL_MIN": "30",
        "TRUST_PROXY": "false",
    })
    env.update({k: str(v) for k, v in overrides.items()})
    return env


def start_app(env: dict) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, str(SERVER_DIR / "run.py")],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Keep the app's output: the drain thread both prevents the classic
    # subprocess pipe-buffer deadlock AND lets deployment checks assert
    # that secret query values never reach the access log.
    proc.log_lines = deque(maxlen=4000)

    def _drain(stream):
        try:
            for line in stream:
                try:
                    proc.log_lines.append(
                        line.decode("utf-8", "replace")
                        if isinstance(line, bytes) else str(line)
                    )
                except Exception:
                    pass
        except Exception:
            pass

    threading.Thread(target=_drain, args=(proc.stdout,), daemon=True).start()

    # wait for the port
    base = f"http://127.0.0.1:{env['PORT']}"
    for _ in range(60):
        try:
            status, _, _ = http(base + "/index.html")
            if status == 200:
                return proc
        except Exception:
            pass
        time.sleep(0.15)
    raise RuntimeError("app instance did not start in time")


def stop_app(proc: subprocess.Popen | None) -> None:
    if not proc:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ===================================================== main
def main() -> int:
    tmp_root = Path(tempfile.mkdtemp(prefix="splix_unlock_tests_"))
    mock_server = None
    apps: list[subprocess.Popen | None] = [None, None, None, None]

    try:
        # ---- mock Google ------------------------------------------
        mock_server = ThreadingHTTPServer(("127.0.0.1", MOCK_PORT), MockGoogleHandler)
        mock_server.daemon_threads = True
        threading.Thread(target=mock_server.serve_forever, daemon=True).start()

        print("\n=== 1. Original site still serves (byte-identical) ===")
        app1_dir = tmp_root / "app1"
        apps[0] = start_app(app_env(
            APP1_PORT, app1_dir,
            DISCORD_WEBHOOK_URL=f"http://127.0.0.1:{MOCK_PORT}/discord-webhook"))
        base1 = f"http://127.0.0.1:{APP1_PORT}"

        for rel in ["index.html", "plus.html", "css/style.css", "js/config.js",
                    "js/main.js", "js/plus.js", "js/particles.js", "favicon.ico"]:
            status, _, body = http(f"{base1}/{rel}")
            on_disk = (ROOT / rel).read_bytes()
            check(f"{rel} served unchanged", status == 200 and body == on_disk,
                  f"status={status} len={len(body)} vs {len(on_disk)}")

        print("\n=== 2. Backend is not exposed ===")
        for rel in ["server/.env", "server/run.py", "server/app.py", "server/data/links.json"]:
            status, _, _ = http(f"{base1}/{rel}")
            check(f"{rel} blocked", status == 404, f"status={status}")
        status, _, _ = http(f"{base1}/../server/run.py")
        check("traversal to server blocked", status in (404, 400), f"status={status}")

        print("\n=== 3. Admin authentication ===")
        status, data, _ = json_call(f"{base1}/api/admin/overview")
        check("admin overview without token -> 401", status == 401, f"status={status}")
        status, data, _ = json_call(f"{base1}/api/admin/overview", token="wrong-token")
        check("admin overview wrong token -> 401", status == 401, f"status={status}")
        status, data, _ = json_call(f"{base1}/api/admin/overview", token=ADMIN_TOKEN)
        check("admin overview with token -> 200", status == 200 and data.get("ok") is True)
        check("verification mode reported as oauth",
              data.get("verification", {}).get("mode") == "oauth",
              str(data.get("verification")))

        print("\n=== 4. Link CRUD ===")
        payload = {
            "title": "Free Fire Panel Access",
            "slug": "panel-access",
            "destination": "https://example.com/secret-panel-download",
            "actionLabel": "Subscribe Channel",
            "actionUrl": "https://www.youtube.com/channel/UC_TEST_CHANNEL_123?sub_confirmation=1",
            "description": "COMPLETE ALL THE STEPS BELOW",
            "actionType": "youtube_subscribe",
            "enabled": True,
        }
        status, data, _ = json_call(f"{base1}/api/admin/links", "POST", payload, token=ADMIN_TOKEN)
        check("create link -> 201", status == 201 and data.get("ok"), str(data)[:160])
        link1 = data.get("link", {})
        check("unlock URL returned to admin",
              link1.get("unlockUrl", "").endswith("/u/panel-access"), link1.get("unlockUrl", ""))
        check("new link starts with zeroed stats",
              link1.get("stats") == {"visits": 0, "verified": 0, "unlocks": 0},
              str(link1.get("stats")))

        # blank slug -> auto-generated from the title (must not error)
        autogen = dict(payload, title="Auto Generated Link", slug="")
        status, data, _ = json_call(f"{base1}/api/admin/links", "POST", autogen, token=ADMIN_TOKEN)
        autolink = data.get("link", {})
        check("blank slug auto-generates a slug -> 201",
              status == 201 and autolink.get("slug", "").startswith("auto-generated-link"),
              f"status={status} slug={autolink.get('slug')}")
        check("auto-generated link has stats block",
              autolink.get("stats") == {"visits": 0, "verified": 0, "unlocks": 0})
        # clean it up so it does not interfere with later checks
        if autolink.get("id"):
            json_call(f"{base1}/api/admin/links/{autolink['id']}", "DELETE", token=ADMIN_TOKEN)

        status, data, _ = json_call(f"{base1}/api/admin/links", "POST", payload, token=ADMIN_TOKEN)
        check("duplicate slug rejected -> 400", status == 400, f"status={status} {data.get('message','')}")

        bad = dict(payload, slug="bad-slug-2", destination="javascript:alert(1)")
        status, data, _ = json_call(f"{base1}/api/admin/links", "POST", bad, token=ADMIN_TOKEN)
        check("javascript: destination rejected -> 400", status == 400, f"status={status}")

        bad = dict(payload, slug="api")
        status, data, _ = json_call(f"{base1}/api/admin/links", "POST", bad, token=ADMIN_TOKEN)
        check("reserved slug rejected -> 400", status == 400, f"status={status}")

        status, data, _ = json_call(
            f"{base1}/api/admin/links/{link1['id']}", "PUT",
            {"title": "Panel Access (updated)", "destination": "https://example.com/v2-secret"},
            token=ADMIN_TOKEN,
        )
        check("update link -> 200", status == 200 and data["link"]["title"] == "Panel Access (updated)")
        link1 = data["link"]

        status, data, _ = json_call(f"{base1}/api/admin/links", token=ADMIN_TOKEN)
        check("admin list contains destination",
              any(l.get("destination", "").startswith("https://example.com") for l in data.get("links", [])))

        # a second link for slug-binding tests
        payload2 = dict(payload, slug="other-destination", destination="https://example.org/other-secret")
        status, data2, _ = json_call(f"{base1}/api/admin/links", "POST", payload2, token=ADMIN_TOKEN)
        check("second link created", status == 201, str(data2)[:120])
        link2 = data2.get("link", {})

        print("\n=== 5. Public surface never leaks destinations ===")
        status, data, _ = json_call(f"{base1}/api/u/panel-access")
        check("public config -> 200", status == 200 and data.get("ok") is True)
        check("public config hides destination", "secret" not in json.dumps(data), json.dumps(data)[:200])
        check("public config reports oauth mode", data.get("mode") == "oauth")
        check("public config state is none", data.get("state") == "none")

        status, _, body = http(f"{base1}/u/panel-access")
        html = body.decode("utf-8", "replace")
        check("unlock page -> 200", status == 200, f"status={status}")
        check("unlock page hides destination", "secret" not in html and "example.com" not in html)
        check("unlock page has title", "Panel Access" in html)
        check("unlock page has no unreplaced tokens", "{{" not in html)
        check("unlock page carries slug + mode",
              'data-slug="panel-access"' in html and 'data-mode="oauth"' in html)

        status, _, _ = http(f"{base1}/u/no-such-link")
        check("unknown unlock page -> 404", status == 404, f"status={status}")

        print("\n=== 6. Bypass attempts all fail ===")
        status, data, _ = json_call(f"{base1}/api/u/panel-access/claim", "POST", {})
        check("claim without session -> 403", status == 403 and data.get("error") == "not_verified",
              f"status={status}")

        status, data, _ = json_call(f"{base1}/api/u/panel-access/claim", "POST", {},
                                    cookie="splix_unlock=forged-session-token")
        check("claim with forged cookie -> 403", status == 403, f"status={status}")

        status, data, _ = json_call(f"{base1}/api/u/panel-access/claim", "POST",
                                    {"verified": True, "url": "x"})
        check("claim with verified:true body -> 403", status == 403, f"status={status}")

        status, data, _ = json_call(f"{base1}/api/u/panel-access?verified=1&v=1")
        check("verified query params ignored", status == 200 and data.get("state") == "none")

        status, _, _ = http(f"{base1}/u/panel-access?v=1")
        check("fake ?v=1 page still renders (no state change)", status == 200)
        status, data, _ = json_call(f"{base1}/api/u/panel-access/claim", "POST", {})
        check("claim after fake ?v=1 -> 403", status == 403, f"status={status}")

        status, data, _ = json_call(f"{base1}/api/u/panel-access/review", "POST", {}, token=ADMIN_TOKEN)
        check("manual review blocked while OAuth mode active -> 403",
              status == 403 and data.get("error") == "automatic_verification_required",
              f"status={status} {data}")

        print("\n=== 7. Official OAuth verification flow ===")
        # 7a. subscribed -> unlock
        MOCK.scenario = "subscribed"
        status, headers, _ = http(f"{base1}/api/u/panel-access/oauth/start")
        check("oauth start -> 302 to provider", status == 302, f"status={status}")
        auth_url = headers.get("Location", "")
        check("consent URL carries state + scope",
              "state=" in auth_url and "youtube.readonly" in auth_url, auth_url[:120])
        browser_cookie = first_cookie(headers.get("Set-Cookie", "")) or ""

        status, _, final_body, cookie, chain = follow(auth_url, cookie=browser_cookie)
        check("callback chain ends at unlock page with ?v=1",
              bool(chain) and chain[-1][1].endswith("/u/panel-access?v=1"),
              json.dumps(chain[-3:]) if chain else "no chain")
        check("session cookie issued", bool(cookie) and cookie.startswith("splix_unlock="), str(cookie))
        raw_set_cookie = ""
        # verify cookie flags
        status2, _, raw2 = http(base1 + "/api/u/panel-access/oauth/start", "GET",
                                {"Cookie": "x=1"})
        # (flags asserted below via a fresh full flow)
        status, data, _ = json_call(f"{base1}/api/u/panel-access", cookie=cookie)
        check("state now verified", status == 200 and data.get("state") == "verified", str(data.get("state")))

        status, data, _ = json_call(f"{base1}/api/u/panel-access/claim", "POST", {}, cookie=cookie)
        check("claim after real API verification -> destination",
              status == 200 and data.get("url") == "https://example.com/v2-secret",
              str(data)[:160])

        # 7b. state single-use (replay the state from 7a)
        if chain:
            state_value = urllib.parse.parse_qs(urllib.parse.urlsplit(auth_url).query).get("state", [""])[0]
            replay_url = f"{base1}/api/oauth/callback?code=code_subscribed&state={state_value}"
            status, _, _ = http(replay_url)
            check("replayed OAuth state rejected", status == 400, f"status={status}")

        # 7g. a verified session is bound to ONE slug only
        status, data, _ = json_call(f"{base1}/api/u/other-destination/claim", "POST", {}, cookie=cookie)
        check("session for panel-access does NOT unlock other-destination -> 403",
              status == 403, f"status={status}")

        # 7c. not subscribed -> stays locked
        MOCK.scenario = "not_subscribed"
        status, headers, _ = http(f"{base1}/api/u/other-destination/oauth/start")
        auth_url = headers.get("Location", "")
        status, _, _, _, chain = follow(auth_url, cookie=first_cookie(headers.get("Set-Cookie", "")))
        check("unsubscribed -> redirect with err=not_subscribed",
              bool(chain) and "err=not_subscribed" in chain[-1][1], json.dumps(chain))
        status, data, _ = json_call(f"{base1}/api/u/other-destination/claim", "POST", {})
        check("unsubscribed cannot claim -> 403", status == 403, f"status={status}")

        # 7d. user denies consent
        MOCK.scenario = "user_denied"
        status, headers, _ = http(f"{base1}/api/u/other-destination/oauth/start")
        auth_url = headers.get("Location", "")
        status, _, _, _, chain = follow(auth_url, cookie=first_cookie(headers.get("Set-Cookie", "")))
        check("denied consent -> err=denied",
              bool(chain) and "err=denied" in chain[-1][1], json.dumps(chain))
        status, data, _ = json_call(f"{base1}/api/u/other-destination/claim", "POST", {})
        check("denied consent cannot claim -> 403", status == 403)

        # 7e. API permission error
        MOCK.scenario = "denied403"
        status, headers, _ = http(f"{base1}/api/u/other-destination/oauth/start")
        auth_url = headers.get("Location", "")
        status, _, _, _, chain = follow(auth_url, cookie=first_cookie(headers.get("Set-Cookie", "")))
        check("API 403 -> err=permission_denied",
              bool(chain) and "err=permission_denied" in chain[-1][1], json.dumps(chain))
        status, data, _ = json_call(f"{base1}/api/u/other-destination/claim", "POST", {})
        check("permission error cannot claim -> 403", status == 403)

        # 7f. token exchange failure
        MOCK.scenario = "exchange_fail"
        status, headers, _ = http(f"{base1}/api/u/other-destination/oauth/start")
        auth_url = headers.get("Location", "")
        status, _, _, _, chain = follow(auth_url, cookie=first_cookie(headers.get("Set-Cookie", "")))
        check("exchange failure -> err=exchange_failed",
              bool(chain) and "err=exchange_failed" in chain[-1][1], json.dumps(chain))
        status, data, _ = json_call(f"{base1}/api/u/other-destination/claim", "POST", {})
        check("exchange failure cannot claim -> 403", status == 403)

        # 7h. cookie flags (cookies carried through the whole chain)
        MOCK.scenario = "subscribed"
        status, headers, _ = http(f"{base1}/api/u/other-destination/oauth/start")
        start_cookie = first_cookie(headers.get("Set-Cookie", ""))
        check("oauth start sets the browser-binding cookie",
              bool(start_cookie) and start_cookie.startswith("splix_oauth="),
              str(headers.get("Set-Cookie")))
        auth_url = headers.get("Location", "")
        url = auth_url
        jar = start_cookie or ""
        final_set_cookie = ""
        for _ in range(6):
            status, hdrs, _raw = http(url, "GET", {"Cookie": jar} if jar else {})
            sc = hdrs.get("Set-Cookie")
            if sc:
                final_set_cookie = sc
                main = first_cookie(sc)
                if main:
                    jar = main
            loc = hdrs.get("Location")
            if status in (301, 302, 303, 307, 308) and loc:
                url = urllib.parse.urljoin(url, loc)
                continue
            break
        check("session cookie is HttpOnly + SameSite=Lax + Path=/",
              "splix_unlock=" in final_set_cookie and "HttpOnly" in final_set_cookie
              and "SameSite=Lax" in final_set_cookie and "Path=/" in final_set_cookie,
              final_set_cookie)

        # 7i. a captured callback URL cannot be replayed in another
        #     browser — the OAuth state is bound to the initiating
        #     browser via an HttpOnly nonce cookie.
        MOCK.scenario = "subscribed"
        status, headers, _ = http(f"{base1}/api/u/panel-access/oauth/start")
        stolen_state = urllib.parse.parse_qs(
            urllib.parse.urlsplit(headers.get("Location", "")).query
        ).get("state", [""])[0]
        status, _, _ = http(f"{base1}/api/oauth/callback?code=code_subscribed&state={stolen_state}")
        check("callback without the binding cookie -> rejected", status == 400, f"status={status}")

        status, headers, _ = http(f"{base1}/api/u/panel-access/oauth/start")
        stolen_state = urllib.parse.parse_qs(
            urllib.parse.urlsplit(headers.get("Location", "")).query
        ).get("state", [""])[0]
        status, _, _ = http(
            f"{base1}/api/oauth/callback?code=code_subscribed&state={stolen_state}",
            headers={"Cookie": "splix_oauth=wrong-nonce-value"})
        check("callback with a forged binding cookie -> rejected", status == 400, f"status={status}")
        status, data, _ = json_call(f"{base1}/api/u/panel-access/claim", "POST", {})
        check("hijacked callback creates no session", status == 403, f"status={status}")

        # 7j. per-link mode: a custom step is NEVER "verified" by a
        #     YouTube sign-in, even when OAuth is configured globally.
        payload_custom = dict(
            payload, slug="discord-step", actionType="custom",
            actionLabel="Join Discord", actionUrl="https://discord.gg/example",
            destination="https://example.com/discord-secret")
        status, data, _ = json_call(f"{base1}/api/admin/links", "POST", payload_custom, token=ADMIN_TOKEN)
        check("custom-step link created while OAuth is configured", status == 201, str(data)[:120])
        custom_id = (data.get("link") or {}).get("id", "")
        status, data, _ = json_call(f"{base1}/api/u/discord-step")
        check("custom step reports manual mode despite global OAuth",
              data.get("mode") == "manual", str(data.get("mode")))
        check("custom step never offers automatic verification",
              data.get("verification", {}).get("ready") is False
              and not data.get("verification", {}).get("missing"),
              str(data.get("verification")))
        status, _, body7j = http(f"{base1}/u/discord-step")
        check("custom step page renders in manual mode",
              status == 200 and b'data-mode="manual"' in body7j, f"status={status}")
        status, data, _ = json_call(f"{base1}/api/u/discord-step/oauth/start")
        check("Google OAuth start refused for a custom step -> 403",
              status == 403 and data.get("error") == "manual_verification",
              f"status={status} {data}")
        status, data, _ = json_call(f"{base1}/api/u/discord-step/review", "POST", {})
        check("manual review allowed for a custom step (OAuth configured)",
              status == 200 and data.get("state") == "pending", f"status={status}")
        if custom_id:
            json_call(f"{base1}/api/admin/links/{custom_id}", "DELETE", token=ADMIN_TOKEN)

        print("\n=== 8. Delete flow ===")
        # youtube_subscribe links must NOT offer manual review while
        # automatic verification is configured — the Verify button is
        # the only path in that mode.
        status, data, _ = json_call(f"{base1}/api/u/other-destination/review", "POST", {})
        check("manual review refused while automatic verification is active",
              status == 403 and data.get("error") == "automatic_verification_required",
              f"status={status} {data}")
        # a custom-step link CAN hold a pending review (custom steps are
        # always manual) — use it to prove delete purges the review queue.
        payload_del = dict(payload, slug="temp-delete-me", actionType="custom",
                           actionLabel="Join Discord", actionUrl="https://discord.gg/example",
                           destination="https://example.com/delete-me-secret")
        status, data, _ = json_call(f"{base1}/api/admin/links", "POST", payload_del, token=ADMIN_TOKEN)
        temp_id = (data.get("link") or {}).get("id", "")
        check("temp custom link created for purge test",
              status == 201 and bool(temp_id), str(data)[:120])
        status, data, _ = json_call(f"{base1}/api/u/temp-delete-me/review", "POST", {})
        check("review created for link before delete",
              status == 200 and data.get("state") == "pending", str(data)[:140])
        status, data, _ = json_call(f"{base1}/api/admin/links/{link2['id']}", "DELETE", token=ADMIN_TOKEN)
        check("delete link -> 200", status == 200)
        status, _, _ = http(f"{base1}/u/other-destination")
        check("deleted unlock page -> 404", status == 404, f"status={status}")
        status, data, _ = json_call(f"{base1}/api/u/other-destination", token=ADMIN_TOKEN)
        check("deleted link gone from API", status == 404)
        if temp_id:
            json_call(f"{base1}/api/admin/links/{temp_id}", "DELETE", token=ADMIN_TOKEN)
        status, data, _ = json_call(f"{base1}/api/admin/reviews", token=ADMIN_TOKEN)
        check("deleting a link purges its review rows",
              status == 200 and not any(r.get("slug") == "temp-delete-me"
                                        for r in data.get("reviews", [])),
              str(data)[:200])

        print("\n=== 8b. Stats + audit log ===")
        status, data, _ = json_call(f"{base1}/api/admin/links", token=ADMIN_TOKEN)
        links_by_slug = {l.get("slug"): l for l in data.get("links", [])}
        panel_stats = (links_by_slug.get("panel-access") or {}).get("stats", {})
        check("page visit recorded", panel_stats.get("visits", 0) >= 1, str(panel_stats))
        check("successful verification recorded", panel_stats.get("verified", 0) >= 1, str(panel_stats))
        check("successful unlock recorded (once)", panel_stats.get("unlocks", 0) >= 1, str(panel_stats))

        # claiming again with the same session must NOT double-count unlocks
        before_unlinks = panel_stats.get("unlocks", 0)
        status, data, _ = json_call(f"{base1}/api/u/panel-access/claim", "POST", {}, cookie=cookie)
        status, data, _ = json_call(f"{base1}/api/admin/links", token=ADMIN_TOKEN)
        links_by_slug = {l.get("slug"): l for l in data.get("links", [])}
        after_unlinks = (links_by_slug.get("panel-access") or {}).get("stats", {}).get("unlocks", 0)
        check("repeat claim does not double-count unlocks", after_unlinks == before_unlinks,
              f"{before_unlinks} -> {after_unlinks}")

        status, data, _ = json_call(f"{base1}/api/admin/events", token=ADMIN_TOKEN)
        check("admin events endpoint -> 200", status == 200 and data.get("ok") is True)
        event_names = [e.get("event") for e in data.get("events", [])]
        check("audit log has link.created", "link.created" in event_names, str(event_names[:8]))
        check("audit log has link.deleted", "link.deleted" in event_names, str(event_names[:8]))
        check("audit log has verify.ok", "verify.ok" in event_names, str(event_names[:8]))
        check("audit log has unlock.claimed", "unlock.claimed" in event_names, str(event_names[:8]))
        check("audit log never stores destinations",
              not any("secret" in json.dumps(e) for e in data.get("events", [])))

        status, data, _ = json_call(f"{base1}/api/admin/events")  # no token
        check("admin events requires auth -> 401", status == 401, f"status={status}")

        print("\n=== 8c. Discord webhook publishing ===")
        # Transport is a channel webhook (no bot token, minimum rights).
        # The embed may carry the unlock URL only — never the destination.
        status, data, _ = json_call(f"{base1}/api/admin/overview", token=ADMIN_TOKEN)
        check("overview reports Discord publishing configured",
              data.get("discord", {}).get("configured") is True, str(data.get("discord")))

        MOCK.discord_payloads.clear()
        status, data, _ = json_call(f"{base1}/api/admin/publish", "POST",
                                    {"id": link1["id"]}, token=ADMIN_TOKEN)
        check("publish to webhook -> 200", status == 200 and data.get("ok") is True,
              f"status={status} {data}")

        check("webhook received the embed", len(MOCK.discord_payloads) >= 1,
              str(len(MOCK.discord_payloads)))
        sent = json.dumps(MOCK.discord_payloads[-1]) if MOCK.discord_payloads else ""
        check("embed points at the unlock page", "/u/panel-access" in sent, sent[:160])
        check("embed never leaks the destination",
              "example.com" not in sent and "v2-secret" not in sent, sent[:240])

        status, data, _ = json_call(f"{base1}/api/admin/events", token=ADMIN_TOKEN)
        event_names = [e.get("event") for e in data.get("events", [])]
        check("successful publish audit-logged", "link.published" in event_names,
              str(event_names[:8]))

        status, data, _ = json_call(f"{base1}/api/admin/publish", "POST",
                                    {"id": link1["id"]})  # no token
        check("publish requires admin auth -> 401", status == 401, f"status={status}")

        status, data, _ = json_call(f"{base1}/api/admin/publish", "POST",
                                    {"id": "does-not-exist"}, token=ADMIN_TOKEN)
        check("publish unknown link rejected", status in (400, 404), f"status={status}")

        MOCK.discord_mode = "fail"
        status, data, _ = json_call(f"{base1}/api/admin/publish", "POST",
                                    {"id": link1["id"]}, token=ADMIN_TOKEN)
        MOCK.discord_mode = "ok"
        check("webhook failure reported -> 502", status == 502, f"status={status} {data}")
        status, data, _ = json_call(f"{base1}/api/admin/events", token=ADMIN_TOKEN)
        event_names = [e.get("event") for e in data.get("events", [])]
        check("failed publish audit-logged", "publish.failed" in event_names,
              str(event_names[:8]))

        print("\n=== 8d. Two-task Subscribe + Like links (OAuth mode) ===")
        # --- creation + validation --------------------------------------
        two_manual = dict(payload, slug="two-manual",
                          destination="https://example.com/two-manual-secret",
                          channelUrl="https://www.youtube.com/@splixtest",
                          videoUrl="https://www.youtube.com/watch?v=TESTVID01",
                          likeMethod="manual")
        status, data, _ = json_call(f"{base1}/api/admin/links", "POST", two_manual,
                                    token=ADMIN_TOKEN)
        check("two-task link created -> 201", status == 201, str(data)[:140])

        bad_vid = dict(two_manual, slug="bad-vid", videoUrl="https://vimeo.com/12345")
        status, data, _ = json_call(f"{base1}/api/admin/links", "POST", bad_vid,
                                    token=ADMIN_TOKEN)
        check("non-YouTube video URL rejected -> 400", status == 400, str(data)[:120])

        bad_chan = dict(two_manual, slug="bad-chan", channelUrl="https://example.com/channel")
        status, data, _ = json_call(f"{base1}/api/admin/links", "POST", bad_chan,
                                    token=ADMIN_TOKEN)
        check("non-YouTube channel URL rejected -> 400", status == 400, str(data)[:120])

        bad_lm = dict(two_manual, slug="bad-lm", likeMethod="auto")
        status, data, _ = json_call(f"{base1}/api/admin/links", "POST", bad_lm,
                                    token=ADMIN_TOKEN)
        check("invalid likeMethod rejected -> 400", status == 400, str(data)[:120])

        status, data, _ = json_call(f"{base1}/api/u/two-manual")
        tasks = data.get("tasks", [])
        check("two tasks exposed (subscribe + like)",
              [t.get("id") for t in tasks] == ["subscribe", "like"], str(tasks)[:200])
        check("task step total is 2",
              data.get("step") == {"total": 2, "done": 0}, str(data.get("step")))
        check("subscribe task auto flag set in OAuth mode",
              bool(tasks) and tasks[0].get("auto") is True, str(tasks[:1]))
        check("two-task config hides destination",
              "two-manual-secret" not in json.dumps(data))

        status, _, body = http(f"{base1}/u/two-manual")
        page2 = body.decode("utf-8", "replace")
        check("two-task page hides destination",
              status == 200 and "two-manual-secret" not in page2)
        check("two-task page totals 2 steps", "0/2" in page2,
              page2[page2.find("stepCount") - 10:][:50])

        # --- review can never substitute for subscribe verification -----
        status, data, _ = json_call(f"{base1}/api/u/two-manual/review", "POST", {})
        check("like review without subscribe session -> 403",
              status == 403 and data.get("error") == "subscribe_verification_required",
              str(data)[:160])

        # --- honor-mode link: full OAuth + like confirmation -------------
        two_honor = dict(payload, slug="two-honor",
                         destination="https://example.com/two-honor-secret",
                         channelUrl="https://www.youtube.com/@splixtest",
                         videoUrl="https://www.youtube.com/watch?v=TESTVID02",
                         likeMethod="honor")
        status, data, _ = json_call(f"{base1}/api/admin/links", "POST", two_honor,
                                    token=ADMIN_TOKEN)
        check("honor-mode link created -> 201", status == 201, str(data)[:140])

        status, data, _ = json_call(f"{base1}/api/u/two-honor/like", "POST",
                                    {"confirm": True})
        check("honor like without session -> 403",
              status == 403 and data.get("error") == "subscribe_required", str(data)[:140])

        MOCK.scenario = "subscribed"
        status, headers, _ = http(f"{base1}/api/u/two-honor/oauth/start")
        auth_url = headers.get("Location", "")
        browser_cookie = first_cookie(headers.get("Set-Cookie", "")) or ""
        status, _, final_body, honor_cookie, chain = follow(auth_url, cookie=browser_cookie)
        check("two-task oauth flow completes", status == 200,
              json.dumps(chain[-2:]) if chain else "no chain")

        status, data, _ = json_call(f"{base1}/api/u/two-honor", cookie=honor_cookie)
        states = {t.get("id"): t.get("state") for t in data.get("tasks", [])}
        check("subscribe verified by API", states.get("subscribe") == "verified", str(states))
        check("like NOT auto-verified (honor unconfirmed)",
              states.get("like") == "unverified", str(states))
        check("partial state until every step is met",
              data.get("state") == "partial" and data.get("step") == {"total": 2, "done": 1},
              f"{data.get('state')} {data.get('step')}")

        status, data, _ = json_call(f"{base1}/api/u/two-honor/claim", "POST", {},
                                    cookie=honor_cookie)
        check("claim blocked at 1/2 -> 403 tasks_incomplete",
              status == 403 and data.get("error") == "tasks_incomplete"
              and data.get("missing") == ["like"], str(data)[:160])

        status, data, _ = json_call(f"{base1}/api/u/two-honor/like", "POST",
                                    {"confirm": True}, cookie=honor_cookie)
        check("honor like confirmed -> 200",
              status == 200 and data.get("state") == "confirmed", str(data)[:140])
        lstate = {t.get("id"): t.get("state") for t in data.get("tasks", [])}
        check("honor confirmation never shown as verified",
              lstate.get("like") == "confirmed", str(lstate))

        status, data, _ = json_call(f"{base1}/api/u/two-honor", cookie=honor_cookie)
        check("both steps done -> state verified 2/2",
              data.get("state") == "verified" and data.get("step") == {"total": 2, "done": 2},
              f"{data.get('state')} {data.get('step')}")

        status, data, _ = json_call(f"{base1}/api/u/two-honor/claim", "POST", {},
                                    cookie=honor_cookie)
        check("claim after subscribe + honor like -> exact destination",
              status == 200 and data.get("url") == "https://example.com/two-honor-secret",
              str(data)[:160])
        status, data, _ = json_call(f"{base1}/api/u/two-honor/claim", "POST", {},
                                    cookie=honor_cookie)
        check("repeated claim still works (session-bound)",
              status == 200 and data.get("url") == "https://example.com/two-honor-secret",
              str(data)[:140])

        status, data, _ = json_call(f"{base1}/api/admin/events", token=ADMIN_TOKEN)
        evnames = [e.get("event") for e in data.get("events", [])]
        check("honor confirmation audit-logged", "task.confirmed" in evnames,
              str(evnames[:12]))
        check("audit log never stores honor destination",
              "two-honor-secret" not in json.dumps(data.get("events", [])[:25]))

        # --- manual-like link: subscribe via API, like via review --------
        MOCK.scenario = "subscribed"
        status, headers, _ = http(f"{base1}/api/u/two-manual/oauth/start")
        auth_url = headers.get("Location", "")
        browser_cookie = first_cookie(headers.get("Set-Cookie", "")) or ""
        status, _, final_body, tm_cookie, chain = follow(auth_url, cookie=browser_cookie)
        check("two-manual oauth flow completes", status == 200,
              json.dumps(chain[-2:]) if chain else "no chain")

        status, data, _ = json_call(f"{base1}/api/u/two-manual/review", "POST", {},
                                    cookie=tm_cookie)
        check("like review allowed after subscribe verified -> 200",
              status == 200 and data.get("state") == "pending", str(data)[:140])
        tm_token = data.get("token", "")

        status, data, _ = json_call(f"{base1}/api/u/two-manual/claim", "POST", {},
                                    cookie=tm_cookie)
        check("cannot claim while like review pending -> 403",
              status == 403 and data.get("error") in ("tasks_incomplete", "not_verified"),
              str(data)[:140])

        status, data, _ = json_call(f"{base1}/api/admin/reviews", token=ADMIN_TOKEN)
        tm_pending = [r for r in data.get("reviews", [])
                      if r.get("slug") == "two-manual" and r.get("state") == "pending"]
        check("admin sees the like review request", len(tm_pending) == 1, str(data)[:200])
        status, data, _ = json_call(
            f"{base1}/api/admin/reviews/{tm_pending[0]['id']}/approve", "POST", {},
            token=ADMIN_TOKEN)
        check("like review approved", status == 200, str(data)[:140])

        status, data, resp = json_call(f"{base1}/api/u/two-manual/review/status", "POST",
                                       {"token": tm_token})
        check("approved like review verifies everything",
              data.get("verified") is True and data.get("state") == "approved", str(data)[:200])
        check("review status reports both task states",
              {t.get("id"): t.get("state") for t in data.get("tasks", [])} ==
              {"subscribe": "verified", "like": "manual_approved"},
              str(data.get("tasks"))[:200])
        tm_cookie2 = first_cookie(resp.get("Set-Cookie", "")) or tm_cookie

        status, data, _ = json_call(f"{base1}/api/u/two-manual/claim", "POST", {},
                                    cookie=tm_cookie2)
        check("claim after API subscribe + approved like -> destination",
              status == 200 and data.get("url") == "https://example.com/two-manual-secret",
              str(data)[:160])

        # honor endpoint must refuse non-honor links
        status, data, _ = json_call(f"{base1}/api/u/two-manual/like", "POST",
                                    {"confirm": True}, cookie=tm_cookie2)
        check("honor confirm refused on review-mode link -> 403",
              status == 403 and data.get("error") == "honor_unavailable", str(data)[:140])

        print("\n=== 9. Manual review mode (no API credentials) ===")
        stop_app(apps[0]); apps[0] = None
        app2_dir = tmp_root / "app2"
        apps[1] = start_app(app_env(APP2_PORT, app2_dir,
                                    GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="",
                                    YOUTUBE_CHANNEL_ID=""))
        base2 = f"http://127.0.0.1:{APP2_PORT}"

        payload3 = dict(payload, slug="manual-link", destination="https://example.com/manual-secret")
        status, data, _ = json_call(f"{base2}/api/admin/links", "POST", payload3, token=ADMIN_TOKEN)
        check("manual-mode link created", status == 201, str(data)[:120])

        status, data, _ = json_call(f"{base2}/api/u/manual-link")
        check("manual mode reported", data.get("mode") == "manual", str(data.get("mode")))
        check("manual mode lists missing credentials",
              bool(data.get("verification", {}).get("missing")), str(data.get("verification")))

        status, _, body = http(f"{base2}/u/manual-link")
        page = body.decode("utf-8", "replace")
        check("manual unlock page renders", status == 200)
        check("manual page hides destination", "manual-secret" not in page)
        check("manual page marks mode", 'data-mode="manual"' in page)

        status, data, _ = json_call(f"{base2}/api/u/manual-link/review", "POST", {})
        check("review request accepted -> token", status == 200 and data.get("state") == "pending",
              str(data)[:160])
        review_token = data.get("token", "")

        status, data, _ = json_call(f"{base2}/api/u/manual-link/review/status", "POST",
                                    {"token": review_token})
        check("pending review not verified", status == 200 and data.get("verified") is False
              and data.get("state") == "pending", str(data))

        status, data, _ = json_call(f"{base2}/api/u/manual-link/claim", "POST", {})
        check("cannot claim while review pending -> 403", status == 403, f"status={status}")

        # admin sees it and approves
        status, data, _ = json_call(f"{base2}/api/admin/reviews", token=ADMIN_TOKEN)
        pending = [r for r in data.get("reviews", []) if r.get("state") == "pending"]
        check("admin sees pending review", len(pending) == 1, str(data)[:200])

        # reject first on a second request to test rejection path
        status, data2, _ = json_call(f"{base2}/api/u/manual-link/review", "POST", {"token": review_token})
        # same token -> same record (idempotent)
        check("repeat review request reuses token", data2.get("token") == review_token)

        review_id = pending[0]["id"]
        status, data, _ = json_call(f"{base2}/api/admin/reviews/{review_id}/approve", "POST", {},
                                    token=ADMIN_TOKEN)
        check("admin approves review", status == 200 and data.get("review", {}).get("state") == "approved",
              str(data)[:160])

        status, data, resp = json_call(f"{base2}/api/u/manual-link/review/status", "POST",
                                       {"token": review_token})
        check("approval issues session cookie",
              data.get("verified") is True and bool(resp.get("Set-Cookie")),
              f"{data} | {resp.get('Set-Cookie','')}")
        manual_cookie = first_cookie(resp.get("Set-Cookie", ""))

        status, data, _ = json_call(f"{base2}/api/u/manual-link/claim", "POST", {}, cookie=manual_cookie)
        check("claim after approval -> destination",
              status == 200 and data.get("url") == "https://example.com/manual-secret",
              str(data)[:160])

        # manual approval must be counted as a verification + an unlock
        status, data, _ = json_call(f"{base2}/api/admin/links", token=ADMIN_TOKEN)
        mstats = {l.get("slug"): l.get("stats", {}) for l in data.get("links", [])}.get("manual-link", {})
        check("manual approval recorded as verification", mstats.get("verified", 0) >= 1, str(mstats))
        check("manual unlock recorded", mstats.get("unlocks", 0) >= 1, str(mstats))

        status, data, _ = json_call(f"{base2}/api/admin/events", token=ADMIN_TOKEN)
        ev2 = [e.get("event") for e in data.get("events", [])]
        check("manual approval audit-logged", "review.approved" in ev2, str(ev2[:8]))

        # rejection path on a second link
        payload4 = dict(payload, slug="manual-link-2", destination="https://example.com/reject-secret")
        json_call(f"{base2}/api/admin/links", "POST", payload4, token=ADMIN_TOKEN)
        status, data, _ = json_call(f"{base2}/api/u/manual-link-2/review", "POST", {})
        token2 = data.get("token", "")
        status, data, _ = json_call(f"{base2}/api/admin/reviews", token=ADMIN_TOKEN)
        pending2 = [r for r in data.get("reviews", []) if r.get("state") == "pending" and r.get("slug") == "manual-link-2"]
        json_call(f"{base2}/api/admin/reviews/{pending2[0]['id']}/reject", "POST", {}, token=ADMIN_TOKEN)
        status, data, resp = json_call(f"{base2}/api/u/manual-link-2/review/status", "POST", {"token": token2})
        check("rejected review reported + not verified",
              data.get("state") == "rejected" and data.get("verified") is False and not resp.get("Set-Cookie"),
              str(data))
        status, data, _ = json_call(f"{base2}/api/u/manual-link-2/claim", "POST", {})
        check("rejected review cannot claim -> 403", status == 403)

        # settings update -> footer changes
        status, data, _ = json_call(f"{base2}/api/admin/settings", "PUT",
                                    {"siteName": "TEST BRAND", "email": "hi@test.dev"},
                                    token=ADMIN_TOKEN)
        check("settings updated", status == 200 and data["settings"]["siteName"] == "TEST BRAND")
        status, _, body = http(f"{base2}/u/manual-link")
        check("footer reflects new settings", b"TEST BRAND" in body and b"hi@test.dev" in body)

        print("\n=== 9b. Two-task links in manual mode (review + honor fallback) ===")
        two_m = dict(payload, slug="mt-manual",
                     destination="https://example.com/mt-manual-secret",
                     channelUrl="https://www.youtube.com/@splixtest",
                     videoUrl="https://www.youtube.com/watch?v=TESTVID03",
                     likeMethod="manual")
        status, data, _ = json_call(f"{base2}/api/admin/links", "POST", two_m,
                                    token=ADMIN_TOKEN)
        check("manual two-task link created", status == 201, str(data)[:120])

        status, data, _ = json_call(f"{base2}/api/u/mt-manual")
        check("manual two-task starts at 0/2",
              data.get("step") == {"total": 2, "done": 0}, str(data.get("step")))
        check("manual two-task hides destination",
              "mt-manual-secret" not in json.dumps(data))

        status, data, _ = json_call(f"{base2}/api/u/mt-manual/review", "POST", {})
        mt_token = data.get("token", "")
        check("manual two-task review accepted",
              status == 200 and data.get("state") == "pending", str(data)[:140])

        status, data, _ = json_call(f"{base2}/api/u/mt-manual/claim", "POST", {})
        check("no claim while review pending -> 403", status == 403, f"status={status}")

        status, data, _ = json_call(f"{base2}/api/admin/reviews", token=ADMIN_TOKEN)
        mt_pend = [r for r in data.get("reviews", [])
                   if r.get("slug") == "mt-manual" and r.get("state") == "pending"]
        check("manual two-task review visible to admin", len(mt_pend) == 1, str(data)[:200])
        status, data, _ = json_call(
            f"{base2}/api/admin/reviews/{mt_pend[0]['id']}/approve", "POST", {},
            token=ADMIN_TOKEN)
        check("manual two-task review approved", status == 200, str(data)[:140])

        status, data, resp = json_call(f"{base2}/api/u/mt-manual/review/status", "POST",
                                       {"token": mt_token})
        check("approval grants both tasks as moderator review",
              {t.get("id"): t.get("state") for t in data.get("tasks", [])} ==
              {"subscribe": "manual_approved", "like": "manual_approved"},
              str(data.get("tasks"))[:200])
        mt_cookie = first_cookie(resp.get("Set-Cookie", "")) or ""

        status, data, _ = json_call(f"{base2}/api/u/mt-manual/claim", "POST", {},
                                    cookie=mt_cookie)
        check("manual two-task claim -> destination",
              status == 200 and data.get("url") == "https://example.com/mt-manual-secret",
              str(data)[:160])
        json_call(f"{base2}/api/u/mt-manual/claim", "POST", {}, cookie=mt_cookie)
        status, data, _ = json_call(f"{base2}/api/admin/links", token=ADMIN_TOKEN)
        mstats2 = {l.get("slug"): l.get("stats", {})
                   for l in data.get("links", [])}.get("mt-manual", {})
        check("two-task unlock counted once", mstats2.get("unlocks", 0) == 1, str(mstats2))

        # --- honor flow in manual mode -----------------------------------
        two_h = dict(two_m, slug="mt-honor",
                     destination="https://example.com/mt-honor-secret",
                     likeMethod="honor")
        status, data, _ = json_call(f"{base2}/api/admin/links", "POST", two_h,
                                    token=ADMIN_TOKEN)
        check("manual honor link created", status == 201, str(data)[:120])

        status, data, _ = json_call(f"{base2}/api/u/mt-honor/like", "POST",
                                    {"confirm": True})
        check("honor like before approval -> 403",
              status == 403 and data.get("error") == "subscribe_required", str(data)[:140])

        status, data, _ = json_call(f"{base2}/api/u/mt-honor/review", "POST", {})
        th_token = data.get("token", "")
        status, data, _ = json_call(f"{base2}/api/admin/reviews", token=ADMIN_TOKEN)
        th_pend = [r for r in data.get("reviews", [])
                   if r.get("slug") == "mt-honor" and r.get("state") == "pending"]
        json_call(f"{base2}/api/admin/reviews/{th_pend[0]['id']}/approve", "POST", {},
                  token=ADMIN_TOKEN)
        status, data, resp = json_call(f"{base2}/api/u/mt-honor/review/status", "POST",
                                       {"token": th_token})
        th_cookie = first_cookie(resp.get("Set-Cookie", "")) or ""
        check("honor approval leaves like for the visitor to confirm",
              data.get("verified") is False and data.get("state") == "approved",
              str(data)[:200])

        status, data, _ = json_call(f"{base2}/api/u/mt-honor", cookie=th_cookie)
        check("honor link: subscribe approved, like still open (1/2)",
              data.get("step") == {"total": 2, "done": 1}, str(data.get("step")))

        status, data, _ = json_call(f"{base2}/api/u/mt-honor/claim", "POST", {},
                                    cookie=th_cookie)
        check("honor link claim blocked until confirmation -> 403",
              status == 403 and data.get("error") == "tasks_incomplete", str(data)[:140])

        status, data, _ = json_call(f"{base2}/api/u/mt-honor/like", "POST",
                                    {"confirm": True}, cookie=th_cookie)
        check("honor like confirm after approval -> 200",
              status == 200 and data.get("state") == "confirmed", str(data)[:140])
        check("honor confirm marks like confirmed, never verified",
              {t.get("id"): t.get("state") for t in data.get("tasks", [])}.get("like")
              == "confirmed", str(data.get("tasks"))[:200])

        status, data, _ = json_call(f"{base2}/api/u/mt-honor", cookie=th_cookie)
        check("honor link now verified 2/2",
              data.get("state") == "verified" and data.get("step") == {"total": 2, "done": 2},
              f"{data.get('state')} {data.get('step')}")
        check("honor config hides destination", "mt-honor-secret" not in json.dumps(data))
        status, data, _ = json_call(f"{base2}/api/u/mt-honor/claim", "POST", {},
                                    cookie=th_cookie)
        check("honor claim -> exact destination",
              status == 200 and data.get("url") == "https://example.com/mt-honor-secret",
              str(data)[:140])

        status, data, _ = json_call(f"{base2}/api/admin/events", token=ADMIN_TOKEN)
        ev3 = [e.get("event") for e in data.get("events", [])]
        check("manual-mode honor confirmation audit-logged",
              "task.confirmed" in ev3, str(ev3[:12]))

        # --- like endpoint guards ----------------------------------------
        status, data, _ = json_call(f"{base2}/api/u/mt-manual/like", "POST",
                                    {"confirm": True}, cookie=mt_cookie)
        check("like confirm refused when policy is manual review -> 403",
              status == 403 and data.get("error") == "honor_unavailable", str(data)[:140])
        status, data, _ = json_call(f"{base2}/api/u/manual-link/like", "POST",
                                    {"confirm": True})
        check("like confirm on subscribe-only link -> 404",
              status == 404 and "video" in str(data.get("message", "")), str(data)[:140])

        # --- deleted two-task link purges reviews + kills the page --------
        two_del = dict(two_m, slug="mt-del",
                       destination="https://example.com/mt-del-secret")
        status, data, _ = json_call(f"{base2}/api/admin/links", "POST", two_del,
                                    token=ADMIN_TOKEN)
        del_id = (data.get("link") or {}).get("id", "")
        json_call(f"{base2}/api/u/mt-del/review", "POST", {})
        status, data, _ = json_call(f"{base2}/api/admin/links/{del_id}", "DELETE",
                                    token=ADMIN_TOKEN)
        check("two-task link deleted", status == 200, str(data)[:120])
        status, data, _ = json_call(f"{base2}/api/admin/reviews", token=ADMIN_TOKEN)
        check("deleted two-task link purged its review",
              not [r for r in data.get("reviews", []) if r.get("slug") == "mt-del"],
              str(data)[:200])
        status, data, _ = json_call(f"{base2}/api/u/mt-del/claim", "POST", {})
        check("deleted two-task link cannot be claimed -> 404", status == 404,
              f"status={status}")

        print("\n=== 10. Expiring reviews + rate limits + disabled admin ===")
        stop_app(apps[1]); apps[1] = None
        app3_dir = tmp_root / "app3"
        app3_dir.mkdir(parents=True, exist_ok=True)
        # Pre-seed one link directly in the store (admin API is disabled here).
        seeded_link = {
            "id": "seed00000001",
            "slug": "ttl-link",
            "title": "TTL Test Link",
            "description": "COMPLETE ALL THE STEPS BELOW",
            "destination": "https://example.com/ttl-secret",
            "actionLabel": "Subscribe Channel",
            "actionUrl": "https://www.youtube.com/channel/UC_TEST?sub_confirmation=1",
            "actionType": "youtube_subscribe",
            "enabled": True,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        }
        (app3_dir / "links.json").write_text(json.dumps([seeded_link]), encoding="utf-8")

        apps[2] = start_app(app_env(APP3_PORT, app3_dir,
                                    GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="",
                                    YOUTUBE_CHANNEL_ID="",
                                    ADMIN_TOKEN="",          # admin disabled
                                    REVIEW_TTL_MIN="0",      # reviews expire instantly
                                    RL_OAUTH="3", RL_API="5000", RL_STATUS="5000",
                                    RL_REVIEW="5000", RL_CLAIM="5000", RL_ADMIN="5000"))
        base3 = f"http://127.0.0.1:{APP3_PORT}"

        status, data, _ = json_call(f"{base3}/api/admin/overview", token="anything")
        check("admin disabled without ADMIN_TOKEN -> 503",
              status == 503 and data.get("error") == "admin_disabled", f"status={status} {data}")

        status, data, _ = json_call(f"{base3}/api/u/no-link/review", "POST", {})
        check("review on unknown slug -> 404", status == 404, f"status={status}")

        status, data, _ = json_call(f"{base3}/api/u/ttl-link/review", "POST", {})
        check("review created (TTL 0)", status == 200 and data.get("state") == "pending", str(data)[:140])
        ttl_token = data.get("token", "")
        status, data, resp = json_call(f"{base3}/api/u/ttl-link/review/status", "POST", {"token": ttl_token})
        check("expired review reported expired + no cookie",
              data.get("state") == "expired" and not resp.get("Set-Cookie"), str(data))
        status, data, _ = json_call(f"{base3}/api/u/ttl-link/claim", "POST", {})
        check("expired review cannot claim -> 403", status == 403)

        # rate limit on oauth start (the limiter runs before anything else)
        statuses, retry_headers = [], []
        for _ in range(6):
            s, h, _b = http(f"{base3}/api/u/ttl-link/oauth/start")
            statuses.append(s)
            if s == 429:
                retry_headers.append(h.get("Retry-After"))
        check("oauth start rate limited (429 after limit)", 429 in statuses, str(statuses))
        check("429 carries Retry-After", all(r and r.isdigit() for r in retry_headers) and retry_headers,
              str(retry_headers))

        # 10b. HTTP/1.1 keep-alive integrity: an early response that never
        #      reads the request body (403 hits before read_json) must still
        #      leave the socket clean — otherwise the leftover body bytes are
        #      parsed as the next request ("Unsupported method ('{}GET')").
        raw_sock = socket.create_connection(("127.0.0.1", APP3_PORT), timeout=10)
        raw_in = raw_sock.makefile("rb")
        kick_body = b"{}"
        raw_sock.sendall(
            b"POST /api/u/ttl-link/claim HTTP/1.1\r\n"
            b"Host: 127.0.0.1:%d\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: %d\r\n"
            b"Connection: keep-alive\r\n\r\n" % (APP3_PORT, len(kick_body)) + kick_body
        )
        first_status = raw_in.readline().decode("latin-1", "replace").strip()
        first_len = 0
        while True:
            hdr_line = raw_in.readline()
            if hdr_line in (b"\r\n", b"\n", b""):
                break
            if hdr_line.lower().startswith(b"content-length:"):
                first_len = int(hdr_line.split(b":", 1)[1].strip() or 0)
        if first_len:
            raw_in.read(first_len)
        check("keep-alive: claim without session -> 403", "403" in first_status, first_status)

        # second request on the SAME socket must parse cleanly
        raw_sock.sendall(
            b"GET /api/u/ttl-link/status HTTP/1.1\r\n"
            b"Host: 127.0.0.1:%d\r\n"
            b"Connection: close\r\n\r\n" % APP3_PORT
        )
        second_status = raw_in.readline().decode("latin-1", "replace").strip()
        check("keep-alive: next request on same socket parses (unread body drained)",
              second_status.startswith("HTTP/1.1 200"), second_status)
        raw_sock.close()

        # 10c. Drain state must reset PER REQUEST: request 1 here READS its
        #      body (400 invalid JSON sets the flag), request 2 still has to
        #      drain its UNREAD 403 body, request 3 must parse cleanly.
        #      (This is the exact sequence a keep-alive HTTP client pool
        #      produces — stale state would leave "{}" on the socket.)
        raw2 = socket.create_connection(("127.0.0.1", APP3_PORT), timeout=10)
        f2 = raw2.makefile("rb")

        def _roundtrip(sock, fh, request_line, payload):
            sock.sendall(
                request_line
                + b"Host: 127.0.0.1:%d\r\n" % (APP3_PORT,)
                + b"Content-Type: application/json\r\n"
                + b"Content-Length: %d\r\n" % (len(payload),)
                + b"Connection: keep-alive\r\n\r\n"
                + payload
            )
            status = fh.readline().decode("latin-1", "replace").strip()
            clen = 0
            while True:
                line = fh.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                if line.lower().startswith(b"content-length:"):
                    clen = int(line.split(b":", 1)[1].strip() or 0)
            body = fh.read(clen) if clen else b""
            return status, body

        p1_status, p1_body = _roundtrip(
            raw2, f2, b"POST /api/u/ttl-link/review HTTP/1.1\r\n", b"not-json")
        p2_status, _ = _roundtrip(
            raw2, f2, b"POST /api/u/ttl-link/claim HTTP/1.1\r\n", b"{}")
        raw2.sendall(
            b"GET /api/u/ttl-link/status HTTP/1.1\r\n"
            b"Host: 127.0.0.1:%d\r\nConnection: close\r\n\r\n" % (APP3_PORT,)
        )
        p3_status = f2.readline().decode("latin-1", "replace").strip()
        raw2.close()
        check("keep-alive: per-request state reset (400-read then 403-unread then GET clean)",
              p1_status.startswith("HTTP/1.1 400") and b"valid JSON" in p1_body
              and p2_status.startswith("HTTP/1.1 403")
              and p3_status.startswith("HTTP/1.1 200"),
              f"p1={p1_status} p2={p2_status} p3={p3_status}")

        print("\n=== 11. UI files present & wired correctly ===")
        for rel in ["unlock-admin.html", "css/unlock.css", "js/unlock.js", "js/admin.js",
                    "server/templates/unlock.html", "server/.env.example", "server/run.py",
                    "server/deploy.py", "server/migrate_data.py", "server/requirements.txt",
                    "render.yaml"]:
            check(f"{rel} exists", (ROOT / rel).is_file())
        admin_html = (ROOT / "unlock-admin.html").read_text(encoding="utf-8")
        check("admin page references admin.js", "js/admin.js" in admin_html)
        check("admin page is noindex", "noindex" in admin_html)
        unlock_js = (ROOT / "js" / "unlock.js").read_text(encoding="utf-8")
        check("unlock.js has no hardcoded URLs and unlocks only via /claim",
              "/claim" in unlock_js and "http://" not in unlock_js and "https://" not in unlock_js)

        # ============================================================
        print("\n=== 12. Deployment readiness (host/port, logs, health, storage) ===")

        # --- 12a. bind/port/public-origin resolution (unit) ----------
        check("deploy: local fallback binds loopback",
              deploy_mod.resolve_host({}, platform_port=False) == "127.0.0.1")
        check("deploy: platform-exported PORT -> 0.0.0.0 bind",
              deploy_mod.resolve_host({}, platform_port=True) == "0.0.0.0")
        check("deploy: PORT in the real environment without .env -> 0.0.0.0",
              deploy_mod.resolve_host({"PORT": "10007"}) == "0.0.0.0")
        check("deploy: platform hint (RENDER) -> 0.0.0.0",
              deploy_mod.resolve_host({"RENDER": "true"}) == "0.0.0.0")
        check("deploy: explicit HOST always wins",
              deploy_mod.resolve_host({"HOST": "10.1.2.3", "RENDER": "true"},
                                      platform_port=True) == "10.1.2.3")
        check("deploy: PORT fallback is 8099", deploy_mod.resolve_port({}) == 8099)
        check("deploy: platform PORT used when valid",
              deploy_mod.resolve_port({"PORT": "10007"}) == 10007)
        check("deploy: invalid PORT falls back to 8099",
              deploy_mod.resolve_port({"PORT": "abc"}) == 8099)
        check("deploy: PUBLIC_URL wins as public origin (trailing / stripped)",
              deploy_mod.default_public_url({"PUBLIC_URL": "https://a.example/"})
              == "https://a.example")
        check("deploy: platform external URL used until PUBLIC_URL is set",
              deploy_mod.default_public_url({"RENDER_EXTERNAL_URL": "https://b.example"})
              == "https://b.example")
        check("deploy: local public-origin fallback",
              deploy_mod.default_public_url({}).startswith("http://127.0.0.1:"))

        # --- 12b. secret-safe logging + HTTPS-aware cookies (unit) ---
        redacted = deploy_mod.redact(
            '"GET /api/oauth/callback?state=SECSTATE&code=SECCODE&v=1 HTTP/1.1" 400 -')
        check("logs: OAuth state + code masked in request lines",
              "SECSTATE" not in redacted and "SECCODE" not in redacted
              and "state=***" in redacted and "code=***" in redacted, redacted)
        check("logs: path, status and harmless params stay readable",
              "/api/oauth/callback" in redacted and "v=1" in redacted
              and "HTTP/1.1" in redacted and "400" in redacted, redacted)
        check("logs: review/token query values masked",
              "REVIEWTOK" not in deploy_mod.redact("POST /x?token=REVIEWTOK&slug=demo"))
        check("cookies: Secure under https PUBLIC_URL",
              deploy_mod.cookie_secure("https://x.example"))
        check("cookies: Secure via trusted X-Forwarded-Proto",
              deploy_mod.cookie_secure("http://x.example", "https", True))
        check("cookies: forged X-Forwarded-Proto ignored without TRUST_PROXY",
              not deploy_mod.cookie_secure("http://x.example", "https", False))
        check("cookies: plain HTTP origin stays non-Secure",
              not deploy_mod.cookie_secure("http://x.example", "", True))
        check("proxy: rightmost X-Forwarded-For hop wins (spoof-proof)",
              deploy_mod.forwarded_client_ip("FAKE, 203.0.113.9", "0.0.0.0")
              == "203.0.113.9")
        check("proxy: empty header falls back to the socket address",
              deploy_mod.forwarded_client_ip("", "192.0.2.1") == "192.0.2.1")

        # --- 12c. SEED_DIR first-boot migration (unit) ---------------
        seed_src = tmp_root / "seed_unit_src"
        seed_dst = tmp_root / "seed_unit_dst"
        seed_src.mkdir(parents=True, exist_ok=True)
        (seed_src / "links.json").write_text(
            json.dumps([{"id": "unit01", "slug": "unit-link"}]), encoding="utf-8")
        (seed_src / "reviews.json").write_text("{BROKEN JSON", encoding="utf-8")
        (seed_src / "events.jsonl").write_text(
            '{"ts":"2026-01-01T00:00:00Z","event":"link.created"}\n', encoding="utf-8")
        (seed_src / "notes.txt").write_text("not a data file", encoding="utf-8")
        seeded = deploy_mod.seed_data_dir(seed_src, seed_dst)
        check("seed: valid data files copied into an empty data folder",
              sorted(seeded) == ["events.jsonl", "links.json"], str(seeded))
        check("seed: invalid JSON skipped instead of corrupting the store",
              not (seed_dst / "reviews.json").exists())
        check("seed: unknown files are ignored",
              not (seed_dst / "notes.txt").exists())
        (seed_dst / "links.json").write_text('[{"existing": true}]', encoding="utf-8")
        reseeded = deploy_mod.seed_data_dir(seed_src, seed_dst)
        check("seed: existing data is never overwritten",
              reseeded == [] and json.loads(
                  (seed_dst / "links.json").read_text(encoding="utf-8"))
              == [{"existing": True}], str(reseeded))
        check("seed: missing seed dir is a no-op",
              deploy_mod.seed_data_dir(tmp_root / "does-not-exist", seed_dst) == [])

        # --- 12d. migrate_data.py backup/verify/pack/restore (CLI) ---
        cli_data = tmp_root / "cli_data"
        cli_data.mkdir(parents=True, exist_ok=True)
        (cli_data / "links.json").write_text(json.dumps([{"id": "cli1"}]), encoding="utf-8")
        (cli_data / "events.jsonl").write_text(
            '{"event":"a"}\n{"event":"b"}\n', encoding="utf-8")

        def _cli(*args):
            return subprocess.run(
                [sys.executable, str(SERVER_DIR / "migrate_data.py"), *args],
                capture_output=True, text=True, timeout=60, check=False)

        r = _cli("verify", "--data", str(cli_data))
        check("migrate_data verify passes on valid data",
              r.returncode == 0 and "ok (2 records" in r.stdout, r.stdout[-300:])
        r_real = _cli("verify", "--data", str(SERVER_DIR / "data"))
        real_slugs: list = []
        try:
            real_slugs = [
                str(item.get("slug", "")) for item in json.loads(
                    (SERVER_DIR / "data" / "links.json").read_text(encoding="utf-8"))
            ]
        except Exception:
            pass
        leaked = [s for s in real_slugs if s and s in r_real.stdout]
        check("verify on live data passes and prints counts only (no slugs/URLs)",
              r_real.returncode == 0 and not leaked and "http" not in r_real.stdout,
              r_real.stdout[-300:])
        cli_backup = tmp_root / "cli_backup"
        r = _cli("backup", "--data", str(cli_data),
                 "--out", str(cli_backup), "--note", "unittest")
        check("migrate_data backup writes and re-verifies its copy",
              r.returncode == 0 and (cli_backup / "links.json").is_file()
              and "backup written" in r.stdout, r.stdout[-300:])
        (cli_data / "links.json").write_text("{now corrupt", encoding="utf-8")
        r = _cli("verify", "--data", str(cli_data))
        check("verify fails (exit 1) on corruption",
              r.returncode == 1 and "CORRUPT" in r.stdout, r.stdout[-300:])
        r = _cli("pack", "--data", str(cli_data),
                 "--out", str(tmp_root / "cli_pack"))
        check("pack refuses corrupt data",
              r.returncode != 0 and "corrupt" in (r.stdout + r.stderr).lower(),
              (r.stdout + r.stderr)[-300:])
        r = _cli("restore", str(cli_backup), "--data", str(cli_data))
        check("restore refused without --yes (destructive guard)",
              r.returncode == 2, f"rc={r.returncode} {r.stdout[-200:]}")
        r = _cli("restore", str(cli_backup), "--data", str(cli_data), "--yes")
        try:
            restored_ok = json.loads(
                (cli_data / "links.json").read_text(encoding="utf-8")) == [{"id": "cli1"}]
        except Exception:
            restored_ok = False
        check("restore --yes recovers the verified backup",
              r.returncode == 0 and restored_ok, (r.stdout + r.stderr)[-300:])
        safety_dirs = (list((tmp_root / "backups").glob("*_pre_restore"))
                       if (tmp_root / "backups").is_dir() else [])
        check("restore snapshots current data first (undo path)",
              bool(safety_dirs) and (safety_dirs[0] / "links.json").is_file(),
              str(safety_dirs))

        # --- 12e. git hygiene + hosting blueprint sanity -------------
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        check(".gitignore keeps .env, data/, backups/ and seed packs out of git",
              all(p in gitignore for p in
                  ("server/.env", "server/data/", "server/backups/", "deploy-seed/")))
        render_yaml = (ROOT / "render.yaml").read_text(encoding="utf-8")
        check("render.yaml has the real build/start/health commands",
              "pip install -r server/requirements.txt" in render_yaml
              and "python server/run.py" in render_yaml
              and "healthCheckPath: /health" in render_yaml)
        check("render.yaml stores no secrets (token generated, Google/Discord dashboard-only)",
              "generateValue: true" in render_yaml
              and "key: ADMIN_TOKEN" in render_yaml
              and "key: GOOGLE_CLIENT" not in render_yaml
              and "key: DISCORD_WEBHOOK" not in render_yaml)
        check("render.yaml points DATA_DIR at the persistent disk",
              "DATA_DIR" in render_yaml and "/var/data" in render_yaml)
        check("render.yaml uses Render's schema-valid singular disk block",
              re.search(r"(?m)^    disk\s*:\s*$", render_yaml) is not None
              and re.search(r"(?m)^\s+disks\s*:", render_yaml) is None
              and "mountPath: /var/data" in render_yaml
              and "sizeGB: 1" in render_yaml)
        env_example = (ROOT / "server" / ".env.example").read_text(encoding="utf-8")
        check(".env.example documents DATA_DIR / SEED_DIR / TRUST_PROXY",
              all(v in env_example for v in ("DATA_DIR", "SEED_DIR", "TRUST_PROXY")))
        check(".env.example keeps every secret placeholder empty",
              all(re.search(rf"(?m)^{key}=$", env_example)
                  for key in ("ADMIN_TOKEN", "GOOGLE_CLIENT_ID",
                              "GOOGLE_CLIENT_SECRET", "DISCORD_WEBHOOK_URL")))

        # --- 12f. live deployment instance (https + proxy + seed) ----
        seed_live = tmp_root / "seed_live"
        seed_live.mkdir(parents=True, exist_ok=True)
        (seed_live / "links.json").write_text(json.dumps([{
            "id": "seed0000000a",
            "slug": "seed-secure",
            "title": "Seeded Secure Link",
            "description": "COMPLETE ALL THE STEPS BELOW",
            "destination": "https://example.com/seed-secure-secret",
            "actionLabel": "Subscribe Channel",
            "actionUrl": "https://www.youtube.com/channel/UC_TEST?sub_confirmation=1",
            "actionType": "youtube_subscribe",
            "channelUrl": "https://www.youtube.com/channel/UC_TEST",
            "videoUrl": "https://www.youtube.com/watch?v=abc12345678",
            "likeMethod": "honor",
            "enabled": True,
            "createdAt": "2026-01-01T00:00:00Z",
            "updatedAt": "2026-01-01T00:00:00Z",
        }]), encoding="utf-8")
        (seed_live / "settings.json").write_text(
            json.dumps({"siteName": "SPLIX X CHEAT"}), encoding="utf-8")
        (seed_live / "events.jsonl").write_text(
            '{"ts":"2026-01-01T00:00:00Z","event":"link.created","slug":"seed-secure"}\n',
            encoding="utf-8")
        (seed_live / "reviews.json").write_text("<definitely not json>", encoding="utf-8")

        app4_dir = tmp_root / "app4"
        apps[3] = start_app(app_env(
            APP4_PORT, app4_dir,
            PUBLIC_URL="https://splix.test",
            GOOGLE_CLIENT_ID="", GOOGLE_CLIENT_SECRET="", YOUTUBE_CHANNEL_ID="",
            TRUST_PROXY="true",
            SEED_DIR=str(seed_live)))
        base4 = f"http://127.0.0.1:{APP4_PORT}"

        status, data, resp = json_call(f"{base4}/health")
        h_hdr = next((v for k, v in resp.items()
                      if k.lower() == "strict-transport-security"), "")
        check("health endpoint answers 200 healthy",
              status == 200 and data.get("ok") is True and data.get("status") == "healthy",
              f"status={status} {str(data)[:120]}")
        check("health payload exposes no config/secrets/destinations",
              not ({"publicUrl", "adminToken", "destination", "url",
                    "token", "counts"} & set(data)), str(sorted(data)))
        check("HTTPS deployment (https PUBLIC_URL) sends HSTS",
              h_hdr.startswith("max-age="), str(h_hdr))
        # App1 was already retired at section 9; app3 is the plain-HTTP
        # instance still running (PUBLIC_URL http -> no HSTS header).
        try:
            status, resp3, _ = http(f"{base3}/health")
        except Exception as exc:
            # Diagnosis: if the instance died mid-suite, show its exit
            # code and captured output tail so the root cause is visible
            # right in the failure detail.
            tail = "".join(list(getattr(apps[2], "log_lines", None) or [])[-20:])
            check("plain-HTTP instance sends no HSTS and still answers health",
                  False, f"app3 unreachable ({exc}); poll={apps[2].poll()}; "
                         f"log tail: {tail[-800:]}")
        else:
            h3 = next((v for k, v in resp3.items()
                       if k.lower() == "strict-transport-security"), "")
            check("plain-HTTP instance sends no HSTS and still answers health",
                  status == 200 and not h3, str(h3))

        # --- SEED_DIR migration on first boot ------------------------
        status, data, _ = json_call(f"{base4}/api/u/seed-secure/status")
        check("SEED_DIR: seeded link is live after first boot",
              status == 200 and data.get("slug") == "seed-secure",
              f"status={status} {str(data)[:120]}")
        status, data, _ = json_call(f"{base4}/api/u/seed-secure")
        tasks = data.get("tasks") or []
        check("SEED_DIR: seeded two-task config, destination still secret",
              status == 200 and len(tasks) == 2
              and "seed-secure-secret" not in json.dumps(data),
              f"status={status} {str(data)[:160]}")
        status, data, _ = json_call(f"{base4}/api/admin/links", token=ADMIN_TOKEN)
        slugs = [lk.get("slug") for lk in (data.get("links") or [])]
        check("SEED_DIR: admin sees exactly the seeded link",
              status == 200 and slugs == ["seed-secure"], str(slugs))
        check("SEED_DIR: invalid seed reviews.json skipped, valid files created",
              not (app4_dir / "reviews.json").exists()
              and (app4_dir / "links.json").is_file()
              and (app4_dir / "events.jsonl").is_file())
        status, data, _ = json_call(f"{base4}/api/admin/reviews", token=ADMIN_TOKEN)
        check("review queue works with the skipped (clean) seed",
              status == 200 and data.get("reviews") == [], str(data)[:140])

        # --- Secure cookies + honest partial state on an https origin -
        status, data, _ = json_call(f"{base4}/api/u/seed-secure/review", "POST", {})
        check("review request accepted (manual-mode instance)",
              status == 200 and data.get("token"),
              f"status={status} {str(data)[:140]}")
        rv_token = data.get("token", "")
        status, data, _ = json_call(f"{base4}/api/admin/reviews", token=ADMIN_TOKEN)
        rv_id = ((data.get("reviews") or [{}])[0]).get("id", "")
        status, data, _ = json_call(
            f"{base4}/api/admin/reviews/{rv_id}/approve", "POST", {}, token=ADMIN_TOKEN)
        check("admin approval on the seeded link succeeds",
              status == 200, f"status={status} {str(data)[:140]}")
        status, data, resp = json_call(
            f"{base4}/api/u/seed-secure/review/status", "POST", {"token": rv_token})
        set_cookie = next((v for k, v in resp.items() if k.lower() == "set-cookie"), "")
        check("session cookie carries Secure + HttpOnly on an https origin",
              status == 200 and "Secure" in set_cookie and "HttpOnly" in set_cookie,
              set_cookie[:180])
        check("honor-policy like stays unconfirmed after approval (partial, not verified)",
              data.get("state") == "approved" and data.get("verified") is False,
              str(data)[:200])

        # --- access-log redaction proven against a real instance -----
        http(f"{base4}/api/oauth/callback?state=LEAKSTATE1&code=LEAKCODE2&v=1")
        # Bounded wait until the request line has been written to the
        # app's captured stdout (the response alone doesn't guarantee it).
        log_text = ""
        for _ in range(40):
            time.sleep(0.15)
            log_text = "".join(getattr(apps[3], "log_lines", None) or [])
            if "/api/oauth/callback" in log_text:
                break
        check("real access log masks OAuth state/code values",
              "LEAKSTATE1" not in log_text and "LEAKCODE2" not in log_text,
              log_text[-400:])
        check("real access log still shows the path (query redacted only)",
              "/api/oauth/callback" in log_text and "state=***" in log_text
              and "code=***" in log_text, log_text[-400:])

        # --- secrets/source never served statically ------------------
        status, _, _ = http(f"{base4}/server/.env")
        check("server/.env is never served (404)", status == 404, f"status={status}")
        status, _, _ = http(f"{base4}/.env")
        check("dotfiles at the root are never served (404)", status == 404, f"status={status}")
        status, _, _ = http(f"{base4}/server/app.py")
        check("server source is never served (404)", status == 404, f"status={status}")

        check("DATA_DIR holds all state (platform-mount pattern)",
              (app4_dir / "links.json").is_file()
              and (app4_dir / "sessions.json").is_file()
              and (app4_dir / "events.jsonl").is_file())
        stop_app(apps[3]); apps[3] = None

    finally:
        for proc in apps:
            stop_app(proc)
        if mock_server:
            try:
                mock_server.shutdown()
            except Exception:
                pass
        shutil.rmtree(tmp_root, ignore_errors=True)

    # ---- summary ---------------------------------------------------
    passed = sum(1 for ok, _, _ in RESULTS if ok)
    failed = [r for r in RESULTS if not r[0]]
    print("\n" + "=" * 64)
    print(f"  {passed}/{len(RESULTS)} checks passed")
    if failed:
        print("  FAILED:")
        for _, name, detail in failed:
            print(f"    - {name}   {detail}")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
