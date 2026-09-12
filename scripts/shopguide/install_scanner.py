"""Install a checksum-pinned scanner from the explicitly reviewed public release lock."""

import argparse
import hashlib
import io
import json
import tarfile
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text())
    if not lock["download"].startswith(
        "https://github.com/aquasecurity/trivy/releases/download/"
    ):
        raise ValueError("UNREVIEWED_SCANNER_ORIGIN")
    with urllib.request.urlopen(lock["download"], timeout=60) as response:
        archive = response.read(256 * 1024 * 1024 + 1)
    if (
        len(archive) > 256 * 1024 * 1024
        or hashlib.sha256(archive).hexdigest() != lock["archive_sha256"]
    ):
        raise ValueError("SCANNER_ARCHIVE_HASH_MISMATCH")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        member = tar.getmember("trivy")
        if not member.isfile() or member.size > 512 * 1024 * 1024:
            raise ValueError("UNSAFE_SCANNER_MEMBER")
        binary = tar.extractfile(member).read()
    if hashlib.sha256(binary).hexdigest() != lock["binary_sha256"]:
        raise ValueError("SCANNER_BINARY_HASH_MISMATCH")
    args.out.mkdir(parents=True, exist_ok=False)
    executable = args.out / "trivy"
    executable.write_bytes(binary)
    executable.chmod(0o755)
    print(json.dumps({"installed": str(executable), "version": lock["version"]}))


if __name__ == "__main__":
    main()
