"""Path security — scoped file access enforcement for the coding agent.

All file operations are validated against ALLOWED_PATHS before execution.
This is defense-in-depth: even if the Claude SDK's cwd scoping is bypassed,
this layer catches traversal attempts.
"""
import os
import sys

# Add shared module to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared_libs"))
from shared.validation import validate_ticket_id, validate_path_within_base, ValidationError


MOUNT_BASE = os.environ.get("MOUNT_PATH", "/mnt/shared")

_allowed_paths: list[str] = []


def configure(ticket_id: str) -> str:
    """Configure allowed paths for this ticket. Returns the ticket work directory."""
    try:
        validate_ticket_id(ticket_id)
    except ValidationError as e:
        raise PermissionError(f"Access denied: {e}")

    ticket_dir = os.path.realpath(os.path.join(MOUNT_BASE, ticket_id))
    _allowed_paths.clear()
    _allowed_paths.extend([
        ticket_dir,
        # Add other allowed paths here if needed (e.g. a shared workspace)
        # "/mnt/workspace",
    ])
    os.makedirs(ticket_dir, exist_ok=True)
    return ticket_dir


def check_path(path: str, base: str | None = None) -> str:
    """Resolve a path and verify it falls within an allowed prefix.

    Args:
        path: The path to check (absolute or relative).
        base: If path is relative, resolve against this base. Defaults to first allowed path.

    Returns:
        The resolved absolute path.

    Raises:
        PermissionError: If the resolved path escapes all allowed prefixes.
    """
    if not _allowed_paths:
        raise PermissionError("Access denied: path security not configured (call configure first)")

    if os.path.isabs(path):
        full = os.path.realpath(path)
    else:
        resolve_base = base or _allowed_paths[0]
        full = os.path.realpath(os.path.join(resolve_base, path))

    for allowed in _allowed_paths:
        if full == allowed or full.startswith(allowed + os.sep):
            return full

    raise PermissionError(
        f"Access denied: path traversal attempt. "
        f"Resolved path {full!r} is outside allowed paths {_allowed_paths}"
    )


def safe_read(relative_path: str, base: str | None = None) -> str:
    """Read a file, enforcing path confinement."""
    full = check_path(relative_path, base)
    if not os.path.exists(full):
        raise FileNotFoundError(f"Not found: {full}")
    return open(full).read()


def safe_write(relative_path: str, data: str, base: str | None = None) -> str:
    """Write a file, enforcing path confinement. Returns the resolved path."""
    full = check_path(relative_path, base)
    os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
    with open(full, "w") as f:
        f.write(data)
    return full


def get_allowed_paths() -> list[str]:
    """Return the current allowed paths (read-only copy)."""
    return list(_allowed_paths)
