#!/usr/bin/python
"""AgentCore Evaluation helpers for Lab 5 - retrieves agent role and attaches evaluation policy."""

import json
import os

import boto3
from boto3.session import Session
from lab_helpers.utils import get_ssm_parameter

boto_session = Session()
REGION = boto_session.region_name

EVALUATION_POLICY_SUFFIX = "AgentCoreEvaluationPolicy"


def get_execution_role_arn_from_runtime():
    """Retrieve the execution role ARN from the AgentCore runtime agent configuration.

    Falls back to SSM parameter if runtime lookup fails.

    Returns:
        str: The execution role ARN
    """
    try:
        # Try SSM first (stored in lab04 via create_agentcore_runtime_execution_role)
        role_arn = get_ssm_parameter("/app/customersupport/agentcore/runtime_execution_role_arn")
        if role_arn:
            print(f"✅ Retrieved execution_role_arn from SSM: {role_arn}")
            return role_arn
    except Exception:
        pass

    # Fallback: get from the runtime agent configuration
    try:
        agent_arn = get_ssm_parameter("/app/customersupport/agentcore/runtime_arn")
        runtime_id = agent_arn.split(":")[-1].split("/")[-1]

        control_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
        response = control_client.get_agent_runtime(agentRuntimeId=runtime_id)
        role_arn = response.get("roleArn")

        if role_arn:
            print(f"✅ Retrieved execution_role_arn from runtime config: {role_arn}")
            return role_arn
    except Exception as e:
        print(f"⚠️  Could not retrieve role from runtime: {e}")

    raise RuntimeError("Could not retrieve execution_role_arn. Please run Lab 4 first.")


def attach_evaluation_policy(execution_role_arn: str, policy_json_path: str = None):
    """Attach the AgentCore evaluation policy to the agent's execution role.

    Args:
        execution_role_arn: The IAM role ARN to attach the policy to.
        policy_json_path: Path to the evaluation policy JSON file.
                          Defaults to lab_helpers/lab5_evaluation/agentcore-evaluation-policy.json

    Returns:
        str: The policy ARN that was attached
    """
    if not policy_json_path:
        policy_json_path = os.path.join(
            os.path.dirname(__file__),
            "lab5_evaluation",
            "agentcore-evaluation-policy.json",
        )

    # Load the policy document
    with open(policy_json_path, "r") as f:
        policy_document = json.load(f)

    iam = boto3.client("iam")
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    role_name = execution_role_arn.split("/")[-1]
    policy_name = f"{role_name}-{EVALUATION_POLICY_SUFFIX}"
    policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"

    # Check if policy already attached
    try:
        attached = iam.list_attached_role_policies(RoleName=role_name)
        for p in attached.get("AttachedPolicies", []):
            if EVALUATION_POLICY_SUFFIX in p["PolicyName"]:
                print(f"ℹ️  Evaluation policy already attached: {p['PolicyArn']}")
                return p["PolicyArn"]
    except Exception:
        pass

    # Create or update the policy
    try:
        iam.get_policy(PolicyArn=policy_arn)
        print(f"ℹ️  Policy {policy_name} already exists")
    except iam.exceptions.NoSuchEntityException:
        iam.create_policy(
            PolicyName=policy_name,
            PolicyDocument=json.dumps(policy_document),
            Description="AgentCore Evaluation permissions for online evaluation",
        )
        print(f"✅ Created policy: {policy_name}")

    # Attach to role
    iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
    print(f"✅ Attached evaluation policy to role: {role_name}")
    return policy_arn


def ensure_evaluation_role(execution_role_arn: str = None):
    """Ensure the execution role has evaluation permissions.

    If execution_role_arn is None or empty, retrieves it from the runtime.
    Then attaches the evaluation policy if not already attached.

    Args:
        execution_role_arn: Optional role ARN. If empty/None, auto-retrieves.

    Returns:
        str: The validated execution_role_arn
    """
    if not execution_role_arn or not execution_role_arn.strip():
        print("⚠️  execution_role_arn is empty, retrieving from runtime...")
        execution_role_arn = get_execution_role_arn_from_runtime()

    # Validate format
    if not execution_role_arn.startswith("arn:aws:iam::"):
        # Looser check - just ensure it looks like an ARN
        if "arn:" not in execution_role_arn or ":role/" not in execution_role_arn:
            raise ValueError(f"Invalid execution_role_arn format: {execution_role_arn}")

    print(f"Using execution_role_arn: {execution_role_arn}")
    attach_evaluation_policy(execution_role_arn)
    return execution_role_arn
