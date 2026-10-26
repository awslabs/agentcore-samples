#!/usr/bin/env python3
"""Zero-dependency, deterministic wheel builder for the ``migration_common`` package.

AWS Glue Python shell jobs can only import shared code from a ``.egg`` or ``.whl``
supplied through ``--extra-py-files``; a plain module ``.zip`` is not a supported
import mechanism for Python shell jobs. This builder produces a PEP 427 compliant
wheel using only the Python standard library, so ``cdk synth``/``cdk deploy`` needs
nothing beyond ``python3`` on PATH -- no ``setuptools``, ``wheel``, ``build``, or
network access at build time.

The package is pure Python, so the resulting ``py3-none-any`` wheel built on any
Python 3.9+ interpreter is compatible with the Glue 3.9 runtime.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import zipfile
from pathlib import Path

PACKAGE = "migration_common"
DIST_NAME = "migration_common"
DEFAULT_VERSION = "0.1.0"
# 1980-01-01: the minimum timestamp a zip entry can encode. Using a fixed value
# keeps the wheel bytes reproducible so the CDK asset hash is stable across builds.
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)


def _resolve_version(source_dir: Path) -> str:
    pyproject = source_dir / "pyproject.toml"
    if pyproject.exists():
        match = re.search(
            r'(?m)^\s*version\s*=\s*"([^"]+)"',
            pyproject.read_text(encoding="utf-8"),
        )
        if match:
            return match.group(1)
    return DEFAULT_VERSION


def _record_line(archive_name: str, data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode("ascii")
    return f"{archive_name},sha256={digest},{len(data)}"


def build_wheel(source_dir: Path, out_dir: Path) -> Path:
    source_dir = source_dir.resolve()
    package_dir = source_dir / PACKAGE
    if not package_dir.is_dir():
        raise SystemExit(f"Package directory not found: {package_dir}")

    version = _resolve_version(source_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dist_info = f"{DIST_NAME}-{version}.dist-info"

    metadata = (
        "Metadata-Version: 2.1\n"
        f"Name: {DIST_NAME}\n"
        f"Version: {version}\n"
        "Summary: Shared runtime for the AWS Agent Registry migration Glue jobs\n"
        "Requires-Python: >=3.9\n"
    )
    wheel_metadata = (
        "Wheel-Version: 1.0\n"
        "Generator: agent-registry-migration-build-wheel (1.0)\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    )

    members: list[tuple[str, bytes]] = []
    for path in sorted(package_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        archive_name = path.relative_to(source_dir).as_posix()
        members.append((archive_name, path.read_bytes()))
    if not members:
        raise SystemExit(f"No Python sources found under {package_dir}")

    # Bundle the API adapter contract (a data file, not Python): a local run reads it straight from
    # the package instead of an SSM parameter, and the CDK app publishes the very same file.
    # Service models are not bundled -- boto3 supplies them. See registry_client.py.
    # The seen-set is belt and braces in case a later pattern overlaps another: a duplicated zip
    # member and a duplicated RECORD line make a malformed PEP 427 wheel.
    seen = {name for name, _ in members}
    for pattern in ("adapter/*.json",):
        for path in sorted(package_dir.glob(pattern)):
            archive_name = path.relative_to(source_dir).as_posix()
            if archive_name in seen:
                continue
            seen.add(archive_name)
            members.append((archive_name, path.read_bytes()))

    members.append((f"{dist_info}/METADATA", metadata.encode("utf-8")))
    members.append((f"{dist_info}/WHEEL", wheel_metadata.encode("utf-8")))

    record_name = f"{dist_info}/RECORD"
    record_lines = [_record_line(name, data) for name, data in members]
    record_lines.append(f"{record_name},,")
    members.append((record_name, ("\n".join(record_lines) + "\n").encode("utf-8")))

    wheel_path = out_dir / f"{DIST_NAME}-{version}-py3-none-any.whl"
    if wheel_path.exists():
        wheel_path.unlink()
    with zipfile.ZipFile(wheel_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=ZIP_EPOCH)
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return wheel_path


def main(argv: list[str] | None = None) -> None:
    default_source = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(
        description="Build the migration_common wheel using only the Python standard library.",
    )
    parser.add_argument(
        "--source",
        default=str(default_source),
        help="Package source root containing the migration_common/ package (default: this directory)",
    )
    parser.add_argument(
        "--outdir",
        default="build/glue-lib",
        help="Directory to write the wheel into (default: build/glue-lib)",
    )
    args = parser.parse_args(argv)
    wheel_path = build_wheel(Path(args.source), Path(args.outdir))
    print(wheel_path)


if __name__ == "__main__":
    main()
