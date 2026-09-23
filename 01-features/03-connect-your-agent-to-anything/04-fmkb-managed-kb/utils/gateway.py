"""Helpers to create / tear down an AgentCore Gateway with an FMKB target."""

from __future__ import annotations

import time
from typing import Optional

import boto3


def create_gateway(name: str, role_arn: str, region: str) -> dict:
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    g = control.create_gateway(
        name=name,
        roleArn=role_arn,
        protocolType="MCP",
        authorizerType="AWS_IAM",
    )
    gw_id = g["gatewayId"]
    for _ in range(60):
        s = control.get_gateway(gatewayIdentifier=gw_id)
        if s["status"] == "READY":
            return s
        if s["status"] in ("FAILED",):
            raise RuntimeError(f"gateway {gw_id} entered {s['status']}")
        time.sleep(5)
    raise RuntimeError(f"gateway {gw_id} did not reach READY in time")


def create_kb_target(gateway_id: str, kb_id: str, name: str, region: str, num_results: int = 5) -> dict:
    """Create the bedrock-knowledge-bases connector target on the gateway.

    Requires botocore >= 1.43.32 — earlier versions don't model
    `targetConfiguration.mcp.connector` and will reject the call client-side.
    """
    control = boto3.client("bedrock-agentcore-control", region_name=region)
    target_config = {
        "mcp": {
            "connector": {
                "source": {"connectorId": "bedrock-knowledge-bases"},
                "configurations": [
                    {
                        "name": "Retrieve",
                        "description": "Search the knowledge base for relevant documents.",
                        "parameterValues": {
                            "knowledgeBaseId": kb_id,
                            "retrievalConfiguration": {"managedSearchConfiguration": {"numberOfResults": num_results}},
                        },
                        "parameterOverrides": [
                            {
                                "path": "$.retrievalQuery.text",
                                "description": "Search query for the knowledge base.",
                                "visible": True,
                            },
                            {
                                "path": "$.retrievalConfiguration.managedSearchConfiguration.numberOfResults",
                                "description": "Number of results to retrieve (1-100).",
                                "visible": True,
                            },
                        ],
                    }
                ],
            }
        }
    }
    created = control.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=name,
        description=f"FMKB Retrieve target for {kb_id}",
        credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        targetConfiguration=target_config,
    )
    target_id = created["targetId"]
    for _ in range(60):
        s = control.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        if s["status"] == "READY":
            return s
        if s["status"] == "FAILED":
            reasons = s.get("statusReasons") or [s.get("failureMessage")]
            raise RuntimeError(f"target {target_id} FAILED: {reasons}")
        time.sleep(5)
    raise RuntimeError(f"target {target_id} did not reach READY in time")


def delete_target(gateway_id: str, target_id: str, region: str) -> None:
    boto3.client("bedrock-agentcore-control", region_name=region).delete_gateway_target(
        gatewayIdentifier=gateway_id, targetId=target_id
    )


def delete_gateway(gateway_id: str, region: str) -> None:
    boto3.client("bedrock-agentcore-control", region_name=region).delete_gateway(gatewayIdentifier=gateway_id)


def gateway_role_trust_policy(account_id: str, region: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/*"},
                },
            }
        ],
    }


def gateway_role_permission_policy(
    account_id: str,
    region: str,
    kb_id: Optional[str] = None,
) -> dict:
    """Least-privilege policy for the gateway execution role.

    Only `bedrock:Retrieve` and `bedrock:GetKnowledgeBase` are needed —
    `Retrieve` is the runtime call, `GetKnowledgeBase` is the validation call
    the gateway makes when you create a KB target.
    """
    kb_arn = (
        f"arn:aws:bedrock:{region}:{account_id}:knowledge-base/{kb_id}"
        if kb_id
        else f"arn:aws:bedrock:{region}:{account_id}:knowledge-base/*"
    )
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "RetrieveAndDescribe",
                "Effect": "Allow",
                "Action": [
                    "bedrock:Retrieve",
                    "bedrock:GetKnowledgeBase",
                ],
                "Resource": kb_arn,
            }
        ],
    }
