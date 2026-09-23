"""Cedar policy engine for the sandbox runtime.

Evaluates Cedar policies against every action BEFORE execution.
This provides deterministic, auditable authorization that cannot be
bypassed by prompt injection (unlike prompt-based guardrails).

The policy engine:
  - Loads Cedar policies from a file (updateable without code changes)
  - Evaluates each sandbox action against the policies
  - Returns structured deny reasons that feed back to the agent
  - Logs all authorization decisions for audit

Default posture: DENY unless a permit policy explicitly allows the action.
Forbid policies override permits (forbid-wins semantics).
"""
import os
import json
import time
import logging
from typing import Optional

try:
    import cedarpy
    CEDAR_AVAILABLE = True
except ImportError:
    CEDAR_AVAILABLE = False

logger = logging.getLogger(__name__)

# Default policy file path (can be overridden via env var)
POLICY_FILE = os.environ.get(
    "CEDAR_POLICY_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "policies", "sandbox.cedar")
)

# Whether to enforce policies (ENFORCE) or just log decisions (AUDIT)
POLICY_MODE = os.environ.get("CEDAR_POLICY_MODE", "ENFORCE")

# Cache the loaded policies
_policies_cache: Optional[str] = None
_policies_mtime: float = 0.0


def _load_policies() -> str:
    """Load Cedar policies from file, with caching based on mtime."""
    global _policies_cache, _policies_mtime

    if not os.path.exists(POLICY_FILE):
        logger.warning(f"Cedar policy file not found: {POLICY_FILE}")
        return ""

    mtime = os.path.getmtime(POLICY_FILE)
    if _policies_cache is not None and mtime == _policies_mtime:
        return _policies_cache

    with open(POLICY_FILE) as f:
        _policies_cache = f.read()
    _policies_mtime = mtime
    logger.info(f"Loaded Cedar policies from {POLICY_FILE} ({len(_policies_cache)} bytes)")
    return _policies_cache


def authorize(action: str, context: dict) -> tuple[bool, str, list[str]]:
    """Evaluate a Cedar policy for a sandbox action.

    Args:
        action: The action being performed (run_command, write_file, read_file, get_details)
        context: Action-specific context (cmd, path, cwd, timeout, etc.)

    Returns:
        Tuple of (allowed: bool, reason: str, matching_policies: list[str])
        - allowed: whether the action is permitted
        - reason: human-readable explanation (empty if allowed)
        - matching_policies: list of policy IDs that matched
    """
    if not CEDAR_AVAILABLE:
        logger.warning("cedarpy not installed — policy enforcement disabled (ALLOW ALL)")
        return True, "", []

    policies = _load_policies()
    if not policies:
        # No policies loaded — fail open with warning (configurable)
        if POLICY_MODE == "ENFORCE":
            logger.error("No policies loaded in ENFORCE mode — denying by default")
            return False, "No Cedar policies loaded (fail-closed)", []
        return True, "", []

    # Build the Cedar authorization request
    request = {
        "principal": 'Principal::"coding-agent"',
        "action": f'Action::"{action}"',
        "resource": 'Resource::"sandbox"',
        "context": _sanitize_context(context),
    }

    try:
        result = cedarpy.is_authorized(request, policies, entities=[])
    except Exception as e:
        logger.error(f"Cedar evaluation error: {e}")
        # Fail closed on evaluation errors in ENFORCE mode
        if POLICY_MODE == "ENFORCE":
            return False, f"Policy evaluation error: {e}", []
        return True, "", []

    decision = result.decision
    reasons = list(result.diagnostics.reasons) if result.diagnostics else []
    errors = list(result.diagnostics.errors) if result.diagnostics else []

    allowed = (decision == cedarpy.Decision.Allow)

    # Build human-readable deny reason
    reason = ""
    if not allowed:
        if reasons:
            reason = f"Denied by policy: {', '.join(reasons)}"
        else:
            reason = "Denied by policy (no matching permit rule)"

    # Log the decision
    log_entry = {
        "event": "cedar_authorization",
        "action": action,
        "decision": "ALLOW" if allowed else "DENY",
        "reasons": reasons,
        "errors": errors,
        "context_summary": _context_summary(action, context),
        "mode": POLICY_MODE,
        "timestamp": time.time(),
    }
    if allowed:
        logger.debug(json.dumps(log_entry))
    else:
        logger.warning(json.dumps(log_entry))

    # In AUDIT mode, log but don't enforce
    if POLICY_MODE == "AUDIT" and not allowed:
        logger.info(f"AUDIT MODE: Would have denied {action} — {reason}")
        return True, "", reasons

    return allowed, reason, reasons


def _sanitize_context(context: dict) -> dict:
    """Prepare context for Cedar evaluation.

    Cedar context values must be primitives or records.
    Truncate long strings to avoid policy evaluation overhead.
    """
    sanitized = {}
    for key, value in context.items():
        if isinstance(value, str):
            # Truncate long values (Cedar pattern matching still works on prefix)
            sanitized[key] = value[:2000]
        elif isinstance(value, (int, float, bool)):
            sanitized[key] = value
        elif isinstance(value, dict):
            # One level of nesting only
            sanitized[key] = {k: str(v)[:500] for k, v in value.items()}
        # Skip complex types (lists, None, etc.)
    return sanitized


def _context_summary(action: str, context: dict) -> str:
    """Create a short summary for logging (avoid logging full file contents)."""
    if action == "run_command":
        return f"cmd={context.get('cmd', '')[:100]}"
    elif action in ("write_file", "read_file"):
        return f"path={context.get('path', '')}"
    return f"action={action}"


def validate_policies() -> tuple[bool, list[str]]:
    """Validate the loaded Cedar policies for syntax errors.

    Returns:
        Tuple of (valid: bool, errors: list[str])
    """
    if not CEDAR_AVAILABLE:
        return False, ["cedarpy not installed"]

    policies = _load_policies()
    if not policies:
        return False, ["No policy file found"]

    try:
        result = cedarpy.validate_policies(policies, schema="")
        # validate_policies returns validation errors if any
        if result and hasattr(result, 'errors') and result.errors:
            return False, [str(e) for e in result.errors]
        return True, []
    except Exception as e:
        return False, [str(e)]
