"""Local filesystem staging, for a migration run with no AWS infrastructure.

``S3Store`` and this class present the same surface to the jobs, so extract and transform/load do
not know which one they are using. What changes is only where the bytes live: a directory tree on
your machine instead of a versioned S3 bucket, laid out with the same keys
(``runs/``, ``reports/``, ``state/``), so the artifacts are the ones documented for a deployed run.

Two S3 behaviours the jobs depend on are reproduced rather than dropped:

* **Immutability.** ``put_json_if_absent`` is S3's ``If-None-Match: *`` in the bucket-backed store.
  Here it is ``os.open`` with ``O_CREAT | O_EXCL``, which is atomic on POSIX and on Windows, so a
  second run reusing a run id fails the same way instead of overwriting a report.
* **Pinning staged bytes.** S3 pins each staged object by version id. A local file has no versions,
  so the content hash *is* the version: ``put_json_lines`` records the SHA-256 as ``versionId``, and
  ``inspect_json_lines_object`` recomputes it from what is on disk now. Edit or truncate a staged
  file between extract and load and the reconciliation fails, which is the guarantee the version id
  was there to give.

Writes are not encrypted here, because there is no server to encrypt them: the file inherits your
directory's permissions. That is the trade you accept by staging locally, and it is why the reports
of a local run are worth putting somewhere you control.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .storage import JsonLinesObject
from .util import json_dumps


class LocalStore:
    """Filesystem-backed twin of :class:`~migration_common.storage.S3Store`."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        # Reports interpolate this into artifact locations. Naming it `bucket` keeps the two stores
        # interchangeable for callers that still format a location themselves.
        self.bucket = str(self.root)

    # -- locations ---------------------------------------------------------------------------
    def location(self, key: str = "") -> str:
        """Return the absolute path for ``key``, usable directly with ``cat``, ``jq`` or an editor."""
        return str(self._path(key)) if key else str(self.root)

    def _path(self, key: str) -> Path:
        """Resolve ``key`` under the root, refusing anything that escapes it."""
        candidate = (self.root / key).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"Refusing to touch {candidate}, which is outside {self.root}")
        return candidate

    def _write_bytes(self, key: str, body: bytes) -> Path:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file and replace, so a reader never sees a half-written report and
        # an interrupted run leaves the previous content intact.
        temporary = path.with_name(f".{path.name}.partial")
        temporary.write_bytes(body)
        os.replace(temporary, path)
        return path

    # -- writes ------------------------------------------------------------------------------
    def put_json(self, key: str, value: Any) -> None:
        """Write ``value`` as pretty-printed JSON, replacing any prior file."""
        self._write_bytes(key, json_dumps(value, indent=2).encode("utf-8"))

    def put_text(self, key: str, text: str, *, content_type: str = "text/plain") -> None:
        """Write ``text``, replacing any prior file. ``content_type`` is accepted and ignored."""
        self._write_bytes(key, text.encode("utf-8"))

    def put_json_if_absent(self, key: str, value: Any) -> None:
        """Write JSON only if ``key`` does not exist yet (the run/attempt lock)."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as error:
            raise RuntimeError(f"Immutable local file already exists: {path}") from error
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json_dumps(value, indent=2))

    def put_json_lines(self, key: str, values: Iterable[Any]) -> dict[str, Any]:
        """Write ``values`` as JSONL; return key, count, hash, size, and the hash as ``versionId``."""
        rows = [json_dumps(value) for value in values]
        body = ("\n".join(rows) + ("\n" if rows else "")).encode("utf-8")
        self._write_bytes(key, body)
        digest = hashlib.sha256(body).hexdigest()
        return JsonLinesObject(
            key=key,
            record_count=len(rows),
            sha256=digest,
            size_bytes=len(body),
            # No object versions on a filesystem: the content hash identifies the exact bytes, which
            # is what the load stage needs it for.
            version_id=digest,
        ).as_dict()

    # -- reads -------------------------------------------------------------------------------
    def list_keys(self, prefix: str) -> list[str]:
        """Return every file key under ``prefix``, sorted -- the filesystem twin of S3 listing.

        Walks only the subtree the prefix names. Walking the whole root and filtering afterwards --
        which is what this did -- costs the entire staging directory on every call, and the report
        commands call it once per run they consider.

        An S3 prefix is not a path, so the directory part is used to pick the subtree and the full
        prefix still filters the results: ``reports/run_id=`` walks ``reports/`` and keeps the keys
        that start with ``run_id=``.
        """
        head, separator, _ = prefix.rpartition("/")
        base = self._path(head) if separator else self.root
        if not base.is_dir():
            return []
        keys: list[str] = []
        for path in base.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            key = path.relative_to(self.root).as_posix()
            if key.startswith(prefix):
                keys.append(key)
        return sorted(keys)

    def get_json(self, key: str) -> Any:
        """Read and JSON-decode the file at ``key``."""
        return json.loads(self._path(key).read_text(encoding="utf-8"))

    def get_json_if_present(self, key: str) -> Any:
        """Read and JSON-decode the file at ``key``, or return ``None`` when it does not exist."""
        try:
            return self.get_json(key)
        except FileNotFoundError:
            return None

    def inspect_json_lines_object(self, expected: dict[str, Any]) -> dict[str, Any]:
        """Recompute a staged file's count, hash and size from what is on disk right now."""
        key = _required_text(expected, "key")
        path = self._path(key)
        if not path.is_file():
            raise RuntimeError(f"Staged object is missing: {path}")
        digest = hashlib.sha256()
        size_bytes = 0
        record_count = 0
        with path.open("rb") as handle:
            for line in handle:
                digest.update(line)
                size_bytes += len(line)
                if line.strip():
                    record_count += 1
        recomputed = digest.hexdigest()
        return JsonLinesObject(
            key=key,
            record_count=record_count,
            sha256=recomputed,
            size_bytes=size_bytes,
            version_id=recomputed,
        ).as_dict()

    def iter_json_lines_objects(
        self,
        objects: Iterable[dict[str, Any]],
        *,
        read_ahead: int = 0,
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Yield ``(key, record)`` for every line of every staged file, in manifest order.

        ``read_ahead`` is accepted for interface parity and ignored: it exists to hide S3 round-trip
        latency, and a local read has none worth hiding.
        """
        for expected in objects:
            key = _required_text(expected, "key")
            path = self._path(key)
            with path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    if not raw_line.strip():
                        continue
                    value = json.loads(raw_line)
                    if not isinstance(value, dict):
                        raise ValueError(f"Expected a JSON object per line in {path}")
                    yield key, value


def _required_text(value: dict[str, Any], key: str) -> str:
    selected = value.get(key)
    if selected in (None, ""):
        raise ValueError(f"Staging manifest object is missing {key}")
    return str(selected)
