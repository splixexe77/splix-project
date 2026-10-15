"""Start the SPLIX site + unlock server.

    python server/run.py

Environment comes from server/.env (see server/.env.example).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import env as env_mod  # noqa: E402

# Capture the hosting platform's PORT BEFORE server/.env is merged in.
# Render (and friends) export PORT into the real environment; a PORT in
# .env is a local preference and must not force a public bind.
_PLATFORM_PORT = "PORT" in os.environ

# Windows consoles default to cp1252, which chokes on UTF-8 output.
# line_buffering matters in production: stdout is a pipe on hosting
# platforms, and access-log lines must reach the log stream immediately
# instead of sitting in an 8 KB block buffer.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

env_mod.load()

from http.server import ThreadingHTTPServer  # noqa: E402

import app as app_mod  # noqa: E402
import deploy as deploy_mod  # noqa: E402


def main() -> int:
    # Production: hosting platform PORT + public 0.0.0.0 bind (with HOST
    # always able to override).  Local: 127.0.0.1:8099 safe fallback.
    host = deploy_mod.resolve_host(platform_port=_PLATFORM_PORT)
    port = deploy_mod.resolve_port()

    try:
        ctx = app_mod.build_context()
        server = app_mod.make_server(host, port, ctx)
    except OSError as exc:
        print(f"[ERROR] Could not bind {host}:{port} — {exc}")
        print("        Is another server already using that port?")
        return 1

    cfg = ctx.verify_config()
    mode = "OAuth (official Google/YouTube API)" if cfg.oauth_ready else "manual review (OAuth not configured)"

    print("=" * 64)
    print("  SPLIX X CHEAT  —  site + subscription unlock server")
    print("=" * 64)
    print(f"  Listening       {host}:{port}"
          + ("  (all interfaces — platform/proxy mode)" if host == "0.0.0.0" else ""))
    print(f"  Site            {ctx.public_url}/index.html")
    print(f"  Unlock pages    {ctx.public_url}/u/<slug>")
    print(f"  Admin panel     {ctx.public_url}/unlock-admin.html")
    print(f"  Health check    {ctx.public_url}/health")
    print(f"  Verification    {mode}")
    if not cfg.oauth_ready and cfg.missing:
        print(f"  Missing for API {', '.join(cfg.missing)}")
    print(f"  Discord webhook {'configured' if ctx.discord_webhook else 'not configured (optional)'}")
    print(f"  Admin API       {'token set' if ctx.admin_token else 'DISABLED (set ADMIN_TOKEN in server/.env)'}")
    print(f"  Public URL      {ctx.public_url}  (used as the OAuth redirect origin)")
    print(f"  Data folder     {ctx.data_dir}")
    print("  Press Ctrl+C to stop.")
    print("=" * 64)

    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
