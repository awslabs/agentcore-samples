"""Build the adapter document locally, with no deployment to read it from.

A deployed run reads ``<prefix>/adapter`` from SSM: the API wire contract, the transform rules, and
an implementation hash, all published by the CDK stack. A local run has no stack, so it builds the
same document here.

Two things keep the two paths honest:

* The API contract is not duplicated. Both sides read ``adapter/api-adapter.json`` from this
  package -- the stack imports it and publishes it, this module reads it directly. There is one
  definition, so a local run and a Glue run cannot disagree about the contract.
* ``implementationHash`` is computed with the same algorithm the stack uses (sorted relative paths
  and file bytes of the runtime Python under ``glue/``, excluding tests), so a locally built adapter
  and a deployed one produce the *same* replay fingerprint for the same code. That means a run
  extracted locally can even be loaded by a deployed job, and a drifting checkout is still caught.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

# Mirrors IMPLEMENTATION_HASH_EXCLUDED_DIRS in lib/migration-engine-stack.ts. Test code is excluded
# deliberately: hashing it would make an in-flight extract un-loadable because someone added a test.
_EXCLUDED_DIRS = {"tests", "__pycache__"}

_ADAPTER_FILE = Path(__file__).resolve().parent / "adapter" / "api-adapter.json"

# Mirrors the defaults in lib/config.ts. A local run may override them from its config file, exactly
# as a deployment does.
DEFAULT_TRANSFORM: dict[str, Any] = {
    "namePrefix": "migrated",
    "allowedRecordTypes": ["AGENT", "MCP", "SKILL", "CUSTOM"],
    "passthroughFields": ["description"],
}


class AdapterDefaultsError(RuntimeError):
    """Raised when the bundled API contract cannot be read."""


def api_adapter() -> dict[str, Any]:
    """Return the ``api`` section: the Preview and target wire contracts, from the shared JSON file."""
    try:
        document = json.loads(_ADAPTER_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise AdapterDefaultsError(
            f"The bundled API contract is missing at {_ADAPTER_FILE}. It ships inside the "
            "migration_common package; reinstall or rebuild the wheel "
            "(npm run verify:lib checks for it)."
        ) from error
    except json.JSONDecodeError as error:
        raise AdapterDefaultsError(f"{_ADAPTER_FILE} is not valid JSON: {error}") from error
    for section in ("preview", "target"):
        if not isinstance(document.get(section), dict):
            raise AdapterDefaultsError(f"{_ADAPTER_FILE} has no {section!r} object")
    return {"preview": document["preview"], "target": document["target"]}


def repository_root() -> Path | None:
    """Return the checkout's ``glue`` directory, or ``None`` when running from an installed wheel.

    ``migration_common`` lives at ``glue/common/migration_common`` in a checkout, so the runtime
    tree the stack hashes is two levels up. Under Glue the package is installed from a wheel and
    that layout does not exist, which is why the caller must tolerate ``None``.
    """
    candidate = Path(__file__).resolve().parents[2]
    if candidate.name == "glue" and (candidate / "common" / "migration_common").is_dir():
        return candidate
    return None


def implementation_hash(root: Path | None = None) -> str:
    """Hash the runtime Python under ``root`` the way the CDK stack does.

    Same algorithm as ``migrationImplementationHash()`` in lib/migration-engine-stack.ts: walk for
    ``*.py`` skipping test and cache directories, sort by path, then feed each file's path relative
    to the root and its bytes into one SHA-256, NUL-separated.
    """
    tree = root or repository_root()
    if tree is None:
        raise AdapterDefaultsError(
            "Cannot compute the implementation hash: the runtime Python tree is not on disk, which "
            "means this is an installed wheel rather than a checkout. Local mode is meant to be run "
            "from a clone of this repository."
        )
    files = sorted(_python_files(tree))
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(tree)).replace(os.sep, "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _python_files(directory: Path) -> list[Path]:
    """Collect runtime ``*.py`` files under ``directory``, skipping test and cache directories.

    Symlinks are skipped, and skipped for a specific reason: ``collectPythonFiles`` in
    lib/migration-engine-stack.ts walks with ``fs.readdirSync(..., {withFileTypes: true})``, and a
    ``Dirent`` for a symlink reports ``isFile()`` and ``isDirectory()`` as *both* false -- so the
    TypeScript walker neither hashes a symlinked file nor descends into a symlinked directory.
    ``Path.is_file()`` and ``Path.is_dir()`` follow symlinks, so without this check the two walkers
    would disagree the moment one appeared. They must not: the hash they produce is the replay
    fingerprint that binds a staged extract to the code that staged it, and a disagreement makes an
    extract staged by one side unloadable by the other.
    """
    files: list[Path] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if entry.is_symlink():
            continue
        if entry.is_dir():
            if entry.name in _EXCLUDED_DIRS:
                continue
            files.extend(_python_files(entry))
        elif entry.is_file() and entry.name.endswith(".py"):
            files.append(entry)
    return files


def local_adapter(
    *,
    transform: dict[str, Any] | None = None,
    staging_directory: str | None = None,
) -> dict[str, Any]:
    """Build the adapter document a local run uses, shaped exactly like the deployed parameter.

    ``transform`` overrides the defaults the same way ``runtime.transform`` does in a deployment.
    ``staging_directory`` is recorded under ``engine`` so a report can say where the run was staged,
    mirroring how a deployment records the bucket it created.
    """
    merged_transform = dict(DEFAULT_TRANSFORM)
    for key, value in (transform or {}).items():
        if value is not None:
            merged_transform[key] = value
    merged_transform["implementationHash"] = implementation_hash()

    engine: dict[str, Any] = {"deploymentId": "local", "mode": "local"}
    if staging_directory:
        engine["stagingDirectory"] = str(staging_directory)

    return {
        "schemaVersion": 1,
        "engine": engine,
        "transform": merged_transform,
        "api": api_adapter(),
    }
