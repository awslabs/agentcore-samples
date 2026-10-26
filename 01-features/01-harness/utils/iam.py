"""IAM helpers for AgentCore Harness — creates the execution role and permissions."""

import json

import boto3

ROLE_NAME = "HarnessExecutionRole"
POLICY_NAME = "HarnessExecutionPolicy"


def _build_trust_policy() -> dict:
    """Trust policy for the AgentCore service principal.

    The `aws:SourceAccount` condition closes a confused-deputy hole: without it
    the policy lets the service principal assume this role on behalf of *any*
    account, not just yours. It is built lazily rather than defined as a module
    constant because it needs an STS call to learn the account ID, and importing
    this module should not require credentials.
    """
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": ["bedrock-agentcore.amazonaws.com"]},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": get_account_id()}},
            }
        ],
    }


PERMISSIONS_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "BedrockInvokeModel",
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            # Scoped to the two resource types a harness can actually invoke.
            # `inference-profile` matters as much as `foundation-model`: every
            # sample here uses a cross-region profile ID (global.* / us.*), which
            # resolves to an inference-profile ARN, not a bare model ARN.
            "Resource": [
                "arn:aws:bedrock:*::foundation-model/*",
                "arn:aws:bedrock:*:*:inference-profile/*",
            ],
        },
        {
            "Sid": "ECRPull",
            "Effect": "Allow",
            "Action": [
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchGetImage",
                "ecr:BatchCheckLayerAvailability",
                "ecr:GetAuthorizationToken",
            ],
            "Resource": "*",
        },
        {
            "Sid": "EcrPublicPull",
            "Effect": "Allow",
            "Action": ["ecr-public:GetAuthorizationToken"],
            "Resource": "*",
        },
        {
            "Sid": "StsForEcrPublicPull",
            "Effect": "Allow",
            "Action": ["sts:GetServiceBearerToken"],
            "Resource": "*",
        },
        {
            "Sid": "XRay",
            "Effect": "Allow",
            "Action": ["xray:PutTraceSegments", "xray:PutTelemetryRecords"],
            "Resource": "*",
        },
        {
            "Sid": "CloudWatchLogs",
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
            ],
            # Harness logs land under this one prefix, so there is no reason to
            # grant the account's whole log estate. Both ARN forms are needed:
            # CreateLogGroup acts on the group, PutLogEvents on the stream.
            "Resource": [
                "arn:aws:logs:*:*:log-group:/aws/bedrock-agentcore/runtimes/*",
                "arn:aws:logs:*:*:log-group:/aws/bedrock-agentcore/runtimes/*:log-stream:*",
            ],
        },
        {
            "Sid": "AgentCore",
            "Effect": "Allow",
            # Enumerated rather than wildcarded. Patterns like
            # `bedrock-agentcore:*Memory*` silently pull in Delete*/Update*, so a
            # sample's execution role could destroy memory stores it only ever
            # reads. Every action below is one the samples in this folder call.
            "Action": [
                "bedrock-agentcore:CreateMemory",
                "bedrock-agentcore:GetMemory",
                "bedrock-agentcore:ListMemories",
                "bedrock-agentcore:DeleteMemory",
                "bedrock-agentcore:RetrieveMemoryRecords",
                "bedrock-agentcore:CreateEvent",
                "bedrock-agentcore:ListEvents",
                "bedrock-agentcore:GetEvent",
                "bedrock-agentcore:StartBrowserSession",
                "bedrock-agentcore:StopBrowserSession",
                "bedrock-agentcore:GetBrowserSession",
                "bedrock-agentcore:ListBrowserSessions",
                "bedrock-agentcore:StartCodeInterpreterSession",
                "bedrock-agentcore:StopCodeInterpreterSession",
                "bedrock-agentcore:GetCodeInterpreterSession",
                "bedrock-agentcore:ListCodeInterpreterSessions",
                "bedrock-agentcore:InvokeCodeInterpreter",
                "bedrock-agentcore:InvokeGateway",
            ],
            "Resource": "*",
        },
        {
            "Sid": "GetAgentCoreApiKeys",
            "Effect": "Allow",
            "Action": ["bedrock-agentcore:GetResourceApiKey"],
            "Resource": "*",
        },
    ],
}


def get_account_id() -> str:
    return boto3.client("sts").get_caller_identity()["Account"]


def create_harness_role(role_name: str = ROLE_NAME) -> str | None:
    """Create the IAM execution role required by AgentCore Harness. Returns the role ARN.

    Idempotent in the full sense: the role, its trust policy AND its permissions
    policy are all brought up to date. Returning early on an existing role is not
    safe here, because every sample in this folder shares one role name — a role
    left behind by an earlier, partially cleaned-up run can exist with the wrong
    permissions (or none at all), and a role created before the trust policy was
    scoped would keep the looser version forever. Both `update_assume_role_policy`
    and `put_role_policy` overwrite in place, so re-running on a healthy role is
    a no-op.
    """
    iam = boto3.client("iam")
    trust_policy = json.dumps(_build_trust_policy())

    try:
        existing = iam.get_role(RoleName=role_name)
        arn = existing["Role"]["Arn"]
        print(f"Role {role_name} already exists: {arn}")
        iam.update_assume_role_policy(RoleName=role_name, PolicyDocument=trust_policy)
    except iam.exceptions.NoSuchEntityException:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust_policy,
            Description="Execution role for Amazon Bedrock AgentCore Harness",
        )
        arn = resp["Role"]["Arn"]
        print(f"Created role: {arn}")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=POLICY_NAME,
        PolicyDocument=json.dumps(PERMISSIONS_POLICY),
    )
    print(f"Attached policy: {POLICY_NAME}")

    return arn


def delete_harness_role(role_name: str = ROLE_NAME) -> None:
    """Delete the Harness execution role and every policy attached to it.

    Deleting only POLICY_NAME is not enough: some samples add their own inline
    policy to this shared role (e.g. the S3 Files access policy). Any policy
    left behind makes `delete_role` fail with DeleteConflictException, which
    would strand the role in a half-configured state for the next sample.
    """
    iam = boto3.client("iam")

    # Paginate: a partial listing would leave a policy behind, which is exactly
    # what makes the delete below fail.
    try:
        for page in iam.get_paginator("list_role_policies").paginate(RoleName=role_name):
            for name in page["PolicyNames"]:
                iam.delete_role_policy(RoleName=role_name, PolicyName=name)
                print(f"Deleted inline policy: {name}")
        for page in iam.get_paginator("list_attached_role_policies").paginate(RoleName=role_name):
            for policy in page["AttachedPolicies"]:
                iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
                print(f"Detached managed policy: {policy['PolicyName']}")
    except iam.exceptions.NoSuchEntityException:
        print(f"Role {role_name} not found")
        return

    try:
        iam.delete_role(RoleName=role_name)
        print(f"Deleted role: {role_name}")
    except iam.exceptions.NoSuchEntityException:
        print(f"Role {role_name} not found")
    except iam.exceptions.DeleteConflictException as e:
        # Something outside this helper still references the role. Say so
        # loudly — a silently surviving role is what poisons later runs.
        print(f"Could not delete role {role_name}: {e}")
        print("  Remove whatever still references it, then delete the role manually.")
