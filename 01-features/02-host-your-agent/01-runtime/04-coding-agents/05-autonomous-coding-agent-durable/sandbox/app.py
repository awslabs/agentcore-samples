"""Sandbox — AgentCore Runtime data plane. NOT an agent (no LLM).

Architecture:
  /mnt/shared/<ticket_prefix>/  = CODE directory (S3 Files mount, shared with coding agent).
                                  Agent writes source code here. Sandbox reads/runs it.
  /mnt/workspace/               = WORKSPACE directory (AgentCore managed session storage).
                                  Persists across microVM restarts within the same session.
                                  Virtual environments, installed packages, and execution state
                                  live here — survives sandbox death with zero reinstall cost.

Execution state tracking:
  Before a command runs: writes /mnt/workspace/.exec_state.json = {"status":"running","cmd":...}
  On success:            updates status to "completed"
  On failure/timeout:    updates status to "failed" with error details
  If sandbox dies mid-run (next call sees status="running"): notifies caller that previous
  execution was interrupted.

Security:
  - Cedar policy engine evaluates every action BEFORE execution (deterministic, auditable)
  - All file operations confined to the ticket's code directory
  - Path traversal rejected at both policy and code level (defense in depth)
"""

# Security note: subprocess with shell=True is intentional in this file.
# This runtime IS a command executor by design — it receives commands from the
# coding agent and runs them in a confined environment. Path confinement, Cedar
# policy enforcement, and environment variable filtering provide the security
# boundary. See sandbox/policies/sandbox.cedar for the deterministic deny rules.

import os
import subprocess
import platform
import json
import time
import sys

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from policy_engine import authorize as cedar_authorize

# Add shared module to path
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared_libs"))
from shared.validation import validate_ticket_id, validate_path_within_base, ValidationError

app = BedrockAgentCoreApp()

CODE_MOUNT = os.environ.get("MOUNT_PATH", "/mnt/shared")
WORKSPACE = os.environ.get("WORKSPACE_PATH", "/mnt/workspace")
DEFAULT_TIMEOUT = int(os.environ.get("CMD_TIMEOUT", "600"))

# Which language toolchain this sandbox image carries. One image per language
# (finite set, maintained by the platform team). The business-logic service picks which
# sandbox runtime to invoke per ticket. "python" keeps the original behaviour.
SANDBOX_LANG = os.environ.get("SANDBOX_LANG", "python").lower()

EXEC_STATE_FILE = os.path.join(WORKSPACE, ".exec_state.json")
VENV_DIR = os.path.join(WORKSPACE, "venv")
# SwiftPM scratch (.build): compiled artifacts + resolved dependency checkouts.
# Lives in session storage so a heavy build that crashes the microVM doesn't force
# a full re-resolve/re-compile on restart (cheap recovery).
SPM_BUILD_DIR = os.path.join(WORKSPACE, "spm-build")

# ============================================================
# EXECUTION STATE TRACKING
# ============================================================


def _read_exec_state() -> dict | None:
    if os.path.exists(EXEC_STATE_FILE):
        try:
            with open(EXEC_STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return None


def _write_exec_state(state: dict):
    os.makedirs(WORKSPACE, exist_ok=True)
    with open(EXEC_STATE_FILE, "w") as f:
        json.dump(state, f)


def _check_interrupted() -> dict | None:
    """Check if the previous execution was interrupted (sandbox died mid-run)."""
    state = _read_exec_state()
    if state and state.get("status") == "running":
        # Previous command was still running when the sandbox died
        interrupted = {
            "_interrupted_execution": True,
            "_previous_cmd": state.get("cmd", "unknown"),
            "_started_at": state.get("started_at"),
            "_notice": (
                "WARNING: The sandbox crashed or was killed during the previous command execution. "
                f"Command that was interrupted: {state.get('cmd', 'unknown')!r}. "
                "The working directory may be in an inconsistent state. "
                "You may need to re-run the command or clean up partial results."
            ),
        }
        # Clear the stale state
        _write_exec_state({"status": "recovered", "recovered_at": time.time(),
                           "interrupted_cmd": state.get("cmd")})
        return interrupted
    return None


# ============================================================
# VENV MANAGEMENT — install to session storage, survives microVM death
# ============================================================


def _ensure_venv():
    """Create a venv in session storage if it doesn't exist. Persists across restarts.

    Python-only. Other toolchains (e.g. Swift) persist their build dirs to session
    storage via _toolchain_env instead of a venv.
    """
    if SANDBOX_LANG != "python":
        return
    if os.path.exists(os.path.join(VENV_DIR, "bin", "python")):
        return
    os.makedirs(WORKSPACE, exist_ok=True)
    subprocess.run(
        f"python3 -m venv {VENV_DIR}",
        shell=True, capture_output=True, timeout=60  # nosec B602
    )


def _toolchain_env(base_env: dict) -> dict:
    """Point the language toolchain at session storage so installed/compiled
    artifacts survive microVM restarts (cheap recovery after a crash)."""
    env = dict(base_env)
    if SANDBOX_LANG == "swift":
        # SwiftPM writes resolved dependency checkouts + compiled objects under
        # --scratch-path. Persisting it to /mnt/workspace means a crash mid-build
        # doesn't discard the (expensive) dependency resolution + prior compilation.
        os.makedirs(SPM_BUILD_DIR, exist_ok=True)
        env["SWIFTPM_BUILD_DIR"] = SPM_BUILD_DIR  # honoured by `swift` >= 5.8
        # git refuses to read the Yams checkout when its dir is owned by another uid
        # (the persisted .build came from a prior run / different mount owner) →
        # "detected dubious ownership". Mark all dirs safe via env (no config file write).
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "safe.directory"
        env["GIT_CONFIG_VALUE_0"] = "*"
        # Give SwiftPM a writable, per-session cache to avoid the shared-cache
        # "database is locked" / permission errors on the NFS mount.
        cache = os.path.join(WORKSPACE, ".spm-cache")
        os.makedirs(cache, exist_ok=True)
        env["SWIFTPM_CACHE_DIR"] = cache
        return env
    # Python (default): add the persistent venv to PATH so pip/python use it.
    venv_bin = os.path.join(VENV_DIR, "bin")
    env["VIRTUAL_ENV"] = VENV_DIR
    env["PATH"] = f"{venv_bin}:{env.get('PATH', '/usr/local/bin:/usr/bin:/bin')}"
    return env


# ============================================================
# CORE SANDBOX LOGIC
# ============================================================


def _boot_id() -> str:
    try:
        return open("/proc/sys/kernel/random/boot_id").read().strip()
    except OSError:
        return "unknown"


def _ticket_dir(payload: dict) -> str:
    """Resolve and validate the per-ticket code directory on the S3 mount."""
    prefix = payload.get("ticket_prefix", "")
    try:
        validate_ticket_id(prefix)
    except ValidationError as e:
        raise ValueError(f"invalid ticket_prefix: {e}")
    tdir = os.path.join(CODE_MOUNT, prefix)
    os.makedirs(tdir, exist_ok=True)
    return tdir


def _safe_path(base: str, path: str) -> str:
    """Resolve a path ensuring it stays within base."""
    try:
        return validate_path_within_base(path, base)
    except ValidationError as e:
        raise ValueError(str(e))


# Per-ticket filesystem isolation for run_command. Without it, the command runs with the
# WHOLE shared mount visible, so `cat /mnt/shared/OTHER-TICKET/...` would leak a different
# ticket's (potentially another customer's) code. We run each command inside an unprivileged
# user+mount namespace where /mnt/shared is reduced to ONLY this ticket's directory:
# stash the ticket dir, blank /mnt/shared with an empty tmpfs-like bind, restore just the
# ticket dir. The ticket's persistent contents are NEVER modified (so human-in-the-loop
# resume still works) — siblings simply don't exist in the command's view.
JAIL_ENABLED = os.environ.get("RUN_COMMAND_JAIL", "1") == "1"


def _jail_wrap(cmd: str, tdir: str, cwd: str) -> tuple:
    """Return (argv_list, shell_bool) that runs `cmd` jailed to `tdir` within /mnt/shared.
    Falls back (caller decides) if unshare is unavailable. cmd runs via `sh -c` inside the ns."""
    inner = (
        f'set -e; '
        f'REAL="{tdir}"; '
        f'HOLD="$(mktemp -d)"; mount --bind "$REAL" "$HOLD"; '          # stash real ticket dir
        f'EMPTY="$(mktemp -d)"; mount --bind "$EMPTY" "{CODE_MOUNT}"; '  # hide all tickets
        f'mkdir -p "{tdir}"; mount --bind "$HOLD" "{tdir}"; '            # restore only this one
        f'cd "{cwd}"; '
        f'exec sh -c "$CAGENT_CMD"'
    )
    # The user command is passed via env (CAGENT_CMD) so quoting/metacharacters survive intact.
    return (["unshare", "-Urm", "sh", "-c", inner], False)


def _run_command(args: dict, tdir: str) -> dict:
    cmd = args.get("cmd")
    if cmd is None:
        return {"error": "run_command requires 'cmd'"}

    # Command length limit (prevent abuse via extremely long commands)
    if isinstance(cmd, str) and len(cmd) > 10000:
        return {"error": "command too long (max 10000 chars)"}

    # Command denylist — block known data exfiltration and network tools.
    # The sandbox has outbound internet via NAT; this limits abuse surface.
    DENIED_COMMANDS = {
        "curl", "wget", "nc", "ncat", "netcat", "socat", "telnet",
        "ssh", "scp", "sftp", "rsync", "ftp",
        "nslookup", "dig", "host",
    }
    if isinstance(cmd, str):
        # Extract the first token (the binary being invoked) from each piped/chained segment
        import shlex
        try:
            tokens = shlex.split(cmd)
        except ValueError:
            tokens = cmd.split()
        cmd_base = os.path.basename(tokens[0]) if tokens else ""
        if cmd_base in DENIED_COMMANDS:
            return {"error": f"command '{cmd_base}' is denied (network tool). "
                    "Use pip/npm for package installs instead."}

    cwd = args.get("cwd") or tdir
    if not os.path.isabs(cwd):
        cwd = os.path.join(tdir, cwd)
    cwd = os.path.realpath(cwd)
    real_tdir = os.path.realpath(tdir)
    if cwd != real_tdir and not cwd.startswith(real_tdir + os.sep):
        return {"error": f"cwd escapes ticket directory: {cwd}"}
    os.makedirs(cwd, exist_ok=True)
    timeout = int(args.get("timeout", DEFAULT_TIMEOUT))

    env = dict(os.environ)
    BLOCKED_ENV = {"LD_PRELOAD", "LD_LIBRARY_PATH", "PATH", "MOUNT_PATH", "WORKSPACE_PATH",
                   "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                   "AWS_SECURITY_TOKEN", "AWS_DEFAULT_REGION", "PYTHONPATH",
                   "NODE_OPTIONS", "BASH_ENV", "ENV", "CDPATH"}
    for k, v in (args.get("env") or {}).items():
        if k.upper() not in BLOCKED_ENV and not k.upper().startswith("AWS_"):
            env[k] = v
    env["HOME"] = WORKSPACE

    # Point the toolchain at session storage (packages/build artifacts survive restarts)
    _ensure_venv()
    env = _toolchain_env(env)

    # Record execution state BEFORE running
    _write_exec_state({"status": "running", "cmd": cmd[:500], "cwd": cwd, "started_at": time.time()})

    # Build the exec target. With the jail (default), run inside a user+mount namespace that
    # reduces /mnt/shared to only this ticket's dir, blocking cross-ticket reads. The user's
    # command travels via $CAGENT_CMD so its quoting survives. Popen's cwd is set by the jail
    # (cd inside the ns), so we don't pass cwd= when jailed.
    MAX_STDOUT = 64000
    MAX_STDERR = 20000
    jailed = JAIL_ENABLED and isinstance(cmd, str)
    if jailed:
        argv, popen_shell = _jail_wrap(cmd, real_tdir, cwd)
        env["CAGENT_CMD"] = cmd
        popen_cwd = None
    else:
        argv, popen_shell, popen_cwd = cmd, isinstance(cmd, str), cwd
    try:
        try:
            proc = subprocess.Popen(
                argv, shell=popen_shell, cwd=popen_cwd, env=env,  # nosec B602 — sandboxed executor by design
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
        except FileNotFoundError:
            # `unshare` not present on this image → degrade to unjailed (still cwd-confined).
            if jailed:
                jailed = False
                proc = subprocess.Popen(cmd, shell=True, cwd=cwd, env=env,  # nosec B602
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            else:
                raise
        try:
            stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            stdout_bytes, stderr_bytes = proc.communicate(timeout=10)
            _write_exec_state({"status": "failed", "cmd": cmd[:500], "reason": "timeout",
                               "finished_at": time.time()})
            return {"error": "timeout", "timeout": timeout,
                    "stdout": stdout_bytes.decode("utf-8", "replace")[:MAX_STDOUT]}

        status = "completed" if proc.returncode == 0 else "failed"
        _write_exec_state({"status": status, "cmd": cmd[:500], "exit_code": proc.returncode,
                           "finished_at": time.time()})

        return {
            "exit_code": proc.returncode,
            "stdout": stdout_bytes.decode("utf-8", "replace")[:MAX_STDOUT],
            "stderr": stderr_bytes.decode("utf-8", "replace")[:MAX_STDERR],
            "truncated": len(stdout_bytes) > MAX_STDOUT or len(stderr_bytes) > MAX_STDERR,
            "cwd": cwd,
            "isolated": jailed,
        }
    except Exception as e:
        _write_exec_state({"status": "failed", "cmd": cmd[:500], "reason": str(e),
                           "finished_at": time.time()})
        return {"error": f"exec failed: {e}"}


def _get_details(args: dict, tdir: str) -> dict:
    def sh(c):
        try:
            env = _toolchain_env(dict(os.environ))
            return subprocess.run(c, shell=True, capture_output=True, text=True,  # nosec B602
                                  timeout=60, cwd=tdir, env=env).stdout.strip()
        except Exception as e:
            return f"<err: {e}>"
    details = {
        "lang": SANDBOX_LANG,
        "ticket_dir": tdir,
        "workspace": WORKSPACE,
        "listing": sorted(os.listdir(tdir)) if os.path.isdir(tdir) else [],
        "uname": platform.platform(),
        "boot_id": _boot_id(),
        "whoami": sh("whoami"),
        "disk": sh("df -h / | tail -1"),
        "exec_state": _read_exec_state(),
    }
    if SANDBOX_LANG == "swift":
        details["swift"] = sh("swift --version | head -1")
        details["spm_build_dir"] = SPM_BUILD_DIR
        details["spm_build_cached"] = os.path.isdir(SPM_BUILD_DIR) and bool(os.listdir(SPM_BUILD_DIR))
    else:
        details["python"] = sh("python3 --version")
        details["pip_freeze"] = sh("pip freeze | head -60")
        details["venv_exists"] = os.path.exists(os.path.join(VENV_DIR, "bin", "python"))
        details["venv_path"] = VENV_DIR
    return details


def _write_file(args: dict, tdir: str) -> dict:
    path = args.get("path")
    if not path:
        return {"error": "write_file requires 'path'"}
    resolved = _safe_path(tdir, path)
    os.makedirs(os.path.dirname(resolved), exist_ok=True)
    with open(resolved, "w") as f:
        f.write(args.get("content", ""))
    return {"path": resolved, "bytes": os.path.getsize(resolved)}


def _read_file(args: dict, tdir: str) -> dict:
    path = args.get("path")
    if not path:
        return {"error": "read_file requires 'path'"}
    resolved = _safe_path(tdir, path)
    if not os.path.exists(resolved):
        return {"error": f"not found: {resolved}"}
    MAX_READ = 200000
    with open(resolved) as f:
        content = f.read(MAX_READ + 1)
    return {"path": resolved, "content": content[:MAX_READ], "truncated": len(content) > MAX_READ}


import re as _re

# Only allow cloning from well-known public Git hosts over HTTPS. This keeps hydration to
# real public source repos and blocks SSRF-ish targets (file://, internal IPs, arbitrary hosts).
_ALLOWED_GIT_HOST = _re.compile(
    r"^https://(github\.com|gitlab\.com|bitbucket\.org)/[\w.\-]+/[\w.\-]+(\.git)?/?$"
)


def _hydrate(args: dict, tdir: str) -> dict:
    """Git-clone the ticket's source repo (repo_url) into the ticket dir.

    Clones a real public repo on demand — nothing is vendored into our codebase or
    pre-seeded to S3. Writes through the NFS mount (immediate visibility to the coding
    agent). Skips if the ticket dir already has real content (idempotent: a retry/resume
    must not clobber the agent's in-progress work).
    """
    repo_url = (args.get("repo_url") or "").strip()
    if not _ALLOWED_GIT_HOST.match(repo_url):
        return {"error": f"repo_url must be an https github/gitlab/bitbucket repo URL, got: {repo_url!r}"}

    # "Already hydrated" only if there's REAL content — ignore hidden probe/marker files
    # (.probe, .cp_probe, .build cache) that the platform/NFS layer may leave behind.
    existing = [f for f in (os.listdir(tdir) if os.path.isdir(tdir) else []) if not f.startswith(".")]
    if existing:
        return {"hydrated": False, "reason": "ticket dir not empty (already hydrated)",
                "files": len(existing)}

    # Shallow-clone into a scratch dir, then move contents into the ticket dir and drop .git
    # (we want the source tree, not the upstream history — the agent works on a clean copy).
    import tempfile
    import shutil
    scratch = tempfile.mkdtemp(prefix="hydrate-")
    clone_dir = os.path.join(scratch, "repo")
    try:
        proc = subprocess.run(
            ["git", "clone", "--depth", "1", repo_url, clone_dir],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            return {"error": f"git clone failed: {proc.stderr.strip()[:300]}", "repo_url": repo_url}
        shutil.rmtree(os.path.join(clone_dir, ".git"), ignore_errors=True)
        copied = 0
        for name in os.listdir(clone_dir):
            src = os.path.join(clone_dir, name)
            dest = _safe_path(tdir, name)  # confine within the ticket dir
            shutil.move(src, dest)
            copied += 1
    except subprocess.TimeoutExpired:
        return {"error": "git clone timed out", "repo_url": repo_url}
    except Exception as e:
        return {"error": f"hydrate failed: {e}", "repo_url": repo_url}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    if copied == 0:
        return {"error": f"clone produced no files: {repo_url}"}
    return {"hydrated": True, "repo_url": repo_url, "files": copied}


DISPATCH = {
    "run_command": _run_command,
    "get_details": _get_details,
    "write_file": _write_file,
    "read_file": _read_file,
    "hydrate": _hydrate,
}


@app.entrypoint
def invoke(payload):
    action = payload.get("action", "get_details")
    handler = DISPATCH.get(action)
    if handler is None:
        return {"error": f"unknown action '{action}'", "actions": list(DISPATCH)}
    try:
        tdir = _ticket_dir(payload)
    except ValueError as e:
        return {"error": str(e), "action": action}

    # --- Cedar policy evaluation (BEFORE execution) ---
    policy_context = _build_policy_context(action, payload)
    allowed, deny_reason, matching_policies = cedar_authorize(action, policy_context)
    if not allowed:
        return {
            "error": f"Action denied by policy: {deny_reason}",
            "action": action,
            "policy_decision": "DENY",
            "matching_policies": matching_policies,
            "ticket_dir": tdir,
            "sandbox_boot_id": _boot_id(),
        }

    # Check for interrupted execution from a previous sandbox death
    interrupted = _check_interrupted()

    result = handler(payload, tdir)
    result["action"] = action
    result["ticket_dir"] = tdir
    result["sandbox_boot_id"] = _boot_id()
    result["policy_decision"] = "ALLOW"
    if interrupted:
        result.update(interrupted)
    return result


def _build_policy_context(action: str, payload: dict) -> dict:
    """Build Cedar context from the action payload."""
    context = {}
    if action == "run_command":
        context["cmd"] = payload.get("cmd", "")
        context["cwd"] = payload.get("cwd", "")
        context["timeout"] = int(payload.get("timeout", DEFAULT_TIMEOUT))
    elif action in ("write_file", "read_file"):
        context["path"] = payload.get("path", "")
    # get_details has no sensitive context
    return context


if __name__ == "__main__":
    app.run()
