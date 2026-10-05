#!/usr/bin/python3 -I
"""Org policy hook (dev box): block direct resource access from Claude Code's Bash tool,
block secrets written into files (Write/Edit/MultiEdit/NotebookEdit), and keep the agent
away from the box's own setup (managed settings, the AWS config, /opt).

Unix permissions already stop the dev user writing
/etc/claude-code, /etc/devbox and /opt; this hook is the guard rail that explains why."""

import json
import os
import re
import sys

SECRETS = [
    (
        r"\b(postgres(ql)?|mysql|mariadb|mongodb(\+srv)?|rediss?|mssql|sqlserver)://|jdbc:[a-z0-9]+:",
        "database connection strings aren't allowed",
    ),
    (r"\b(AKIA|ASIA)[0-9A-Z]{16}\b", "AWS access keys can't be used"),
    (r"(?i)\baws_(secret_access_key|session_token|access_key_id)\s*=", "setting AWS credentials isn't allowed"),
]
# The IdC token cache lives in ~/.aws/sso on the box.
CRED_FILES = (r"\.aws/(credentials|config|sso)\b|\.ssh/|\.pgpass\b", "credential files are off limits")
ORG_SETUP = "the org's Claude Code setup (managed settings, MCP servers, plugins, this hook) can't be changed from here"
BOX_PATHS = r"/etc/claude-code\b|/etc/devbox\b|(^|[\s'\"=:;|&(])/opt(/|\b)"
OWN_IDENTITY = "only this session's own AWS identity may be used"
AWS_VARS = r"AWS_(PROFILE|DEFAULT_PROFILE|CONFIG_FILE|SHARED_CREDENTIALS_FILE)"


def bash_rules():
    return [
        (
            r"\b(psql|pg_dump|mysql|mysqldump|mongosh|mongo|redis-cli|sqlcmd|sqlplus|cqlsh)\b",
            "direct database clients aren't allowed; use the approved org tools",
        ),
        *SECRETS,
        (r"--profile\b", OWN_IDENTITY),
        # Re-pointing or clearing the AWS SDK settings would sidestep AWS_PROFILE=devbox.
        (
            r"\b"
            + AWS_VARS
            + r"\s*=|\bunset\b[^;|&]*\b"
            + AWS_VARS
            + r"\b|\benv\b[^;|&]*\s(-u\s*|--unset[= ])"
            + AWS_VARS
            + r"\b|\benv\s+(-i|--ignore-environment)\b",
            OWN_IDENTITY,
        ),
        CRED_FILES,
        (
            BOX_PATHS
            + r"|\bclaude\s+(mcp|plugins?)\b|\bclaude\b[^|;&]*--(mcp-config|strict-mcp-config|plugin-dir|settings)\b",
            ORG_SETUP,
        ),
    ]


def protected_path(path, cwd):
    if not path:
        return False
    joined = os.path.join(cwd or "/", path)
    # As written and as resolved: a symlink in the workspace can point into /etc/claude-code.
    for full in {os.path.normpath(joined), os.path.realpath(joined)}:
        if re.match(r"/(etc/claude-code|etc/devbox|opt)(/|\Z)", full):
            return True
        if re.search(r"(^|/)\.claude/settings(\.local)?\.json\Z|managed-(mcp|settings)\.json\Z", full):
            return True
    return False


def decide(event):
    """Returns the deny reason, or None to let the call through."""
    tool = event.get("tool_name", "Bash")
    inp = event.get("tool_input") or {}
    if tool == "Bash":
        text = inp.get("command", "")
        rules = bash_rules()
    else:  # a file write: check where it goes and what goes into it
        parts = [
            inp.get("file_path", ""),
            inp.get("notebook_path", ""),
            inp.get("content", ""),
            inp.get("new_string", ""),
            inp.get("new_source", ""),
        ]
        parts += [e.get("new_string", "") for e in inp.get("edits") or [] if isinstance(e, dict)]
        text = "\n".join(p for p in parts if isinstance(p, str))
        rules = [*SECRETS, CRED_FILES]
        for p in (inp.get("file_path"), inp.get("notebook_path")):
            if isinstance(p, str) and protected_path(p, event.get("cwd")):
                return ORG_SETUP  # where it goes is enough
    if not isinstance(text, str):
        return None
    for pattern, reason in rules:
        if re.search(pattern, text):
            return reason
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    reason = decide(event) if isinstance(event, dict) else None
    if reason:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"Blocked by org policy: {reason}.",
                    }
                }
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
