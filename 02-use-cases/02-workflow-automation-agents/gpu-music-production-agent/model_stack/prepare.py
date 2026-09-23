"""Build the generation stack onto the capacity provider's ``models`` volume.

Runs inside the composition agent's own process, which matters: the volume is
mounted ``2775 root:agentcore-runtime-user`` and the agent process holds that
supplementary group, while a shell started by ``InvokeAgentRuntimeCommand`` does
not and gets EACCES. Measured on a live instance -- the agent is the only thing
that can populate this volume.

Why the stack is on a volume rather than in the container image: an AgentCore
Runtime container image is capped at 2 GB, and a CUDA build of torch resolves to
3.13 GB of wheels before any model weight is added. The image therefore stays
tiny (agent + SDK) and everything heavy lands here, in a self-contained venv the
agent shells out to.

Idempotent by design. Each stage writes a stamp, so a resumed session or a
second invocation skips work already done, and a partly-built volume can be
completed rather than restarted.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

# Pinned commit on ace-step/ACE-Step main. Pinned rather than tracking a branch
# because this is the version the sample was tested against, and pinned to a
# GitHub tarball rather than PyPI because the published `ace-step` sdist is
# broken: its setup.py reads a requirements.txt the archive does not contain, so
# `pip install ace-step` fails at metadata generation.
ACESTEP_SHA = os.environ.get("ACESTEP_SHA", "1bee4c9f5b43e30995f8d4d33b3919197ce1bd68")
ACESTEP_TARBALL = f"https://codeload.github.com/ace-step/ACE-Step/tar.gz/{ACESTEP_SHA}"

# ACE-Step v1 3.5B. Apache-2.0 and ungated, which is why it is the default here:
# no license acceptance and no HuggingFace token, so the sample deploys
# unattended. See the README for the licensing comparison.
WEIGHTS_REPO = os.environ.get("ACESTEP_WEIGHTS_REPO", "ACE-Step/ACE-Step-v1-3.5B")

STAGES = ("source", "venv", "install", "weights", "runner")


def log(msg: str) -> None:
    print(f"[prepare] {msg}", flush=True)


def run(cmd: list[str], cwd: str | None = None) -> None:
    log(f"$ {' '.join(cmd[:6])}{' ...' if len(cmd) > 6 else ''}")
    subprocess.run(cmd, check=True, cwd=cwd, stdout=sys.stdout, stderr=sys.stderr)


class Stamps:
    """Per-stage completion markers, so preparation is resumable."""

    def __init__(self, root: Path) -> None:
        self.dir = root / ".stamps"
        self.dir.mkdir(parents=True, exist_ok=True)

    def done(self, stage: str) -> bool:
        return (self.dir / stage).exists()

    def mark(self, stage: str, detail: dict) -> None:
        (self.dir / stage).write_text(json.dumps(detail, indent=2), encoding="utf-8")


def fetch_source(root: Path, stamps: Stamps) -> Path:
    """Download and unpack the pinned ACE-Step tree.

    A tarball rather than `git clone` so the container image needs no git, which
    keeps it to a zero-RUN-step Dockerfile and therefore buildable for amd64 from
    an arm64 machine without emulation.
    """
    src_root = root / "src"
    expected = src_root / f"ACE-Step-{ACESTEP_SHA}"
    if stamps.done("source") and (expected / "setup.py").exists():
        log(f"source already present at {expected}")
        return expected

    src_root.mkdir(parents=True, exist_ok=True)
    archive = root / "acestep-src.tar.gz"
    log(f"downloading {ACESTEP_TARBALL}")
    t0 = time.time()
    with urllib.request.urlopen(ACESTEP_TARBALL, timeout=180) as r, open(archive, "wb") as fh:
        shutil.copyfileobj(r, fh)
    log(f"downloaded {archive.stat().st_size / 1e6:.1f} MB in {time.time() - t0:.0f}s")

    with tarfile.open(archive) as tf:
        # Refuse any member that would escape the extraction root.
        for member in tf.getmembers():
            target = (src_root / member.name).resolve()
            if not str(target).startswith(str(src_root.resolve())):
                raise RuntimeError(f"unsafe tar member {member.name!r}")
        tf.extractall(src_root)
    archive.unlink(missing_ok=True)

    if not (expected / "setup.py").exists():
        candidates = [p for p in src_root.iterdir() if p.is_dir() and (p / "setup.py").exists()]
        if not candidates:
            raise RuntimeError(f"no setup.py found under {src_root}")
        expected = candidates[0]
    stamps.mark("source", {"sha": ACESTEP_SHA, "path": str(expected)})
    return expected


def make_venv(root: Path, stamps: Stamps) -> Path:
    venv = root / "venv"
    python = venv / "bin" / "python"
    if stamps.done("venv") and python.exists():
        log("venv already present")
        return python
    log(f"creating venv at {venv} from {sys.executable}")
    run([sys.executable, "-m", "venv", str(venv)])
    run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip", "wheel", "setuptools"])
    stamps.mark("venv", {"python": str(python)})
    return python


def install_stack(python: Path, source: Path, root: Path, stamps: Stamps) -> None:
    if stamps.done("install"):
        log("stack already installed")
        return
    log("installing ACE-Step and its dependencies (torch + CUDA, several GB)")
    t0 = time.time()
    run([str(python), "-m", "pip", "install", "--no-cache-dir", str(source)])
    size = sum(f.stat().st_size for f in (root / "venv").rglob("*") if f.is_file())
    log(f"installed in {time.time() - t0:.0f}s, venv is {size / 1e9:.2f} GB")
    stamps.mark("install", {"seconds": round(time.time() - t0), "venv_bytes": size})


def fetch_weights(python: Path, root: Path, stamps: Stamps) -> Path:
    target = root / "weights" / WEIGHTS_REPO.split("/")[-1]
    if stamps.done("weights") and target.exists():
        log(f"weights already present at {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    log(f"downloading weights {WEIGHTS_REPO}")
    t0 = time.time()
    script = (
        "from huggingface_hub import snapshot_download;"
        f"print(snapshot_download({WEIGHTS_REPO!r}, local_dir={str(target)!r}))"
    )
    run([str(python), "-c", script])
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    log(f"weights downloaded in {time.time() - t0:.0f}s, {size / 1e9:.2f} GB")
    stamps.mark("weights", {"repo": WEIGHTS_REPO, "bytes": size, "seconds": round(time.time() - t0)})
    return target


def install_runner(root: Path, stamps: Stamps) -> Path:
    """Copy the generation entry point next to the venv that runs it."""
    runner = root / "generate.py"
    source = Path(__file__).resolve().parent / "generate.py"
    shutil.copyfile(source, runner)
    try:
        runner.chmod(0o664)
    except PermissionError:
        pass
    stamps.mark("runner", {"path": str(runner)})
    return runner


def prepare(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    stamps = Stamps(root)
    started = time.time()

    source = fetch_source(root, stamps)
    python = make_venv(root, stamps)
    install_stack(python, source, root, stamps)
    weights = fetch_weights(python, root, stamps)
    runner = install_runner(root, stamps)

    ready = {
        "ready": True,
        "acestep_sha": ACESTEP_SHA,
        "weights_repo": WEIGHTS_REPO,
        "python": str(python),
        "weights_dir": str(weights),
        "runner": str(runner),
        "prepared_in_seconds": round(time.time() - started),
    }
    (root / "READY.json").write_text(json.dumps(ready, indent=2), encoding="utf-8")
    log(f"ready in {ready['prepared_in_seconds']}s")
    return ready


def status(root: Path) -> dict:
    marker = root / "READY.json"
    if marker.exists():
        return {**json.loads(marker.read_text()), "stage": "ready"}
    stamps = Stamps(root)
    completed = [s for s in STAGES if stamps.done(s)]
    return {
        "ready": False,
        "completed_stages": completed,
        "next_stage": next((s for s in STAGES if s not in completed), None),
    }


if __name__ == "__main__":
    target_root = Path(sys.argv[2] if len(sys.argv) > 2 else "/mnt/models")
    action = sys.argv[1] if len(sys.argv) > 1 else "prepare"
    print(json.dumps(status(target_root) if action == "status" else prepare(target_root), indent=2))
