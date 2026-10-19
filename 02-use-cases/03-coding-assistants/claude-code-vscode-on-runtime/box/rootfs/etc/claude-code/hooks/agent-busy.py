#!/usr/bin/python3 -I
"""Managed hook that tells the box Claude is working on a turn, so /ping stays HealthyBusy and
AgentCore keeps the box while a turn runs with no browser attached (after the laptop closes).

  UserPromptSubmit, PostToolUse  write $HOME/.devbox/busy/<session id>.json = {sessionId, transcriptPath, ts}
  Stop, SessionEnd               remove it, and marks left over from turns that never ended

The supervisor counts a mark only while it, or the transcript it names, changed in the last 15
minutes (dev_helper.agent_busy), so an interrupted turn or a hung Claude can't hold the box up.
It prints nothing (UserPromptSubmit output would be added to Claude's context) and always exits 0:
it must never get in the way of a prompt."""

import json
import os
import re
import sys
import tempfile
import time

SESSION_ID = re.compile(r"[A-Za-z0-9-]{1,64}")
WORKING = ("UserPromptSubmit", "PostToolUse")
DONE = ("Stop", "SessionEnd")
LEFTOVER_SECONDS = 24 * 3600


def busy_dir(home):
    return os.path.join(home, ".devbox", "busy")


def mark(folder, event, now):
    os.makedirs(folder, mode=0o700, exist_ok=True)
    transcript = event.get("transcript_path")
    rec = {
        "sessionId": event["session_id"],
        "transcriptPath": transcript if isinstance(transcript, str) else None,
        "ts": int(now),
    }
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".mark.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(rec, f)
        os.replace(tmp, os.path.join(folder, f"{event['session_id']}.json"))
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise


def clear(folder, session_id, now):
    try:
        os.unlink(os.path.join(folder, f"{session_id}.json"))
    except FileNotFoundError:
        pass
    # An interrupt (no Stop) or a box reclaimed mid-turn leaves a mark behind.
    with os.scandir(folder) as entries:
        for entry in entries:
            if (
                entry.is_file(follow_symlinks=False)
                and now - entry.stat(follow_symlinks=False).st_mtime > LEFTOVER_SECONDS
            ):
                os.unlink(entry.path)


def main():
    try:
        event = json.load(sys.stdin)
        home = os.environ.get("HOME")
        if not isinstance(event, dict) or not home:
            return 0
        name, session_id = event.get("hook_event_name"), event.get("session_id")
        if not isinstance(session_id, str) or not SESSION_ID.fullmatch(session_id):
            return 0
        if name in WORKING:
            mark(busy_dir(home), event, time.time())
        elif name in DONE:
            clear(busy_dir(home), session_id, time.time())
    except Exception:  # noqa: BLE001, S110 - never get in the way of a session
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
