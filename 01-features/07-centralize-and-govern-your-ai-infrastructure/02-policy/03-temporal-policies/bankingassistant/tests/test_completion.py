"""Focused round-2 regressions. Fakes only; no service clients or secret access."""

import copy
import fnmatch
import json
import unittest
from datetime import UTC, datetime, timezone
from unittest.mock import Mock, patch

import oauth_provider as oauth
import test_migration
import test_safety
from resource_state import OWNER_TAG
from test_migration import error, setup


class CompletionTests(unittest.TestCase):
    setUp = test_migration.MigrationTests.setUp
    tearDown = test_migration.MigrationTests.tearDown

    def test_fresh_collision_and_access_error_never_update_or_create(self):
        for failure in (None, error("AccessDeniedException")):
            with self.subTest(failure=failure), patch.object(oauth, "STATE_FILE", self.config):
                self.config.unlink(missing_ok=True)
                ctrl = Mock()
                ctrl.get_oauth2_credential_provider.return_value = {
                    "name": "banking-oauth",
                    "credentialProviderVendor": "CustomOauth2",
                    "credentialProviderArn": f"arn:aws:bedrock-agentcore:{setup.REGION}:123456789012:token-vault/default/oauth2credentialprovider/banking-oauth",
                }
                ctrl.list_tags_for_resource.return_value = {"tags": {OWNER_TAG: "unrelated-owner"}}
                ctrl.get_oauth2_credential_provider.side_effect = failure
                with self.assertRaises((RuntimeError, type(error()))):
                    oauth.prepare(
                        ctrl,
                        "123456789012",
                        setup.REGION,
                        "new-client",
                        "https://example.invalid",
                        "fake-secret",
                        name="banking-oauth",
                    )
                ctrl.create_oauth2_credential_provider.assert_not_called()
                ctrl.update_oauth2_credential_provider.assert_not_called()
                self.assertEqual(json.loads(self.config.read_text())["name"], "banking-oauth")

    def oauth_fake(self):
        ctrl = Mock()
        store, tags = {}, {}
        model = test_migration.Session().get_service_model("bedrock-agentcore-control")

        def checked_response(operation, response):
            # Reject invented SDK response members, not only malformed create requests.
            test_migration.validate_parameters(response, model.operation_model(operation).output_shape)
            return copy.deepcopy(response)

        def get(**params):
            if not store:
                raise error()
            self.assertEqual(params["name"], store["name"])
            return checked_response("GetOauth2CredentialProvider", store)

        def list_tags(**params):
            self.assertEqual(params["resourceArn"], store["credentialProviderArn"])
            return checked_response("ListTagsForResource", {"tags": tags})

        def create(**params):
            test_migration.validate_parameters(
                params, model.operation_model("CreateOauth2CredentialProvider").input_shape
            )
            state = json.loads(self.config.read_text())
            self.assertEqual(params["tags"][OWNER_TAG], state["ownership_id"])
            prefix = f"arn:aws:bedrock-agentcore:{setup.REGION}:123456789012:"
            config = copy.deepcopy(params["oauth2ProviderConfigInput"])
            config["customOauth2ProviderConfig"].pop("clientSecret")
            tags.update(params["tags"])
            store.update(
                name=params["name"],
                credentialProviderVendor="CustomOauth2",
                credentialProviderArn=prefix + "token-vault/default/oauth2credentialprovider/" + params["name"],
                clientSecretArn={
                    "secretArn": f"arn:aws:secretsmanager:{setup.REGION}:123456789012:secret:owned-123456"
                },
                createdTime=datetime(2026, 1, 1, tzinfo=UTC),
                lastUpdatedTime=datetime(2026, 1, 1, tzinfo=UTC),
                oauth2ProviderConfigOutput=config,
            )
            return checked_response(
                "CreateOauth2CredentialProvider",
                {k: store[k] for k in ("name", "credentialProviderArn", "clientSecretArn")},
            )

        ctrl.get_oauth2_credential_provider.side_effect = get
        ctrl.list_tags_for_resource.side_effect = list_tags
        ctrl.create_oauth2_credential_provider.side_effect = create
        return ctrl, store, create

    def test_oauth_fresh_durable_name_intentional_reuse_and_drift(self):
        ctrl, store, _ = self.oauth_fake()
        args = (ctrl, "123456789012", setup.REGION, "client", "https://example.invalid", "fake-secret")
        with patch.object(oauth, "STATE_FILE", self.config):
            name = oauth.prepare(*args)
            self.assertRegex(name, r"^banking-oauth-[0-9a-f]{32}$")
            ledger = json.loads(self.config.read_text())
            self.assertNotIn("fake-secret", self.config.read_text())
            with self.assertRaisesRegex(RuntimeError, "--reuse-owned"):
                oauth.prepare(*args)
            self.assertEqual(oauth.prepare(*args, reuse_owned=True), name)
            for field, value in (
                ("createdTime", datetime(2026, 2, 1, tzinfo=UTC)),
                ("clientSecretArn", {"secretArn": ledger["secret_arn"] + "other"}),
                ("credentialProviderArn", ledger["provider_arn"] + "other"),
                (
                    "oauth2ProviderConfigOutput",
                    {
                        "customOauth2ProviderConfig": {
                            "clientId": "other",
                            "oauthDiscovery": {"discoveryUrl": "https://other.invalid"},
                        }
                    },
                ),
            ):
                original = store[field]
                store[field] = value
                tag_reads = ctrl.list_tags_for_resource.call_count
                with self.subTest(field=field), self.assertRaises(RuntimeError):
                    oauth.prepare(*args, reuse_owned=True)
                if field == "credentialProviderArn":
                    self.assertEqual(ctrl.list_tags_for_resource.call_count, tag_reads)
                store[field] = original
            ctrl.create_oauth2_credential_provider.assert_called_once()
            ctrl.update_oauth2_credential_provider.assert_not_called()

    def test_oauth_tag_reads_fail_closed_without_updates(self):
        ctrl, _store, _ = self.oauth_fake()
        args = (ctrl, "123456789012", setup.REGION, "client", "https://example.invalid", "fake-secret")
        with patch.object(oauth, "STATE_FILE", self.config):
            oauth.prepare(*args)
            original = ctrl.list_tags_for_resource.side_effect
            for response in ({}, {"tags": {}}, {"tags": {OWNER_TAG: "unrelated"}}, error("AccessDeniedException")):
                with self.subTest(response=response):
                    ctrl.list_tags_for_resource.side_effect = response if isinstance(response, Exception) else None
                    ctrl.list_tags_for_resource.return_value = response
                    with self.assertRaises((RuntimeError, type(error()))):
                        oauth.prepare(*args, reuse_owned=True)
                    self.assertTrue(self.config.exists())
            ctrl.list_tags_for_resource.side_effect = original
            oauth.prepare(*args, reuse_owned=True)
            ctrl.create_oauth2_credential_provider.assert_called_once()
            ctrl.update_oauth2_credential_provider.assert_not_called()

    def test_oauth_failed_tag_read_after_create_retains_intent_for_recovery(self):
        ctrl, store, _ = self.oauth_fake()
        args = (ctrl, "123456789012", setup.REGION, "client", "https://example.invalid", "fake-secret")
        tags = ctrl.list_tags_for_resource.side_effect
        ctrl.list_tags_for_resource.side_effect = error("AccessDeniedException")
        with patch.object(oauth, "STATE_FILE", self.config):
            with self.assertRaises(type(error())):
                oauth.prepare(*args)
            pending = json.loads(self.config.read_text())
            self.assertNotIn("provider_arn", pending)
            ctrl.list_tags_for_resource.side_effect = tags
            self.assertEqual(oauth.prepare(*args, reuse_owned=True), pending["name"])
            self.assertEqual(json.loads(self.config.read_text())["provider_arn"], store["credentialProviderArn"])
            ctrl.create_oauth2_credential_provider.assert_called_once()
            ctrl.update_oauth2_credential_provider.assert_not_called()

    def test_oauth_lost_response_recovers_tagged_intent_and_racing_collision_fails(self):
        ctrl, store, create = self.oauth_fake()
        args = (ctrl, "123456789012", setup.REGION, "client", "https://example.invalid", "fake-secret")
        with patch.object(oauth, "STATE_FILE", self.config):

            def lost(**params):
                create(**params)
                raise TimeoutError("accepted; response lost")

            ctrl.create_oauth2_credential_provider.side_effect = lost
            with self.assertRaises(TimeoutError):
                oauth.prepare(*args)
            name = json.loads(self.config.read_text())["name"]
            self.assertEqual(oauth.prepare(*args, reuse_owned=True), name)
            ctrl.create_oauth2_credential_provider.assert_called_once()
            ctrl.update_oauth2_credential_provider.assert_not_called()
            # A different fresh reservation's GET/CREATE race must not become an upsert.
            self.config.unlink()
            store.clear()
            ctrl.create_oauth2_credential_provider.side_effect = error("ConflictException")
            with self.assertRaises(type(error())):
                oauth.prepare(*args)
            ctrl.update_oauth2_credential_provider.assert_not_called()
            self.assertTrue(self.config.exists())

    def test_oauth_checkpoint_failure_and_unrecorded_reuse_send_nothing(self):
        ctrl = Mock()
        args = (ctrl, "123456789012", setup.REGION, "client", "https://example.invalid", "fake-secret")
        with patch.object(oauth, "STATE_FILE", self.config):
            with self.assertRaises(RuntimeError):
                oauth.prepare(*args, reuse_owned=True)
            with (
                patch.object(oauth, "atomic_save", side_effect=OSError("disk full")),
                self.assertRaises(OSError),
            ):
                oauth.prepare(*args)
            self.assertEqual(ctrl.mock_calls, [])

    def test_gateway_grant_exact_contract_and_unrelated_resource_negatives(self):
        prefix = f"arn:aws:bedrock-agentcore:{setup.REGION}:123456789012:"
        cfg = {
            "account_id": "123456789012",
            "region": setup.REGION,
            "role_name": "r",
            "engine_arn": prefix + "policy-engine/owned",
            "gateway_arn": prefix + "gateway/owned",
            "workload_identity_arn": prefix + "workload-identity-directory/default/workload-identity/owned",
        }
        provider = {
            "provider_arn": prefix + "token-vault/default/oauth2credentialprovider/owned",
            "secret_arn": f"arn:aws:secretsmanager:{setup.REGION}:123456789012:secret:owned-123456",
        }
        iam = Mock()
        setup.apply_gateway_policy(iam, cfg, provider)
        statements = json.loads(iam.put_role_policy.call_args.kwargs["PolicyDocument"])["Statement"]
        by_sid = {s["Sid"]: s for s in statements}
        self.assertEqual(
            set(by_sid["PolicyEngineAuthorization"]["Action"]),
            {
                "bedrock-agentcore:GetPolicyEngine",
                "bedrock-agentcore:CheckAuthorizePermissions",
                "bedrock-agentcore:AuthorizeAction",
                "bedrock-agentcore:PartiallyAuthorizeActions",
            },
        )
        self.assertEqual(by_sid["PolicyEngineAuthorization"]["Resource"], [cfg["engine_arn"], cfg["gateway_arn"]])
        directory = prefix + "workload-identity-directory/default"
        self.assertEqual(by_sid["GetWorkloadAccessToken"]["Resource"], [directory, cfg["workload_identity_arn"]])
        self.assertEqual(
            by_sid["ResourceOauth2Token"]["Resource"],
            [prefix + "token-vault/default", provider["provider_arn"], directory, cfg["workload_identity_arn"]],
        )
        self.assertEqual(by_sid["GetSecretValue"]["Resource"], provider["secret_arn"])
        grants = [
            r for s in statements for r in (s["Resource"] if isinstance(s["Resource"], list) else [s["Resource"]])
        ]
        self.assertTrue(all("*" not in r and "?" not in r for r in grants))
        for resource in (*cfg.values(), *provider.values()):
            if not resource.startswith("arn:"):
                continue
            for unrelated in (resource + "-other", resource.replace("123456789012", "000000000000")):
                self.assertFalse(any(fnmatch.fnmatchcase(unrelated, r) for r in grants))
        # Bootstrap has no unknown gateway/OAuth wildcard while create is pending.
        setup.apply_gateway_policy(
            iam, {k: v for k, v in cfg.items() if k not in ("gateway_arn", "workload_identity_arn")}, provider
        )
        bootstrap = json.loads(iam.put_role_policy.call_args.kwargs["PolicyDocument"])["Statement"]
        self.assertEqual(len(bootstrap), 1)
        self.assertEqual(bootstrap[0]["Resource"], [cfg["engine_arn"]])

    def test_unresolved_children_block_cleanup_until_recovered(self):
        helper = test_safety.SafetyTests()
        helper.config = self.config
        for kind, intent in (
            ("target", {"params": {"name": setup.TARGET_NAME}}),
            ("policy:permit_get_account_balance", {"params": {"name": "permit_get_account_balance"}}),
        ):
            cfg = helper.ledger()
            cfg["create_intents"] = {kind: intent}
            ctrl, iam = helper.clients(cfg)
            with self.subTest(kind=kind), self.assertRaisesRegex(RuntimeError, "Unresolved"):
                helper.run_cleanup(cfg, ctrl, iam)
            helper.no_deletes(ctrl, iam)
            # IDs recovered by setup let inventory + deletion proceed for dedicated parents.
            if kind == "target":
                cfg["target_id_" + setup.TARGET_NAME] = "recovered-target"
                ctrl.list_gateway_targets.return_value = {"items": [{"targetId": "recovered-target"}]}
                ctrl.get_gateway_target.side_effect = error()
            else:
                cfg["base_policy_ids"] = {"permit_get_account_balance": "recovered-policy"}
                ctrl.list_policies.return_value = {"policies": [{"policyId": "recovered-policy"}]}
                ctrl.get_policy.side_effect = error()
            for get in (ctrl.get_gateway, ctrl.get_policy_engine, iam.get_role):
                get.side_effect = [get.return_value, error()]
            helper.run_cleanup(cfg, ctrl, iam)
            self.assertFalse(self.config.exists())


if __name__ == "__main__":
    unittest.main()
