"""Shared input validation for ticket IDs and paths.

Used by orchestrator, coding agent, and sandbox to enforce consistent
security rules. A single source of truth prevents divergence between
components.
"""
import os
import re


# Strict allowlist: alphanumeric, hyphens, underscores. 1-64 chars.
# No dots (prevents ../ tricks), no slashes, no whitespace, no null bytes.
TICKET_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Maximum ticket ID length (also enforced by the regex, but explicit for clarity)
MAX_TICKET_ID_LENGTH = 64


class ValidationError(ValueError):
    """Raised when input fails validation checks."""
    pass


def validate_ticket_id(ticket_id: str) -> str:
    """Validate a ticket ID against the strict allowlist.

    Args:
        ticket_id: The ticket identifier to validate.

    Returns:
        The validated ticket_id (unchanged).

    Raises:
        ValidationError: If the ticket ID is invalid.
    """
    if not ticket_id:
        raise ValidationError("ticket_id is required")

    if not isinstance(ticket_id, str):
        raise ValidationError(f"ticket_id must be a string, got {type(ticket_id).__name__}")

    # Check for null bytes (could bypass string checks in C-backed libs)
    if "\x00" in ticket_id:
        raise ValidationError("ticket_id contains null bytes")

    if len(ticket_id) > MAX_TICKET_ID_LENGTH:
        raise ValidationError(
            f"ticket_id too long ({len(ticket_id)} chars, max {MAX_TICKET_ID_LENGTH})"
        )

    if not TICKET_ID_PATTERN.match(ticket_id):
        raise ValidationError(
            f"ticket_id contains invalid characters: {ticket_id!r}. "
            f"Must match pattern: {TICKET_ID_PATTERN.pattern}"
        )

    return ticket_id


def validate_path_within_base(path: str, base: str) -> str:
    """Resolve a path and verify it stays within the given base directory.

    Handles both absolute and relative paths. Uses realpath to resolve
    symlinks and normalize traversal sequences.

    Args:
        path: The path to validate (absolute or relative to base).
        base: The base directory that the path must stay within.

    Returns:
        The resolved absolute path.

    Raises:
        ValidationError: If the resolved path escapes the base directory.
    """
    if not path:
        raise ValidationError("path is required")

    if not base:
        raise ValidationError("base directory is required")

    # Check for null bytes
    if "\x00" in path or "\x00" in base:
        raise ValidationError("path contains null bytes")

    if os.path.isabs(path):
        resolved = os.path.realpath(path)
    else:
        resolved = os.path.realpath(os.path.join(base, path))

    real_base = os.path.realpath(base)

    # Must equal the base exactly OR be a child of it (with os.sep boundary)
    if resolved != real_base and not resolved.startswith(real_base + os.sep):
        raise ValidationError(
            f"path escapes base directory: resolved={resolved!r}, base={real_base!r}"
        )

    return resolved
