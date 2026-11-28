"""Offline config/query regressions; use the locked application Python environment.
No real AWS credentials or service calls are used.

Run: python -m unittest discover -s scripts -p test_offline.py -v
"""

import importlib.util
import json
import socket
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from configure_gateway_auth import configure as configure_auth
from configure_targets import configure as configure_targets

ROOT = Path(__file__).resolve().parents[1]


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        (self.project / "agentcore").mkdir()
        (self.project / "agentcore/agentcore.json").write_text((ROOT / "agentcore/agentcore.json").read_text())

    def test_targets_preserve_identity_and_other_targets(self):
        configure_targets(self.project, "111111111111", "us-west-2")
        file = self.project / "agentcore/aws-targets.json"
        original = file.read_text()
        configure_targets(self.project, "111111111111", "us-west-2")
        self.assertEqual(file.read_text(), original)
        with self.assertRaises(ValueError):
            configure_targets(self.project, "222222222222", "us-west-2")
        self.assertEqual(file.read_text(), original)
        with self.assertRaises(ValueError):
            configure_targets(self.project, "not-an-account", "us-west-2")

    def test_missing_targets_with_state_is_not_reset(self):
        state = self.project / "agentcore/.cli/deployed-state.json"
        state.parent.mkdir()
        state.write_text('{"targets":{"dev":{"resources":{}}}}')
        with self.assertRaises(ValueError):
            configure_targets(self.project, "111111111111", "us-west-2")
        self.assertIn('"dev"', state.read_text())
        self.assertFalse((self.project / "agentcore/aws-targets.json").exists())

    def test_jwt_keeps_gateway_tools_policy_search_and_eval(self):
        file = self.project / "agentcore/agentcore.json"
        before = json.loads(file.read_text())
        configure_auth(
            self.project,
            "https://example.invalid/.well-known/openid-configuration",
            "test-audience",
            "test-client",
            ["resolve:tickets"],
        )
        after = json.loads(file.read_text())
        gateway = after["agentCoreGateways"][0]
        self.assertTrue(gateway["enableSemanticSearch"])
        self.assertEqual(gateway["targets"], before["agentCoreGateways"][0]["targets"])
        self.assertEqual(gateway["policyEngineConfiguration"]["mode"], "LOG_ONLY")
        self.assertEqual(after["onlineEvalConfigs"], before["onlineEvalConfigs"])
        self.assertEqual(after["memories"], before["memories"])
        env = {e["name"]: e["value"] for e in after["runtimes"][0]["envVars"]}
        self.assertEqual(env["GATEWAY_AUTH_MODE"], "CUSTOM_JWT")
        self.assertEqual(env["GATEWAY_OAUTH_PROVIDER_NAME"], "ITIncidentAgent-gateway-m2m")
        self.assertEqual(env["GATEWAY_OAUTH_SCOPES"], "resolve:tickets")
        self.assertNotIn("client_secret", file.read_text())

    def test_tool_schema_files_match_gateway_names_and_argument_contract(self):
        spec = json.loads((ROOT / "agentcore/agentcore.json").read_text())
        for target in spec["agentCoreGateways"][0]["targets"]:
            tools = json.loads((ROOT / target["lambdaFunctionArn"]["toolSchemaFile"]).read_text())
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0]["name"], target["name"].replace("-", "_"))
            schema = tools[0]["inputSchema"]
            self.assertEqual(schema["type"], "object")
            self.assertTrue(set(schema.get("required", [])).issubset(schema["properties"]))
        # Preserve the audit-only demo: reason is optional, not an enforcement guarantee.
        change = json.loads((ROOT / "tool-schemas/create-change-request.json").read_text())[0]
        self.assertNotIn("reason", change["inputSchema"]["required"])

    def load_evaluate(self, client):
        boto3 = types.ModuleType("boto3")
        boto3.client = MagicMock(return_value=client)
        spec = importlib.util.spec_from_file_location("offline_evaluate", ROOT / "scripts/evaluate.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"boto3": boto3}):
            spec.loader.exec_module(module)
        return module

    def test_evaluation_query_uses_seconds_and_preserves_scores(self):
        client = MagicMock()
        client.start_query.return_value = {"queryId": "offline-query"}
        client.get_query_results.return_value = {
            "status": "Complete",
            "results": [
                [
                    {
                        "field": "@message",
                        "value": json.dumps(
                            {
                                "traceId": "trace",
                                "attributes": {
                                    "gen_ai.evaluation.name": "Builtin.Correctness",
                                    "gen_ai.evaluation.score.value": 0.8,
                                },
                            }
                        ),
                    }
                ]
            ],
        }
        module = self.load_evaluate(client)
        with patch.object(module.time, "time", return_value=2000000000):
            result = module.query_eval_results("test-log-group", 1)
        self.assertEqual(client.start_query.call_args.kwargs["startTime"], 1999996400)
        self.assertEqual(client.start_query.call_args.kwargs["endTime"], 2000000000)
        self.assertEqual(result[0]["score"], 0.8)

    def test_evaluation_failure_and_timeout_are_not_empty_success(self):
        client = MagicMock()
        client.start_query.return_value = {"queryId": "offline-query"}
        module = self.load_evaluate(client)
        client.get_query_results.return_value = {"status": "Failed"}
        with self.assertRaises(RuntimeError):
            module.query_eval_results("test-log-group")
        client.get_query_results.return_value = {"status": "Running"}
        with patch.object(module.time, "sleep"), self.assertRaises(TimeoutError):
            module.query_eval_results("test-log-group")
        client.stop_query.assert_called_once_with(queryId="offline-query")


if __name__ == "__main__":
    unittest.main()
