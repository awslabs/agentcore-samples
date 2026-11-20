"""
One-time setup: an AgentCore Gateway with the AWS Marketplace connector, so the agent can
find x402 sellers instead of being handed a URL.

Creates (or reuses):
  1. AgentCoreGatewayMarketplaceRole  gateway execution role, Marketplace read-only actions
  2. Gateway (MCP, AWS_IAM authorizer)
  3. Target on connector `aws-marketplace` with SearchListings, GetListing and
     ListFulfillmentOptions
  4. Permission for MarketplaceBuyerAgentRole to call the gateway

Writes GATEWAY_URL and GATEWAY_REGION to .env.

Usage:
    python setup_gateway.py [--region us-east-1]
"""

from __future__ import annotations

import argparse
import json
import os
import time

import boto3
from botocore.exceptions import ClientError
from marketplace_payments import AGENT_ROLE_NAME
from setup_aws_billing import write_env

ROLE_NAME = "AgentCoreGatewayMarketplaceRole"
GATEWAY_NAME = "marketplace-gw"
TARGET_NAME = "marketplace"
TOOLS = ["SearchListings", "GetListing", "ListFulfillmentOptions"]


def ensure_role(iam, account: str) -> str:
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:*:{account}:gateway/{GATEWAY_NAME}-*"},
                },
            }
        ],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "MarketplaceDiscovery",
                "Effect": "Allow",
                "Action": [
                    "aws-marketplace:SearchListings",
                    "aws-marketplace:GetListing",
                    "aws-marketplace:ListFulfillmentOptions",
                ],
                # Catalog ARNs have no Region or account. Listing operations use listing ARNs,
                # and ListFulfillmentOptions uses product ARNs.
                "Resource": [
                    "arn:aws:aws-marketplace:::catalog/AWSMarketplace/listing/*",
                    "arn:aws:aws-marketplace:::catalog/AWSMarketplace/product/*",
                ],
            }
        ],
    }
    try:
        arn = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
        print(f"  reusing {ROLE_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        print(f"  creating {ROLE_NAME}")
        arn = iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="AgentCore Gateway execution role for AWS Marketplace discovery",
        )["Role"]["Arn"]
        time.sleep(10)
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName="MarketplaceDiscovery", PolicyDocument=json.dumps(policy))
    return arn


def wait(get, key="status", ready="READY", failed=("FAILED", "CREATE_FAILED", "UPDATE_UNSUCCESSFUL")):
    for _ in range(60):
        item = get()
        if item[key] == ready:
            return item
        if item[key] in failed:
            raise SystemExit(f"Failed: {item.get('statusReasons') or item}")
        time.sleep(3)
    raise SystemExit("Timed out waiting for READY")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region", default=os.environ.get("GATEWAY_REGION") or os.environ.get("AWS_REGION", "us-east-1"))
    args = p.parse_args()

    session = boto3.Session(region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    cp = session.client("bedrock-agentcore-control")
    print(f"Account {account}, Region {args.region}")

    print("1. Gateway execution role")
    role_arn = ensure_role(session.client("iam"), account)

    print("2. Gateway")
    gws = [g for g in cp.list_gateways()["items"] if g["name"] == GATEWAY_NAME]
    if gws:
        gw_id = gws[0]["gatewayId"]
        print(f"  reusing {gw_id}")
    else:
        gw_id = cp.create_gateway(
            name=GATEWAY_NAME,
            roleArn=role_arn,
            protocolType="MCP",
            authorizerType="AWS_IAM",
            description="Discover AWS Marketplace x402 sellers",
        )["gatewayId"]
        print(f"  created {gw_id}")
    gw = wait(lambda: cp.get_gateway(gatewayIdentifier=gw_id))

    print("3. AWS Marketplace connector target")
    targets = [t for t in cp.list_gateway_targets(gatewayIdentifier=gw_id)["items"] if t["name"] == TARGET_NAME]
    if targets:
        print(f"  reusing {targets[0]['targetId']}")
    else:
        # Every configuration needs parameterValues, even when empty, or the API reports
        # "Connector configurations must not be empty".
        t = cp.create_gateway_target(
            gatewayIdentifier=gw_id,
            name=TARGET_NAME,
            description="AWS Marketplace discovery",
            targetConfiguration={
                "mcp": {
                    "connector": {
                        "source": {"connectorId": "aws-marketplace"},
                        "configurations": [{"name": n, "parameterValues": {}} for n in TOOLS],
                    }
                }
            },
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        )
        wait(lambda: cp.get_gateway_target(gatewayIdentifier=gw_id, targetId=t["targetId"]))
        print(f"  created {t['targetId']}")

    print("4. Let the agent role call the gateway")
    try:
        session.client("iam").put_role_policy(
            RoleName=AGENT_ROLE_NAME,
            PolicyName="MarketplaceBuyerGateway",
            PolicyDocument=json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {"Effect": "Allow", "Action": "bedrock-agentcore:InvokeGateway", "Resource": gw["gatewayArn"]}
                    ],
                }
            ),
        )
        print(f"  granted bedrock-agentcore:InvokeGateway to {AGENT_ROLE_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        print(f"  {AGENT_ROLE_NAME} not found; run setup_aws_billing.py in this account first")

    write_env({"GATEWAY_URL": gw["gatewayUrl"], "GATEWAY_REGION": args.region, "GATEWAY_ID": gw_id})
    print(f"\nDone. GATEWAY_URL={gw['gatewayUrl']}\nNext: python strands_marketplace_agent.py")


if __name__ == "__main__":
    main()
