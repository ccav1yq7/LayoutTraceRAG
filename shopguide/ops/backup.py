"""Offline consistent backups; restore never overwrites an existing root."""

import hashlib
import json
import shutil
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from ..storage.locking import RootLock

DATABASES = ("metadata.db", "checkpoints.db")
TREES = ("assets", "indexes", "uploads")


@contextmanager
def database(*args, **kwargs):
    db = sqlite3.connect(*args, **kwargs)
    try:
        with db:
            yield db
    finally:
        db.close()


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def regular(path):
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("UNSAFE_BACKUP_FILE")


def inventory(root):
    files = {}
    for p in sorted(root.rglob("*")):
        if p.is_symlink():
            raise ValueError("BACKUP_SYMLINK_REJECTED")
        if p.is_dir():
            continue
        regular(p)
        name = p.relative_to(root).as_posix()
        if name != "manifest.json":
            files[name] = {"sha256": sha(p), "bytes": p.stat().st_size}
    return files


def sqlite_snapshot(source, target):
    regular(source)
    with database(source.resolve().as_uri() + "?mode=ro", uri=True) as src:
        if src.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("DATABASE_INTEGRITY_FAILED")
        with database(target) as dst:
            src.backup(dst)


def backup(root: Path, out: Path):
    root = root.resolve(strict=True)
    out = out.absolute()
    if out.resolve().is_relative_to(root):
        raise ValueError("BACKUP_MUST_BE_OUTSIDE_DATA_ROOT")
    with RootLock(root, exclusive=True):
        if not (root / "metadata.db").is_file():
            raise ValueError("MISSING_METADATA_DATABASE")
        out.mkdir(parents=True, mode=0o700, exist_ok=False)
        try:
            for name in DATABASES:
                if (root / name).exists():
                    sqlite_snapshot(root / name, out / name)
            for name in TREES:
                source = root / name
                if source.exists():
                    if source.is_symlink():
                        raise ValueError("BACKUP_SYMLINK_REJECTED")
                    inventory(
                        source
                    )  # Reject links/devices before copytree follows anything.
                    shutil.copytree(source, out / name)
            with database(out / "metadata.db") as db:
                schema = db.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchone()[0]
                snapshots = db.execute(
                    "SELECT id,state FROM sg_snapshots ORDER BY id"
                ).fetchall()
            from ..agent.prompts import AGENT_PROMPT_VERSION
            from ..qa.prompts import PROMPT_VERSION

            manifest = {
                "format": 1,
                "consistency": "offline-cooperative-root-lock",
                "schema": schema,
                "snapshots": snapshots,
                "agent_prompt": AGENT_PROMPT_VERSION,
                "qa_prompt": PROMPT_VERSION,
                "model_credentials": "excluded; reattach private config at startup",
                "files": inventory(out),
            }
            (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            return {
                "backup": str(out),
                "files": len(manifest["files"]),
                "manifest_sha256": sha(out / "manifest.json"),
                "schema": schema,
            }
        except BaseException:
            shutil.rmtree(out)
            raise


def verify_backup(source: Path, expected_sha=None):
    if source.is_symlink():
        raise ValueError("BACKUP_SYMLINK_REJECTED")
    regular(source / "manifest.json")
    if expected_sha and sha(source / "manifest.json") != expected_sha:
        raise ValueError("BACKUP_MANIFEST_HASH_MISMATCH")
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest.get("format") != 1 or manifest.get("schema") != "sg0004":
        raise ValueError("UNSUPPORTED_BACKUP_SCHEMA")
    for name in manifest["files"]:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or path.as_posix() != name
        ):
            raise ValueError("UNSAFE_BACKUP_PATH")
        if name not in DATABASES and (not path.parts or path.parts[0] not in TREES):
            raise ValueError("UNEXPECTED_BACKUP_FILE")
    if inventory(source) != manifest["files"] or "metadata.db" not in manifest["files"]:
        raise ValueError("BACKUP_CONTENT_MISMATCH")
    return manifest


def restore(source: Path, target: Path, *, expected_sha=None):
    manifest = verify_backup(source, expected_sha)
    target = target.absolute()
    if target.resolve().is_relative_to(source.resolve()):
        raise ValueError("RESTORE_MUST_BE_OUTSIDE_BACKUP")
    target.mkdir(parents=True, mode=0o700, exist_ok=False)
    try:
        for name in manifest["files"]:
            dst = target / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, dst)
            if sha(dst) != manifest["files"][name]["sha256"]:
                raise ValueError("RESTORE_COPY_HASH_MISMATCH")
        for name in DATABASES:
            if (target / name).exists():
                with database(target / name) as db:
                    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("RESTORE_DATABASE_INTEGRITY_FAILED")
        # Revoke pre-backup HTTP credentials; preserve business/session history.
        with database(target / "metadata.db") as db:
            db.execute("DELETE FROM sg_http_auth")
        return {
            "restored": str(target),
            "schema": manifest["schema"],
            "http_credentials_revoked": True,
            "backup_manifest_sha256": sha(source / "manifest.json"),
        }
    except BaseException:
        shutil.rmtree(target)
        raise
