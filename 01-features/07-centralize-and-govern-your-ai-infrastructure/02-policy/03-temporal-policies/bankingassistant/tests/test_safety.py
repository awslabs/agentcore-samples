"""Regression cases from both PR1 reviews; fakes only, never service clients."""

import copy
import json
import os
import re
import subprocess
import unittest
from unittest.mock import Mock, patch

import test_migration
from policy_wire import ENGINE, PolicyWire
from resource_state import OWNER_TAG
from test_migration import ROOT, cleanup, error, setup


class SafetyTests(unittest.TestCase):
    setUp = test_migration.MigrationTests.setUp
    tearDown = test_migration.MigrationTests.tearDown

    def ledger(self):
        return {
            "account_id": "123456789012",
            "region": setup.REGION,
            "ownership_id": "unique-owner",
            "role_name": "banking-gateway-role",
            "role_id": "AROAFAKE",
            "role_arn": "arn:aws:iam::123456789012:role/banking-gateway-role",
            "gateway_name": "banking-gateway",
            "gateway_id": "g",
            "gateway_arn": f"arn:aws:bedrock-agentcore:{setup.REGION}:123456789012:gateway/g",
            "engine_name": "banking_policy_engine",
            "engine_id": ENGINE,
            "engine_arn": f"arn:aws:bedrock-agentcore:{setup.REGION}:123456789012:policy-engine/{ENGINE}",
        }

    def clients(self, cfg):
        ctrl, iam = Mock(), Mock()
        engine = {
            "policyEngineId": cfg["engine_id"],
            "policyEngineArn": cfg["engine_arn"],
            "name": cfg["engine_name"],
            "status": "CREATE_FAILED",
        }
        gateway = {
            "gatewayId": cfg["gateway_id"],
            "gatewayArn": cfg["gateway_arn"],
            "name": cfg["gateway_name"],
            "roleArn": cfg["role_arn"],
            "policyEngineConfiguration": {"arn": cfg["engine_arn"]},
            "status": "FAILED",
        }
        role = {
            "RoleName": cfg["role_name"],
            "Arn": cfg["role_arn"],
            "RoleId": cfg["role_id"],
            "Tags": [{"Key": OWNER_TAG, "Value": cfg["ownership_id"]}],
        }
        ctrl.get_policy_engine.return_value = engine
        ctrl.get_gateway.return_value = gateway
        ctrl.list_tags_for_resource.return_value = {"tags": {OWNER_TAG: cfg["ownership_id"]}}
        iam.get_role.return_value = {"Role": role}
        ctrl.list_policies.return_value = {"policies": []}
        ctrl.list_gateway_targets.return_value = {"items": []}
        return ctrl, iam

    def run_cleanup(self, cfg, ctrl, iam):
        self.config.write_text(json.dumps(cfg))
        session, sts = Mock(), Mock()
        sts.get_caller_identity.return_value = {"Account": cfg["account_id"]}
        session.client.side_effect = lambda name, **kw: {"sts": sts, "iam": iam, "bedrock-agentcore-control": ctrl}[
            name
        ]
        with (
            patch.object(cleanup.boto3, "Session", return_value=session),
            patch("sys.argv", ["cleanup.py"]),
            patch.dict(os.environ, {"REGION": cfg["region"]}),
        ):
            cleanup.main()

    def no_deletes(self, ctrl, iam):
        self.assertFalse(any(c[0].startswith(("delete", "put", "update")) for m in (ctrl, iam) for c in m.mock_calls))
        self.assertTrue(self.config.exists())

    def test_cleanup_rejects_all_mixed_arn_id_name_region_cases(self):
        for key, value in (
            ("gateway_id", "unrelated"),
            ("engine_id", "unrelated"),
            ("role_name", "unrelated-production-role"),
            ("engine_arn", "arn:aws:bedrock-agentcore:eu-west-1:123456789012:policy-engine/e"),
            ("gateway_arn", "malformed"),
        ):
            with self.subTest(key=key):
                cfg = self.ledger()
                ctrl, iam = self.clients(cfg)
                cfg[key] = value
                with self.assertRaises(RuntimeError):
                    self.run_cleanup(cfg, ctrl, iam)
                self.no_deletes(ctrl, iam)

    def test_cleanup_remote_identity_ownership_and_references_checked_first(self):
        for mutation in ("role", "engine", "name", "tag", "role_id", "access"):
            with self.subTest(mutation=mutation):
                cfg = self.ledger()
                ctrl, iam = self.clients(cfg)
                if mutation == "role":
                    ctrl.get_gateway.return_value["roleArn"] = "arn:aws:iam::123456789012:role/other"
                elif mutation == "engine":
                    ctrl.get_gateway.return_value["policyEngineConfiguration"]["arn"] = cfg["engine_arn"] + "other"
                elif mutation == "name":
                    ctrl.get_policy_engine.return_value["name"] = "unrelated"
                elif mutation == "tag":
                    ctrl.list_tags_for_resource.return_value = {"tags": {OWNER_TAG: "other-owner"}}
                elif mutation == "role_id":
                    iam.get_role.return_value["Role"]["RoleId"] = "replacement-role"
                else:
                    iam.get_role.side_effect = error("AccessDeniedException")
                with self.assertRaises((RuntimeError, type(error()))):
                    self.run_cleanup(cfg, ctrl, iam)
                self.no_deletes(ctrl, iam)

    def test_legitimate_partial_create_cleanup_checks_remote_then_deletes(self):
        cfg = self.ledger()
        ctrl, iam = self.clients(cfg)
        cfg.pop("gateway_arn")  # ID was checkpointed before polling failed.
        cfg["create_intents"] = {"gateway": {"params": {"name": cfg["gateway_name"]}}}
        for get in (ctrl.get_gateway, ctrl.get_policy_engine, iam.get_role):
            get.side_effect = [get.return_value, error()]
        self.run_cleanup(cfg, ctrl, iam)
        ctrl.delete_gateway.assert_called_once()
        iam.delete_role.assert_called_once()
        self.assertFalse(self.config.exists())

    def test_second_inventory_access_error_prevents_all_deletion(self):
        cfg = self.ledger()
        ctrl, iam = self.clients(cfg)
        ctrl.list_gateway_targets.side_effect = [{"items": [], "nextToken": "n"}, error("AccessDeniedException")]
        with self.assertRaises(type(error())):
            self.run_cleanup(cfg, ctrl, iam)
        self.no_deletes(ctrl, iam)

    def test_unresolved_intent_retains_ledger(self):
        cfg = self.ledger()
        ctrl, iam = self.clients(cfg)
        cfg.pop("gateway_id")
        cfg.pop("gateway_arn")
        cfg["create_intents"] = {"gateway": {"params": {"name": cfg["gateway_name"]}}}
        with self.assertRaisesRegex(RuntimeError, "Unresolved"):
            self.run_cleanup(cfg, ctrl, iam)
        self.no_deletes(ctrl, iam)

    def test_every_token_api_reuses_pre_request_intent_after_response_lost(self):
        operations = [
            ("engine", "create_policy_engine", lambda c, cfg: setup.ensure_policy_engine(c, cfg)),
            (
                "gateway",
                "create_gateway",
                lambda c, cfg: setup.ensure_gateway(c, "r", "e", "https://discovery.invalid", "c", cfg),
            ),
            (
                "target",
                "create_gateway_target",
                lambda c, cfg: setup.ensure_mcp_target(c, "g", "https://runtime.invalid", "provider", cfg),
            ),
            (
                "policy:permit_get_account_balance",
                "create_policy",
                lambda c, cfg: setup.ensure_base_permits(c, "e", "arn:gateway", cfg),
            ),
        ]
        for key, method, invoke in operations:
            with self.subTest(key=key):
                self.config.unlink(missing_ok=True)
                ctrl = Mock()

                def lost(*, key=key, **params):
                    intent = setup.load_config()["create_intents"][key]
                    self.assertEqual(params["clientToken"], intent["clientToken"])
                    self.assertEqual({k: v for k, v in params.items() if k != "clientToken"}, intent["params"])
                    raise TimeoutError("accepted; response lost")

                getattr(ctrl, method).side_effect = lost
                for _ in range(2):
                    with self.assertRaises(TimeoutError):
                        invoke(ctrl, setup.load_config())
                calls = getattr(ctrl, method).call_args_list
                self.assertEqual(calls[0].kwargs["clientToken"], calls[1].kwargs["clientToken"])

    def test_pre_request_checkpoint_failure_sends_nothing(self):
        ctrl = Mock()
        with patch.object(setup, "atomic_save", side_effect=OSError("disk full")), self.assertRaises(OSError):
            setup.ensure_policy_engine(ctrl, {})
        ctrl.create_policy_engine.assert_not_called()

    def test_post_response_checkpoint_failure_retains_token(self):
        ctrl = Mock()
        ctrl.create_policy_engine.return_value = {"policyEngineId": "e", "policyEngineArn": "arn:e"}
        ctrl.get_policy_engine.return_value = {"status": "ACTIVE"}
        original = setup.save_config

        def fail_result(data):
            if "engine_id" in data:
                raise OSError("checkpoint disk failure")
            original(data)

        with patch.object(setup, "save_config", side_effect=fail_result), self.assertRaises(OSError):
            setup.ensure_policy_engine(ctrl, {})
        setup.ensure_policy_engine(ctrl, setup.load_config())
        calls = ctrl.create_policy_engine.call_args_list
        self.assertEqual(calls[0].kwargs["clientToken"], calls[1].kwargs["clientToken"])
        self.assertEqual(setup.load_config()["engine_id"], "e")

    def test_iam_response_lost_recovers_only_uniquely_tagged_intent(self):
        iam = Mock()
        iam.get_role.side_effect = error("NoSuchEntity")
        created = {}

        def lost(**params):
            self.assertIn("role", setup.load_config()["create_intents"])
            created.update(
                {
                    "Arn": "arn:aws:iam::123456789012:role/" + setup.GATEWAY_ROLE_NAME,
                    "RoleId": "AROAFAKE",
                    "RoleName": params["RoleName"],
                    "Tags": params["Tags"],
                }
            )
            raise TimeoutError("IAM accepted but response lost")

        iam.create_role.side_effect = lost
        with self.assertRaises(TimeoutError):
            setup.ensure_gateway_role(iam, "123456789012", {})
        iam.get_role.side_effect = None
        iam.get_role.return_value = {"Role": created}
        setup.ensure_gateway_role(iam, "123456789012", setup.load_config())
        self.assertEqual(iam.create_role.call_count, 1)
        self.assertEqual(setup.load_config()["role_id"], "AROAFAKE")
        created["Tags"] = []
        with self.assertRaisesRegex(RuntimeError, "unrecorded"):
            setup.ensure_gateway_role(iam, "123456789012", setup.load_config())

    def test_iam_access_error_never_means_absence(self):
        iam = Mock()
        iam.get_role.side_effect = error("AccessDeniedException")
        with self.assertRaises(type(error())):
            setup.ensure_gateway_role(iam, "123456789012", {})
        iam.create_role.assert_not_called()

    def test_policy_failed_conflicting_and_recorded_drift_paths(self):
        name = "permit_get_account_balance"
        definition = {
            "cedar": {
                "statement": 'permit(principal, action == AgentCore::Action::"banking-assistant-tools___get_account_balance", resource == AgentCore::Gateway::"arn:gateway");'
            }
        }
        for path in ("create_failed", "conflict", "recorded_mode", "recorded_definition", "missing"):
            with self.subTest(path=path), patch.object(setup, "BASE_PERMITS", [setup.BASE_PERMITS[0]]):
                self.config.unlink(missing_ok=True)
                ctrl, wire = Mock(), PolicyWire()
                current = wire.create_policy(
                    name=name, policyEngineId=ENGINE, definition=definition, enforcementMode="ACTIVE"
                )
                current.pop("ResponseMetadata")
                current["createdAt"] = current["updatedAt"] = 0
                current["status"] = "ACTIVE"
                pid = current["policyId"]
                cfg = {}
                ctrl.create_policy.side_effect = wire.create_policy
                if path == "create_failed":
                    current["status"] = "CREATE_FAILED"
                elif path == "conflict":
                    ctrl.create_policy.side_effect = error("ConflictException")
                    ctrl.list_policies.side_effect = wire.list_policies
                    current["definition"] = {"cedar": {"statement": "permit(principal, action, resource);"}}
                else:
                    cfg["base_policy_ids"] = {name: pid}
                    if path == "recorded_mode":
                        current["enforcementMode"] = "LOG_ONLY"
                    else:
                        current["definition"] = {"cedar": {"statement": "forbid(principal, action, resource);"}}
                ctrl.get_policy.side_effect = lambda wire=wire, current=current, **p: (
                    wire.request("GetPolicy", p),
                    wire.response("GetPolicy", current),
                )[1]
                if path == "missing":
                    ctrl.get_policy.side_effect = error()
                with self.assertRaises((RuntimeError, type(error()))):
                    setup.ensure_base_permits(ctrl, ENGINE, "arn:gateway", cfg)
                ctrl.get_policy.assert_called_once()
                self.assertNotIn(name, setup.load_config().get("base_permits", []))

    def test_full_fresh_setup_retry_and_cleanup_with_fake_sdk(self):
        """Exercise the actual orchestrators, not replacement setup functions."""
        cfg = self.ledger()
        ctrl, iam, sts, session = Mock(), Mock(), Mock(), Mock()
        policy_wire = PolicyWire()
        store = {"policies": policy_wire.policies, "targets": {}, "tags": {}}
        oauth_state = {"account_id": cfg["account_id"], "region": cfg["region"]}
        oauth_path = self.config.with_name("oauth.json")
        oauth_path.write_text(json.dumps(oauth_state))
        provider = {"provider_arn": "arn:provider", "secret_arn": "arn:secret"}

        def get(key):
            if key not in store:
                raise error("NoSuchEntity" if key == "role" else "ResourceNotFoundException")
            return store[key]

        def create_role(**p):
            role = {"RoleName": p["RoleName"], "Arn": cfg["role_arn"], "RoleId": cfg["role_id"], "Tags": p["Tags"]}
            store["role"] = {"Role": role}
            return store["role"]

        def create_engine(**p):
            policy_wire.request("CreatePolicyEngine", p)
            store["engine"] = {
                "name": p["name"],
                "description": p["description"],
                "policyEngineId": ENGINE,
                "policyEngineArn": cfg["engine_arn"],
                "createdAt": 0,
                "updatedAt": 0,
                "status": "ACTIVE",
                "statusReasons": [],
            }
            store["tags"][cfg["engine_arn"]] = p["tags"]
            return policy_wire.response("CreatePolicyEngine", {**store["engine"], "status": "CREATING"})

        def get_engine(**p):
            policy_wire.request("GetPolicyEngine", p)
            return policy_wire.response("GetPolicyEngine", get("engine"))

        def delete_engine(**p):
            policy_wire.request("DeletePolicyEngine", p)
            return policy_wire.response("DeletePolicyEngine", {**store.pop("engine"), "status": "DELETING"})

        def create_gateway(**p):
            store["gateway"] = {
                **p,
                "gatewayId": "g",
                "gatewayArn": cfg["gateway_arn"],
                "status": "READY",
                "workloadIdentityDetails": {
                    "workloadIdentityArn": f"arn:aws:bedrock-agentcore:{cfg['region']}:{cfg['account_id']}:workload-identity-directory/default/workload-identity/g"
                },
            }
            store["tags"][cfg["gateway_arn"]] = p["tags"]
            return store["gateway"]

        def create_target(**p):
            store["targets"]["t"] = {**p, "targetId": "t", "status": "READY"}
            return store["targets"]["t"]

        def get_child(kind, key):
            if key not in store[kind]:
                raise error()
            return store[kind][key]

        def create_policy(**p):
            return policy_wire.create_policy(**p)

        iam.get_role.side_effect = lambda **p: get("role")
        iam.create_role.side_effect = create_role
        iam.update_assume_role_policy.side_effect = lambda **p: store["role"]["Role"].update(
            AssumeRolePolicyDocument=json.loads(p["PolicyDocument"])
        )
        ctrl.create_policy_engine.side_effect = create_engine
        ctrl.get_policy_engine.side_effect = get_engine
        ctrl.create_gateway.side_effect = create_gateway
        ctrl.get_gateway.side_effect = lambda **p: get("gateway")
        ctrl.update_gateway.side_effect = lambda **p: store["gateway"].update(p)
        ctrl.create_gateway_target.side_effect = create_target
        ctrl.get_gateway_target.side_effect = lambda **p: get_child("targets", p["targetId"])
        ctrl.create_policy.side_effect = create_policy
        ctrl.get_policy.side_effect = policy_wire.get_policy
        ctrl.list_tags_for_resource.side_effect = lambda **p: {"tags": store["tags"][p["resourceArn"]]}
        ctrl.list_policies.side_effect = policy_wire.list_policies
        ctrl.list_gateway_targets.side_effect = lambda **p: {"items": list(store["targets"].values())}
        ctrl.delete_policy.side_effect = policy_wire.delete_policy
        ctrl.delete_gateway_target.side_effect = lambda **p: store["targets"].pop(p["targetId"])
        ctrl.delete_gateway.side_effect = lambda **p: store.pop("gateway")
        ctrl.delete_policy_engine.side_effect = delete_engine
        iam.delete_role.side_effect = lambda **p: store.pop("role")
        sts.get_caller_identity.return_value = {"Account": cfg["account_id"]}
        session.client.side_effect = lambda name, **p: {"sts": sts, "iam": iam, "bedrock-agentcore-control": ctrl}[name]
        with (
            patch.object(setup.boto3, "Session", return_value=session),
            patch.object(setup, "OAUTH_STATE_FILE", oauth_path),
            patch.object(setup, "verify_provider", return_value=provider),
            patch.dict(
                os.environ,
                {
                    "DISCOVERY_URL": "https://discovery.invalid",
                    "GATEWAY_CLIENT_ID": "gateway-client",
                    "MCP_SERVER_URL": "https://runtime.invalid",
                    "MCP_CREDENTIAL_PROVIDER_ARN": "arn:provider",
                },
            ),
        ):
            setup.main()
            first = setup.load_config()
            intended_trust = copy.deepcopy(store["role"]["Role"]["AssumeRolePolicyDocument"])
            for drift in ({}, {"ArnLike": {"aws:SourceArn": cfg["gateway_arn"] + "other"}}):
                store["role"]["Role"]["AssumeRolePolicyDocument"]["Statement"][0]["Condition"] = drift
                setup.main()
                self.assertEqual(store["role"]["Role"]["AssumeRolePolicyDocument"], intended_trust)
            # A replaced role must fail before either trust or inline-policy writes.
            store["role"]["Role"]["RoleId"] = "REPLACEMENT"
            writes = (iam.update_assume_role_policy.call_count, iam.put_role_policy.call_count)
            with self.assertRaises(RuntimeError):
                setup.main()
            self.assertEqual(writes, (iam.update_assume_role_policy.call_count, iam.put_role_policy.call_count))
            store["role"]["Role"]["RoleId"] = cfg["role_id"]
        self.assertEqual(setup.load_config(), first)
        self.assertEqual(ctrl.create_policy.call_count, 10)
        self.assertEqual(ctrl.get_policy.call_count, 30)
        for create in (iam.create_role, ctrl.create_policy_engine, ctrl.create_gateway, ctrl.create_gateway_target):
            create.assert_called_once()
        self.run_cleanup(first, ctrl, iam)
        self.assertFalse(self.config.exists())
        self.assertEqual(store["policies"], {})
        self.assertEqual(store["targets"], {})
        self.assertTrue({"role", "engine", "gateway"}.isdisjoint(store))

    def test_target_endpoint_provider_type_and_scope_drift(self):
        ctrl = Mock()
        ctrl.create_gateway_target.return_value = {"targetId": "t"}
        # Obtain the exact intended configuration from the actual create request.
        ctrl.get_gateway_target.return_value = {"status": "FAILED"}
        with self.assertRaises(RuntimeError):
            setup.ensure_mcp_target(ctrl, "g", "https://runtime.invalid", "provider", {})
        expected = {**ctrl.create_gateway_target.call_args.kwargs, "targetId": "t", "status": "READY"}
        for field in ("endpoint", "provider", "type", "scopes", "none"):
            current = copy.deepcopy(expected)
            if field == "endpoint":
                current["targetConfiguration"]["mcp"]["mcpServer"]["endpoint"] = "https://stale.invalid"
            elif field == "type":
                current["credentialProviderConfigurations"][0]["credentialProviderType"] = "GATEWAY_IAM_ROLE"
            elif field in ("provider", "scopes"):
                current["credentialProviderConfigurations"][0]["credentialProvider"]["oauthCredentialProvider"][
                    "providerArn" if field == "provider" else "scopes"
                ] = "wrong"
            ctrl.get_gateway_target.return_value = current
            if field == "none":
                self.assertEqual(
                    setup.ensure_mcp_target(ctrl, "g", "https://runtime.invalid", "provider", setup.load_config()), "t"
                )
            else:
                with self.assertRaises(RuntimeError):
                    setup.ensure_mcp_target(ctrl, "g", "https://runtime.invalid", "provider", setup.load_config())

    def test_approval_display_matches_canonical_command(self):
        text = "\n".join((ROOT / name).read_text() for name in ("README.md", "DEPLOYMENT.md"))
        displayed = next(b for b in re.findall(r"```cedar\n(.*?)```", text, re.DOTALL) if "___approve_trade" in b)
        block = next(
            b
            for b in re.findall(r"```bash\n(.*?)```", text, re.DOTALL)
            if b.startswith("aws bedrock-agentcore-control create-policy") and "--name portfolio_approval_gate" in b
        )
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", 'set +H; aws() { printf "%s\\0" "$@"; }; ' + block],
            env={
                "PATH": os.environ["PATH"],
                "ENGINE_ID": "fake",
                "GATEWAY_ARN": "<GATEWAY_ARN>",
                "REGION": "us-west-2",
            },
            capture_output=True,
            check=True,
        )
        args = result.stdout.decode().split("\0")
        canonical = json.loads(args[args.index("--definition") + 1])["cedar"]["statement"]
        self.assertEqual(re.sub(r"\s+", "", displayed), re.sub(r"\s+", "", canonical))
        self.assertIn('input.status: "approved"', displayed)


if __name__ == "__main__":
    unittest.main()
