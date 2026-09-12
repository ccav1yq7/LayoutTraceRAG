import importlib.util
from pathlib import Path

import pytest


def module(name):
    path = Path(__file__).resolve().parents[2] / "scripts/shopguide" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


@pytest.mark.parametrize(
    "bad", ["latest", "sha256:xyz", "sha256:" + "a" * 64 + "/../token"]
)
def test_importer_accepts_only_content_digests(bad):
    with pytest.raises(ValueError):
        module("import_python_base").require_digest(bad)


def test_scan_gate_keeps_unfixed_high_severity():
    scanner = module("summarize_image_scan")
    report = {
        "Metadata": {"ImageID": "sha256:expected"},
        "Results": [
            {
                "Target": "debian",
                "Class": "os-pkgs",
                "Packages": [{"Name": "lib"}],
                "Vulnerabilities": [
                    {
                        "VulnerabilityID": "CVE-fixture",
                        "Severity": "HIGH",
                        "FixedVersion": "",
                    }
                ],
            }
        ],
    }
    result = scanner.summarize(report, "sha256:expected")
    assert result["status"] == "fail"
    assert result["high_critical_with_available_fix"] == 0
    with pytest.raises(ValueError, match="IMAGE_MISMATCH"):
        scanner.summarize(report, "sha256:wrong")
    with pytest.raises(ValueError, match="COVERAGE_MISSING"):
        scanner.summarize(
            {"Metadata": report["Metadata"], "Results": []}, "sha256:expected"
        )


def test_registry_hash_mismatch_stops_before_loading(monkeypatch):
    from io import BytesIO

    loader = module("import_python_base")

    class Opener:
        def open(self, *args, **kwargs):
            return BytesIO(b"corrupt")

    monkeypatch.setattr(loader.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(ValueError, match="INTEGRITY"):
        loader.fetch("blobs", "sha256:" + "0" * 64, "fixture-public-pull-token")


def test_cross_host_redirect_does_not_forward_pull_token():
    loader = module("import_python_base")
    request = loader.urllib.request.Request(
        "https://registry-1.docker.io/blob", headers={"Authorization": "Bearer fixture"}
    )
    redirected = loader.Redirect().redirect_request(
        request, None, 302, "", {}, "https://cdn.example/blob"
    )
    assert not redirected.has_header("Authorization")
    with pytest.raises(ValueError, match="NON_HTTPS"):
        loader.Redirect().redirect_request(
            request, None, 302, "", {}, "http://cdn.example/blob"
        )


@pytest.mark.parametrize("case", ["valid", "bad_hash", "symlink"])
def test_scanner_installer_checks_archive_and_member(tmp_path, monkeypatch, case):
    import hashlib
    import io
    import json
    import sys
    import tarfile

    loader = module("install_scanner")
    payload = b"fixture scanner binary"
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        member = tarfile.TarInfo("trivy")
        member.size = len(payload)
        if case == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = "/outside"
            member.size = 0
        tar.addfile(member, io.BytesIO(payload))
    archive = stream.getvalue()
    lock = tmp_path / "scanner.json"
    lock.write_text(
        json.dumps(
            {
                "download": "https://github.com/aquasecurity/trivy/releases/download/fixture/test.tar.gz",
                "version": "fixture",
                "archive_sha256": "0" * 64
                if case == "bad_hash"
                else hashlib.sha256(archive).hexdigest(),
                "binary_sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    )
    output = tmp_path / "bin"
    monkeypatch.setattr(
        sys, "argv", ["installer", "--lock", str(lock), "--out", str(output)]
    )
    monkeypatch.setattr(
        loader.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(archive)
    )
    if case == "valid":
        loader.main()
        assert (output / "trivy").read_bytes() == payload
    else:
        with pytest.raises(ValueError):
            loader.main()
        assert not output.exists()
