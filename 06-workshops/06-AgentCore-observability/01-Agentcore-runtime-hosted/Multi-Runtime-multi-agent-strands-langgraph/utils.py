"""
Utility functions for multi-agent tutorial.
Only includes functionality not provided by bedrock-agentcore-starter-toolkit.
"""

import boto3
import json
from boto3.session import Session


def update_orchestrator_permissions(sub_agent_arns: list, orchestrator_agent_id: str, region=None):
    """
    Add permissions to orchestrator role to invoke sub-agents directly.

    Args:
        sub_agent_arns: List of sub-agent runtime ARNs
        orchestrator_agent_id: The orchestrator's agent runtime ID (e.g., 'orchestrator_a2a-eHQbJjFPxX')
        region: AWS region (optional)
    """
    if region is None:
        region = Session().region_name

    account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    iam_client = boto3.client("iam", region_name=region)
    agentcore_client = boto3.client("bedrock-agentcore-control", region_name=region)

    # Get the execution role ARN from the runtime configuration
    runtime_info = agentcore_client.get_agent_runtime(agentRuntimeId=orchestrator_agent_id)
    role_arn = runtime_info.get("roleArn") or runtime_info.get("agentRuntime", {}).get("roleArn")
    if not role_arn:
        raise ValueError(f"Could not find execution role for runtime {orchestrator_agent_id}")

    # Extract role name from ARN (arn:aws:iam::account:role/role-name)
    orchestrator_role_name = role_arn.split("/")[-1]

    orchestrator_permissions = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeSubAgents",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:InvokeAgentRuntime",
                    "bedrock-agentcore:InvokeAgentRuntimeForUser",
                ],
                "Resource": [f"{arn}/runtime-endpoint/DEFAULT" for arn in sub_agent_arns] + sub_agent_arns,
            },
            {
                "Sid": "SSMParameterAccess",
                "Effect": "Allow",
                "Action": ["ssm:GetParameter"],
                "Resource": f"arn:aws:ssm:{region}:{account_id}:parameter/agents/*",
            },
        ],
    }

    iam_client.put_role_policy(
        RoleName=orchestrator_role_name,
        PolicyName="SubAgentPermissions",
        PolicyDocument=json.dumps(orchestrator_permissions),
    )
    print(f"Updated {orchestrator_role_name} with sub-agent permissions")


def cleanup_runtime(launch_result, agent_name, region=None):
    """
    Clean up AgentCore Runtime, ECR repository, and IAM role.

    Args:
        launch_result: Result object from runtime.launch()
        agent_name: Name of the agent (used for IAM role cleanup)
        region: AWS region (optional)
    """
    if region is None:
        region = Session().region_name

    agentcore_client = boto3.client("bedrock-agentcore-control", region_name=region)
    ecr_client = boto3.client("ecr", region_name=region)
    iam_client = boto3.client("iam", region_name=region)

    # Delete runtime
    agentcore_client.delete_agent_runtime(agentRuntimeId=launch_result.agent_id)
    print(f"Deleted runtime: {launch_result.agent_id}")

    # Delete ECR repository
    repo_name = launch_result.ecr_uri.split("/")[1].split(":")[0]
    ecr_client.delete_repository(repositoryName=repo_name, force=True)
    print(f"Deleted ECR repo: {repo_name}")

    # Delete IAM role (created by auto_create_execution_role=True)
    # Role name follows pattern: agentcore-{agent_name}-role
    role_name = f"agentcore-{agent_name}-role"
    try:
        # First delete all inline policies
        policies = iam_client.list_role_policies(RoleName=role_name)
        for policy_name in policies.get("PolicyNames", []):
            iam_client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)

        # Then delete the role
        iam_client.delete_role(RoleName=role_name)
        print(f"Deleted IAM role: {role_name}")
    except iam_client.exceptions.NoSuchEntityException:
        pass  # Role doesn't exist or already deleted


def cleanup_ssm_parameters(parameter_names: list):
    """
    Clean up SSM parameters.

    Args:
        parameter_names: List of parameter names to delete
    """
    ssm = boto3.client("ssm")
    for name in parameter_names:
        try:
            ssm.delete_parameter(Name=name)
            print(f"Deleted parameter: {name}")
        except ssm.exceptions.ParameterNotFound:
            pass
