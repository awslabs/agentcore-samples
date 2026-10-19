"""Tests for the entrypoint's renderers, the pointer and agent-busy hooks, the dev-side helper, the
supervisor's filesystem and socket handling, and devbox-claude's environment and user switch. They
run on the laptop (no root needed) and again inside the image."""

import contextlib
import datetime
import errno
import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from typing import ClassVar
from unittest import mock

ROOTFS = pathlib.Path(__file__).resolve().parent.parent / "rootfs"
sys.path.insert(0, str(ROOTFS / "opt/devbox/lib"))

from devbox import claude_terminal, config, dev_helper, supervisor

HOOK = ROOTFS / "etc/claude-code/hooks/last-session.py"
spec = importlib.util.spec_from_file_location("last_session", HOOK)
last_session = importlib.util.module_from_spec(spec)
spec.loader.exec_module(last_session)
BUSY_HOOK = ROOTFS / "etc/claude-code/hooks/agent-busy.py"

ENV = {
    "DEVBOX_SSO_START_URL": "https://d-1234567890.awsapps.com/start",
    "DEVBOX_SSO_REGION": "us-east-1",
    "DEVBOX_ACCOUNT_ID": "111122223333",
    "DEVBOX_SSO_ROLE": "ClaudeCode-Power",
}


class AwsConfig(unittest.TestCase):
    def test_renders_the_spec_shape(self):
        self.assertEqual(
            config.aws_config(ENV),
            (
                "[sso-session devbox]\n"
                "sso_start_url = https://d-1234567890.awsapps.com/start\n"
                "sso_region = us-east-1\n"
                "sso_registration_scopes = sso:account:access\n"
                "[profile devbox]\n"
                "sso_session = devbox\n"
                "sso_account_id = 111122223333\n"
                "sso_role_name = ClaudeCode-Power\n"
                "region = us-east-1\n"
            ),
        )

    def test_rejects_missing_and_injected_values(self):
        with self.assertRaises(config.ConfigError):
            config.aws_config({})
        for key, value in [
            ("DEVBOX_SSO_START_URL", "https://x.awsapps.com/start\ncredential_process = /tmp/x"),
            ("DEVBOX_SSO_START_URL", "http://x.awsapps.com/start"),
            ("DEVBOX_ACCOUNT_ID", "1234"),
            ("DEVBOX_SSO_ROLE", "Power\n[profile default]"),
            ("DEVBOX_SSO_REGION", "us-east-1\n"),
        ]:
            with self.subTest(key=key, value=value), self.assertRaises(config.ConfigError):
                config.aws_config({**ENV, key: value})


class TierDropin(unittest.TestCase):
    def test_alias_map(self):
        got = config.tier_dropin(
            json.dumps(
                {
                    "opus": "us.anthropic.claude-opus-4-6-v1",
                    "sonnet": "us.anthropic.claude-sonnet-4-6",
                    "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                }
            )
        )
        self.assertEqual(
            got,
            {
                "model": "sonnet",
                "availableModels": ["opus", "sonnet", "haiku"],
                "enforceAvailableModels": True,
                "env": {
                    "ANTHROPIC_DEFAULT_OPUS_MODEL": "us.anthropic.claude-opus-4-6-v1",
                    "ANTHROPIC_DEFAULT_SONNET_MODEL": "us.anthropic.claude-sonnet-4-6",
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                },
            },
        )

    def test_settings_shape_and_top_level_keys(self):
        got = config.tier_dropin(
            json.dumps(
                {
                    "availableModels": ["sonnet", "haiku", "sonnet"],
                    "model": "haiku",
                    "env": {"ANTHROPIC_DEFAULT_SONNET_MODEL": "us.anthropic.claude-sonnet-4-6[1m]"},
                    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "arn:aws:bedrock:us-east-1:111122223333:application-inference-profile/abc",
                }
            )
        )
        self.assertEqual(got["availableModels"], ["sonnet", "haiku"])
        self.assertEqual(got["model"], "haiku")
        self.assertEqual(set(got["env"]), {"ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL"})

    def test_bad_input(self):
        for raw in [
            "",
            "not json",
            "[]",
            "{}",
            json.dumps({"sonnet": "bad id with spaces"}),
            json.dumps({"availableModels": "sonnet"}),
            json.dumps({"availableModels": ["ok", "no way"]}),
        ]:
            with self.subTest(raw=raw), self.assertRaises(config.ConfigError):
                config.tier_dropin(raw)

    def test_standard_tier_defaults_to_sonnet(self):
        got = config.tier_dropin(json.dumps({"haiku": "h-1", "sonnet": "s-1", "model": "opus"}))
        self.assertEqual((got["model"], got["availableModels"]), ("sonnet", ["sonnet", "haiku"]))


class ManagedMcp(unittest.TestCase):
    def test_literal_gateway_url(self):
        url = "https://devbox-tools-abc123.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
        server = config.managed_mcp(url)["mcpServers"]["web-search"]
        self.assertEqual(server["type"], "stdio")
        self.assertEqual(server["command"], "/opt/mcp-proxy/bin/mcp-proxy-for-aws")
        self.assertEqual(
            server["args"][:7], [url, "--service", "bedrock-agentcore", "--profile", "devbox", "--region", "us-east-1"]
        )
        self.assertNotIn("${", json.dumps(server))

    def test_no_url_means_no_servers_and_bad_urls_fail(self):
        self.assertEqual(config.managed_mcp(""), {"mcpServers": {}})
        for url in ["http://x/mcp", "${DEVBOX_TOOLS_GATEWAY_URL}", "https://x/mcp?a=b", "https://x/ mcp"]:
            with self.subTest(url=url), self.assertRaises(config.ConfigError):
                config.managed_mcp(url)


class SmallRenderers(unittest.TestCase):
    def test_folder_open_task(self):
        task = config.folder_open_tasks()["tasks"][0]
        self.assertEqual(task["command"], "/usr/local/bin/devbox-claude")
        self.assertEqual(task["runOptions"]["runOn"], "folderOpen")

    def test_claude_onboarding_seed(self):
        seed = config.claude_onboarding("2.1.277")
        self.assertIs(seed["hasCompletedOnboarding"], True)
        self.assertEqual(seed["lastOnboardingVersion"], "2.1.277")
        self.assertEqual(seed["theme"], "dark")
        self.assertEqual(
            seed["projects"],
            {"/mnt/workspace/projects": {"hasTrustDialogAccepted": True, "hasCompletedProjectOnboarding": True}},
        )
        for version in (None, "", "2.1", "2.1.277\n", 5):
            with self.subTest(version=version):
                self.assertNotIn("lastOnboardingVersion", config.claude_onboarding(version))

    def test_sandbox_dropin(self):
        self.assertIsNone(config.sandbox_dropin(False, False))
        strict = config.sandbox_dropin(True, True)["sandbox"]
        self.assertFalse(strict["allowUnsandboxedCommands"])
        self.assertNotIn("enableWeakerNestedSandbox", strict)
        self.assertTrue(config.sandbox_dropin(False, True)["sandbox"]["enableWeakerNestedSandbox"])

    def test_managed_settings_file(self):
        data = json.loads((ROOTFS / "etc/claude-code/managed-settings.json").read_text())
        self.assertEqual(data["permissions"]["disableBypassPermissionsMode"], "disable")
        for key in ("allowManagedHooksOnly", "allowManagedPermissionRulesOnly", "allowManagedMcpServersOnly"):
            self.assertIs(data[key], True, key)
        self.assertEqual(data["strictKnownMarketplaces"], [])
        self.assertIs(data["skipWebFetchPreflight"], True)
        self.assertEqual(data["env"]["AWS_PROFILE"], "devbox")
        self.assertEqual(data["env"]["AWS_CONFIG_FILE"], "/etc/devbox/aws-config")
        self.assertNotIn("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", data["env"])
        self.assertIn("WebFetch", data["permissions"]["deny"])
        self.assertIn("--use-device-code --no-browser", data["awsAuthRefresh"])
        self.assertNotIn("availableModels", data, "tier models come only from the drop-in")
        commands = [h["command"] for event in data["hooks"].values() for m in event for h in m["hooks"]]
        self.assertTrue(all(c.startswith("/usr/bin/python3 -I /etc/claude-code/hooks/") for c in commands))
        self.assertTrue(data["hooks"]["Stop"][0]["hooks"][0]["async"])
        busy = "/usr/bin/python3 -I /etc/claude-code/hooks/agent-busy.py"
        for event in ("UserPromptSubmit", "PostToolUse", "Stop", "SessionEnd"):
            with self.subTest(event=event):
                self.assertIn(busy, [h["command"] for m in data["hooks"][event] for h in m["hooks"]])
        # The mark must exist before the turn starts, so this one isn't async.
        self.assertFalse(data["hooks"]["UserPromptSubmit"][0]["hooks"][0].get("async", False))

    def test_write_root_file_is_atomic_and_sets_mode(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "x.json")
            config.write_json(path, {"a": 1}, mode=0o640)
            self.assertEqual(json.loads(pathlib.Path(path).read_text()), {"a": 1})
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o640)
            self.assertEqual(os.listdir(d), ["x.json"])


class PointerHook(unittest.TestCase):
    def run_hook(self, home, event):
        env = {"HOME": home, "PATH": os.environ.get("PATH", "")}
        return subprocess.run(
            [sys.executable, "-I", str(HOOK)],
            input=json.dumps(event),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_session_start_then_stop(self):
        with tempfile.TemporaryDirectory() as home:
            start = {
                "hook_event_name": "SessionStart",
                "source": "startup",
                "session_id": "0b1c-22",
                "cwd": "/mnt/workspace/projects/app",
                "transcript_path": "/t/0b1c-22.jsonl",
            }
            res = self.run_hook(home, start)
            self.assertEqual((res.returncode, res.stdout), (0, ""), "SessionStart output would reach Claude")
            pointer = json.loads(pathlib.Path(home, ".devbox/last-session.json").read_text())
            self.assertEqual(
                {k: pointer[k] for k in ("sessionId", "cwd", "transcriptPath", "source")},
                {
                    "sessionId": "0b1c-22",
                    "cwd": "/mnt/workspace/projects/app",
                    "transcriptPath": "/t/0b1c-22.jsonl",
                    "source": "startup",
                },
            )
            self.assertIsInstance(pointer["ts"], int)
            self.run_hook(
                home, {"hook_event_name": "Stop", "session_id": "0b1c-22", "cwd": "/x", "transcript_path": "/t"}
            )
            self.assertEqual(
                json.loads(pathlib.Path(home, ".devbox/last-session.json").read_text())["source"], "startup"
            )
            self.run_hook(
                home, {"hook_event_name": "Stop", "session_id": "other", "cwd": "/x", "transcript_path": "/t"}
            )
            self.assertEqual(json.loads(pathlib.Path(home, ".devbox/last-session.json").read_text())["source"], "stop")

    def test_bad_events_write_nothing(self):
        with tempfile.TemporaryDirectory() as home:
            for event in [{}, {"session_id": 5}, {"session_id": ""}]:
                self.assertEqual(self.run_hook(home, event).returncode, 0)
            self.assertFalse(pathlib.Path(home, ".devbox/last-session.json").exists())

    def test_record_keeps_only_strings(self):
        rec = last_session.record(
            {"session_id": "a", "cwd": 3, "transcript_path": None, "source": "resume"}, None, 12.7
        )
        self.assertEqual(rec, {"sessionId": "a", "cwd": None, "transcriptPath": None, "source": "resume", "ts": 12})


class AgentBusyHook(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        self.busy = pathlib.Path(self.home.name, ".devbox/busy")

    def run_hook(self, event):
        env = {"HOME": self.home.name, "PATH": os.environ.get("PATH", "")}
        raw = event if isinstance(event, str) else json.dumps(event)
        return subprocess.run(
            [sys.executable, "-I", str(BUSY_HOOK)],
            input=raw,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def event(self, name, session="4f1c-aa"):
        return {
            "hook_event_name": name,
            "session_id": session,
            "cwd": "/mnt/workspace/projects",
            "transcript_path": f"/t/{session}.jsonl",
        }

    def test_prompt_marks_busy_and_stop_clears(self):
        res = self.run_hook(self.event("UserPromptSubmit"))
        self.assertEqual((res.returncode, res.stdout), (0, ""), "UserPromptSubmit output would reach Claude")
        mark = json.loads((self.busy / "4f1c-aa.json").read_text())
        self.assertEqual((mark["sessionId"], mark["transcriptPath"]), ("4f1c-aa", "/t/4f1c-aa.jsonl"))
        os.utime(self.busy / "4f1c-aa.json", (1, 1))
        self.run_hook(self.event("PostToolUse"))
        self.assertGreater((self.busy / "4f1c-aa.json").stat().st_mtime, 1, "a tool call refreshes the mark")
        self.run_hook(self.event("UserPromptSubmit", "other-1"))
        self.run_hook(self.event("Stop"))
        self.assertEqual(sorted(p.name for p in self.busy.iterdir()), ["other-1.json"], "Stop clears only its session")
        self.run_hook(self.event("SessionEnd", "other-1"))
        self.assertEqual(list(self.busy.iterdir()), [])

    def test_stop_prunes_marks_left_by_turns_that_never_ended(self):
        self.run_hook(self.event("UserPromptSubmit", "interrupted"))
        self.run_hook(self.event("UserPromptSubmit", "recent"))
        os.utime(self.busy / "interrupted.json", (time.time() - 2 * 86400,) * 2)
        self.run_hook(self.event("Stop", "someone"))
        self.assertEqual(sorted(p.name for p in self.busy.iterdir()), ["recent.json"])

    def test_bad_input_writes_nothing_and_exits_0(self):
        for event in [
            "not json",
            "[1]",
            {},
            self.event("UserPromptSubmit", "../../escape"),
            self.event("UserPromptSubmit", ""),
            {**self.event("UserPromptSubmit"), "session_id": 5},
        ]:
            with self.subTest(event=event):
                res = self.run_hook(event)
                self.assertEqual((res.returncode, res.stdout), (0, ""))
        self.assertFalse(self.busy.exists())


class DevHelper(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        dev_helper.HOME = self.home.name
        self.cache = pathlib.Path(
            self.home.name, ".aws/sso/cache", hashlib.sha1(b"devbox", usedforsecurity=False).hexdigest() + ".json"
        )

    def test_signed_in(self):
        now = datetime.datetime(2026, 9, 28, 12, 0, tzinfo=datetime.timezone.utc)
        self.assertFalse(dev_helper.signed_in(now))
        self.cache.parent.mkdir(parents=True)
        for expires, expected in [
            ("2026-09-28T13:00:00Z", True),
            ("2026-09-28T13:00:00.123Z", True),
            ("2026-09-28T11:59:59Z", False),
            ("2026-09-28T13:00:00UTC", True),
            ("garbage", False),
            (None, False),
        ]:
            self.cache.write_text(json.dumps({"accessToken": "secret", "expiresAt": expires}))
            self.assertIs(dev_helper.signed_in(now), expected, expires)

    def test_last_session_is_sanitized(self):
        self.assertIsNone(dev_helper.last_session())
        path = pathlib.Path(self.home.name, ".devbox/last-session.json")
        path.parent.mkdir()
        path.write_text(json.dumps({"sessionId": "s", "cwd": "/p", "transcriptPath": 7, "ts": True, "x": 1}))
        self.assertEqual(dev_helper.last_session(), {"sessionId": "s", "cwd": "/p", "transcriptPath": None, "ts": None})
        path.write_text("[1]")
        self.assertIsNone(dev_helper.last_session())

    def test_agent_busy(self):
        now = time.time()
        busy = pathlib.Path(self.home.name, ".devbox/busy")
        self.assertFalse(dev_helper.agent_busy(now), "no marks folder")
        busy.mkdir(parents=True)
        transcript = pathlib.Path(self.home.name, "t.jsonl")
        transcript.write_text("{}\n")
        mark = busy / "s-1.json"
        mark.write_text(json.dumps({"sessionId": "s-1", "transcriptPath": str(transcript)}))
        self.assertTrue(dev_helper.agent_busy(now))
        old = now - dev_helper.AGENT_ACTIVE_SECONDS - 1
        os.utime(mark, (old, old))
        self.assertTrue(dev_helper.agent_busy(now), "the transcript is still being written")
        os.utime(transcript, (old, old))
        self.assertFalse(dev_helper.agent_busy(now), "a mark nothing has touched for 15 minutes doesn't count")
        mark.unlink()
        (busy / "link.json").symlink_to(transcript)
        os.utime(transcript, (now, now))
        self.assertFalse(dev_helper.agent_busy(now), "a symlink isn't a mark")

    def test_seed_claude_json(self):
        seed = config.claude_onboarding("2.1.277")
        path = pathlib.Path(self.home.name, ".claude.json")
        self.assertEqual(dev_helper.seed_claude_json(seed), "created")
        self.assertEqual(json.loads(path.read_text()), seed)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(dev_helper.seed_claude_json(seed), "kept")

        mine = {
            "numStartups": 40,
            "theme": "light",
            "userID": "x",
            "projects": {
                "/mnt/workspace/projects": {"hasTrustDialogAccepted": False, "allowedTools": ["Bash"]},
                "/mnt/workspace/projects/app": {"hasTrustDialogAccepted": True},
            },
        }
        path.write_text(json.dumps(mine))
        self.assertEqual(dev_helper.seed_claude_json(seed), "merged")
        got = json.loads(path.read_text())
        self.assertEqual((got["numStartups"], got["theme"], got["userID"]), (40, "light", "x"), "existing values win")
        self.assertIs(got["hasCompletedOnboarding"], True)
        self.assertEqual(
            got["projects"]["/mnt/workspace/projects"],
            {"hasTrustDialogAccepted": False, "allowedTools": ["Bash"], "hasCompletedProjectOnboarding": True},
        )
        self.assertEqual(got["projects"]["/mnt/workspace/projects/app"], {"hasTrustDialogAccepted": True})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(sorted(os.listdir(self.home.name)), [".claude.json"], "no temp file left behind")

        for text in ("[1]", "not json"):
            with self.subTest(text=text):
                path.write_text(text)
                self.assertEqual(dev_helper.seed_claude_json(seed), "kept")
                self.assertEqual(path.read_text(), text)
        path.unlink()
        elsewhere = pathlib.Path(self.home.name, "elsewhere.json")
        elsewhere.write_text("{}")
        path.symlink_to(elsewhere)
        self.assertEqual(dev_helper.seed_claude_json(seed), "kept")
        self.assertEqual(elsewhere.read_text(), "{}", "a planted symlink is never followed")
        self.assertEqual(dev_helper.seed_claude_json(None), "skipped")

    def test_firstboot_creates_once(self):
        projects = tempfile.TemporaryDirectory()
        self.addCleanup(projects.cleanup)
        dev_helper.PROJECTS = projects.name
        self.assertEqual(dev_helper.firstboot("{}\n"), "created")
        self.assertEqual(dev_helper.firstboot('{"changed": true}\n'), "kept")
        self.assertEqual(pathlib.Path(projects.name, ".vscode/tasks.json").read_text(), "{}\n")


class SupervisorPieces(unittest.TestCase):
    def test_setpriv_argv(self):
        with mock.patch.object(supervisor, "SWITCH_USERS", True):
            self.assertEqual(
                supervisor.as_user(1000, 1000, [2000], ["/bin/true"]),
                [
                    "/usr/bin/setpriv",
                    "--reuid",
                    "1000",
                    "--regid",
                    "1000",
                    "--groups",
                    "2000",
                    "--no-new-privs",
                    "--",
                    "/bin/true",
                ],
            )
            self.assertIn("--clear-groups", supervisor.as_user(1001, 1001, [], ["/bin/true"]))

    def test_single_user_mode_runs_as_this_uid(self):
        # AgentCore Instances: uid 0 in a user namespace with no capabilities, so no setpriv at all.
        with mock.patch.object(supervisor, "SWITCH_USERS", False):
            self.assertEqual(supervisor.as_user(1000, 1000, [2000], ["/bin/true"]), ["/bin/true"])

    def test_can_switch_users_needs_chown_setgid_setuid(self):
        status = lambda cap: f"Name:\tx\nCapEff:\t{cap}\n"
        with (
            mock.patch.object(supervisor.os, "geteuid", return_value=0),
            mock.patch.dict(supervisor.os.environ, {}, clear=False),
        ):
            for cap, expected in (("00000000a80425fb", True), ("0000000000000000", False), ("00000000000000c0", False)):
                with mock.patch("builtins.open", mock.mock_open(read_data=status(cap))):
                    self.assertEqual(supervisor.can_switch_users(), expected, cap)

    def test_vscode_flags_match_the_spec(self):
        self.assertEqual(
            " ".join(supervisor.VSCODE_ARGS[1:]),
            "--host 127.0.0.1 --port 3000 --without-connection-token --telemetry-level off "
            "--disable-workspace-trust --server-data-dir /mnt/workspace/home/.openvscode-server "
            "--default-folder /mnt/workspace/projects --reconnection-grace-time 259200 "
            "--accept-server-license-terms "
            # A root-owned, read-only extensions folder with a build-time extensions.json
            "--extensions-dir /opt/devbox/extensions",
        )

    def test_dev_env_drops_the_container_credentials(self):
        env = supervisor.dev_env(
            {
                "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://169.254.170.23/v1/credentials",
                "AWS_EC2_METADATA_SERVICE_ENDPOINT": "http://100.88.0.1:1338",
                "AWS_ACCESS_KEY_ID": "AKIA...",
                "DEVBOX_OWNER": "ada",
                "DEVBOX_SESSION_ID": "dbx-x",
            }
        )
        self.assertEqual(env["HOME"], "/mnt/workspace/home")
        self.assertEqual(env["AWS_PROFILE"], "devbox")
        self.assertEqual(env["DEVBOX_OWNER"], "ada")
        for name in (
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
            "AWS_EC2_METADATA_SERVICE_ENDPOINT",
            "AWS_ACCESS_KEY_ID",
            "DEVBOX_SESSION_ID",
        ):
            self.assertNotIn(name, env)
        self.assertNotIn("AWS_ACCESS_KEY_ID", supervisor.proxy_env({"AWS_ACCESS_KEY_ID": "x"}))

    def test_prepare_dir_never_follows_a_planted_symlink(self):
        uid, gid = os.getuid(), os.getgid()
        with tempfile.TemporaryDirectory() as root:
            precious = pathlib.Path(root, "precious")
            precious.mkdir(mode=0o755)
            home = pathlib.Path(root, "home")
            home.symlink_to(precious)
            supervisor.prepare_dir(str(home), uid, gid)
            self.assertTrue(home.is_dir() and not home.is_symlink())
            self.assertEqual(stat.S_IMODE(precious.stat().st_mode), 0o755, "the symlink target was changed")
            self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o700)
            moved = [p.name for p in pathlib.Path(root).iterdir() if p.name.startswith("home.moved-")]
            self.assertEqual(len(moved), 1)

            projects = pathlib.Path(root, "projects")
            projects.write_text("a file")
            supervisor.prepare_dir(str(projects), uid, gid)
            self.assertTrue(projects.is_dir())

            projects.chmod(0o777)
            supervisor.prepare_dir(str(projects), uid, gid)
            self.assertEqual(stat.S_IMODE(projects.stat().st_mode), 0o700)

    def test_workspace_group(self):
        # Instances gave the volume a group of its own; an EFS access point forces 1000:1000.
        self.assertEqual(supervisor.workspace_group(2000), 2000)
        for gid in (0, 1000, 65534):
            with self.subTest(gid=gid):
                self.assertIsNone(supervisor.workspace_group(gid))

    def test_prepare_dir_leaves_a_matching_owner_and_mode_alone(self):
        with (
            tempfile.TemporaryDirectory() as root,
            mock.patch.object(supervisor, "SWITCH_USERS", True),
            mock.patch.object(supervisor.os, "fchown") as fchown,
            mock.patch.object(supervisor.os, "fchmod") as fchmod,
        ):
            home = pathlib.Path(root, "home")
            home.mkdir(mode=0o700)
            home.chmod(0o700)
            supervisor.prepare_dir(str(home), os.getuid(), os.getgid())
            fchown.assert_not_called()  # EFS: the access point already made it dev's
            fchmod.assert_not_called()

    def test_prepare_dir_logs_a_refused_chown_or_chmod_and_carries_on(self):
        denied = PermissionError(errno.EPERM, "Operation not permitted")
        for switch in (True, False):
            with (
                self.subTest(two_user=switch),
                tempfile.TemporaryDirectory() as root,
                mock.patch.object(supervisor, "SWITCH_USERS", switch),
                mock.patch.object(supervisor, "log") as log,
                mock.patch.object(supervisor.os, "fchown", side_effect=denied) as fchown,
                mock.patch.object(supervisor.os, "fchmod", side_effect=denied),
            ):
                home = pathlib.Path(root, "home")
                home.mkdir()
                home.chmod(0o755)
                supervisor.prepare_dir(str(home), os.getuid() + 1, os.getgid())  # returns: no exception
                self.assertEqual(fchown.called, switch, "single-user mode never chowns")
                said = " ".join(c.args[0] for c in log.call_args_list)
                self.assertIn("can't chmod", said)
                self.assertIn("EPERM", said)
                self.assertEqual("can't chown" in said, switch)
                self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o755)

    def test_prepare_dir_really_denied_chown_is_not_fatal(self):
        # The real kernel: an unprivileged process can't give a directory to another uid.
        if os.geteuid() == 0:
            self.skipTest("root may chown")
        with (
            tempfile.TemporaryDirectory() as root,
            mock.patch.object(supervisor, "SWITCH_USERS", True),
            mock.patch.object(supervisor, "log") as log,
        ):
            home = pathlib.Path(root, "home")
            supervisor.prepare_dir(str(home), os.getuid() + 1, os.getgid())
            self.assertTrue(home.is_dir())
            self.assertEqual(stat.S_IMODE(home.stat().st_mode), 0o700)
            self.assertIn("can't chown", log.call_args_list[0].args[0])

    def test_prepare_workspace_on_an_efs_like_root_owned_by_dev(self):
        """The mount root and everything on it belong to dev (EFS access point, PosixUser
        1000:1000), and chown/chmod are refused, in both modes: the boot still completes."""
        denied = PermissionError(errno.EPERM, "Operation not permitted")
        for switch in (True, False):
            with self.subTest(two_user=switch), tempfile.TemporaryDirectory() as ws, contextlib.ExitStack() as stack:
                pathlib.Path(ws, "home").mkdir()  # left by an earlier boot
                pathlib.Path(ws, "home").chmod(0o755)
                box = supervisor.Supervisor.__new__(supervisor.Supervisor)
                box.state = {"supervisor": {"workspacePrepared": 0}, "workspaceMount": None}
                box.dev_helper = mock.Mock(return_value={"tasks": "created", "claudeJson": "created"})
                for name, value in (
                    ("WORKSPACE", ws),
                    ("HOME", f"{ws}/home"),
                    ("PROJECTS", f"{ws}/projects"),
                    ("DEV_UID", os.getuid()),
                    ("DEV_GID", os.getgid()),
                    ("SWITCH_USERS", switch),
                ):
                    stack.enter_context(mock.patch.object(supervisor, name, value))
                stack.enter_context(mock.patch.object(supervisor.os, "fchown", side_effect=denied))
                stack.enter_context(mock.patch.object(supervisor.os, "fchmod", side_effect=denied))
                log = stack.enter_context(mock.patch.object(supervisor, "log"))
                box.prepare_workspace()
                self.assertIn("can't chmod", " ".join(c.args[0] for c in log.call_args_list))
                self.assertTrue(pathlib.Path(ws, "projects").is_dir())
                self.assertIsNone(box.workspace_gid, "dev's own group isn't added as a supplementary group")
                self.assertEqual(box.dev_groups(), [])
                self.assertEqual(box.dev_helper.call_args.args[:2], ("firstboot", []))

    # What a microVM's container shows (live 2026-09-29): /mnt/workspace is already a mount at start,
    # root's and 755, and AgentCore mounts the EFS access point over it at the first invocation.
    PLACEHOLDER = "812 790 0:61 / /mnt/workspace rw,relatime - ext4 /dev/vdb rw\n"
    EFS = "830 812 0:77 / /mnt/workspace rw,relatime - nfs4 127.0.0.1:/ rw,vers=4.1,port=20049\n"
    OTHER = "700 1 0:30 / / rw - overlay overlay rw\n901 700 0:80 / /mnt/work\\040space rw - tmpfs tmpfs rw\n"

    def mountinfo(self, text):
        path = pathlib.Path(tempfile.mkdtemp(), "mountinfo")
        self.addCleanup(shutil.rmtree, path.parent)
        path.write_text(text)
        return str(path)

    def test_workspace_mount_is_the_last_one_listed(self):
        self.assertIsNone(supervisor.workspace_mount(mountinfo=self.mountinfo(self.OTHER)))
        self.assertEqual(
            supervisor.workspace_mount(mountinfo=self.mountinfo(self.OTHER + self.PLACEHOLDER)),
            {"fstype": "ext4", "source": "/dev/vdb"},
        )
        self.assertEqual(
            supervisor.workspace_mount(mountinfo=self.mountinfo(self.OTHER + self.PLACEHOLDER + self.EFS)),
            {"fstype": "nfs4", "source": "127.0.0.1:/"},
            "a later mount hides the earlier one",
        )
        self.assertEqual(
            supervisor.workspace_mount("/mnt/work space", mountinfo=self.mountinfo(self.OTHER)),
            {"fstype": "tmpfs", "source": "tmpfs"},
            "octal escapes in the mount point",
        )
        self.assertIsNone(supervisor.workspace_mount(mountinfo="/nonexistent/mountinfo"))

    def box_waiting_for(self, want):
        box = supervisor.Supervisor.__new__(supervisor.Supervisor)
        box.mount_delay, box.booted, box.want_fstype, box.seen_mount = 0, 0, want, None
        box.state = {"workspaceMount": None}
        return box

    def test_on_a_microvm_the_box_waits_for_the_efs_mount_not_the_placeholder(self):
        box = self.box_waiting_for("nfs")
        with (
            mock.patch.object(supervisor.os.path, "ismount", return_value=True),
            mock.patch.object(supervisor, "log") as log,
        ):
            with mock.patch.object(
                supervisor, "workspace_mount", return_value={"fstype": "ext4", "source": "/dev/vdb"}
            ):
                self.assertFalse(box.mounted())
                self.assertFalse(box.mounted())
            self.assertEqual(log.call_count, 1, "said once, not every loop")
            self.assertIn("waiting for the nfs mount", log.call_args.args[0])
            with mock.patch.object(
                supervisor, "workspace_mount", return_value={"fstype": "nfs4", "source": "127.0.0.1:/"}
            ):
                self.assertTrue(box.mounted())
            self.assertEqual(box.state["workspaceMount"], {"fstype": "nfs4", "source": "127.0.0.1:/"})
        with mock.patch.object(supervisor.os.path, "ismount", return_value=False):
            self.assertFalse(box.mounted(), "nothing mounted at all")

    def test_without_an_expected_type_any_mount_will_do(self):
        # the local test and the smoke test: a Docker volume is there from the start
        box = self.box_waiting_for("")
        with (
            mock.patch.object(supervisor.os.path, "ismount", return_value=True),
            mock.patch.object(supervisor, "workspace_mount", side_effect=AssertionError("not asked")),
        ):
            self.assertTrue(box.mounted())

    def test_a_remount_under_a_running_box_sets_it_up_again(self):
        box = supervisor.Supervisor.__new__(supervisor.Supervisor)
        box.prepared_device, box.next_remount_check = 51, 0.0
        box.state = {"volume": "mounted"}
        box.vscode = mock.Mock()
        with mock.patch.object(supervisor, "log"):
            with mock.patch.object(supervisor, "device_of", return_value=51):
                box.check_remount(10.0)
            self.assertEqual(box.state["volume"], "mounted")
            box.vscode.stop.assert_not_called()
            with mock.patch.object(supervisor, "device_of", return_value=77):
                box.check_remount(11.0)
                self.assertEqual(box.state["volume"], "mounted", "checked every 2 s, not every loop")
                box.check_remount(12.0)
        self.assertEqual(box.state["volume"], "waiting")
        self.assertIsNone(box.prepared_device)
        box.vscode.stop.assert_called_once()

    @unittest.skipUnless(sys.platform.startswith("linux") and socket.has_dualstack_ipv6(), "Linux bind rules, IPv6")
    def test_proxy_listener_takes_the_port_on_both_families(self):
        listener = supervisor.proxy_listener(0)
        self.addCleanup(listener.close)
        port = listener.getsockname()[1]
        self.assertEqual(listener.family, socket.AF_INET6)
        self.assertEqual(listener.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY), 0)
        attempts = [
            (socket.AF_INET, "0.0.0.0", None),
            (socket.AF_INET, "127.0.0.1", None),
            (socket.AF_INET6, "::", 1),
            (socket.AF_INET6, "::", 0),
            (socket.AF_INET6, "::1", 1),
        ]
        for family, host, v6only in attempts:
            with self.subTest(host=host, v6only=v6only), socket.socket(family, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if v6only is not None:
                    s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, v6only)
                with self.assertRaises(OSError) as caught:
                    s.bind((host, port))
                self.assertEqual(caught.exception.errno, errno.EADDRINUSE)

    @unittest.skipUnless(os.path.exists("/proc/self/status"), "needs Linux /proc")
    def test_process_ids(self):
        ids = supervisor.process_ids(os.getpid())
        self.assertEqual((ids["uid"], ids["gid"]), (os.getuid(), os.getgid()))


class DevboxClaude(unittest.TestCase):
    """devbox-claude, started from VS Code's task or from AgentCore's terminal, which
    inherits nothing from the supervisor and may carry the container's own environment."""

    # Roughly what a process execed into the container could see: the container's env, a root
    # HOME and PATH, a dumb terminal.
    AGENTCORE_SHELL: ClassVar[dict[str, str]] = {
        "HOME": "/root",
        "PATH": "/tmp/evil:/usr/sbin:/usr/bin",
        "USER": "root",
        "TERM": "dumb",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI": "http://169.254.170.23/v1/credentials",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN": "secret",
        "AWS_EC2_METADATA_SERVICE_ENDPOINT": "http://100.88.0.1:1338",
        "AWS_ACCESS_KEY_ID": "AKIA...",
        "AWS_PROFILE": "default",
        "AWS_CONFIG_FILE": "/root/.aws/config",
        "DEVBOX_OWNER": "ada",
        "DEVBOX_TIER": "power",
        "DEVBOX_SESSION_ID": "dbx-x",
        "LD_PRELOAD": "/tmp/x.so",
        "PYTHONPATH": "/tmp",
        "BASH_ENV": "/tmp/rc",
    }

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        ws = pathlib.Path(self.root.name, "ws")
        self.home, self.projects = ws / "home", ws / "projects"
        self.home.mkdir(parents=True)
        self.projects.mkdir()
        self.state = pathlib.Path(self.root.name, "state.json")
        for name, value in (
            ("WORKSPACE", str(ws)),
            ("HOME", str(self.home)),
            ("PROJECTS", str(self.projects)),
            ("STATE_FILE", str(self.state)),
        ):
            patcher = mock.patch.object(claude_terminal, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        cwd = os.getcwd()
        self.addCleanup(os.chdir, cwd)

    def pointer(self, transcript=True, **data):
        path = self.home / ".devbox/last-session.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(data))
        if transcript and data.get("sessionId"):
            folder = self.home / ".claude/projects/-mnt-workspace-projects-app"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{data['sessionId']}.jsonl").write_text("{}\n")

    def test_environment_is_dev_env_whoever_calls(self):
        env = claude_terminal.environment(self.AGENTCORE_SHELL)
        expected = {**supervisor.dev_env(self.AGENTCORE_SHELL), "TERM": "xterm-256color"}
        self.assertEqual(env, expected, "exactly what VS Code gets, plus TERM")
        self.assertEqual(
            {
                k: env[k]
                for k in (
                    "HOME",
                    "USER",
                    "SHELL",
                    "PATH",
                    "LANG",
                    "AWS_PROFILE",
                    "AWS_CONFIG_FILE",
                    "AWS_REGION",
                    "AWS_EC2_METADATA_DISABLED",
                    "DEVBOX_OWNER",
                    "DEVBOX_TIER",
                )
            },
            {
                "HOME": "/mnt/workspace/home",
                "USER": "dev",
                "SHELL": "/bin/bash",
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "AWS_PROFILE": "devbox",
                "AWS_CONFIG_FILE": "/etc/devbox/aws-config",
                "AWS_REGION": "us-east-1",
                "AWS_EC2_METADATA_DISABLED": "true",
                "DEVBOX_OWNER": "ada",
                "DEVBOX_TIER": "power",
            },
        )
        for name in (
            "AWS_CONTAINER_CREDENTIALS_FULL_URI",
            "AWS_CONTAINER_AUTHORIZATION_TOKEN",
            "AWS_ACCESS_KEY_ID",
            "AWS_EC2_METADATA_SERVICE_ENDPOINT",
            "DEVBOX_SESSION_ID",
            "LD_PRELOAD",
            "PYTHONPATH",
            "BASH_ENV",
        ):
            self.assertNotIn(name, env)
        self.assertEqual(claude_terminal.environment({})["TERM"], "xterm-256color", "no TERM at all")

    def test_terminal_type_and_terminal_vars(self):
        terminfo = pathlib.Path(self.root.name, "terminfo")
        (terminfo / "x").mkdir(parents=True)
        (terminfo / "x/xterm-kitty").write_text("")
        with mock.patch.object(claude_terminal, "TERMINFO_DIRS", (str(terminfo),)):
            for term, expected in (
                ("xterm-kitty", "xterm-kitty"),
                ("xterm-unknown", "xterm-256color"),
                ("dumb", "xterm-256color"),
                ("../../x/xterm-kitty", "xterm-256color"),
                ("", "xterm-256color"),
                (None, "xterm-256color"),
            ):
                with self.subTest(term=term):
                    self.assertEqual(claude_terminal.terminal_type(term), expected)
        env = claude_terminal.environment(
            {
                "TERM_PROGRAM": "vscode",
                "COLORTERM": "truecolor",
                "CLAUDE_CODE_SSE_PORT": "40123",
                "TERM_PROGRAM_VERSION": "1.109.5\nAWS_PROFILE=default",
                "VSCODE_IPC_HOOK_CLI": "/tmp/s",
            }
        )
        self.assertEqual(
            (env["TERM_PROGRAM"], env["COLORTERM"], env["CLAUDE_CODE_SSE_PORT"]), ("vscode", "truecolor", "40123")
        )
        self.assertNotIn("TERM_PROGRAM_VERSION", env, "a value with a newline is dropped")
        self.assertNotIn("VSCODE_IPC_HOOK_CLI", env)

    def test_when_to_switch_to_dev(self):
        switch = claude_terminal.switch_to_dev
        # (euid, can switch users, supervisor's singleUser, home owner) -> re-run as dev first?
        for args, expected in (
            ((0, True, False, 1000), True),  # two-user microVM: root with capabilities
            ((0, True, None, 1000), True),  # no state yet: the files belong to dev
            ((0, True, None, 0), False),  # ...and they don't
            ((0, True, False, 0), True),  # VS Code runs as dev: never Claude as root
            ((0, True, True, 1000), False),  # single-user: VS Code runs as this uid too
            ((0, False, None, 1000), False),  # no capabilities (Instances): as it is
            ((0, False, False, 1000), False),
            ((1000, True, False, 1000), False),
        ):  # already dev (VS Code's task)
            with self.subTest(args=args):
                self.assertIs(switch(*args), expected)

    def test_state_file(self):
        self.assertIsNone(claude_terminal.supervisor_single_user())
        for text, expected in (
            ('{"singleUser": true}', True),
            ('{"singleUser": false}', False),
            ('{"singleUser": 1}', None),
            ("[1]", None),
            ("not json", None),
        ):
            with self.subTest(text=text):
                self.state.write_text(text)
                self.assertIs(claude_terminal.supervisor_single_user(), expected)

    def test_last_session_and_the_tmux_command(self):
        self.assertEqual(claude_terminal.last_session(str(self.home)), (None, str(self.projects)))
        app = self.projects / "app"
        app.mkdir()
        self.pointer(sessionId="0b1c-22", cwd=str(app))
        self.assertEqual(claude_terminal.last_session(str(self.home)), ("0b1c-22", str(app)))
        self.pointer(sessionId="0b1c-22; rm -rf ~", cwd=str(self.root.name) + "/gone")
        self.assertEqual(claude_terminal.last_session(str(self.home)), (None, str(self.projects)))
        self.pointer(sessionId="0b1c-22", cwd="relative/app")
        self.assertEqual(claude_terminal.last_session(str(self.home))[1], str(self.projects))
        self.assertEqual(
            claude_terminal.tmux_argv("/p/a b", "0b1c-22"),
            [
                "/usr/bin/tmux",
                "new-session",
                "-A",
                "-s",
                "claude",
                "-c",
                "/p/a b",
                "claude --resume 0b1c-22 || exec claude",
            ],
        )
        self.assertEqual(claude_terminal.tmux_argv("/p", None)[-1], "exec claude")

    def test_a_session_that_left_no_transcript_is_not_resumed(self):
        # live 2026-09-29: a Claude that never got a message leaves a pointer but no transcript, and
        # `claude --resume` then prints "No conversation found" before starting afresh
        app = self.projects / "app"
        app.mkdir()
        self.pointer(transcript=False, sessionId="06138455-dd34", cwd=str(app))
        self.assertEqual(claude_terminal.last_session(str(self.home)), (None, str(app)))

    def test_claude_starts_in_the_projects_tree_never_in_home(self):
        # the first microVM boot left a pointer to home (projects didn't exist on EFS yet); folder trust
        # covers projects only, and home holds the dotfiles and the AWS sign-in cache
        self.pointer(sessionId="0b1c-22", cwd=str(self.home))
        self.assertEqual(claude_terminal.last_session(str(self.home)), ("0b1c-22", str(self.projects)))
        outside = pathlib.Path(self.root.name, "elsewhere")
        outside.mkdir()
        (self.projects / "link").symlink_to(outside)
        self.pointer(sessionId="0b1c-22", cwd=str(self.projects / "link"))
        self.assertEqual(
            claude_terminal.last_session(str(self.home))[1], str(self.projects), "a symlink out of projects"
        )
        self.projects.rename(self.projects.with_name("gone"))
        self.assertEqual(claude_terminal.last_session(str(self.home))[1], str(self.home), "no projects at all: home")

    def test_root_with_capabilities_reruns_itself_as_dev(self):
        self.state.write_text('{"singleUser": false}')
        path, argv, env, cwd = claude_terminal.plan(self.AGENTCORE_SHELL, euid=0, can_switch=True)
        self.assertEqual(path, "/usr/bin/setpriv")
        self.assertEqual(argv[:5], ["/usr/bin/setpriv", "--reuid", "1000", "--regid", "1000"])
        self.assertEqual(argv[-3:], ["--no-new-privs", "--", "/usr/local/bin/devbox-claude"])
        self.assertTrue("--clear-groups" in argv or "--groups" in argv, "never root's groups")
        self.assertEqual(env, claude_terminal.environment(self.AGENTCORE_SHELL))
        self.assertEqual(cwd, "/")
        # ... and as dev the second run goes straight to tmux, with the same environment.
        path, argv, env2, cwd = claude_terminal.plan(env, euid=1000, can_switch=False)
        self.assertEqual(argv[:6], ["/usr/bin/tmux", "new-session", "-A", "-s", "claude", "-c"])
        self.assertEqual(env2, env, "idempotent")

    def test_single_user_mode_runs_as_it_is_in_the_last_folder(self):
        app = self.projects / "app"
        app.mkdir()
        self.pointer(sessionId="4f1c-aa", cwd=str(app))
        for state, can_switch in (('{"singleUser": true}', True), ('{"singleUser": true}', False), (None, False)):
            with self.subTest(state=state, can_switch=can_switch):
                if state:
                    self.state.write_text(state)
                elif self.state.exists():
                    self.state.unlink()
                _path, argv, env, cwd = claude_terminal.plan(self.AGENTCORE_SHELL, euid=0, can_switch=can_switch)
                self.assertEqual(
                    argv,
                    [
                        "/usr/bin/tmux",
                        "new-session",
                        "-A",
                        "-s",
                        "claude",
                        "-c",
                        str(app),
                        "claude --resume 4f1c-aa || exec claude",
                    ],
                )
                self.assertEqual(cwd, str(app))
                self.assertNotIn("AWS_CONTAINER_CREDENTIALS_FULL_URI", env)

    def test_refusals(self):
        with mock.patch.object(claude_terminal, "say") as say:
            self.assertEqual(
                claude_terminal.plan({"TMUX": "/tmp/tmux-1000/default,1,0"}, euid=1000, can_switch=False), 1
            )
            self.assertIn("already inside tmux", say.call_args.args[0])
            self.home.rmdir()
            self.assertEqual(claude_terminal.plan({}, euid=1000, can_switch=False), 1)
            self.assertIn("hasn't set up your workspace", say.call_args.args[0])

    def test_workspace_ready_needs_the_supervisor_and_home(self):
        self.assertFalse(claude_terminal.workspace_ready(), "no state file yet")
        self.state.write_text('{"volume": "waiting"}')
        self.assertFalse(claude_terminal.workspace_ready(), "still the placeholder")
        self.state.write_text('{"volume": "mounted"}')
        self.assertTrue(claude_terminal.workspace_ready())
        self.home.rmdir()
        self.assertFalse(claude_terminal.workspace_ready())

    def test_a_terminal_opened_early_waits_for_the_workspace(self):
        # The page's first status request is what makes AgentCore mount EFS, so the terminal can win.
        t, answers = [0.0], iter([False, False, False, True])
        clock, sleep = (lambda: t[0]), (lambda s: t.__setitem__(0, t[0] + s))
        with mock.patch.object(claude_terminal, "say") as say:
            self.assertTrue(
                claude_terminal.wait_for_workspace(timeout=120, sleep=sleep, clock=clock, ready=lambda: next(answers))
            )
            say.assert_called_once()
            self.assertIn("waiting for your workspace", say.call_args.args[0])
            self.assertEqual(t[0], 3.0)
            t[0] = 0.0
            self.assertFalse(
                claude_terminal.wait_for_workspace(timeout=5, sleep=sleep, clock=clock, ready=lambda: False)
            )
            self.assertEqual(t[0], 5.0, "gives up after the timeout")
        with mock.patch.object(claude_terminal, "say") as say:
            self.assertTrue(claude_terminal.wait_for_workspace(ready=lambda: True))
            say.assert_not_called()

    def test_main_waits_then_plans_but_not_inside_tmux(self):
        wait = mock.Mock(return_value=True)
        with mock.patch.object(claude_terminal, "say"):
            claude_terminal.main({"TMUX": "/tmp/tmux-1000/default,1,0"}, execve=mock.Mock(), wait=wait)
        wait.assert_not_called()
        with mock.patch.object(claude_terminal.os, "geteuid", return_value=1000):
            claude_terminal.main(self.AGENTCORE_SHELL, execve=mock.Mock(), wait=wait)
        wait.assert_called_once()

    def test_main_execs_the_plan(self):
        calls = []
        self.state.write_text('{"volume": "mounted"}')
        with mock.patch.object(claude_terminal.os, "geteuid", return_value=1000):
            code = claude_terminal.main(self.AGENTCORE_SHELL, execve=lambda *a: calls.append(a))
        self.assertEqual(code, 0)
        ((path, argv, env),) = calls
        self.assertEqual((path, argv[-1]), ("/usr/bin/tmux", "exec claude"))
        self.assertEqual(os.path.realpath(os.getcwd()), os.path.realpath(self.projects))
        self.assertEqual(env["HOME"], "/mnt/workspace/home")

    def test_the_installed_script_runs(self):
        script = ROOTFS / "usr/local/bin/devbox-claude"
        res = subprocess.run(
            [sys.executable, "-I", str(script)],
            env={"TMUX": "/tmp/tmux-1000/default,1,0", "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("already inside tmux", res.stderr)


if __name__ == "__main__":
    unittest.main()
