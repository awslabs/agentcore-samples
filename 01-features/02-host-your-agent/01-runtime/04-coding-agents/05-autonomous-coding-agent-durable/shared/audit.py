"""Audit trail for sandbox command execution.

Appends every executed command to a per-ticket JSON Lines audit log.
The audit log is append-only and stored in session storage (persists across
microVM restarts). It provides a complete forensic record of what the agent
did for each ticket.

Each entry includes: timestamp, action, command/path, exit_code, duration,
policy decision, and session context.
"""
import json
import os
import time
from typing import Optional


WORKSPACE = os.environ.get("WORKSPACE_PATH", "/mnt/workspace")
AUDIT_DIR = os.path.join(WORKSPACE, "audit")


def log_action(
    action: str,
    ticket_id: str,
    session_id: str = "",
    *,
    cmd: str = "",
    path: str = "",
    exit_code: Optional[int] = None,
    duration_ms: Optional[float] = None,
    policy_decision: str = "ALLOW",
    policy_reasons: Optional[list] = None,
    error: str = "",
    truncated: bool = False,
) -> None:
    """Append an audit entry to the per-ticket audit log.

    Args:
        action: The sandbox action (run_command, write_file, read_file, get_details)
        ticket_id: The ticket being processed
        session_id: The runtime session ID
        cmd: Command string (for run_command)
        path: File path (for file operations)
        exit_code: Process exit code (for run_command)
        duration_ms: Execution duration in milliseconds
        policy_decision: ALLOW or DENY
        policy_reasons: Cedar policy IDs that matched
        error: Error message if failed
        truncated: Whether output was truncated
    """
    entry = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "epoch": time.time(),
        "action": action,
        "ticket_id": ticket_id,
        "session_id": session_id,
        "policy_decision": policy_decision,
    }

    if cmd:
        entry["cmd"] = cmd[:500]  # Cap for log size
    if path:
        entry["path"] = path
    if exit_code is not None:
        entry["exit_code"] = exit_code
    if duration_ms is not None:
        entry["duration_ms"] = round(duration_ms, 1)
    if policy_reasons:
        entry["policy_reasons"] = policy_reasons
    if error:
        entry["error"] = error[:200]
    if truncated:
        entry["truncated"] = True

    _append_to_log(ticket_id, entry)


def _append_to_log(ticket_id: str, entry: dict) -> None:
    """Append a JSON line to the ticket's audit log file."""
    os.makedirs(AUDIT_DIR, exist_ok=True)
    # Sanitize ticket_id for filename safety
    safe_id = "".join(c for c in ticket_id if c.isalnum() or c in "-_")[:64]
    log_file = os.path.join(AUDIT_DIR, f"{safe_id}.jsonl")

    try:
        with open(log_file, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        # Audit logging should never crash the sandbox
        pass


def get_audit_log(ticket_id: str) -> list[dict]:
    """Read the audit log for a ticket (for inspection/debugging)."""
    safe_id = "".join(c for c in ticket_id if c.isalnum() or c in "-_")[:64]
    log_file = os.path.join(AUDIT_DIR, f"{safe_id}.jsonl")

    if not os.path.exists(log_file):
        return []

    entries = []
    try:
        with open(log_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError:
        pass
    return entries
