"""Small, dependency-free helpers shared by the migration Glue jobs.

Covers logging setup, dotted-path access into nested dicts, timestamp parsing, deterministic S3
path-segment sanitization, and JSON serialization that tolerates the datetime,
Decimal, bytes, and set values that appear in registry payloads.
"""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import json
import logging
import re
from typing import Any

#: This tool's version, as it appears in the ``User-Agent`` of every AWS call it makes.
#:
#: One constant because there used to be three literals -- two saying 0.2.0, one saying 0.1.0, none
#: of them matching package.json or pyproject.toml. A user agent that disagrees with itself makes
#: this tool's traffic hard to identify in CloudTrail, which is the only reason to set one.
#: ``test_settings`` checks it against pyproject.toml so the two cannot drift again.
VERSION = "0.1.0"

#: Ready-to-use ``botocore.config.Config(user_agent_extra=...)`` value.
USER_AGENT_EXTRA = f"agent-registry-migration/{VERSION}"

# Keys that are safe to echo into manifests and reports. Deliberately excludes
# externalId so the report artifacts never carry the cross-account trust secret.
_PUBLIC_ENDPOINT_FIELDS = frozenset({"accountId", "region", "registryId", "registryArn", "roleArn"})

#: Libraries whose INFO output is about their own internals, not about the migration.
#:
#: ``botocore`` is the one that matters. Where a control-plane model arrives without an endpoint
#: ruleset, every client this tool builds makes botocore log "No endpoints ruleset found for service
#: ..., falling back to legacy endpoint routing" at INFO. That is expected, it is
#: not actionable, and it appeared on every single command -- three times in one short session log --
#: which teaches an operator to skim past the lines that do matter.
_THIRD_PARTY_LOGGERS = ("boto3", "botocore", "s3transfer", "urllib3")


def configure_logging(level: int = logging.INFO) -> None:
    """Configure logging for an entry point, keeping third-party INFO chatter out of the output.

    ``logging.basicConfig`` configures the *root* logger, so raising this tool's own level to INFO
    raised every library's too. Each entry point called it separately, which also meant the three
    copies could drift. This is that one call plus the third-party levels, so the output an operator
    reads is the migration's own.

    Warnings and errors from those libraries still come through: a throttling retry or a TLS problem
    is the operator's business, a routing implementation detail is not.
    """
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
    for name in _THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def get_path(value: Any, path: str | None, default: Any = None) -> Any:
    """Return the value at a dotted ``path`` within nested dicts, or ``default``."""
    if not path:
        return value
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def set_path(target: dict[str, Any], path: str | None, value: Any) -> None:
    """Set ``value`` at a dotted ``path``, creating intermediate dicts as needed.

    An empty path merges ``value`` (which must be a dict) into ``target``.
    """
    if not path:
        if not isinstance(value, dict):
            raise ValueError("A root request value must be an object")
        target.update(value)
        return
    current = target
    parts = path.split(".")
    for part in parts[:-1]:
        child = current.get(part)
        if child is None:
            child = {}
            current[part] = child
        if not isinstance(child, dict):
            raise ValueError(f"Cannot set nested path {path}: {part} is not an object")
        current = child
    current[parts[-1]] = value


def parse_timestamp(value: Any) -> dt.datetime:
    """Parse a datetime, epoch number, or ISO-8601 string into a UTC datetime.

    A trailing ``Z`` (either case) is the only one rewritten, and only where it belongs. This used to
    be ``value.replace("Z", "+00:00")``, which substitutes *every* ``Z`` in the string -- harmless
    for a well-formed timestamp, wrong for anything else, and it silently accepted no lowercase
    ``z`` at all even though RFC 3339 permits one.
    """
    if isinstance(value, dt.datetime):
        parsed = value
    elif isinstance(value, bool):
        # bool is a subclass of int, and `True` as an epoch is never what anybody meant.
        raise ValueError(f"Unsupported timestamp value: {value!r}")
    elif isinstance(value, (int, float, decimal.Decimal)):
        parsed = dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc)
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized[-1:] in ("Z", "z"):
            normalized = f"{normalized[:-1]}+00:00"
        parsed = dt.datetime.fromisoformat(normalized)
    else:
        raise ValueError(f"Unsupported timestamp value: {value!r}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string with a ``Z`` suffix."""
    return dt.datetime.now(tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def safe_segment(value: Any, fallback: str = "unknown") -> str:
    """Sanitize ``value`` into a bounded, filesystem/S3-safe path segment."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", str(value or fallback)).strip("-.")
    return cleaned[:128] or fallback


def public_endpoint(endpoint: dict[str, Any]) -> dict[str, Any]:
    """Project an endpoint down to the non-sensitive fields used in reports."""
    return {key: value for key, value in endpoint.items() if key in _PUBLIC_ENDPOINT_FIELDS}


def json_dumps(value: Any, *, indent: int | None = None) -> str:
    """Serialize ``value`` to JSON, compact by default and pretty when ``indent`` is set.

    ``indent`` is compared against ``None`` rather than tested for truthiness: ``indent=0`` is a
    legitimate request for newline-separated output with no indentation, and a truthiness test
    silently turned it into the compact single-line form.
    """
    return json.dumps(
        value,
        default=json_default,
        separators=None if indent is not None else (",", ":"),
        indent=indent,
    )


def json_default(value: Any) -> Any:
    """Fallback serializer for datetime, Decimal, bytes, and set values.

    ``bytes`` is decoded as UTF-8 where it can be, and base64-encoded where it cannot. A strict
    decode was the whole handler before, so a single non-UTF-8 byte anywhere in a registry payload
    raised ``UnicodeDecodeError`` from inside a report write -- losing the report for a run whose
    records had already been migrated.
    """
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            # Marked so a reader can tell an encoded blob from text that happened to look like one.
            return {
                "__type__": "base64",
                "value": base64.b64encode(bytes(value)).decode("ascii"),
            }
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
