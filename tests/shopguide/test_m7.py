import json
import sqlite3

import pytest

from shopguide.api.demo import seed_demo
from shopguide.ops.backup import backup, restore, sha
from shopguide.ops.release import release_check
from shopguide.storage.locking import RootLock
from shopguide.storage.repository import Repository


@pytest.fixture
def data_root(tmp_path):
    root = tmp_path / "data"
    repo = Repository(root / "metadata.db")
    seed_demo(repo, root)
    repo.close()
    return root


def test_backup_requires_no_live_repository(data_root, tmp_path):
    repo = Repository(data_root / "metadata.db")
    try:
        with pytest.raises(RuntimeError, match="BUSY"):
            backup(data_root, tmp_path / "blocked")
        assert not (tmp_path / "blocked").exists()
    finally:
        repo.close()
    report = backup(data_root, tmp_path / "backup")
    assert report["schema"] == "sg0004"


def test_restore_verifies_files_preserves_state_and_revokes_auth(data_root, tmp_path):
    with sqlite3.connect(data_root / "metadata.db") as db:
        db.execute(
            "INSERT INTO sg_http_auth VALUES ('tokenhash','u','csrf',9999999999)"
        )
    # Exercise a second SQLite database, not just the main metadata file.
    with sqlite3.connect(data_root / "checkpoints.db") as db:
        db.execute("CREATE TABLE marker(value TEXT)")
        db.execute("INSERT INTO marker VALUES ('checkpoint')")
    saved = tmp_path / "backup"
    report = backup(data_root, saved)
    restored = tmp_path / "restored"
    result = restore(saved, restored, expected_sha=report["manifest_sha256"])
    assert result["http_credentials_revoked"]
    with sqlite3.connect(restored / "metadata.db") as db:
        assert db.execute("SELECT COUNT(*) FROM sg_http_auth").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM sg_snapshots").fetchone()[0] == 1
    with sqlite3.connect(restored / "checkpoints.db") as db:
        assert db.execute("SELECT value FROM marker").fetchone()[0] == "checkpoint"
    assert sha(restored / "checkpoints.db") == sha(saved / "checkpoints.db")
    with pytest.raises(FileExistsError):
        restore(saved, restored)


@pytest.mark.parametrize(
    "attack", ["corrupt", "extra", "symlink", "traversal", "manifest"]
)
def test_backup_tampering_is_rejected(data_root, tmp_path, attack):
    saved = tmp_path / "backup"
    report = backup(data_root, saved)
    manifest_path = saved / "manifest.json"
    expected = report["manifest_sha256"]
    if attack == "corrupt":
        (saved / "metadata.db").write_bytes(b"damaged")
    elif attack == "extra":
        (saved / "extra").write_text("unregistered")
    elif attack == "symlink":
        (saved / "metadata.db").unlink()
        (saved / "metadata.db").symlink_to(data_root / "metadata.db")
    elif attack == "traversal":
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["../outside"] = {"sha256": "0" * 64, "bytes": 0}
        manifest_path.write_text(json.dumps(manifest))
        expected = None
    else:
        manifest_path.write_text(manifest_path.read_text() + " ")
    with pytest.raises(ValueError):
        restore(saved, tmp_path / "target", expected_sha=expected)
    assert not (tmp_path / "target").exists()


def test_backup_excludes_configuration_and_rejects_source_links(data_root, tmp_path):
    (data_root / "LLM.config").write_text("fixture-private")
    backup(data_root, tmp_path / "backup")
    assert not (tmp_path / "backup/LLM.config").exists()
    (data_root / "assets/unsafe").symlink_to(data_root / "LLM.config")
    with pytest.raises(ValueError, match="SYMLINK"):
        backup(data_root, tmp_path / "unsafe-backup")
    assert not (tmp_path / "unsafe-backup").exists()


def test_backup_refuses_nested_or_existing_output(data_root, tmp_path):
    with pytest.raises(ValueError):
        backup(data_root, data_root / "backup")
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(FileExistsError):
        backup(data_root, existing)
    assert existing.is_dir()


def test_exclusive_service_lease_allows_only_one_runtime(tmp_path):
    with (
        RootLock(tmp_path, exclusive=True, service=True),
        pytest.raises(RuntimeError, match="BUSY"),
    ):
        RootLock(tmp_path, exclusive=True, service=True)
    with RootLock(tmp_path, exclusive=True, service=True):
        pass


def test_release_gate_cannot_drop_unfinished_benchmarks(tmp_path):
    report = tmp_path / "test.json"
    report.write_text('{"passed": 1}')
    checklist = tmp_path / "checklist.json"
    checklist.write_text(
        json.dumps(
            {
                "gates": {
                    "python_regression": {
                        "status": "pass",
                        "evidence": [{"path": "test.json", "sha256": sha(report)}],
                    }
                }
            }
        )
    )
    result = release_check(checklist, tmp_path)
    assert result["decision"] == "do_not_release"
    assert result["gates"]["python_regression"]["status"] == "pass"
    assert result["gates"]["pm209_official"]["status"] == "blocked"
    report.write_text("changed")
    assert (
        release_check(checklist, tmp_path)["gates"]["python_regression"]["status"]
        == "blocked"
    )
