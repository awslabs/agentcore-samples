# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""CDK unit tests for U1 (infrastructure)'s InfrastructureStack, using
aws-cdk-lib.assertions to check the synthesized template's resource properties
without live AWS access.

Covers the stack's guardrails. The adapter role has no DynamoDB/registry
permissions and no SNS-key grant, the EventBridge pattern matches only the
Approved detail-type, and the DLQ's maxReceiveCount is 3.
"""

from __future__ import annotations

import json

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from agent_registry_sample.infrastructure.stack import InfrastructureStack

_ORCHESTRATOR_ARN = "arn:aws:bedrock-agentcore:us-east-1:111111111111:runtime/test-orchestrator"
_ORCHESTRATOR_ROLE_ARN = "arn:aws:iam::111111111111:role/test-orchestrator-role"
_REGISTRY_ARN = "arn:aws:agent-registry:us-east-1:111111111111:registry/test-registry"


def _synth_template() -> Template:
    app = cdk.App()
    stack = InfrastructureStack(
        app,
        "TestInfrastructureStack",
        orchestrator_agent_runtime_arn=_ORCHESTRATOR_ARN,
        orchestrator_agent_runtime_id="test-orchestrator",
        orchestrator_agent_runtime_role_arn=_ORCHESTRATOR_ROLE_ARN,
        registry_arn=_REGISTRY_ARN,
        env=cdk.Environment(region="us-east-1"),
    )
    return Template.from_stack(stack)


def test_stack_synthesizes_without_error():
    """Baseline: the stack synthesizes to a template at all."""
    template = _synth_template()
    assert template is not None


def test_approved_event_rule_pattern_matches_approved_exactly():
    """The EventBridge rule's pattern matches only the Approved transition
    (source=aws.agent-registry, detail-type="Registry Record State changed to
    Approved") and nothing broader."""
    template = _synth_template()
    template.has_resource_properties(
        "AWS::Events::Rule",
        {
            "EventPattern": {
                "source": ["aws.agent-registry"],
                "detail-type": ["Registry Record State changed to Approved"],
            }
        },
    )


def test_event_rule_has_exactly_two_targets():
    """Exactly two targets (the consumer queue and the topic)."""
    template = _synth_template()
    resources = template.find_resources("AWS::Events::Rule")
    assert len(resources) == 1
    rule = next(iter(resources.values()))
    targets = rule["Properties"]["Targets"]
    assert len(targets) == 2


def test_dlq_max_receive_count_is_three():
    """The consumer queue's redrive policy has maxReceiveCount=3."""
    template = _synth_template()
    template.has_resource_properties(
        "AWS::SQS::Queue",
        {
            "RedrivePolicy": {
                "maxReceiveCount": 3,
            }
        },
    )


def test_dynamo_table_has_composite_key_recordid_state():
    """Partition key recordId, sort key state."""
    template = _synth_template()
    template.has_resource_properties(
        "AWS::DynamoDB::GlobalTable",
        Match.object_like(
            {
                "AttributeDefinitions": Match.array_with(
                    [
                        Match.object_like({"AttributeName": "recordId", "AttributeType": "S"}),
                        Match.object_like({"AttributeName": "state", "AttributeType": "S"}),
                    ]
                ),
            }
        ),
    )


def test_adapter_role_has_no_dynamodb_permissions():
    """TriggerAdapterRole grants no DynamoDB permissions."""
    template = _synth_template()
    policies = template.find_resources("AWS::IAM::Policy")
    adapter_policy = None
    for _name, resource in policies.items():
        statements = resource["Properties"]["PolicyDocument"]["Statement"]
        actions = _all_actions(statements)
        if any("bedrock-agentcore:InvokeAgentRuntime" in a for a in actions):
            adapter_policy = resource
            break
    assert adapter_policy is not None, "could not find TriggerAdapterRole's policy"
    all_actions = _all_actions(adapter_policy["Properties"]["PolicyDocument"]["Statement"])
    assert not any(a.lower().startswith("dynamodb:") for a in all_actions), (
        f"adapter role must not have any dynamodb: permission, found: {all_actions}"
    )


def test_adapter_role_has_no_registry_permissions():
    """TriggerAdapterRole grants no agent-registry-control or agent-registry
    (data-plane) permissions of any kind."""
    template = _synth_template()
    policies = template.find_resources("AWS::IAM::Policy")
    adapter_policy = None
    for _name, resource in policies.items():
        statements = resource["Properties"]["PolicyDocument"]["Statement"]
        actions = _all_actions(statements)
        if any("bedrock-agentcore:InvokeAgentRuntime" in a for a in actions):
            adapter_policy = resource
            break
    assert adapter_policy is not None
    all_actions = _all_actions(adapter_policy["Properties"]["PolicyDocument"]["Statement"])
    assert not any(
        a.lower().startswith("agent-registry:") or a.lower().startswith("agent-registry:")
        for a in all_actions
    ), f"adapter role must not have any registry-client permission, found: {all_actions}"


def test_adapter_role_has_no_grant_on_sns_kms_key():
    """TriggerAdapterRole has kms:Decrypt on SqsKmsKey only.

    CDK's grant_decrypt() on a KMS key adds the permission to the grantee's
    (the role's) own identity-based policy, not the key's resource policy. In
    the synthesized template the key's KeyPolicy lists only the account root
    and, where applicable, the EventBridge service principal, never a role
    ARN. So this test inspects the adapter role's own IAM::Policy statements
    for a kms:Decrypt action and checks that its Resource references one KMS
    key (by key logical id), not two.
    """
    template = _synth_template()
    keys = template.find_resources("AWS::KMS::Key")
    assert len(keys) >= 2, "expected at least two KMS keys (SqsKmsKey, SnsKmsKey)"
    sqs_kms_key_arn_key = next(
        logical_id for logical_id in keys if logical_id.startswith("SqsKmsKey")
    )
    sns_kms_key_arn_key = next(
        logical_id for logical_id in keys if logical_id.startswith("SnsKmsKey")
    )

    policies = template.find_resources("AWS::IAM::Policy")
    adapter_policy = None
    for _name, resource in policies.items():
        statements = resource["Properties"]["PolicyDocument"]["Statement"]
        if any("bedrock-agentcore:InvokeAgentRuntime" in _all_actions([s]) for s in statements):
            adapter_policy = resource
            break
    assert adapter_policy is not None, "could not find TriggerAdapterRole's policy"

    kms_decrypt_resource_refs: list[str] = []
    for statement in adapter_policy["Properties"]["PolicyDocument"]["Statement"]:
        if statement.get("Action") == "kms:Decrypt":
            resource_ref = statement.get("Resource")
            kms_decrypt_resource_refs.append(json.dumps(resource_ref))

    assert len(kms_decrypt_resource_refs) == 1, (
        f"expected exactly one kms:Decrypt statement on the adapter role, "
        f"found {len(kms_decrypt_resource_refs)}: {kms_decrypt_resource_refs}"
    )
    (only_ref,) = kms_decrypt_resource_refs
    assert sqs_kms_key_arn_key in only_ref, (
        f"adapter role's kms:Decrypt must target SqsKmsKey, got: {only_ref}"
    )
    assert sns_kms_key_arn_key not in only_ref, (
        f"adapter role's kms:Decrypt must NOT target SnsKmsKey, got: {only_ref}"
    )


def test_two_separate_kms_keys_exist():
    """Two separate customer-managed KMS keys, both with rotation enabled."""
    template = _synth_template()
    keys = template.find_resources("AWS::KMS::Key")
    assert len(keys) >= 2
    for _logical_id, resource in keys.items():
        assert resource["Properties"].get("EnableKeyRotation") is True


def test_two_distinct_publisher_roles_no_permission_overlap():
    """The submitter and approver roles exist as separate IAM::Role resources,
    and neither grants the other's action set."""
    template = _synth_template()
    policies = template.find_resources("AWS::IAM::Policy")

    submitter_actions: list[str] = []
    approver_actions: list[str] = []
    for _name, resource in policies.items():
        actions = _all_actions(resource["Properties"]["PolicyDocument"]["Statement"])
        if any("CreateRegistryRecord" in a for a in actions):
            submitter_actions = actions
        if any("UpdateRegistryRecordStatus" in a for a in actions):
            approver_actions = actions

    assert submitter_actions, "could not find the submitter role's policy"
    assert approver_actions, "could not find the approver role's policy"
    assert "agent-registry:UpdateRegistryRecordStatus" not in submitter_actions, (
        "submitter role must not have UpdateRegistryRecordStatus (cannot self-approve)"
    )
    assert "agent-registry:CreateRegistryRecord" not in approver_actions, (
        "approver role must not have CreateRegistryRecord (cannot create)"
    )
    assert "agent-registry:SubmitRegistryRecordForApproval" not in approver_actions, (
        "approver role must not have SubmitRegistryRecordForApproval (cannot submit)"
    )


def test_no_wildcard_resource_in_adapter_or_publisher_policies():
    """No Resource '*' anywhere in the roles this stack defines. The
    account-root KMS key-administrator statement is a CDK-default statement on
    the key resource itself, not a role policy, so this check excludes it.
    """
    template = _synth_template()
    policies = template.find_resources("AWS::IAM::Policy")
    for name, resource in policies.items():
        for statement in resource["Properties"]["PolicyDocument"]["Statement"]:
            resource_field = statement.get("Resource")
            if resource_field is None:
                continue
            resources_list = (
                resource_field if isinstance(resource_field, list) else [resource_field]
            )
            for r in resources_list:
                assert r != "*", f"wildcard resource found in policy {name}: {statement}"


def _orchestrator_policy(template):
    """Return (statements, all_actions) for the orchestrator role's IAM::Policy,
    found by its data-plane Search grant. Only the orchestrator role has that
    grant. The publishers have control-plane actions and the adapter has
    InvokeAgentRuntime."""
    policies = template.find_resources("AWS::IAM::Policy")
    for _name, resource in policies.items():
        stmts = resource["Properties"]["PolicyDocument"]["Statement"]
        actions = _all_actions(stmts)
        if "agent-registry:SearchDiscoverableRegistryRecords" in actions:
            return stmts, actions
    return None, []


def test_orchestrator_role_granted_runtime_dataplane_and_dedupe():
    """The infra stack attaches the orchestrator's runtime grants to its
    imported execution role, each at its required resource level. Search is
    registry level. GetDiscoverableRegistryRecord is record level and is the
    IAM action that authorizes the batch_get API (there is no BatchGet... IAM
    action). PutItem/UpdateItem/GetItem target the idempotency table. Granting
    them here avoids a circular dependency, because the orchestrator deploys
    before this stack creates the table."""
    template = _synth_template()
    stmts, actions = _orchestrator_policy(template)
    assert stmts is not None, "orchestrator role policy (with Search) not found"

    # Data-plane action names are Search + GetDiscoverable. The non-existent
    # BatchGet... IAM action must not appear.
    assert "agent-registry:SearchDiscoverableRegistryRecords" in actions
    assert "agent-registry:GetDiscoverableRegistryRecord" in actions
    assert "agent-registry:BatchGetDiscoverableRegistryRecord" not in actions
    assert {"dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:GetItem"}.issubset(set(actions))

    # Resource levels are registry for Search and record for Get.
    def _resource_for(action: str):
        for s in stmts:
            if action in _all_actions([s]):
                return s.get("Resource")
        return None

    search_res = _resource_for("agent-registry:SearchDiscoverableRegistryRecords")
    get_res = _resource_for("agent-registry:GetDiscoverableRegistryRecord")
    assert search_res not in (None, "*")
    assert get_res not in (None, "*")
    # Only the record-level Get ARN carries the /record/ segment.
    assert "/record/" in json.dumps(get_res)
    assert "/record/" not in json.dumps(search_res)


def test_orchestrator_role_has_no_invoke_or_controlplane_grant():
    """The orchestrator role carries no bedrock-agentcore:InvokeAgentRuntime,
    because each peer's resource-based policy authorizes the A2A invoke (the
    consumer-group model). It also carries no registry control-plane action,
    because deploy-time self-registration runs under the operator's own
    credentials, not the runtime."""
    template = _synth_template()
    _stmts, orch_actions = _orchestrator_policy(template)
    assert orch_actions, "could not find the orchestrator role's policy"
    assert "bedrock-agentcore:InvokeAgentRuntime" not in orch_actions
    assert "agent-registry:CreateRegistryRecord" not in orch_actions
    assert "agent-registry:SubmitRegistryRecordForApproval" not in orch_actions
    assert "agent-registry:UpdateRegistryRecordStatus" not in orch_actions


def _all_actions(statements: list[dict]) -> list[str]:
    actions: list[str] = []
    for statement in statements:
        action = statement.get("Action", [])
        if isinstance(action, str):
            actions.append(action)
        else:
            actions.extend(action)
    return actions
