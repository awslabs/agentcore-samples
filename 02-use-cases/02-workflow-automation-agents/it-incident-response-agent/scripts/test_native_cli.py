"""Native CLI parser/config tests, no AWS dispatch or credential acquisition."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from lifecycle import cli_environment
from test_contracts import ROOT


class NativeCliTests(unittest.TestCase):
    def test_private_preference_and_actual_deploy_flags(self):
        cli = os.environ.get("AGENTCORE_CLI") or shutil.which("agentcore")
        self.assertTrue(cli, "Install pinned CLI 1.2.0 before running native contract tests")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env = cli_environment(root)

            def run(*args, data=None):
                return subprocess.run(
                    [cli, *args], cwd=root, env=env, input=data, text=True, capture_output=True, timeout=30, check=True
                )

            self.assertEqual(run("--version").stdout.strip(), "1.2.0")
            self.assertEqual(run("config", "transactionSearch").stdout.strip(), "false")
            help_text = run("deploy", "--help").stdout
            self.assertIn("--skip-bootstrap", help_text)
            self.assertIn("--target", help_text)
            # 1.2.0 config is a global preference, NOT a made-up deploy option.
            self.assertNotIn("--no-transaction-search", help_text)
            (root / "agentcore").mkdir()
            spec = json.loads((ROOT / "agentcore/agentcore.json").read_text())
            spec["name"] = "NativeIT"
            (root / "agentcore/agentcore.json").write_text(json.dumps(spec))
            (root / "agentcore/cdk").mkdir()
            (root / "agentcore/cdk/package.json").write_text((ROOT / "agentcore/cdk/package.json").read_text())
            result = run(
                "add",
                "credentials",
                "oauth",
                "--name",
                "NativeIT-gateway-m2m",
                "--client-id",
                "offline-client",
                "--discovery-url",
                "https://example.invalid/.well-known/openid-configuration",
                "--scopes",
                "resolve:tickets",
                "--client-secret",
                "-",
                data="OFFLINE-SECRET-SENTINEL",
            )
            generated = json.loads((root / "agentcore/agentcore.json").read_text())
            credential = generated["credentials"][0]
            self.assertEqual(credential["name"], "NativeIT-gateway-m2m")
            self.assertEqual(credential["scopes"], ["resolve:tickets"])
            self.assertNotIn("OFFLINE-SECRET-SENTINEL", json.dumps(generated) + result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
