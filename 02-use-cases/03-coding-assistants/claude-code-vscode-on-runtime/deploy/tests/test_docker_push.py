"""The real build_and_push against a throwaway local registry (opt in: DEVBOX_DOCKER_TESTS=1).

It builds a tiny linux/arm64 image, pushes it with the throwaway credential file, and checks that
the registry got one plain arm64 image manifest (not an index with attestations, which Lambda and
AgentCore can't use) and that ~/.docker/config.json wasn't touched."""

import base64
import hashlib
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

PORT = 9471
REGISTRY = f"localhost:{PORT}"
NAME = "devbox-deploy-registry-test"
pytestmark = pytest.mark.skipif(
    os.environ.get("DEVBOX_DOCKER_TESTS") != "1", reason="set DEVBOX_DOCKER_TESTS=1 (needs Docker)"
)


def get(path, accept):
    req = urllib.request.Request(f"http://{REGISTRY}{path}", headers={"Accept": accept})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.headers.get("Content-Type"), json.load(r)


@pytest.fixture
def registry():
    subprocess.run(
        ["docker", "rm", "-f", "-v", NAME], capture_output=True, check=False
    )  # -v: registry:2 has an anonymous VOLUME
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", NAME, "-p", f"127.0.0.1:{PORT}:5000", "registry:2"],
        check=True,
        capture_output=True,
    )
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://{REGISTRY}/v2/", timeout=2)
                break
            except OSError:
                time.sleep(0.2)
        yield
    finally:
        subprocess.run(
            ["docker", "rm", "-f", "-v", NAME], capture_output=True, check=False
        )  # -v: registry:2 has an anonymous VOLUME


def test_build_and_push(devbox, registry, tmp_path):
    cfg = Path.home() / ".docker" / "config.json"
    before = hashlib.sha256(cfg.read_bytes()).hexdigest() if cfg.exists() else None
    (tmp_path / "Dockerfile").write_text("FROM scratch\nCOPY hello.txt /hello.txt\n")
    (tmp_path / "hello.txt").write_text("hi\n")
    uri = f"{REGISTRY}/devbox-pushtest:dev"
    try:
        devbox.build_and_push(tmp_path, uri, REGISTRY, base64.b64encode(b"AWS:not-a-real-password").decode())
        ctype, manifest = get(
            "/v2/devbox-pushtest/manifests/dev",
            "application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json, "
            "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json",
        )
        assert "index" not in ctype and "list" not in ctype, f"pushed an index ({ctype}): provenance/SBOM must be off"
        _, config = get(f"/v2/devbox-pushtest/blobs/{manifest['config']['digest']}", "*/*")
        assert (config["os"], config["architecture"]) == ("linux", "arm64")
    finally:
        subprocess.run(["docker", "image", "rm", "-f", uri], capture_output=True, check=False)
    after = hashlib.sha256(cfg.read_bytes()).hexdigest() if cfg.exists() else None
    assert before == after, "the push must not write ~/.docker/config.json"


def test_context_files_match_what_docker_sends(devbox, tmp_path):
    """devbox.py's reading of .dockerignore against Docker's own: copy the whole context into an image
    and compare the file lists."""
    ctx = tmp_path / "ctx"
    files = [
        "Dockerfile",
        "keep.txt",
        "README.md",
        "rootfs/etc/a.json",
        "rootfs/opt/x/__pycache__/m.pyc",
        "rootfs/opt/x/m.py",
        "proxy/server.mjs",
        "proxy/lib/u.mjs",
        "proxy/node_modules/ws/i.js",
        "proxy/package.json",
        "test/t.mjs",
        "deep/a/b/.cache/big.bin",
        "deep/a/b/c.txt",
        "notes.md",
    ]
    for f in files:
        (ctx / f).parent.mkdir(parents=True, exist_ok=True)
        (ctx / f).write_text(f)
    (ctx / "Dockerfile").write_text("FROM scratch\nCOPY . /ctx/\n")
    (ctx / ".dockerignore").write_text(
        "*\n!Dockerfile\n!keep.txt\n!rootfs\n!proxy/server.mjs\n!proxy/lib\n!deep\n**/__pycache__\n**/.cache\n*.md\n"
    )
    out = tmp_path / "out"
    r = subprocess.run(
        ["docker", "buildx", "build", "--platform", "linux/arm64", "--output", f"type=local,dest={out}", str(ctx)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr[-2000:]
    sent = sorted(p.relative_to(out / "ctx").as_posix() for p in (out / "ctx").rglob("*") if p.is_file())
    ours = sorted(p.relative_to(ctx).as_posix() for p in devbox.context_files(ctx))
    assert ours == sent, (sorted(set(ours) - set(sent)), sorted(set(sent) - set(ours)))
