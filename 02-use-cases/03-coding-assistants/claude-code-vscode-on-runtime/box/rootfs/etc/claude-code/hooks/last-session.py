#!/usr/bin/python3 -I
"""Managed SessionStart and Stop hook: remember the last Claude Code session, so devbox-claude
can resume it after a restart and the loader can show it (op: status reads it via the supervisor).

Writes $HOME/.devbox/last-session.json = {sessionId, cwd, transcriptPath, source, ts}.
It prints nothing: SessionStart output would be added to Claude's context."""

import json
import os
import sys
import tempfile
import time


def pointer_path(home):
    return os.path.join(home, ".devbox", "last-session.json")


def record(event, previous, now):
    session_id = event.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    # Stop has no source; keep the one SessionStart saw for this session.
    source = event.get("source")
    if not isinstance(source, str) or not source:
        same = isinstance(previous, dict) and previous.get("sessionId") == session_id
        source = previous.get("source") if same else str(event.get("hook_event_name", "")).lower() or None

    def text(key):
        value = event.get(key)
        return value if isinstance(value, str) else None

    return {
        "sessionId": session_id,
        "cwd": text("cwd"),
        "transcriptPath": text("transcript_path"),
        "source": source,
        "ts": int(now),
    }


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    home = os.environ.get("HOME")
    if not isinstance(event, dict) or not home:
        return 0
    path = pointer_path(home)
    try:
        with open(path) as f:
            previous = json.load(f)
    except (OSError, ValueError):
        previous = None
    rec = record(event, previous, time.time())
    if rec is None:
        return 0
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".last-session.")
        with os.fdopen(fd, "w") as f:
            json.dump(rec, f)
        os.replace(tmp, path)
    except OSError:
        pass  # never get in the way of a session
    return 0


if __name__ == "__main__":
    sys.exit(main())
