"""S3 access for the migration jobs: JSON reports and versioned JSONL staging.

Every write is SSE-AES256 encrypted. JSONL writers return the object's content hash,
byte size, record count, and S3 version id so the transform/load stage can reconcile
the immutable staged data against the extract manifest before it processes anything.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from botocore.config import Config
from botocore.exceptions import ClientError

from .util import USER_AGENT_EXTRA, json_dumps

# Largest staged object the reader will buffer in memory for read-ahead. Anything bigger is
# streamed line by line instead, so a wide `recordsPerObject` cannot turn prefetch into an OOM.
_MAX_PREFETCH_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class JsonLinesObject:
    """Integrity metadata for one staged JSONL object, embedded in the extract manifest."""

    key: str
    record_count: int
    sha256: str
    size_bytes: int
    version_id: str

    def as_dict(self) -> dict[str, Any]:
        """Return the camelCase manifest representation."""
        return {
            "key": self.key,
            "recordCount": self.record_count,
            "sha256": self.sha256,
            "sizeBytes": self.size_bytes,
            "versionId": self.version_id,
        }


class S3Store:
    """Thin wrapper over an S3 client for the migration's report and staging objects."""

    def __init__(self, s3_client: Any, bucket: str) -> None:
        self._client = s3_client
        self.bucket = bucket

    def location(self, key: str = "") -> str:
        """Return the ``s3://`` URI for ``key``, as reports quote it.

        The local store answers the same question with a filesystem path, which is why callers ask
        the store instead of formatting a URI themselves.
        """
        return f"s3://{self.bucket}/{key}" if key else f"s3://{self.bucket}"

    @classmethod
    def from_boto3(cls, boto3_module: Any, bucket: str, region: str | None = None) -> S3Store:
        """Create a store backed by an adaptively-retrying S3 client.

        ``region`` is the region the staging bucket lives in. Passed explicitly because the caller's
        default region is frequently not the engine's, and a client pointed at the wrong one has to
        be redirected on every request at best.
        """
        client = boto3_module.client(
            "s3",
            **({"region_name": region} if region else {}),
            config=Config(
                retries={"max_attempts": 10, "mode": "adaptive"},
                user_agent_extra=USER_AGENT_EXTRA,
            ),
        )
        return cls(client, bucket)

    def put_json(self, key: str, value: Any) -> None:
        """Write ``value`` as pretty-printed, encrypted JSON, overwriting any prior object."""
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json_dumps(value, indent=2).encode("utf-8"),
            ContentType="application/json",
            ServerSideEncryption="AES256",
        )

    def put_text(self, key: str, text: str, *, content_type: str = "text/plain") -> None:
        """Write ``text`` as an encrypted object, overwriting any prior object."""
        self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=text.encode("utf-8"),
            ContentType=content_type,
            ServerSideEncryption="AES256",
        )

    def put_json_if_absent(self, key: str, value: Any) -> None:
        """Write JSON only if ``key`` does not already exist (atomic run/attempt lock).

        Call this from one thread only. Unlike the other methods here it temporarily registers an
        event handler on the shared boto3 client (see below), and concurrent calls would leave the
        client's handler chain in an unpredictable state. Both jobs take their locks once during
        start-up, before any worker thread exists, so this holds today; keep it that way.
        """
        event_name = "before-sign.s3.PutObject"
        event_id = f"agent-registry-migration-if-none-match-{id(self)}"

        def add_if_none_match_header(request: Any, **_: Any) -> None:
            request.headers["If-None-Match"] = "*"

        # Glue's bundled botocore may not model PutObject.IfNoneMatch even though S3
        # supports the header. Inject it immediately before SigV4 signing so the write
        # remains atomic without depending on the installed service model version.
        self._client.meta.events.register_first(
            event_name,
            add_if_none_match_header,
            unique_id=event_id,
        )
        try:
            self._client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=json_dumps(value, indent=2).encode("utf-8"),
                ContentType="application/json",
                ServerSideEncryption="AES256",
            )
        except ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            code = error.response.get("Error", {}).get("Code")
            if status == 412 or code in {"PreconditionFailed", "ConditionalRequestConflict"}:
                raise RuntimeError(f"Immutable S3 key already exists: s3://{self.bucket}/{key}") from error
            raise
        finally:
            self._client.meta.events.unregister(event_name, unique_id=event_id)

    def list_keys(self, prefix: str) -> list[str]:
        """Return every object key under ``prefix``, sorted.

        Used to find a run's reports without asking the operator to remember a run id. The
        local store answers the same question from the filesystem.
        """
        keys: list[str] = []
        token: str | None = None
        while True:
            request: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                request["ContinuationToken"] = token
            response = self._client.list_objects_v2(**request)
            keys.extend(str(item["Key"]) for item in response.get("Contents", []))
            token = response.get("NextContinuationToken")
            if not token:
                break
        return sorted(keys)

    def get_json(self, key: str) -> Any:
        """Read and JSON-decode the object at ``key``."""
        response = self._client.get_object(Bucket=self.bucket, Key=key)
        return json.loads(response["Body"].read().decode("utf-8"))

    def get_json_if_present(self, key: str) -> Any:
        """Read and JSON-decode the object at ``key``, returning ``None`` when it does not exist.

        A missing object is an expected state for optional artifacts such as incremental-load
        watermarks; every other S3 error still propagates.
        """
        try:
            return self.get_json(key)
        except Exception as error:
            code = error.response.get("Error", {}).get("Code") if hasattr(error, "response") else None
            if code in {"NoSuchKey", "404", "NotFound"}:
                return None
            no_such_key = getattr(getattr(self._client, "exceptions", None), "NoSuchKey", None)
            if no_such_key is not None and isinstance(error, no_such_key):
                return None
            raise

    def put_json_lines(self, key: str, values: Iterable[Any]) -> dict[str, Any]:
        """Write ``values`` as a JSONL object; return its key, count, hash, size, and version id."""
        rows = [json_dumps(value) for value in values]
        body = ("\n".join(rows) + ("\n" if rows else "")).encode("utf-8")
        response = self._client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=body,
            ContentType="application/x-ndjson",
            ServerSideEncryption="AES256",
        )
        version_id = response.get("VersionId")
        if not version_id:
            raise RuntimeError(
                f"S3 did not return a VersionId for s3://{self.bucket}/{key}; "
                "the staging bucket must have versioning enabled"
            )
        return JsonLinesObject(
            key=key,
            record_count=len(rows),
            sha256=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
            version_id=str(version_id),
        ).as_dict()

    def inspect_json_lines_object(self, expected: dict[str, Any]) -> dict[str, Any]:
        """Stream a specific object version and recompute its count, hash, and size."""
        key = _required_text(expected, "key")
        version_id = _required_text(expected, "versionId")
        response = self._client.get_object(
            Bucket=self.bucket,
            Key=key,
            VersionId=version_id,
        )
        digest = hashlib.sha256()
        size_bytes = 0
        record_count = 0
        pending = b""
        for chunk in response["Body"].iter_chunks(chunk_size=1024 * 1024):
            if not chunk:
                continue
            digest.update(chunk)
            size_bytes += len(chunk)
            pending += chunk
            lines = pending.split(b"\n")
            pending = lines.pop()
            record_count += sum(1 for line in lines if line.strip())
        if pending.strip():
            record_count += 1
        return JsonLinesObject(
            key=key,
            record_count=record_count,
            sha256=digest.hexdigest(),
            size_bytes=size_bytes,
            version_id=version_id,
        ).as_dict()

    def iter_json_lines_objects(
        self,
        objects: Iterable[dict[str, Any]],
        *,
        read_ahead: int = 0,
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Yield ``(key, record)`` for each line of each pinned object version, in manifest order.

        With ``read_ahead`` above zero, that many following objects are fetched in the background
        while the current one is being consumed, so the next S3 GET is not a stall between batches
        of records. Objects are still yielded strictly in order, and only objects whose manifest
        ``sizeBytes`` is under ``_MAX_PREFETCH_BYTES`` are prefetched -- a large object is streamed
        as before rather than buffered, which keeps the memory bound at
        ``read_ahead * _MAX_PREFETCH_BYTES``.
        """
        pinned = list(objects)
        if read_ahead <= 0 or len(pinned) < 2:
            for expected in pinned:
                yield from self._iter_object_records(expected)
            return

        workers = min(int(read_ahead), len(pinned) - 1)
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="staged-read") as pool:
            prefetched: OrderedDict[int, Future[bytes]] = OrderedDict()
            next_to_submit = 0
            for index, expected in enumerate(pinned):
                # Keep the window full: submit the upcoming small objects, skip the big ones.
                while next_to_submit < len(pinned) and len(prefetched) < workers + 1:
                    candidate = pinned[next_to_submit]
                    if self._is_prefetchable(candidate):
                        prefetched[next_to_submit] = pool.submit(self._read_object_bytes, candidate)
                    next_to_submit += 1
                buffered = prefetched.pop(index, None)
                if buffered is None:
                    yield from self._iter_object_records(expected)
                else:
                    key = _required_text(expected, "key")
                    for raw_line in buffered.result().split(b"\n"):
                        record = self._decode_record(raw_line, key)
                        if record is not None:
                            yield key, record

    def _iter_object_records(
        self,
        expected: dict[str, Any],
    ) -> Iterator[tuple[str, dict[str, Any]]]:
        """Stream one pinned object version line by line, without buffering it."""
        key = _required_text(expected, "key")
        response = self._client.get_object(
            Bucket=self.bucket,
            Key=key,
            VersionId=_required_text(expected, "versionId"),
        )
        for raw_line in response["Body"].iter_lines():
            record = self._decode_record(raw_line, key)
            if record is not None:
                yield key, record

    def _read_object_bytes(self, expected: dict[str, Any]) -> bytes:
        """Read one pinned object version fully into memory (used only for prefetch)."""
        response = self._client.get_object(
            Bucket=self.bucket,
            Key=_required_text(expected, "key"),
            VersionId=_required_text(expected, "versionId"),
        )
        return response["Body"].read()

    @staticmethod
    def _is_prefetchable(expected: dict[str, Any]) -> bool:
        size_bytes = expected.get("sizeBytes")
        try:
            return 0 < int(size_bytes) <= _MAX_PREFETCH_BYTES
        except (TypeError, ValueError):
            return False

    def _decode_record(self, raw_line: bytes, key: str) -> dict[str, Any] | None:
        if not raw_line.strip():
            return None
        value = json.loads(raw_line.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object in s3://{self.bucket}/{key}")
        return value


class JsonArrayWriter:
    """Write a long sequence of records as a series of JSON-array objects.

    Report dumps are meant to be read and diffed by people and tools (``jq``, ``diff``), so each
    part is a self-contained pretty-printed JSON array rather than JSONL. Parts are flushed every
    ``chunk_size`` records so memory stays bounded no matter how many records a registry holds.
    """

    def __init__(self, store: S3Store, prefix: str, *, basename: str, chunk_size: int = 500) -> None:
        self._store = store
        self._prefix = prefix.rstrip("/")
        self._basename = basename
        self._chunk_size = max(1, int(chunk_size))
        self._buffer: list[Any] = []
        self._part = 0
        self.keys: list[str] = []
        self.record_count = 0

    def append(self, value: Any) -> None:
        self._buffer.append(value)
        self.record_count += 1
        if len(self._buffer) >= self._chunk_size:
            self._flush()

    def close(self) -> list[str]:
        if self._buffer:
            self._flush()
        return self.keys

    def _flush(self) -> None:
        # `<basename>-00000.json`, mirroring the raw staging convention (`part-00000.jsonl`).
        key = f"{self._prefix}/{self._basename}-{self._part:05d}.json"
        self._store.put_json(key, self._buffer)
        self.keys.append(key)
        self._part += 1
        self._buffer = []


def _required_text(value: dict[str, Any], key: str) -> str:
    selected = value.get(key)
    if selected in (None, ""):
        raise ValueError(f"Staging manifest object is missing {key}")
    return str(selected)
