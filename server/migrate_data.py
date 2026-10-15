"""Backup, verify, pack and restore the JSON data folder (server/data).

The unlock server keeps its entire state in five files:

    links.json      unlock links incl. destination URLs + stats
    reviews.json    manual review requests
    settings.json   site/footer settings
    sessions.json   hashed verified-session records
    events.jsonl    append-only audit log

Run this BEFORE any deployment or migration:

    python server/migrate_data.py backup --note pre_deploy
    python server/migrate_data.py verify
    python server/migrate_data.py pack --out deploy-seed
    python server/migrate_data.py restore backups/<dir> --yes

* backup  — copies the data files into server/backups/<ts>_<note> and
            re-verifies every copy, printing record counts only.
* verify  — parses every present file; exits 1 if anything is corrupt.
* pack    — writes a seed folder (e.g. for a hosting "secret file" or
            SEED_DIR) that the server copies into an EMPTY data folder
            on first boot.  Never write the pack inside a committed
            folder: it contains destination URLs (use deploy-seed/,
            which is git-ignored, or a path outside the repository).
* restore — copies a backup over DATA_DIR.  It is refused without the
            explicit --yes flag and always snapshots the current data
            first, so a restore itself can be undone.

Nothing in this tool ever prints file contents, tokens or destination
URLs — only paths and record counts.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import deploy as deploy_mod  # noqa: E402

DEFAULT_DATA_DIR = SERVER_DIR / "data"
BACKUPS_DIR = SERVER_DIR / "backups"


def _fail(message: str) -> int:
    print(f"[FAIL] {message}")
    return 1


def _count(name: str, raw: bytes) -> int:
    """Number of records in one data file (0 for unreadable/empty)."""
    try:
        if name.endswith(".jsonl"):
            return sum(
                1 for line in raw.decode("utf-8").splitlines()
                if line.strip() and isinstance(json.loads(line), dict)
            )
        data = json.loads(raw.decode("utf-8"))
        if isinstance(data, list):
            return len(data)
        if isinstance(data, dict):
            return len(data)
    except Exception:
        return -1
    return 0


def verify_dir(data_dir: Path) -> bool:
    """Parse every data file; report counts; True when nothing is corrupt."""
    ok = True
    if not data_dir.is_dir():
        print(f"[FAIL] data folder not found: {data_dir}")
        return False
    for name in deploy_mod.DATA_FILES:
        path = data_dir / name
        if not path.is_file():
            print(f"  {name:<14} missing (created on first use)")
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            print(f"  {name:<14} UNREADABLE ({exc})")
            ok = False
            continue
        if name.endswith(".jsonl"):
            valid = deploy_mod._valid_jsonl(raw)
        else:
            valid = deploy_mod._valid_json(raw)
        count = _count(name, raw)
        if not valid or count < 0:
            print(f"  {name:<14} CORRUPT — not valid JSON")
            ok = False
        else:
            print(f"  {name:<14} ok ({count} record{'s' if count != 1 else ''})")
    return ok


def cmd_backup(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        return _fail(f"data folder not found: {data_dir}")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    note = "".join(ch for ch in (args.note or "") if ch.isalnum() or ch in "-_").strip("_")
    out_dir = Path(args.out) if args.out else BACKUPS_DIR / f"{stamp}_{note or 'backup'}"
    if out_dir.exists() and any(out_dir.iterdir()):
        return _fail(f"backup target already exists and is not empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for name in deploy_mod.DATA_FILES:
        src = data_dir / name
        if not src.is_file():
            continue
        shutil.copy2(src, out_dir / name)
        copied += 1
    if not copied:
        return _fail(f"nothing to back up in {data_dir}")

    print(f"Verifying the backup copy in {out_dir} …")
    if not verify_dir(out_dir):
        return _fail("backup copy failed verification — check the source disk")
    print(f"[OK]   backup written: {out_dir}  ({copied} file{'s' if copied != 1 else ''})")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    print(f"Verifying {data_dir}")
    return 0 if verify_dir(data_dir) else 1


def cmd_pack(args: argparse.Namespace) -> int:
    data_dir = Path(args.data)
    out_dir = Path(args.out)
    if not data_dir.is_dir():
        return _fail(f"data folder not found: {data_dir}")
    try:
        if out_dir.resolve() == data_dir.resolve():
            return _fail("refusing to pack into the data folder itself")
    except OSError:
        pass
    out_dir.mkdir(parents=True, exist_ok=True)
    packed = 0
    for name in deploy_mod.DATA_FILES:
        src = data_dir / name
        if not src.is_file():
            continue
        raw = src.read_bytes()
        valid = deploy_mod._valid_jsonl(raw) if name.endswith(".jsonl") else deploy_mod._valid_json(raw)
        if not valid:
            return _fail(f"{name} is corrupt — run 'verify' before packing")
        shutil.copy2(src, out_dir / name)
        packed += 1
    if not packed:
        return _fail(f"nothing to pack in {data_dir}")
    print(f"[OK]   packed {packed} file{'s' if packed != 1 else ''} into {out_dir}")
    print("       Upload this folder privately (e.g. as a platform secret")
    print("       file) and point SEED_DIR at it — the server copies it")
    print("       into an EMPTY data folder on first boot only.")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    backup_dir = Path(args.backup)
    data_dir = Path(args.data)
    if not backup_dir.is_dir():
        return _fail(f"backup folder not found: {backup_dir}")
    missing = [n for n in deploy_mod.DATA_FILES if not (backup_dir / n).is_file()]
    if len(missing) == len(deploy_mod.DATA_FILES):
        return _fail(f"no data files inside {backup_dir}")
    print(f"Restore {backup_dir}  ->  {data_dir}")
    if not verify_dir(backup_dir):
        return _fail("backup is corrupt — refusing to restore it")
    if not args.yes:
        print("[STOP] this overwrites existing data files.")
        print("       Re-run with --yes to authorize it (a snapshot of the")
        print("       current data is taken automatically first).")
        return 2

    # Snapshot whatever is live right now so the restore is reversible.
    if data_dir.is_dir() and any((data_dir / n).is_file() for n in deploy_mod.DATA_FILES):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        safety = data_dir.parent / "backups" / f"{stamp}_pre_restore"
        safety.mkdir(parents=True, exist_ok=True)
        for name in deploy_mod.DATA_FILES:
            src = data_dir / name
            if src.is_file():
                shutil.copy2(src, safety / name)
        print(f"[OK]   current data snapshotted to {safety}")

    data_dir.mkdir(parents=True, exist_ok=True)
    restored = 0
    for name in deploy_mod.DATA_FILES:
        src = backup_dir / name
        if not src.is_file():
            continue
        tmp = data_dir / (name + ".tmp")
        shutil.copy2(src, tmp)
        tmp.replace(data_dir / name)
        restored += 1
    if not verify_dir(data_dir):
        return _fail("restore verification failed — re-run 'verify'")
    print(f"[OK]   restored {restored} file{'s' if restored != 1 else ''} into {data_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data", default=str(DEFAULT_DATA_DIR),
                        help="data folder (default: server/data)")

    parser = argparse.ArgumentParser(
        prog="migrate_data.py",
        description="Backup / verify / pack / restore the SPLIX data folder.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_backup = sub.add_parser("backup", parents=[common],
                              help="copy data files into server/backups/<ts>_<note>")
    p_backup.add_argument("--out", default="", help="explicit backup target folder")
    p_backup.add_argument("--note", default="", help="suffix for the folder name")
    p_backup.set_defaults(func=cmd_backup)

    p_verify = sub.add_parser("verify", parents=[common],
                              help="parse every data file (exit 1 on corruption)")
    p_verify.set_defaults(func=cmd_verify)

    p_pack = sub.add_parser("pack", parents=[common],
                            help="write a seed folder for SEED_DIR migrations")
    p_pack.add_argument("--out", required=True, help="target folder (outside version control!)")
    p_pack.set_defaults(func=cmd_pack)

    p_restore = sub.add_parser("restore", parents=[common],
                               help="overwrite DATA_DIR with a backup (needs --yes)")
    p_restore.add_argument("backup", help="backup folder to restore from")
    p_restore.add_argument("--yes", action="store_true", help="authorize the overwrite")
    p_restore.set_defaults(func=cmd_restore)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
