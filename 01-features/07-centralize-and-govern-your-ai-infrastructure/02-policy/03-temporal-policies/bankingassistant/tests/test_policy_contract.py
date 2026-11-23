"""Genuine pinned policy wire contracts; no service clients or network."""
import contextlib
import copy
import gzip
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
import botocore
import botocore.session
from botocore.loaders import Loader
from botocore.validate import validate_parameters
import test_migration
from test_migration import setup, cleanup, error, ROOT
import policy_contract
from policy_wire import PolicyWire, ENGINE


class PolicyContracts(unittest.TestCase):
    setUp = test_migration.MigrationTests.setUp
    tearDown = test_migration.MigrationTests.tearDown

    def test_actual_ten_permits_create_get_retry_and_delete(self):
        wire = PolicyWire()
        setup.ensure_base_permits(wire, ENGINE, "arn:aws:bedrock-agentcore:us-west-2:123456789012:gateway/abcdefghijkl", {})
        cfg = setup.load_config()
        self.assertEqual(len(cfg["base_policy_ids"]), 10)
        self.assertEqual(len(cfg["base_permits"]), 10)
        setup.ensure_base_permits(wire, ENGINE, "arn:aws:bedrock-agentcore:us-west-2:123456789012:gateway/abcdefghijkl", cfg)
        self.assertEqual(sum(op == "CreatePolicy" for op, _, _ in wire.requests), 10)
        for op, params, request in wire.requests:
            if op == "CreatePolicy":
                self.assertEqual(params["enforcementMode"], "ACTIVE")
                self.assertEqual(set(params["definition"]), {"cedar"})
                self.assertEqual(json.loads(request["body"])["definition"], params["definition"])
        for item in cleanup.list_all(wire.list_policies, "policies", policyEngineId=ENGINE):
            p = {"policyEngineId": ENGINE, "policyId": item["policyId"]}
            cleanup.delete_if_present(wire.delete_policy, **p)
            cleanup.wait_deleted(lambda: wire.get_policy(**p), "Policy")
        self.assertEqual(wire.policies, {})

    def seed(self):
        wire = PolicyWire()
        with patch.object(setup, "BASE_PERMITS", [setup.BASE_PERMITS[0]]):
            setup.ensure_base_permits(wire, ENGINE, "arn:gateway", {})
        return wire, setup.load_config()

    def test_checked_readiness_cli_preserves_parsed_enforcement(self):
        wire, _ = self.seed()
        pid = next(iter(wire.policies))
        session = Mock()
        session.client.return_value = wire
        stream = io.StringIO()
        with patch.object(policy_contract.boto3, "Session", return_value=session), \
             patch("sys.argv", ["policy_contract.py", "get-policy", "--policy-engine-id", ENGINE,
                                "--policy-id", pid, "--region", setup.REGION]), contextlib.redirect_stdout(stream):
            policy_contract.main()
        result = json.loads(stream.getvalue())
        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual(result["enforcementMode"], "ACTIVE")
        self.assertEqual(result["policyId"], pid)
        self.assertEqual(wire.requests[-1][0], "GetPolicy")

    def test_engine_lifecycle_uses_serialized_create_and_parsed_get(self):
        wire = PolicyWire()
        ctrl = Mock()
        body = {"policyEngineId": ENGINE, "name": setup.ENGINE_NAME, "createdAt": 0, "updatedAt": 0,
                "policyEngineArn": f"arn:aws:bedrock-agentcore:{setup.REGION}:123456789012:policy-engine/{ENGINE}",
                "status": "ACTIVE", "statusReasons": []}
        def create(**params):
            wire.request("CreatePolicyEngine", params)
            return wire.response("CreatePolicyEngine", {**body, "status": "CREATING"})
        def get(**params):
            wire.request("GetPolicyEngine", params)
            return wire.response("GetPolicyEngine", body)
        ctrl.create_policy_engine.side_effect = create
        ctrl.get_policy_engine.side_effect = get
        setup.ensure_policy_engine(ctrl, {})
        setup.ensure_policy_engine(ctrl, setup.load_config())
        self.assertEqual(ctrl.create_policy_engine.call_count, 1)
        self.assertNotIn("enforcementMode", wire.responses[-1][2])

    def test_policy_delete_failure_and_present_resource_timeout_are_not_absence(self):
        wire, _ = self.seed()
        pid = next(iter(wire.policies))
        params = {"policyEngineId": ENGINE, "policyId": pid}
        def denied(**p):
            wire.request("DeletePolicy", p)
            raise error("AccessDeniedException")
        with self.assertRaises(type(error())):
            cleanup.delete_if_present(denied, **params)
        wire.request("DeletePolicy", params)
        wire.response("DeletePolicy", {**wire.policies[pid], "status": "DELETING"})
        with self.assertRaises(TimeoutError):
            cleanup.wait_deleted(lambda: wire.get_policy(**params), "Policy", attempts=2)
        self.assertIn(pid, wire.policies)
        self.assertTrue(self.config.exists())

    def test_both_definition_variants_and_modes_use_real_parser(self):
        wire, cfg = self.seed()
        pid = next(iter(wire.policies))
        with patch.object(setup, "BASE_PERMITS", [setup.BASE_PERMITS[0]]):
            for wrapper in ("cedar", "policy"):
                statement = policy_contract.policy_statement(wire.policies[pid]["definition"])
                wire.policies[pid]["definition"] = {wrapper: {"statement": statement}}
                setup.ensure_base_permits(wire, ENGINE, "arn:gateway", cfg)
                for mode in ("LOG_ONLY", None):
                    wire.get_transform = lambda body, mode=mode: ({k: v for k, v in body.items() if k != "enforcementMode"} if mode is None else {**body, "enforcementMode": mode})
                    with self.assertRaisesRegex(RuntimeError, "enforcementMode"):
                        setup.ensure_base_permits(wire, ENGINE, "arn:gateway", cfg)
                wire.get_transform = lambda body: body

    def test_failed_timeout_definition_identity_and_missing_policy(self):
        wire, cfg = self.seed()
        with patch.object(setup, "BASE_PERMITS", [setup.BASE_PERMITS[0]]):
            for field, value in (("status", "CREATE_FAILED"), ("status", "UPDATE_FAILED"),
                                 ("name", "wrong-name"), ("policyEngineId", "wrongengine1"),
                                 ("definition", {"cedar": {"statement": "forbid(principal, action, resource);"}})):
                wire.get_transform = lambda body, field=field, value=value: {**body, field: value}
                with self.subTest(field=field, value=value), self.assertRaises(RuntimeError):
                    setup.ensure_base_permits(wire, ENGINE, "arn:gateway", cfg)
            wire.get_transform = lambda body: {**body, "status": "UPDATING"}
            with self.assertRaises(TimeoutError):
                setup.ensure_base_permits(wire, ENGINE, "arn:gateway", cfg)
            wire.policies.clear()
            with self.assertRaises(type(error())):
                setup.ensure_base_permits(wire, ENGINE, "arn:gateway", cfg)
        self.assertTrue(self.config.exists())

    def test_response_loss_conflict_and_changed_intent_keep_original_token(self):
        wire = PolicyWire()
        with patch.object(setup, "BASE_PERMITS", [setup.BASE_PERMITS[0]]):
            wire.lose_create_response = True
            with self.assertRaises(TimeoutError):
                setup.ensure_base_permits(wire, ENGINE, "arn:gateway", {})
            intent = copy.deepcopy(setup.load_config()["create_intents"])
            wire.lose_create_response = False
            wire.create_failure = error("ConflictException")
            setup.ensure_base_permits(wire, ENGINE, "arn:gateway", setup.load_config())
            calls = [p for op, p, _ in wire.requests if op == "CreatePolicy"]
            self.assertEqual(calls[0], calls[1])
            self.assertEqual(setup.load_config()["create_intents"], intent)
            # Keep an unresolved original request; never rewrite a prior wrapper/token.
            cfg = setup.load_config(); cfg.pop("base_policy_ids"); self.config.write_text(json.dumps(cfg))
            with self.assertRaisesRegex(RuntimeError, "create intent"):
                setup.ensure_base_permits(wire, ENGINE, "arn:different-gateway", cfg)
            self.assertEqual(setup.load_config()["create_intents"], intent)

    def test_genuine_models_reject_invented_wire_and_ambiguous_definitions(self):
        wire, _ = self.seed()
        body = next(iter(wire.policies.values()))
        with self.assertRaises(botocore.exceptions.ParamValidationError):
            wire.response("GetPolicy", {**body, "inventedField": "ACTIVE"})
        for definition in ({"cedar": {"statement": "s"}, "policy": {"statement": "s"}},
                           {"policyGeneration": {}}, {"cedar": {"statement": "s", "other": True}}):
            with self.assertRaises(RuntimeError):
                policy_contract.policy_statement(definition)

    def test_shadowed_model_refused_before_any_provisioning(self):
        # Copy genuine bytes into an explicitly labeled shadow-model fixture.
        source = Path(botocore.__file__).parent / "data" / policy_contract.SERVICE / policy_contract.API_VERSION / "service-2.json.gz"
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / policy_contract.SERVICE / policy_contract.API_VERSION / "service-2.json"
            target.parent.mkdir(parents=True)
            target.write_bytes(gzip.decompress(source.read_bytes()))
            with patch.dict(os.environ, {"AWS_DATA_PATH": tmp}), patch.object(setup.boto3, "Session") as client_factory:
                with self.assertRaisesRegex(RuntimeError, "shadowed"):
                    setup.main()
                client_factory.assert_not_called()
                self.assertFalse(self.config.exists())
            # Unsupported model is a negative fixture only, never used to validate positives.
            data = json.loads(target.read_text())
            shape = data["shapes"][data["operations"]["CreatePolicy"]["input"]["shape"]]
            shape["members"].pop("enforcementMode")
            target.write_text(json.dumps(data))
            session = botocore.session.Session()
            session.register_component("data_loader", Loader(extra_search_paths=[tmp], include_default_search_paths=False))
            with self.assertRaisesRegex(RuntimeError, "Unsupported"):
                policy_contract.require_policy_model(session)

    def test_twelve_readme_requests_serialize_as_portable_cedar(self):
        wire = PolicyWire(); statements = []
        text = "\n".join((ROOT / name).read_text() for name in ("README.md", "DEPLOYMENT.md"))
        blocks = re.findall(r"```bash\n(.*?)```", text, re.S)
        for block in blocks:
            if not block.startswith("aws bedrock-agentcore-control create-policy"):
                continue
            result = subprocess.run(["bash", "--noprofile", "--norc", "-c", "set +H; aws() { printf '%s\\0' \"$@\"; }; " + block],
                env={"PATH": os.environ["PATH"], "ENGINE_ID": ENGINE, "GATEWAY_ARN": "arn:gateway", "REGION": "us-west-2"}, capture_output=True, check=True)
            args = result.stdout.decode().split("\0")
            # One fence can contain two create commands.
            parts = [i for i, arg in enumerate(args) if arg == "create-policy"]
            for start, end in zip(parts, parts[1:] + [len(args)]):
                fields = args[start:end]
                params = {key: fields[fields.index(flag) + 1] for flag, key in (("--policy-engine-id", "policyEngineId"), ("--name", "name"), ("--description", "description"), ("--validation-mode", "validationMode"))}
                params["definition"] = json.loads(fields[fields.index("--definition") + 1])
                self.assertEqual(set(params["definition"]), {"cedar"})
                wire.request("CreatePolicy", params)
                statements.append(params["definition"]["cedar"]["statement"])
        self.assertEqual(len(statements), 12)
        self.assertTrue(all("when temporal" in s for s in statements))


if __name__ == "__main__":
    unittest.main()
