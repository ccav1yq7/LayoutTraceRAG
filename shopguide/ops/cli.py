import argparse
import json
from pathlib import Path

from .backup import backup, restore, verify_backup
from .release import release_check


def main(argv=None):
    parser = argparse.ArgumentParser(prog="shopguide ops")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser(
        "backup", help="requires stopped service and exclusive data-root lock"
    )
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    for name in ("verify-backup", "restore"):
        p = sub.add_parser(name)
        p.add_argument("--backup", type=Path, required=True)
        p.add_argument("--manifest-sha256", required=True)
        if name == "restore":
            p.add_argument("--target", type=Path, required=True)
    p = sub.add_parser("release-check")
    p.add_argument("--checklist", type=Path, required=True)
    p.add_argument("--base", type=Path, default=Path.cwd())
    p.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "backup":
        result = backup(args.root, args.out)
    elif args.command == "restore":
        result = restore(args.backup, args.target, expected_sha=args.manifest_sha256)
    elif args.command == "verify-backup":
        manifest = verify_backup(args.backup, args.manifest_sha256)
        result = {"verified": True, "files": len(manifest["files"])}
    else:
        result = release_check(args.checklist, args.base)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    if args.command == "release-check" and result["decision"] == "do_not_release":
        raise SystemExit(2)
