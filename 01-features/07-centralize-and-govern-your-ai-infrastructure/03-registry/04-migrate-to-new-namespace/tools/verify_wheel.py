#!/usr/bin/env python3
"""Assert that a built ``migration_common`` wheel carries everything a Glue worker needs.

Glue installs this wheel from ``--extra-py-files`` and nothing else, so a missing module or data
file is not a build warning -- it is a job that fails at import time in production. Run it after
``npm run build:lib``::

    python3 tools/verify_wheel.py build/glue-lib

Exits 0 when the wheel is complete, 1 with the missing (or unexpected) entries listed.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

REQUIRED = (
    "migration_common/__init__.py",
    "migration_common/aws_auth.py",
    "migration_common/preflight.py",
    "migration_common/registry_api.py",
    "migration_common/registry_client.py",
    "migration_common/settings.py",
    "migration_common/storage.py",
    "migration_common/local_store.py",
    "migration_common/stores.py",
    "migration_common/adapter_defaults.py",
    "migration_common/transform.py",
    "migration_common/report_html.py",
    "migration_common/util.py",
    "migration_common/watermark.py",
    "migration_common/__main__.py",
    "migration_common/teardown.py",
    "migration_common/target_registry.py",
    "migration_common/jobs/__init__.py",
    "migration_common/jobs/extract.py",
    "migration_common/jobs/transform_load.py",
    # Service models are deliberately absent: boto3 supplies both control-plane models, so the
    # wheel carries none. See migration_common/registry_client.py.
    # The API contract the CDK app publishes to SSM and a local run reads directly. Missing it, a
    # local run cannot build an adapter and has nothing to fall back on.
    "migration_common/adapter/api-adapter.json",
)


def main(argv: list[str]) -> int:
    target = Path(argv[1] if len(argv) > 1 else "build/glue-lib")
    wheels = sorted(target.glob("*.whl"))
    if not wheels:
        print(f"error: no wheel found in {target}", file=sys.stderr)
        return 1
    # Exactly one, the same requirement lib/glue-python-library.ts enforces before it uploads. This
    # used to take the newest by mtime, which is ambiguous when two builds land in the same second
    # and quietly wrong when a stale wheel from an earlier version is still sitting in the directory
    # -- verifying the wrong file is worse than refusing to guess. build_wheel.py only removes the
    # exact filename it is about to write, so stale wheels do accumulate here.
    if len(wheels) != 1:
        print(
            f"error: expected exactly one wheel in {target}, found {len(wheels)}: "
            + ", ".join(wheel.name for wheel in wheels)
            + "\n       Remove the stale ones (or rebuild with: npm run build:lib) so it is "
            "unambiguous which wheel is being verified.",
            file=sys.stderr,
        )
        return 1
    wheel = wheels[0]

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
    entries = set(names)

    missing = [name for name in REQUIRED if name not in entries]
    # Tests must not ship to the worker: they are not needed there, and they would drag the test
    # fixtures along with them. Matched on any path segment named `tests`, not just `/tests/`: the
    # suite lives at glue/common/tests, a *sibling* of migration_common, so a `/tests/` substring
    # could only ever have matched a hypothetical migration_common/tests -- the check advertised
    # protection it could not provide. Anchoring on the segment also catches a top-level `tests/`.
    unexpected = sorted(
        name for name in entries if "tests" in Path(name).parts or name.endswith(("_test.py", "test_helpers.py"))
    )
    # A wheel with a duplicated member is malformed (and duplicates a RECORD line), which zipfile
    # will happily produce and pip will not reliably install.
    duplicates = sorted({name for name in names if names.count(name) > 1})

    print(f"{wheel.name}: {len(names)} entries")
    for name in missing:
        print(f"  MISSING  {name}")
    for name in unexpected:
        print(f"  UNEXPECTED  {name}")
    for name in duplicates:
        print(f"  DUPLICATED  {name}")
    if missing or unexpected or duplicates:
        return 1
    print(f"  all {len(REQUIRED)} required modules and data files present, no test files")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
