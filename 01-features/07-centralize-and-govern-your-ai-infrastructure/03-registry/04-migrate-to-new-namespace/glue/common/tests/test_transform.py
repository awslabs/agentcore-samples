"""Characterization tests for the Preview -> target record/registry transform.

These lock the exact target output shape for representative Preview records (the breaking-change
mappings from the design doc): descriptor restructure, inlineContent->data, version collapse,
per-descriptor source placement, recordType inference, and the markdown-only skill rule that
regressed once before (now settled against the live service: it becomes an agentSkillsDefinition
carrying the Markdown under additionalData.skillMd). The synthesized `name` is a source-namespaced
hash, so tests assert its contract (prefix + 32 hex) and compare the rest of the record exactly.
"""

from __future__ import annotations

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from migration_common.transform import (
    RecordTransformer,
    TransformError,
    transform_registry_configuration,
)

SOURCE = {"accountId": "111122223333", "region": "us-east-1", "registryId": "reg-abc"}
# Only used when a record has no usable name of its own; see TargetNameCarriesOverFromTheSource.
GENERATED_NAME_RE = re.compile(r"^migrated-[0-9a-f]{32}$")


def _transform(preview, *, config=None, context=None):
    transformer = RecordTransformer(config or {})
    result = transformer.transform(preview, context or {"source": SOURCE})
    return result


def _record_without_name(result):
    """Drop `name` after asserting it equals the source record's own name.

    The target name is the dedup key and what you filter by, so it must stay the source name rather
    than anything generated. The rest of the record is compared field by field by the caller.
    """
    record = dict(result.record)
    record.pop("name")
    return record


class RecordTransformCharacterization(unittest.TestCase):
    def test_mcp_flat_with_inherited_source(self):
        creds = [
            {
                "credentialProviderType": "IAM",
                "credentialProvider": {
                    "iamCredentialProvider": {
                        "roleArn": "arn:aws:iam::111122223333:role/r",
                        "service": "s",
                        "region": "us-east-1",
                    }
                },
            }
        ]
        preview = {
            "recordId": "rec-mcp",
            "name": "my-mcp",
            "descriptors": {"mcp": {"server": {"inlineContent": "SERVER_JSON", "schemaVersion": "1.0"}}},
            "synchronizationConfiguration": {
                "fromUrl": {"url": "https://mcp.example.com", "credentialProviderConfigurations": creds}
            },
        }
        result = _transform(preview)
        self.assertEqual(result.warnings, [])
        self.assertEqual(result.old_record_id, "rec-mcp")
        self.assertEqual(
            _record_without_name(result),
            {
                "displayName": "my-mcp",
                "recordType": "MCP",
                "descriptors": {
                    "mcpServer": {
                        "data": "SERVER_JSON",
                        "dataSchemaVersion": "1.0",
                        "source": {
                            "fromUrl": {
                                "url": "https://mcp.example.com",
                                "credentialProviderConfigurations": creds,
                            }
                        },
                    }
                },
            },
        )

    def test_a2a_agent_card_no_source(self):
        preview = {
            "recordId": "rec-a2a",
            "name": "agent-a",
            "descriptors": {"agent": {"a2aAgentCard": {"inlineContent": "CARD", "schemaVersion": "2"}}},
        }
        result = _transform(preview)
        self.assertEqual(result.warnings, [])
        self.assertEqual(
            _record_without_name(result),
            {
                "displayName": "agent-a",
                "recordType": "AGENT",
                "descriptors": {"a2aAgentCard": {"data": "CARD", "dataSchemaVersion": "2"}},
            },
        )

    def test_skill_definition_with_skill_md_additional_data(self):
        preview = {
            "recordId": "rec-skill",
            "name": "skill-s",
            "descriptors": {
                "agentSkills": {
                    "skillDefinition": {"inlineContent": "DEF", "schemaVersion": "1"},
                    "skillMd": {"inlineContent": "# MD"},
                }
            },
        }
        result = _transform(preview)
        self.assertEqual(result.warnings, [])
        self.assertEqual(
            _record_without_name(result),
            {
                "displayName": "skill-s",
                "recordType": "SKILL",
                "descriptors": {
                    "agentSkillsDefinition": {
                        "data": "DEF",
                        "dataSchemaVersion": "1",
                        "additionalData": {"skillMd": {"data": "# MD"}},
                    }
                },
            },
        )

    def test_markdown_only_skill_maps_to_definition_with_markdown_in_additional_data(self):
        # Settled against the live service, which rejects the two shapes this mapping has
        # flip-flopped between before. Verified by direct CreateRegistryRecord calls:
        #
        #   {"agentSkillsMd": {"data": md}}                  -> 400 "Exactly one valid descriptor is
        #       allowed for record type SKILL. Valid descriptors: [agentSkillsDefinition, custom]"
        #   {"agentSkillsDefinition": {"data": md}}          -> 400 "data is not valid JSON"
        #   {"custom": {"data": md}} as CUSTOM               -> 400 "data is not valid JSON"
        #   {"agentSkillsDefinition": {"additionalData":
        #       {"skillMd": {"data": md}}}}                   -> 202 accepted
        #
        # So the Markdown has to travel under additionalData.skillMd with no data on the primary.
        # Do not "simplify" this back to either rejected form.
        preview = {
            "recordId": "rec-md",
            "name": "md-skill",
            "descriptors": {"agentSkills": {"skillMd": {"inlineContent": "# HELLO"}}},
        }
        result = _transform(preview)
        self.assertEqual(
            result.warnings,
            [
                (
                    "Preview markdown-only skill was migrated as an agentSkillsDefinition carrying "
                    "the Markdown under additionalData.skillMd, because the service accepts no agentSkillsMd "
                    "descriptor."
                )
            ],
        )
        self.assertEqual(
            _record_without_name(result),
            {
                "displayName": "md-skill",
                "recordType": "SKILL",
                "descriptors": {"agentSkillsDefinition": {"additionalData": {"skillMd": {"data": "# HELLO"}}}},
            },
        )

    def test_markdown_only_skill_keeps_its_source_on_the_markdown_child(self):
        # agentSkillsDefinition does not support source; skillMd does. The sync config must therefore
        # end up on the child, not be moved to the primary and not be dropped.
        preview = {
            "recordId": "rec-md-src",
            "name": "md-skill-src",
            "descriptors": {"agentSkills": {"skillMd": {"inlineContent": "# HELLO"}}},
            "synchronizationConfiguration": {"fromUrl": {"url": "https://example.com/skill.md"}},
        }
        result = _transform(preview)
        self.assertEqual(
            result.record["descriptors"],
            {
                "agentSkillsDefinition": {
                    "additionalData": {
                        "skillMd": {
                            "data": "# HELLO",
                            "source": {"fromUrl": {"url": "https://example.com/skill.md"}},
                        }
                    }
                }
            },
        )

    def test_custom(self):
        preview = {"recordId": "rec-c", "name": "C", "descriptors": {"custom": {"inlineContent": "BLOB"}}}
        result = _transform(preview)
        self.assertEqual(result.warnings, [])
        self.assertEqual(
            _record_without_name(result),
            {"displayName": "C", "recordType": "CUSTOM", "descriptors": {"custom": {"data": "BLOB"}}},
        )

    def test_custom_with_inherited_source_is_omitted_with_warning(self):
        preview = {
            "recordId": "rec-c2",
            "name": "C2",
            "descriptors": {"custom": {"inlineContent": "BLOB"}},
            "synchronizationConfiguration": {"fromUrl": {"url": "https://x"}},
        }
        result = _transform(preview)
        self.assertEqual(
            _record_without_name(result),
            {"displayName": "C2", "recordType": "CUSTOM", "descriptors": {"custom": {"data": "BLOB"}}},
        )
        self.assertTrue(any("does not support source" in w for w in result.warnings), result.warnings)

    def test_record_version_and_description_passthrough(self):
        preview = {
            "recordId": "rec-v",
            "name": "V",
            "recordVersion": "3",
            "description": "hello",
            "descriptors": {"mcp": {"server": {"inlineContent": "S"}}},
        }
        result = _transform(preview)
        self.assertEqual(result.warnings, [])
        self.assertEqual(
            _record_without_name(result),
            {
                "displayName": "V",
                "recordType": "MCP",
                "descriptors": {"mcpServer": {"data": "S"}},
                "recordVersion": "3",
                "description": "hello",
            },
        )

    def test_display_name_fallback_when_no_name(self):
        preview = {"recordId": "rec-none", "descriptors": {"custom": {"inlineContent": "B"}}}
        result = _transform(preview)
        self.assertEqual(result.record["displayName"], "Migrated rec-none")
        self.assertTrue(any("fallback displayName" in w for w in result.warnings), result.warnings)

    def test_context_old_record_id_takes_precedence(self):
        preview = {"recordId": "rec-ignored", "name": "N", "descriptors": {"custom": {"inlineContent": "B"}}}
        by_record_id = _transform(preview)
        by_context = _transform(preview, context={"source": SOURCE, "oldRecordId": "CTX"})
        self.assertEqual(by_context.old_record_id, "CTX")
        # The reported old record id changes, but the target name is the source record's own name and
        # therefore does not depend on which id the extract stage reported.
        self.assertEqual(by_record_id.record["name"], "N")
        self.assertEqual(by_context.record["name"], "N")

    def test_disallowed_record_type_raises(self):
        preview = {"recordId": "rec-x", "name": "X", "descriptors": {"mcp": {"server": {"inlineContent": "S"}}}}
        with self.assertRaises(TransformError):
            _transform(preview, config={"allowedRecordTypes": ["CUSTOM"]})

    def test_explicit_source_on_unsupported_descriptor_raises(self):
        preview = {
            "recordId": "rec-bad",
            "name": "X",
            "descriptors": {
                "agentSkills": {"skillDefinition": {"inlineContent": "D", "source": {"fromUrl": {"url": "https://x"}}}}
            },
        }
        with self.assertRaises(TransformError):
            _transform(preview)

    def test_empty_descriptors_raises(self):
        with self.assertRaises(TransformError):
            _transform({"recordId": "r", "name": "N", "descriptors": {}})


class TargetNameCarriesOverFromTheSource(unittest.TestCase):
    """The target `name` is the dedup key, the filter key and the lookup key.

    It has to be the name the record already had, or dual-write dedup and
    `--filters name=<name>` both stop working against migrated records.
    """

    def _name(self, preview_name, **kwargs):
        preview = {"recordId": "rec-1", "descriptors": {"custom": {"inlineContent": "B"}}}
        if preview_name is not None:
            preview["name"] = preview_name
        return _transform(preview, **kwargs)

    def test_source_name_is_carried_over_unchanged(self):
        result = self._name("payments-mcp")
        self.assertEqual(result.record["name"], "payments-mcp")
        self.assertEqual(result.record["displayName"], "payments-mcp")
        self.assertEqual(result.warnings, [])

    def test_every_shape_the_preview_api_allows_survives_verbatim(self):
        # The Preview API constrains names to [a-zA-Z0-9][a-zA-Z0-9_\-./]*, so anything it accepted
        # must come through untouched -- no sanitising, no suffix, no warning.
        for name in ("a", "A1", "team/payments-mcp", "payments_mcp.v2", "x" * 255):
            with self.subTest(name=name):
                result = self._name(name)
                self.assertEqual(result.record["name"], name)
                self.assertEqual(result.warnings, [])

    def test_a_name_needing_sanitising_keeps_a_recognisable_form_and_warns(self):
        result = self._name("Payments MCP")
        self.assertTrue(result.record["name"].startswith("Payments-MCP-"), result.record["name"])
        self.assertIn("not a valid target name", result.warnings[0])
        # displayName keeps the original text; only the key was coerced.
        self.assertEqual(result.record["displayName"], "Payments MCP")

    def test_two_similar_names_cannot_collapse_onto_one(self):
        first = self._name("Payments MCP").record["name"]
        second = self._name("Payments/MCP").record["name"]
        self.assertNotEqual(first, second)

    def test_sanitising_is_deterministic(self):
        self.assertEqual(self._name("Payments MCP").record["name"], self._name("Payments MCP").record["name"])

    def test_an_over_long_name_is_truncated_within_the_target_limit(self):
        result = self._name("Bad name " + "y" * 300)
        self.assertLessEqual(len(result.record["name"]), 255)

    def test_a_record_with_no_name_falls_back_to_a_generated_one(self):
        result = self._name(None)
        self.assertRegex(result.record["name"], GENERATED_NAME_RE)
        self.assertTrue(any("had no name" in warning for warning in result.warnings), result.warnings)

    def test_a_name_with_nothing_usable_falls_back_to_a_generated_one(self):
        result = self._name("...")
        self.assertRegex(result.record["name"], GENERATED_NAME_RE)
        self.assertTrue(any("no characters the service accepts" in w for w in result.warnings), result.warnings)

    def test_the_generated_fallback_uses_the_configured_prefix(self):
        result = self._name(None, config={"namePrefix": "wave1"})
        self.assertTrue(result.record["name"].startswith("wave1-"), result.record["name"])

    def test_the_generated_fallback_is_namespaced_by_source_registry(self):
        other = {"accountId": "111122223333", "region": "us-east-1", "registryId": "reg-other"}
        self.assertNotEqual(
            self._name(None).record["name"],
            self._name(None, context={"source": other}).record["name"],
        )


class SourceStatusTravelsWithTheRecord(unittest.TestCase):
    """The load stage reproduces the source status, so the transform reports rather than warns."""

    def _with_status(self, status):
        return _transform(
            {
                "recordId": "rec-1",
                "name": "payments-mcp",
                "status": status,
                "descriptors": {"custom": {"inlineContent": "B"}},
            }
        )

    def test_a_reproducible_status_is_carried_without_a_warning(self):
        for status in ("DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED", "DEPRECATED"):
            with self.subTest(status=status):
                result = self._with_status(status)
                self.assertEqual(result.source_status, status)
                self.assertEqual(result.warnings, [])

    def test_a_status_that_cannot_exist_on_a_new_record_warns(self):
        for status in ("CREATE_FAILED", "UPDATE_FAILED", "CREATING", "UPDATING"):
            with self.subTest(status=status):
                result = self._with_status(status)
                self.assertTrue(any("cannot be reproduced" in w for w in result.warnings), result.warnings)


class RegistryConfigurationTransform(unittest.TestCase):
    def test_authorizer_grouping_and_auto_approval_rules(self):
        preview = {
            "name": "reg1",
            "description": "d",
            "authorizerType": "AWS_IAM",
            "authorizerConfiguration": {"customJWTAuthorizer": {"discoveryUrl": "https://idp/.well-known"}},
            "approvalConfiguration": {"autoApproval": True},
        }
        self.assertEqual(
            transform_registry_configuration(preview),
            {
                "name": "reg1",
                "description": "d",
                "discoveryConfiguration": {
                    "authorizerType": "AWS_IAM",
                    "authorizerConfiguration": {"customJWTAuthorizer": {"discoveryUrl": "https://idp/.well-known"}},
                },
                "approvalConfiguration": {"autoApprovalRules": ["APPROVE_ALL"]},
            },
        )

    def test_auto_approval_false_maps_to_empty_rules(self):
        preview = {
            "name": "reg2",
            "authorizerType": "CUSTOM_JWT",
            "approvalConfiguration": {"autoApproval": False},
        }
        result = transform_registry_configuration(preview)
        self.assertEqual(result["approvalConfiguration"], {"autoApprovalRules": []})


class RegistryAuthorizerIsProjectedOntoTheTargetShape(unittest.TestCase):
    """The Preview registry authorizer is not the target one, so it cannot be copied verbatim.

    Preview registries carry ``bedrock-agentcore``'s authorizer structure, shared with Gateway and
    Runtime, so it has members the target registry API does not model. Copying those through builds a
    payload the service refuses -- and because this payload is applied by hand, the failure lands on
    a person with no explanation of which field caused it.
    """

    JWT_PATH = ("discoveryConfiguration", "authorizerConfiguration", "customJWTAuthorizer")

    def _jwt(self, result):
        value = result
        for key in self.JWT_PATH:
            value = value[key]
        return value

    def _preview(self, **jwt):
        return {
            "name": "reg-jwt",
            "registryId": "7UTnSjchy17rHV0u",
            "authorizerType": "CUSTOM_JWT",
            "authorizerConfiguration": {
                "customJWTAuthorizer": {
                    "discoveryUrl": "https://idp/.well-known/openid-configuration",
                    **jwt,
                }
            },
        }

    def test_every_target_supported_field_is_carried_over(self):
        warnings: list[str] = []
        result = transform_registry_configuration(
            self._preview(
                allowedAudience=["https://aud"],
                allowedClients=["client-1"],
                allowedScopes=["registry:read"],
                customClaims=[{"inboundTokenClaimName": "dept"}],
            ),
            warnings=warnings,
        )
        self.assertEqual(
            self._jwt(result),
            {
                "discoveryUrl": "https://idp/.well-known/openid-configuration",
                "allowedAudience": ["https://aud"],
                "allowedClients": ["client-1"],
                "allowedScopes": ["registry:read"],
                "customClaims": [{"inboundTokenClaimName": "dept"}],
            },
        )
        self.assertEqual(warnings, [])

    def test_preview_only_fields_are_dropped_and_named(self):
        warnings: list[str] = []
        result = transform_registry_configuration(
            self._preview(
                allowedScopes=["registry:read"],
                advertisedScopeMapping={"registry:read": "read"},
                allowedWorkloadConfiguration={"workloadIdentities": ["wi-1"]},
                privateEndpoint={"vpcId": "vpc-1"},
            ),
            warnings=warnings,
        )
        jwt = self._jwt(result)
        self.assertNotIn("advertisedScopeMapping", jwt)
        self.assertNotIn("allowedWorkloadConfiguration", jwt)
        self.assertNotIn("privateEndpoint", jwt)
        self.assertEqual(jwt["allowedScopes"], ["registry:read"])
        self.assertEqual(len(warnings), 1)
        for dropped in (
            "advertisedScopeMapping",
            "allowedWorkloadConfiguration",
            "privateEndpoint",
        ):
            self.assertIn(dropped, warnings[0])

    def test_an_audience_naming_the_preview_registry_is_reported(self):
        """The value cannot be corrected here -- the target registry id does not exist yet."""
        stale = "https://bedrock-agentcore.us-west-2.amazonaws.com/registry/7UTnSjchy17rHV0u/mcp"
        warnings: list[str] = []
        result = transform_registry_configuration(
            self._preview(allowedAudience=[stale, "https://bedrock-agentcore.us-west-2.amazonaws.com"]),
            warnings=warnings,
        )
        # Reported, not rewritten: an audience guessed wrong is an authorization bug.
        self.assertEqual(
            self._jwt(result)["allowedAudience"],
            [stale, "https://bedrock-agentcore.us-west-2.amazonaws.com"],
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("7UTnSjchy17rHV0u", warnings[0])
        self.assertIn("allowedAudience", warnings[0])
        self.assertIn("UpdateRegistry", warnings[0])

    def test_an_audience_that_does_not_name_the_registry_is_not_reported(self):
        warnings: list[str] = []
        transform_registry_configuration(
            self._preview(allowedAudience=["https://bedrock-agentcore.us-west-2.amazonaws.com"]),
            warnings=warnings,
        )
        self.assertEqual(warnings, [])

    def test_the_registry_id_can_come_from_the_caller(self):
        """A response that does not echo registryId must not disable the check."""
        preview = self._preview(allowedAudience=["https://host/registry/abcd1234/mcp"])
        del preview["registryId"]
        warnings: list[str] = []
        transform_registry_configuration(preview, warnings=warnings, source_registry_id="abcd1234")
        self.assertEqual(len(warnings), 1)
        self.assertIn("abcd1234", warnings[0])

    def test_a_missing_discovery_url_is_refused(self):
        preview = self._preview()
        del preview["authorizerConfiguration"]["customJWTAuthorizer"]["discoveryUrl"]
        with self.assertRaises(TransformError):
            transform_registry_configuration(preview)

    def test_an_unknown_authorizer_variant_is_refused(self):
        preview = self._preview()
        preview["authorizerConfiguration"]["someFutureAuthorizer"] = {}
        with self.assertRaises(TransformError) as raised:
            transform_registry_configuration(preview)
        self.assertIn("someFutureAuthorizer", str(raised.exception))

    def test_an_aws_iam_registry_needs_no_authorizer_configuration(self):
        warnings: list[str] = []
        result = transform_registry_configuration({"name": "reg-iam", "authorizerType": "AWS_IAM"}, warnings=warnings)
        self.assertEqual(result["discoveryConfiguration"], {"authorizerType": "AWS_IAM"})
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
