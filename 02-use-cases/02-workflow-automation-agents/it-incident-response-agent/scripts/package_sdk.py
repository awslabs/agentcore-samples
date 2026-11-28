#!/usr/bin/env python3
"""Build the locked pure-Python infrastructure SDK layer locally (no AWS calls)."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "lambdas/sdk-requirements.txt"
DEST = ROOT / ".lambda-sdk"


def main():
    if DEST.exists():
        raise SystemExit(".lambda-sdk already exists; keep/review the old artifact, or move it aside before a rebuild")
    with tempfile.TemporaryDirectory(prefix="it-sdk-", dir=ROOT) as temp:
        stage = Path(temp) / "layer"
        stage.mkdir()
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                sys.executable,
                "--require-hashes",
                "--only-binary",
                ":all:",
                "--no-deps",
                "--target",
                str(stage / "python"),
                "-r",
                str(LOCK),
            ],
            check=True,
            timeout=180,
        )
        files = {
            str(p.relative_to(stage)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(stage.rglob("*"))
            if p.is_file()
        }
        (stage / "manifest.json").write_text(
            json.dumps({"lock_sha256": hashlib.sha256(LOCK.read_bytes()).hexdigest(), "files": files}, indent=2)
        )
        stage.rename(DEST)
    print("Prepared locked SDK layer (boto3/botocore 1.44.10); no AWS call was made")


if __name__ == "__main__":
    main()
