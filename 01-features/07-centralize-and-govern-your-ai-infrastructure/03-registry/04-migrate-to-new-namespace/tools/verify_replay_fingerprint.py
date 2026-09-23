#!/usr/bin/env python3
"""Assert that the replay fingerprint agrees across every way this tool can be installed.

A live load refuses to run unless the transform + target adapter fingerprint matches the one recorded
when the records were staged. Part of that fingerprint is ``implementationHash``: a hash of the
runtime Python under ``glue/``, computed in three independent places.

    checkout   migration_common.adapter_defaults.implementation_hash()
    packaged   the same function, running from an npm tarball, resolving its own tree
    deployed   migrationImplementationHash() in lib/migration-engine-stack.ts, baked into the
               <prefix>/adapter parameter at synth time

They must be identical, because a run staged one way has to be loadable the other way -- extract
locally, load on Glue, or the reverse. Nothing in the unit suite can catch a disagreement: the
packaged value depends on ``files[]`` in package.json, so shipping one ``.py`` too many or too few
changes it while every test stays green. The failure is silent until someone's cutover is blocked
by ``Replay configuration validation failed``.

    npm run verify:fingerprint

or directly, against artifacts you already have:

    python3 tools/verify_replay_fingerprint.py --tarball <pkg.tgz> --template <template.json>

Exits 0 when all three agree, 1 with the differing values listed.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIBRARY_DIR = REPO_ROOT / "glue" / "common"

# Matches the hash in a synthesized template, where the adapter parameter's value is a JSON string
# and its quotes may be escaped (JSON template) or not (YAML template).
_TEMPLATE_HASH = re.compile(r'implementationHash\\?"\s*:\s*\\?"([0-9a-f]{64})')

DEFAULT_TEMPLATE = REPO_ROOT / "cdk.out" / "AgentRegistryMigrationEngine.template.json"


def checkout_hash() -> str:
    """The hash as the checkout computes it."""
    sys.path.insert(0, str(LIBRARY_DIR))
    from migration_common.adapter_defaults import implementation_hash

    return implementation_hash()


def packaged_hash(tarball: Path) -> str:
    """The hash as an installed package computes it, from its own extracted tree.

    Deliberately runs the *packaged* module in a subprocess rather than calling the checkout's
    function with a different root: that proves the shipped layout is one ``repository_root()`` can
    resolve at all, which is the half of this that a path-only comparison would miss.
    """
    with tempfile.TemporaryDirectory() as directory:
        with tarfile.open(tarball) as archive:
            _safe_extract(archive, directory)
        package = Path(directory) / "package"
        library = package / "glue" / "common"
        if not (library / "migration_common").is_dir():
            raise SystemExit(
                f"{tarball} does not ship glue/common/migration_common. Check 'files' in "
                "package.json -- an installed run cannot compute its fingerprint without it."
            )
        # `python -c` puts the *current directory* first on sys.path, ahead of PYTHONPATH. Run from
        # a directory that itself holds a `migration_common/` -- glue/common, say -- the subprocess
        # would import the checkout and report its hash as the packaged one, so this check would
        # agree with itself having never opened the tarball. `cwd` points somewhere with no
        # importable package, and PYTHONSAFEPATH stops the CWD being added at all on 3.11+.
        environment = {**os.environ, "PYTHONPATH": str(library), "PYTHONSAFEPATH": "1"}
        environment.pop("PYTHONHOME", None)
        # The executable, program and flags are constants owned by this repository; no archive or
        # command-line value reaches argv, and shell=False is explicit. A subprocess is required to
        # prove the packaged module resolves from its own isolated interpreter rather than from
        # modules already imported by this verification process.
        result = subprocess.run(  # nosec B603  # nosemgrep: dangerous-subprocess-use-audit
            [
                sys.executable,
                "-c",
                ("from migration_common.adapter_defaults import implementation_hash;print(implementation_hash())"),
            ],
            capture_output=True,
            text=True,
            cwd=directory,
            env=environment,
            shell=False,
            check=False,
        )
        if result.returncode != 0:
            raise SystemExit(
                "The packaged tree could not compute its own fingerprint, so an installed run "
                f"cannot live-load anything:\n{result.stderr.strip()}"
            )
        return result.stdout.strip()


def _safe_extract(archive: tarfile.TarFile, destination: str) -> None:
    """Extract only regular files and directories contained by ``destination``.

    This intentionally avoids :meth:`TarFile.extractall`, including on Python versions whose
    extraction filter is unavailable. Pre-validating every member before writing anything rejects
    traversal paths, symlinks, hard links, devices and FIFOs, so a malicious npm tarball cannot
    redirect a later member outside the temporary directory.
    """
    root = Path(destination).resolve()
    members: list[tuple[tarfile.TarInfo, Path]] = []
    for member in archive.getmembers():
        target = (root / member.name).resolve()
        if root not in target.parents and target != root:
            raise SystemExit(f"Refusing to extract {member.name}: it escapes {root}")
        if not (member.isdir() or member.isfile()):
            raise SystemExit(f"Refusing to extract {member.name}: only regular files and directories are allowed")
        members.append((member, target))

    for member, target in members:
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit(f"Could not read regular archive member {member.name}")
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output)


def deployed_hash(template: Path) -> str:
    """The hash the CDK baked into the adapter parameter at synth time."""
    if not template.is_file():
        raise SystemExit(
            f"No synthesized template at {template}. Produce one first:\n"
            "  npx cdk synth -c config=config/migration.example.json"
        )
    found = set(_TEMPLATE_HASH.findall(template.read_text(encoding="utf-8")))
    if not found:
        raise SystemExit(
            f"No implementationHash in {template}. The stack publishes it in the "
            "<prefix>/adapter parameter; if that changed, update this check with it."
        )
    if len(found) > 1:
        # First-match-wins would hide this. A template carrying two different hashes means two
        # adapters disagreeing inside one deployment, which is worse than any mismatch this tool
        # was written to find.
        raise SystemExit(
            f"{template} carries {len(found)} different implementationHash values "
            f"({', '.join(sorted(found))}). One deployment cannot publish two fingerprints."
        )
    return found.pop()


def find_tarball() -> Path:
    matches = sorted(glob.glob(str(REPO_ROOT / "aws-agent-registry-migration-*.tgz")))
    if not matches:
        raise SystemExit("No package tarball found. Produce one first:\n  npm pack")
    return Path(matches[-1])


#: How recently a tarball must have been written for --clean to treat it as this run's own output.
#: `npm run verify:fingerprint` runs `npm pack` immediately before this, so seconds is generous.
_RECENT_TARBALL_SECONDS = 600


def _is_recent(path: Path) -> bool:
    """Whether ``path`` was written recently enough to be this invocation's own artifact."""
    return (time.time() - path.stat().st_mtime) < _RECENT_TARBALL_SECONDS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tarball", help="npm pack output (default: the newest in the repo root)")
    parser.add_argument("--template", help=f"synthesized template (default: {DEFAULT_TEMPLATE})")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="delete the tarball afterwards, for use from an npm script",
    )
    options = parser.parse_args(argv)

    tarball = Path(options.tarball) if options.tarball else find_tarball()
    template = Path(options.template) if options.template else DEFAULT_TEMPLATE

    # Recorded before the work, so --clean can only remove a tarball this invocation is responsible
    # for. It used to delete whatever the glob found, which is fine when `npm run verify:fingerprint`
    # has just produced one and destructive when an operator had staged a tarball in the repo root.
    # A --tarball the caller named is never deleted, and neither is one older than this run.
    removable = tarball if not options.tarball and tarball.is_file() and _is_recent(tarball) else None

    hashes = {
        "checkout": checkout_hash(),
        "packaged": packaged_hash(tarball),
        "deployed": deployed_hash(template),
    }
    width = max(len(name) for name in hashes)
    for name, value in hashes.items():
        print(f"  {name:<{width}}  {value}")

    if options.clean and removable is not None:
        removable.unlink(missing_ok=True)
    elif options.clean:
        print(
            f"\nkept {tarball.name}: --clean only removes a tarball this run produced",
            file=sys.stderr,
        )

    if len(set(hashes.values())) != 1:
        print(
            "\nThe replay fingerprint differs between install methods, so a run staged one way "
            "cannot be live-loaded the other.\n"
            "Most likely cause: 'files' in package.json ships a different set of runtime .py "
            "files than the checkout has, or than lib/migration-engine-stack.ts hashes "
            "(IMPLEMENTATION_HASH_EXCLUDED_DIRS).",
            file=sys.stderr,
        )
        return 1
    print("\nreplay fingerprint agrees across checkout, package and deployment")
    return 0


if __name__ == "__main__":
    sys.exit(main())
