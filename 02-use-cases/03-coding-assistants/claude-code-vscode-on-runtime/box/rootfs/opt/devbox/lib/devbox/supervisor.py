"""The dev box's PID 1 (root). It starts the proxy as devboxproxy right away, so /ping answers
during a cold start, writes the root-owned config files, waits for the workspace volume, then runs
openvscode-server as dev and keeps both running. What the proxy reports comes from the state file
this writes (/run/devbox/state.json, root-owned, world-readable, no secrets)."""

import errno
import json
import os
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.request

from devbox import config

DEV_UID = DEV_GID = 1000
PROXY_UID = PROXY_GID = 1001
# What the kernel shows for an id this container's user namespace doesn't map (kernel.overflowgid).
OVERFLOW_GID = 65534
WORKSPACE = "/mnt/workspace"
HOME = f"{WORKSPACE}/home"
PROJECTS = f"{WORKSPACE}/projects"
OVS = "/opt/openvscode-server"
NODE = f"{OVS}/node"
# The server's own extensions folder: root-owned and read-only, with the extensions.json the image
# build generated, so the running server can't install or side-load anything. Claude
# Code is a system extension in /opt/openvscode-server/extensions.
EXTENSIONS_DIR = "/opt/devbox/extensions"
BUILD_INFO = "/opt/devbox/build-info.json"
PROXY = "/opt/devbox/proxy/server.mjs"
DEV_HELPER = "/opt/devbox/lib/devbox/dev_helper.py"
STATE_FILE = "/run/devbox/state.json"
CLAUDE_DIR = "/etc/claude-code"
AWS_CONFIG = "/etc/devbox/aws-config"
SETPRIV = "/usr/bin/setpriv"
PYTHON = "/usr/bin/python3"
VSCODE_PORT = 3000
PROXY_PORT = 8080
LISTEN_FD = 3

VSCODE_ARGS = [
    f"{OVS}/bin/openvscode-server",
    "--host",
    "127.0.0.1",
    "--port",
    str(VSCODE_PORT),
    "--without-connection-token",
    "--telemetry-level",
    "off",
    "--disable-workspace-trust",
    "--server-data-dir",
    f"{HOME}/.openvscode-server",
    "--default-folder",
    PROJECTS,
    "--reconnection-grace-time",
    "259200",
    "--accept-server-license-terms",
    "--extensions-dir",
    EXTENSIONS_DIR,
]


def log(message):
    print(f"[devbox] {message}", flush=True)


# Linux capability bits the privilege drop needs: CAP_CHOWN, CAP_SETGID, CAP_SETUID.
_DROP_CAPS = (0, 6, 7)


def can_switch_users():
    """True when this root can become dev and devboxproxy. AgentCore Instances starts the container
    as uid 0 in a user namespace with no capabilities at all (CapEff 0, no_new_privs), so it can't:
    there, everything runs as that one unprivileged uid. DEVBOX_SINGLE_USER=1 forces that for tests."""
    if os.environ.get("DEVBOX_SINGLE_USER") == "1" or os.geteuid() != 0:
        return False
    try:
        with open("/proc/self/status") as f:
            cap_eff = int(next(l.split(":", 1)[1] for l in f if l.startswith("CapEff:")), 16)
    except (OSError, StopIteration, ValueError):
        return False
    return all(cap_eff >> bit & 1 for bit in _DROP_CAPS)


SWITCH_USERS = can_switch_users()


def setpriv_argv(uid, gid, groups, argv):
    """setpriv argv: drop to uid/gid with exactly `groups` as supplementary groups. no_new_privs
    means no setuid binary can take the process back up."""
    group_args = ["--groups", ",".join(str(g) for g in groups)] if groups else ["--clear-groups"]
    return [SETPRIV, "--reuid", str(uid), "--regid", str(gid), *group_args, "--no-new-privs", "--", *argv]


def as_user(uid, gid, groups, argv):
    if not SWITCH_USERS:
        return list(argv)  # single-user mode: everything runs as this uid
    return setpriv_argv(uid, gid, groups, argv)


def workspace_group(gid):
    """The volume's group, which dev keeps as a supplementary group (AgentCore Instances gave the
    agent process the volume's group), or None when there's nothing to keep: root's group, dev's
    own (an EFS access point forces 1000:1000 on every file) or an id this namespace doesn't map."""
    return None if gid in (0, DEV_GID, OVERFLOW_GID) else gid


def dev_env(env):
    """A fresh environment for everything the person runs. Nothing from AgentCore's environment
    (such as the container credential endpoint) is passed on. devbox-claude (claude_terminal)
    builds the same one for AgentCore's terminal, which inherits nothing from here."""
    out = {
        "HOME": HOME,
        "USER": "dev",
        "LOGNAME": "dev",
        "SHELL": "/bin/bash",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "AWS_PROFILE": config.AWS_PROFILE,
        "AWS_CONFIG_FILE": AWS_CONFIG,
        "AWS_REGION": config.REGION,
        "AWS_EC2_METADATA_DISABLED": "true",
    }
    for name in ("DEVBOX_OWNER", "DEVBOX_TIER"):
        if env.get(name):
            out[name] = env[name]
    return out


def proxy_env(env):
    out = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "NODE_ENV": "production",
        "DEVBOX_STATE_FILE": STATE_FILE,
        "DEVBOX_LISTEN_FD": str(LISTEN_FD),
    }
    for name in ("DEVBOX_OWNER", "DEVBOX_SESSION_ID"):
        if env.get(name):
            out[name] = env[name]
    return out


def proxy_listener(port=PROXY_PORT):
    """The proxy's listening socket, owned by PID 1 on IPv4 and IPv6 at once. Owning only
    0.0.0.0 would leave [::]:8080 and [::1]:8080 free for a dev process to listen on."""
    if socket.has_dualstack_ipv6():
        try:
            return socket.create_server(("::", port), family=socket.AF_INET6, dualstack_ipv6=True, backlog=511)
        except OSError as err:
            log(f"no dual-stack listener ({err}); listening on IPv4 only")
    return socket.create_server(("0.0.0.0", port), backlog=511)


def claude_version():
    try:
        with open(BUILD_INFO) as f:
            return json.load(f).get("claudeCode")
    except (OSError, ValueError, AttributeError):
        return None


def prepare_dir(path, uid, gid, mode=0o700):
    """Create or fix a top-level volume directory without following anything dev could have
    planted there on an earlier boot (a symlink named home would otherwise hand root's chown to
    any path). No dev process is running yet when this is called.

    Owner and mode are only changed when they differ, and a refusal is logged, not fatal. On an
    EFS access point (PosixUser 1000:1000) every file already belongs to dev, and a root without
    CAP_FOWNER (single-user mode) gets EPERM for chmod on a file the kernel sees as 1000's."""
    try:
        st = os.lstat(path)
        if not stat.S_ISDIR(st.st_mode):
            aside = f"{path}.moved-{time.time_ns()}"
            os.rename(path, aside)
            log(f"{path} was not a directory; moved it to {aside}")
            raise FileNotFoundError
    except FileNotFoundError:
        os.mkdir(path, mode)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if SWITCH_USERS and (st.st_uid, st.st_gid) != (uid, gid):
            try:
                os.fchown(fd, uid, gid)
            except OSError as err:
                log(f"can't chown {path} to {uid}:{gid} ({errno_name(err)}); it stays {st.st_uid}:{st.st_gid}")
        if stat.S_IMODE(st.st_mode) != mode:
            try:
                os.fchmod(fd, mode)
            except OSError as err:
                log(f"can't chmod {path} to {mode:o} ({errno_name(err)}); it stays {stat.S_IMODE(st.st_mode):o}")
    finally:
        os.close(fd)


def errno_name(err):
    return f"{errno.errorcode.get(err.errno, err.errno)}: {err.strerror}"


def _unescape(field):
    """mountinfo writes space, tab, newline and backslash in paths as octal escapes (\\040 ...)."""
    return field.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


def workspace_mount(path=WORKSPACE, mountinfo="/proc/self/mountinfo"):
    """{"fstype", "source"} of the mount you see at `path` (the last one listed for it: a later mount
    hides an earlier one), or None when nothing is mounted there."""
    found = None
    try:
        with open(mountinfo) as f:
            for line in f:
                left, sep, right = line.partition(" - ")
                fields, tail = left.split(), right.split()
                if sep and len(fields) >= 5 and len(tail) >= 2 and _unescape(fields[4]) == path:
                    found = {"fstype": tail[0], "source": _unescape(tail[1])}
    except OSError:
        return None
    return found


def device_of(path):
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


class Child:
    def __init__(self, name, argv, env, pass_fd=None):
        self.name, self.argv, self.env = name, argv, env
        self.pass_fd = pass_fd
        self.pid = None
        self.started = 0.0
        self.failures = 0
        self.next_start = 0.0

    def start(self):
        actions = [(os.POSIX_SPAWN_OPEN, 0, "/dev/null", os.O_RDONLY, 0)]
        if self.pass_fd is not None:
            actions.append((os.POSIX_SPAWN_DUP2, self.pass_fd, LISTEN_FD))
        self.pid = os.posix_spawn(self.argv[0], self.argv, self.env, setsid=True, file_actions=actions)
        self.started = time.monotonic()
        log(f"started {self.name} (pid {self.pid})")

    def exited(self, status, backoff_cap):
        code = os.waitstatus_to_exitcode(status)
        ran = time.monotonic() - self.started
        self.failures = 0 if ran > 60 else self.failures + 1
        self.next_start = time.monotonic() + min(backoff_cap, 0.5 * 2**self.failures)
        log(f"{self.name} exited ({code}) after {ran:.0f}s; restarting")
        self.pid = None

    def stop(self, sig=signal.SIGTERM):
        if self.pid:
            try:
                os.killpg(self.pid, sig)
            except ProcessLookupError:
                pass


class Supervisor:
    def __init__(self, env):
        self.env = env
        self.stopping = False
        self.mount_delay = float(env.get("DEVBOX_TEST_MOUNT_DELAY") or 0)
        # On a microVM, /mnt/workspace is already a mount when the container starts (root's, 755), and
        # AgentCore mounts the person's EFS access point over it at the first invocation. deploy sets
        # DEVBOX_WORKSPACE_FSTYPE=nfs there, so the box waits for that mount instead of setting up home
        # on the placeholder, where the EFS mount would hide it. Unset: any mount will do (local test).
        self.want_fstype = (env.get("DEVBOX_WORKSPACE_FSTYPE") or "").strip()
        self.seen_mount = None
        self.prepared_device = None
        self.next_remount_check = 0.0
        self.booted = time.monotonic()
        self.commit = self._commit()
        self.workspace_gid = None
        self.state = {
            "volume": "waiting",
            "vscode": "waiting",
            "serverStartId": None,
            "commit": self.commit,
            "signedIn": False,
            "lastSession": None,
            "agentBusy": False,
            "vscodeProcess": None,
            "userNamespace": None,
            "supervisor": {"proxyStarts": 0, "vscodeStarts": 0, "configErrors": [], "workspacePrepared": 0},
            "singleUser": not SWITCH_USERS,
            "workspaceMount": None,
        }
        self._written = None
        # PID 1 owns the :8080 socket (both address families) and lends it to each proxy process,
        # so the port is never free for a dev process to take, not even while the proxy restarts.
        self.listener = proxy_listener()
        self.proxy = Child(
            "proxy", as_user(PROXY_UID, PROXY_GID, [], [NODE, PROXY]), proxy_env(env), pass_fd=self.listener.fileno()
        )
        self.vscode = None
        self.ready_checks_failed = 0
        self.next_health = 0.0
        self.next_probe = 0.0

    def _commit(self):
        try:
            with open(f"{OVS}/product.json") as f:
                return json.load(f)["commit"]
        except (OSError, ValueError, KeyError):
            return None

    # -- state ---------------------------------------------------------------------------------

    def write_state(self):
        if self.state == self._written:
            return
        os.makedirs(os.path.dirname(STATE_FILE), mode=0o755, exist_ok=True)
        config.write_json(STATE_FILE, self.state)
        self._written = json.loads(json.dumps(self.state))

    # -- one-time setup ------------------------------------------------------------------------

    def write_configs(self):
        errors = self.state["supervisor"]["configErrors"]
        try:
            config.write_root_file(AWS_CONFIG, config.aws_config(self.env))
        except config.ConfigError as err:
            # Without a profile Claude fails to sign in, rather than falling back to anything else.
            config.write_root_file(AWS_CONFIG, f"# not configured: {err}\n")
            errors.append(f"aws-config: {err}")
        tier = f"{CLAUDE_DIR}/managed-settings.d/20-tier.json"
        try:
            config.write_json(tier, config.tier_dropin(self.env.get("DEVBOX_MODELS", "")))
        except config.ConfigError as err:
            if os.path.exists(tier):
                os.unlink(tier)
            errors.append(f"20-tier.json: {err}")
        try:
            mcp = config.managed_mcp(self.env.get("DEVBOX_TOOLS_GATEWAY_URL", ""))
        except config.ConfigError as err:
            mcp = config.managed_mcp("")
            errors.append(f"managed-mcp.json: {err}")
        config.write_json(f"{CLAUDE_DIR}/managed-mcp.json", mcp)
        self.write_sandbox_dropin()
        for err in errors:
            log(f"config: {err}")

    def write_sandbox_dropin(self):
        path = f"{CLAUDE_DIR}/managed-settings.d/30-sandbox.json"
        bwrap, socat = shutil.which("bwrap"), shutil.which("socat")
        strict = weak = False
        if bwrap and socat:
            base = [bwrap, "--unshare-user", "--unshare-pid", "--unshare-net"]
            tail = ["--dev-bind", "/", "/", "/bin/true"]
            strict = self.run_quiet(as_user(DEV_UID, DEV_GID, [], base + ["--proc", "/proc"] + tail))
            weak = strict or self.run_quiet(as_user(DEV_UID, DEV_GID, [], base + ["--bind", "/proc", "/proc"] + tail))
        dropin = config.sandbox_dropin(strict, weak)
        if dropin:
            config.write_json(path, dropin)
        elif os.path.exists(path):
            os.unlink(path)
        log(f"sandbox: {'strict' if strict else 'weak' if weak else 'off (no bwrap or no user namespaces)'}")

    def run_quiet(self, argv, stdin_text=None, timeout=15):
        try:
            res = subprocess.run(
                argv,
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=dev_env(self.env),
                cwd="/",
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return res.returncode == 0

    def dev_helper(self, mode, groups, stdin_text=None):
        argv = as_user(DEV_UID, DEV_GID, groups, [PYTHON, "-I", DEV_HELPER, mode])
        try:
            res = subprocess.run(
                argv,
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=15,
                env=dev_env(self.env),
                cwd="/",
                check=False,
            )
            return json.loads(res.stdout) if res.returncode == 0 else {"error": res.stderr.strip()[:300]}
        except (OSError, subprocess.TimeoutExpired, ValueError) as err:
            return {"error": str(err)[:300]}

    def mounted(self):
        if time.monotonic() - self.booted < self.mount_delay:
            return False
        if not os.path.ismount(WORKSPACE):
            return False
        if not self.want_fstype:
            return True
        mount = workspace_mount()
        if mount != self.seen_mount:
            self.seen_mount = mount
            self.state["workspaceMount"] = mount
            what = f"{mount['fstype']} from {mount['source']}" if mount else "nothing"
            log(
                f"{WORKSPACE} is {what}"
                + (
                    ""
                    if mount and mount["fstype"].startswith(self.want_fstype)
                    else f"; waiting for the {self.want_fstype} mount (AgentCore mounts it at the first invocation)"
                )
            )
        return bool(mount and mount["fstype"].startswith(self.want_fstype))

    def check_remount(self, now):
        """Something mounted over the workspace after it was set up: set it up again, and restart VS
        Code, whose open folders and files are on the one now hidden."""
        if self.prepared_device is None or now < self.next_remount_check:
            return
        self.next_remount_check = now + 2
        device = device_of(WORKSPACE)
        if device == self.prepared_device:
            return
        log(
            f"{WORKSPACE} changed under the running box (device {self.prepared_device} -> {device}); "
            "setting it up again and restarting VS Code"
        )
        self.prepared_device = None
        self.state["volume"] = "waiting"
        if self.vscode:
            self.vscode.stop()

    def prepare_workspace(self):
        self.prepared_device = device_of(WORKSPACE)
        self.state["supervisor"]["workspacePrepared"] += 1
        self.state["workspaceMount"] = workspace_mount()
        root = os.stat(WORKSPACE)
        gid = root.st_gid
        self.workspace_gid = workspace_group(gid)
        owner_gid = self.workspace_gid if self.workspace_gid is not None else DEV_GID
        log(f"workspace {WORKSPACE}: {root.st_uid}:{gid} {stat.S_IMODE(root.st_mode):o}")
        for path in (HOME, PROJECTS):
            prepare_dir(path, DEV_UID, owner_gid)
        payload = {
            "tasks": json.dumps(config.folder_open_tasks(), indent=2) + "\n",
            "claudeJson": config.claude_onboarding(claude_version()),
        }
        result = self.dev_helper("firstboot", self.dev_groups(), json.dumps(payload))
        log(
            f"workspace ready (group {gid}); tasks.json {result.get('tasks', result)}; "
            f".claude.json {result.get('claudeJson', '?')}"
        )

    def dev_groups(self):
        return [self.workspace_gid] if self.workspace_gid is not None else []

    # -- running -------------------------------------------------------------------------------

    def start_vscode(self):
        self.vscode = Child(
            "openvscode-server", as_user(DEV_UID, DEV_GID, self.dev_groups(), VSCODE_ARGS), dev_env(self.env)
        )
        self.launch_vscode()

    def launch_vscode(self):
        self.vscode.start()
        self.state["supervisor"]["vscodeStarts"] += 1
        self.state["serverStartId"] = secrets.token_hex(8)
        self.state["vscode"] = "failed" if self.vscode.failures >= 3 else "starting"
        self.state["vscodeProcess"] = None
        self.ready_checks_failed = 0

    def vscode_answers(self):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{VSCODE_PORT}/version", timeout=2) as res:
                return res.status == 200 and (self.commit is None or res.read().decode().strip() == self.commit)
        except OSError:
            return False

    def check_vscode(self, now):
        if not self.vscode or not self.vscode.pid:
            return
        if self.state["vscode"] in ("starting", "failed"):
            if self.vscode_answers():
                self.state["vscode"] = "ready"
                self.state["vscodeProcess"] = process_ids(self.vscode.pid)
                self.vscode.failures = 0
                log("openvscode-server is ready")
        elif now >= self.next_health:
            self.next_health = now + 10
            self.ready_checks_failed = 0 if self.vscode_answers() else self.ready_checks_failed + 1
            if self.ready_checks_failed >= 3:
                log("openvscode-server stopped answering; restarting it")
                self.vscode.stop(signal.SIGKILL)

    def reap(self):
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return
            if pid == 0:
                return
            if pid == self.proxy.pid:
                self.proxy.exited(status, backoff_cap=5)
            elif self.vscode and pid == self.vscode.pid:
                self.vscode.stop(signal.SIGKILL)  # anything left in its process group
                self.vscode.exited(status, backoff_cap=30)
                self.state["vscode"] = "failed" if self.vscode.failures >= 3 else "starting"
                self.state["vscodeProcess"] = None
            # anything else is an orphan (tmux, terminals) that PID 1 has to reap

    def probe_user(self, now):
        if now < self.next_probe:
            return
        self.next_probe = now + 3
        found = self.dev_helper("state", self.dev_groups())
        if "error" not in found:
            self.state["signedIn"] = found.get("signedIn") is True
            self.state["lastSession"] = found.get("lastSession")
            self.state["agentBusy"] = found.get("agentBusy") is True

    def run(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self.on_signal)
        if not SWITCH_USERS:
            log(
                "single-user mode: no capabilities to switch users, so the proxy and VS Code run as "
                f"uid {os.geteuid()} (unprivileged outside this container)"
            )
        self.write_state()
        self.proxy.start()
        self.state["supervisor"]["proxyStarts"] += 1
        self.write_state()
        self.write_configs()
        self.state["userNamespace"] = self.dev_helper("userns", [])
        self.write_state()
        log(f"waiting for {WORKSPACE} to be mounted")
        while not self.stopping:
            now = time.monotonic()
            self.reap()
            if self.proxy.pid is None and now >= self.proxy.next_start:
                self.proxy.start()
                self.state["supervisor"]["proxyStarts"] += 1
            if self.state["volume"] == "waiting" and self.mounted():
                self.prepare_workspace()
                self.state["volume"] = "mounted"
                self.write_state()
                if self.vscode is None:
                    self.start_vscode()
            self.check_remount(now)
            if self.vscode and self.state["volume"] == "mounted":
                if self.vscode.pid is None and now >= self.vscode.next_start:
                    self.launch_vscode()
                self.check_vscode(now)
                self.probe_user(now)
            self.write_state()
            time.sleep(0.25 if self.state["vscode"] != "ready" else 1.0)
        self.shutdown()
        return 0

    def on_signal(self, signum, frame):
        self.stopping = True

    def shutdown(self):
        log("stopping")
        children = [c for c in (self.vscode, self.proxy) if c and c.pid]
        for child in children:
            child.stop()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(c.pid for c in children):
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                break
            for child in children:
                if child.pid == pid:
                    child.pid = None
            if pid == 0:
                time.sleep(0.1)
        for child in children:
            child.stop(signal.SIGKILL)


def process_ids(pid):
    """Uid, Gid and Groups of a running process, as the kernel sees them."""
    try:
        with open(f"/proc/{pid}/status") as f:
            fields = dict(line.split(":", 1) for line in f if ":" in line)
    except OSError:
        return None
    ids = lambda key: [int(x) for x in fields.get(key, "").split()]
    uid, gid = ids("Uid"), ids("Gid")
    return {"pid": pid, "uid": uid[0] if uid else None, "gid": gid[0] if gid else None, "groups": ids("Groups")}


def main():
    if os.getuid() != 0:
        log("devbox-entrypoint must start as root; it drops to dev and devboxproxy itself")
        return 1
    return Supervisor(dict(os.environ)).run()


if __name__ == "__main__":
    sys.exit(main())
