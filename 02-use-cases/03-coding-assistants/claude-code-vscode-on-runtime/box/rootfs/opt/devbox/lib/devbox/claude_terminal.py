"""devbox-claude: Claude Code in the tmux session "claude", so it outlives browser reloads,
reconnects and the terminal that opened it.

Two things start it: VS Code's folder-open task (projects/.vscode/tasks.json), and a person in the
AgentCore terminal (InvokeAgentRuntimeCommandShell). AgentCore's terminal is a separate
process execed into the container. It inherits none of what the supervisor gives VS Code (HOME,
PATH, AWS_PROFILE, AWS_CONFIG_FILE ...), and it may carry the container's own environment,
credential endpoint included. So this never trusts the caller's environment:

1. It builds the person's environment from scratch: supervisor.dev_env(), exactly what VS Code
   gets, plus TERM (default xterm-256color) and a few terminal-identity variables.
2. Two-user mode (root with the capabilities to switch users, and the files belong to dev): it
   re-runs itself as dev through setpriv (no_new_privs), so Claude never runs as root and the
   terminal lands in dev's tmux server, the one VS Code's task uses. Single-user mode (no
   capabilities: Instances, or a capability-less microVM): it runs as it is.
3. As the person, it goes to the last session's folder if that's under /mnt/workspace/projects (else
   projects itself) and attaches to the tmux session "claude", or creates it running
   `claude --resume <last session> || exec claude` (plain `claude` when that session left no transcript).
   `tmux new-session -A` attaches when the session exists, so a second terminal, from VS Code or
   AgentCore, joins the same session and never starts a second Claude."""

import glob
import json
import os
import re
import stat
import sys
import time

from devbox import supervisor

SESSION = "claude"
TMUX = "/usr/bin/tmux"
SELF = "/usr/local/bin/devbox-claude"
WORKSPACE = supervisor.WORKSPACE
HOME = supervisor.HOME
PROJECTS = supervisor.PROJECTS
STATE_FILE = supervisor.STATE_FILE
DEFAULT_TERM = "xterm-256color"
WAIT_SECONDS = 120
TERMINFO_DIRS = ("/etc/terminfo", "/lib/terminfo", "/usr/share/terminfo")
# What a terminal tells the programs in it about itself, and the port the Claude Code extension
# sets in VS Code terminals so the CLI finds the editor. Nothing else from the caller is kept.
TERMINAL_VARS = ("COLORTERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "CLAUDE_CODE_SSE_PORT", "ENABLE_IDE_INTEGRATION")
_PLAIN = re.compile(r"[A-Za-z0-9._:+-]{1,64}")
_TERM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}")
_SESSION_ID = re.compile(r"[A-Za-z0-9-]{1,64}")


def say(message):
    print(f"devbox-claude: {message}", file=sys.stderr, flush=True)


def terminal_type(term):
    """The caller's TERM if this image has a terminfo entry for it, else xterm-256color (tmux
    refuses to attach to a terminal it can't describe, and "dumb" can't clear the screen)."""
    if (
        isinstance(term, str)
        and term != "dumb"
        and _TERM.fullmatch(term)
        and any(os.path.isfile(os.path.join(d, term[0], term)) for d in TERMINFO_DIRS)
    ):
        return term
    return DEFAULT_TERM


def environment(caller):
    """The person's environment, whoever started this: the supervisor's dev_env() (HOME, USER,
    LOGNAME, SHELL, PATH, LANG, AWS_PROFILE=devbox, AWS_CONFIG_FILE, AWS_REGION,
    AWS_EC2_METADATA_DISABLED and the DEVBOX_* names it passes on), plus TERM and TERMINAL_VARS."""
    env = supervisor.dev_env(caller)
    env["TERM"] = terminal_type(caller.get("TERM"))
    for name in TERMINAL_VARS:
        value = caller.get(name)
        if isinstance(value, str) and _PLAIN.fullmatch(value):
            env[name] = value
    return env


def read_json(path, limit=64 * 1024):
    """A small regular file's JSON, or None. Never follows a symlink or blocks on a FIFO."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return None
            return json.loads(f.read(limit))
    except (OSError, ValueError):
        return None


def workspace_ready(path=None):
    """The supervisor has set up the person's workspace (on a microVM: the EFS mount, not the
    placeholder under it) and home is there."""
    state = read_json(path or STATE_FILE)
    return isinstance(state, dict) and state.get("volume") == "mounted" and os.path.isdir(HOME)


def wait_for_workspace(timeout=WAIT_SECONDS, sleep=time.sleep, clock=time.monotonic, ready=workspace_ready):
    """A terminal can open before the box has its workspace (the first request mounts it). Wait for
    it, saying so once, rather than failing."""
    deadline = clock() + timeout
    if ready():
        return True
    say("waiting for your workspace to be mounted (the first minute of a new box)...")
    while clock() < deadline:
        sleep(1)
        if ready():
            return True
    return False


def supervisor_single_user(path=None):
    """True or False from the supervisor's state file (whether VS Code runs as this same uid), or
    None when the supervisor hasn't written it."""
    state = read_json(path or STATE_FILE)
    single = state.get("singleUser") if isinstance(state, dict) else None
    return single if isinstance(single, bool) else None


def owner(path):
    try:
        return os.lstat(path).st_uid
    except OSError:
        return None


def switch_to_dev(euid, can_switch, single_user, home_uid):
    """Whether to re-run as dev first. Only root with the capabilities can; then do it when the
    supervisor runs VS Code as dev, or, before it has said, when the files belong to dev. When the
    supervisor says single-user mode, VS Code runs as this uid, so this does too."""
    if euid == supervisor.DEV_UID or not can_switch or single_user is True:
        return False
    return single_user is False or home_uid == supervisor.DEV_UID


def under_projects(folder):
    """A folder in the projects tree: the one folder trust covers (config.claude_onboarding), and
    not home, where Claude would see the person's dotfiles and sign-in cache."""
    if not (isinstance(folder, str) and os.path.isabs(folder)):
        return False
    real, root = os.path.realpath(folder), os.path.realpath(PROJECTS)
    return (real == root or real.startswith(root + os.sep)) and os.path.isdir(real)


def has_transcript(home, session_id):
    """Claude keeps a session at ~/.claude/projects/<folder>/<id>.jsonl, and only once something was
    said in it. Without one, --resume just prints "No conversation found" before starting afresh."""
    return bool(glob.glob(os.path.join(glob.escape(home), ".claude", "projects", "*", f"{session_id}.jsonl")))


def last_session(home):
    """(session id or None, folder) from the managed pointer hook's last-session.json."""
    pointer = read_json(os.path.join(home, ".devbox", "last-session.json"))
    if not isinstance(pointer, dict):
        pointer = {}
    session_id = pointer.get("sessionId")
    if not (isinstance(session_id, str) and _SESSION_ID.fullmatch(session_id) and has_transcript(home, session_id)):
        session_id = None
    for folder in (pointer.get("cwd"), PROJECTS):
        if under_projects(folder):
            return session_id, folder
    return session_id, home if os.path.isdir(home) else "/"


def tmux_argv(folder, session_id):
    """If the session can't be resumed (its transcript is gone), Claude starts a new one."""
    command = f"claude --resume {session_id} || exec claude" if session_id else "exec claude"
    return [TMUX, "new-session", "-A", "-s", SESSION, "-c", folder, command]


def plan(caller, euid, can_switch):
    """(path, argv, env, cwd) to exec, or an int exit code after saying why."""
    if caller.get("TMUX"):
        say(f"already inside tmux; switch to the Claude session with: tmux switch-client -t {SESSION}")
        return 1
    env = environment(caller)
    single_user = supervisor_single_user()
    if switch_to_dev(euid, can_switch, single_user, owner(HOME)):
        groups = []
        try:
            group = supervisor.workspace_group(os.stat(WORKSPACE).st_gid)
            groups = [group] if group is not None else []
        except OSError:
            pass
        argv = supervisor.setpriv_argv(supervisor.DEV_UID, supervisor.DEV_GID, groups, [SELF])
        return argv[0], argv, env, "/"
    if not os.path.isdir(HOME):
        say(
            f"{HOME} isn't there: the box hasn't set up your workspace. op: status (volume) and the "
            "session's log say why."
        )
        return 1
    if single_user is False and euid != supervisor.DEV_UID:
        say(
            f"running as uid {euid}, which can't switch to dev, so this is a separate tmux session "
            "from the one VS Code opened"
        )
    session_id, folder = last_session(HOME)
    argv = tmux_argv(folder, session_id)
    return argv[0], argv, env, folder


def main(caller=None, execve=os.execve, wait=wait_for_workspace):
    caller = dict(os.environ) if caller is None else caller
    if not caller.get("TMUX"):
        wait()
    step = plan(caller, os.geteuid(), supervisor.can_switch_users())
    if isinstance(step, int):
        return step
    path, argv, env, cwd = step
    try:
        os.chdir(cwd)
        execve(path, argv, env)
    except OSError as err:
        say(f"can't run {path}: {err.strerror}")
        return 1
    return 0
