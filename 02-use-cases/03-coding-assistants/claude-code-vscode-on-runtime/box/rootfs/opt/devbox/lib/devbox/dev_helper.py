#!/usr/bin/python3 -I
"""Runs as the dev user (the supervisor starts it through setpriv), so anything it reads or
writes on the volume is limited to what dev itself can reach. Symlinks or FIFOs planted in
$HOME can't trick root into reading or blocking on something else.

  state     print {"signedIn": bool, "lastSession": {...}|null, "agentBusy": bool} as JSON
  firstboot stdin {"tasks": text, "claudeJson": {...}}: create projects/.vscode/tasks.json if it
            doesn't exist, and add the onboarding keys ~/.claude.json is missing
  userns    print what this process may do with namespaces (for op: diag)

Standard library only: it runs with -I, so nothing next to it is importable."""

import datetime
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time

HOME = os.environ.get("HOME", "/mnt/workspace/home")
PROJECTS = "/mnt/workspace/projects"
# A turn counts as under way while its busy mark, or its transcript, changed this recently.
AGENT_ACTIVE_SECONDS = 15 * 60
MAX_BUSY_MARKS = 256


def signed_in(now=None):
    """The IdC token cache for sso-session "devbox" exists and hasn't expired. Never its contents."""
    # The AWS CLI names its SSO cache file by the SHA1 of the session name: a file name, not a security use.
    name = hashlib.sha1(b"devbox", usedforsecurity=False).hexdigest() + ".json"
    try:
        with open(os.path.join(HOME, ".aws", "sso", "cache", name)) as f:
            expires = json.load(f).get("expiresAt")
        when = datetime.datetime.fromisoformat(expires.replace("UTC", "+00:00"))
    except (OSError, ValueError, AttributeError, TypeError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when > (now or datetime.datetime.now(datetime.timezone.utc))


def last_session():
    try:
        with open(os.path.join(HOME, ".devbox", "last-session.json")) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("sessionId"), str):
        return None
    text = lambda k: data[k][:4096] if isinstance(data.get(k), str) else None
    ts = data.get("ts")
    return {
        "sessionId": text("sessionId"),
        "cwd": text("cwd"),
        "transcriptPath": text("transcriptPath"),
        "ts": ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None,
    }


def read_small_json(path, limit=64 * 1024):
    """A regular file's JSON, never following a symlink or blocking on a FIFO."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError("not a regular file")
        return json.loads(f.read(limit))


def agent_busy(now=None):
    """Claude is working on a turn: the managed agent-busy hook left a mark in ~/.devbox/busy, and
    the mark or the transcript it names changed in the last 15 minutes. So a turn that was
    interrupted (no Stop hook) or a Claude that hung stops counting, and can't hold the box up."""
    now = time.time() if now is None else now
    marks = []
    try:
        with os.scandir(os.path.join(HOME, ".devbox", "busy")) as entries:
            for entry in entries:
                if len(marks) >= MAX_BUSY_MARKS:
                    break
                try:
                    if entry.name.endswith(".json") and entry.is_file(follow_symlinks=False):
                        marks.append((entry.stat(follow_symlinks=False).st_mtime, entry.path))
                except OSError:
                    continue  # removed by a Stop hook while we looked
    except OSError:
        return False
    if any(now - mtime < AGENT_ACTIVE_SECONDS for mtime, _ in marks):
        return True
    for _, path in sorted(marks, reverse=True)[:8]:
        try:
            transcript = read_small_json(path).get("transcriptPath")
            if isinstance(transcript, str) and now - os.stat(transcript).st_mtime < AGENT_ACTIVE_SECONDS:
                return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


def firstboot(tasks_text):
    os.makedirs(os.path.join(HOME, ".devbox"), mode=0o700, exist_ok=True)
    folder = os.path.join(PROJECTS, ".vscode")
    path = os.path.join(folder, "tasks.json")
    if os.path.lexists(path):
        return "kept"
    os.makedirs(folder, exist_ok=True)
    with open(path, "x") as f:
        f.write(tasks_text)
    return "created"


def with_missing_keys(current, seed):
    """`current` plus every key of `seed` it doesn't have (and, inside "projects", every missing
    folder or folder key). A value that is already there always wins."""
    merged = dict(current)
    for key, value in seed.items():
        if key not in merged:
            merged[key] = value
        elif key == "projects" and isinstance(merged[key], dict) and isinstance(value, dict):
            projects = dict(merged[key])
            for folder, entry in value.items():
                have = projects.get(folder)
                if have is None:
                    projects[folder] = entry
                elif isinstance(have, dict) and isinstance(entry, dict):
                    projects[folder] = {**entry, **have}
            merged[key] = projects
    return merged


def seed_claude_json(seed):
    """Claude Code's onboarding state in ~/.claude.json (config.claude_onboarding), so its terminal
    starts at the prompt. Creates the file (0600) if there is none, else adds only missing keys.
    Anything that isn't a regular file holding a JSON object is left alone."""
    if not isinstance(seed, dict):
        return "skipped"
    path = os.path.join(HOME, ".claude.json")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(seed, indent=2) + "\n")
        return "created"
    try:
        current = read_small_json(path, limit=64 * 1024 * 1024)
    except (OSError, ValueError):
        return "kept"
    if not isinstance(current, dict):
        return "kept"
    merged = with_missing_keys(current, seed)
    if merged == current:
        return "kept"
    fd, tmp = tempfile.mkstemp(dir=HOME, prefix=".claude.json.")  # 0600
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(merged, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.lexists(tmp):
            os.unlink(tmp)
        raise
    return "merged"


def status_lines():
    keep = ("Uid", "Gid", "Groups", "NoNewPrivs", "Seccomp", "CapEff", "CapBnd")
    try:
        with open("/proc/self/status") as f:
            return {k: v.strip() for k, v in (line.split(":", 1) for line in f if ":" in line) if k in keep}
    except OSError:
        return {}


def userns():
    result = {"status": status_lines()}
    for name in (
        "/proc/sys/user/max_user_namespaces",
        "/proc/sys/kernel/unprivileged_userns_clone",
        "/proc/sys/kernel/apparmor_restrict_unprivileged_userns",
    ):
        try:
            with open(name) as f:
                result[os.path.basename(name)] = f.read().strip()
        except OSError:
            pass
    # In a child, so this process stays in the namespaces it started in.
    probe = subprocess.run(
        ["/usr/bin/unshare", "--user", "--map-root-user", "/bin/true"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    result["unshareUser"] = {"ok": probe.returncode == 0, "stderr": probe.stderr.strip()[:300]}
    return result


def main(argv):
    mode = argv[1] if len(argv) > 1 else ""
    if mode == "state":
        out = {"signedIn": signed_in(), "lastSession": last_session(), "agentBusy": agent_busy()}
    elif mode == "firstboot":
        payload = json.loads(sys.stdin.read())
        out = {"tasks": firstboot(payload["tasks"])}
        try:
            out["claudeJson"] = seed_claude_json(payload.get("claudeJson"))
        except OSError as err:
            out["claudeJson"] = f"failed ({err.strerror})"
    elif mode == "userns":
        out = userns()
    else:
        print("usage: dev_helper.py state|firstboot|userns", file=sys.stderr)
        return 2
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
