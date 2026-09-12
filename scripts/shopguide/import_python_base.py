"""Import pinned official linux/amd64 Python content when daemon networking is unavailable.

Checks every manifest/config/compressed layer and rootfs diff ID; never extracts layer paths.
Only anonymous public Docker Hub pull tokens are used, and never persisted.
"""

import argparse
import gzip
import hashlib
import io
import json
import subprocess
import tarfile
import urllib.parse
import urllib.request
from pathlib import Path

REPOSITORY = "library/python"
ACCEPT = (
    "application/vnd.oci.image.index.v1+json, "
    "application/vnd.oci.image.manifest.v1+json, "
    "application/vnd.docker.distribution.manifest.list.v2+json, "
    "application/vnd.docker.distribution.manifest.v2+json"
)


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise ValueError("NON_HTTPS_REGISTRY_REDIRECT")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if (
            redirected
            and urllib.parse.urlsplit(req.full_url).netloc
            != urllib.parse.urlsplit(newurl).netloc
        ):
            redirected.remove_header("Authorization")
        return redirected


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def require_digest(value):
    import re

    if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("INVALID_DIGEST")
    return value


def fetch(kind, identifier, token, *, limit=128 * 1024 * 1024):
    require_digest(identifier)
    request = urllib.request.Request(
        f"https://registry-1.docker.io/v2/{REPOSITORY}/{kind}/{identifier}",
        headers={"Authorization": "Bearer " + token, "Accept": ACCEPT},
    )
    with urllib.request.build_opener(Redirect()).open(request, timeout=60) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit or digest(raw) != identifier:
        raise ValueError("REGISTRY_CONTENT_INTEGRITY_FAILED")
    return raw


def add_bytes(archive, name, value):
    info = tarfile.TarInfo(name)
    info.size = len(value)
    info.mode = 0o644
    archive.addfile(info, io.BytesIO(value))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-digest", required=True, type=require_digest)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--load", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    with urllib.request.urlopen(
        f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{REPOSITORY}:pull",
        timeout=30,
    ) as response:
        token = json.load(response)["token"]
    raw = fetch("manifests", args.index_digest, token, limit=4 * 1024 * 1024)
    (args.out / "index.json").write_bytes(raw)
    index = json.loads(raw)
    selected = [
        m
        for m in index["manifests"]
        if m["platform"].get("architecture") == "amd64"
        and m["platform"].get("os") == "linux"
    ]
    if len(selected) != 1:
        raise ValueError("AMBIGUOUS_PLATFORM")
    manifest_digest = selected[0]["digest"]
    raw = fetch("manifests", manifest_digest, token, limit=4 * 1024 * 1024)
    (args.out / "platform.json").write_bytes(raw)
    manifest = json.loads(raw)
    config_raw = fetch(
        "blobs", manifest["config"]["digest"], token, limit=4 * 1024 * 1024
    )
    config = json.loads(config_raw)
    if config.get("architecture") != "amd64" or config.get("os") != "linux":
        raise ValueError("CONFIG_PLATFORM_MISMATCH")
    diff_ids = config["rootfs"]["diff_ids"]
    if len(diff_ids) != len(manifest["layers"]):
        raise ValueError("LAYER_COUNT_MISMATCH")
    config_name = manifest["config"]["digest"].split(":")[1] + ".json"
    tag = "shopguide-base:verified-" + manifest_digest.split(":")[1][:16]
    archive_path = args.out / "docker-image.tar"
    with tarfile.open(archive_path, "w") as archive:
        add_bytes(archive, config_name, config_raw)
        layers = []
        for i, layer in enumerate(manifest["layers"]):
            compressed = fetch("blobs", layer["digest"], token)
            if len(compressed) != layer["size"]:
                raise ValueError("LAYER_SIZE_MISMATCH")
            if layer["mediaType"] not in (
                "application/vnd.oci.image.layer.v1.tar+gzip",
                "application/vnd.docker.image.rootfs.diff.tar.gzip",
            ):
                raise ValueError("UNSUPPORTED_LAYER_COMPRESSION")
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
                uncompressed = stream.read(512 * 1024 * 1024 + 1)
            if (
                len(uncompressed) > 512 * 1024 * 1024
                or digest(uncompressed) != diff_ids[i]
            ):
                raise ValueError("ROOTFS_INTEGRITY_FAILED")
            name = f"layer-{i}/layer.tar"
            add_bytes(archive, name, uncompressed)
            layers.append(name)
        add_bytes(
            archive,
            "manifest.json",
            json.dumps(
                [{"Config": config_name, "RepoTags": [tag], "Layers": layers}]
            ).encode(),
        )
    proof = {
        "official_repository": "registry-1.docker.io/" + REPOSITORY,
        "index_digest": args.index_digest,
        "platform_digest": manifest_digest,
        "config_digest": manifest["config"]["digest"],
        "rootfs_diff_ids": diff_ids,
        "local_tag": tag,
        "content_verified": True,
        "publisher_signature_verified": False,
        "loaded": False,
    }
    if args.load:
        subprocess.run(
            ["docker", "load", "-i", str(archive_path)],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        actual = json.loads(
            subprocess.check_output(["docker", "image", "inspect", tag])
        )[0]
        if (
            actual["Id"] != proof["config_digest"]
            or actual["RootFS"]["Layers"] != diff_ids
        ):
            raise ValueError("LOADED_IMAGE_MISMATCH")
        proof["loaded"] = True
    (args.out / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")
    print(json.dumps(proof))


if __name__ == "__main__":
    main()
