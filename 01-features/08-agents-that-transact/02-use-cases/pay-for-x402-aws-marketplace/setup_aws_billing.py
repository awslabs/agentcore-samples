"""
One-time setup: let agents in this AWS account pay AWS Marketplace sellers with AWS Billing.

Creates (or reuses) these, then writes them to .env:
  1. AWSServiceRoleForMarketplaceAgenticCommerce  service-linked role AWS Marketplace uses to
                                                   create the agreement and meter usage
  2. AgentCorePaymentsResourceRetrievalRole        service role for the payment manager
  3. Payment manager (AWS_IAM authorizer)          AWS Billing requires AWS_IAM, not CUSTOM_JWT
  4. AWS Billing payment connector                 one per manager; no credentials to supply
  5. MarketplaceBuyerSessionRole                   creates budgeted payment sessions; can't pay
  6. MarketplaceBuyerAgentRole                     what the agent runs as; can pay within a
                                                   session, can't create or change one

Run it with an identity that can manage IAM roles and AgentCore payments resources.

Usage:
    python setup_aws_billing.py [--region us-east-1] [--manager-name marketplacebuyer]
    python setup_aws_billing.py --role-arn arn:aws:iam::<acct>:role/<existing-role>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from urllib.parse import unquote

import boto3
from botocore.exceptions import ClientError
from marketplace_payments import (
    AGENT_ROLE_NAME,
    SESSION_ROLE_NAME,
    SLR_ROLE_NAME,
    SLR_SERVICE_NAME,
    AgentCorePaymentsClient,
    PaymentsApiError,
)

ROLE_NAME = "AgentCorePaymentsResourceRetrievalRole"
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def ensure_service_linked_role(iam) -> str:
    try:
        return iam.get_role(RoleName=SLR_ROLE_NAME)["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
    print(f"  creating {SLR_ROLE_NAME}")
    return iam.create_service_linked_role(AWSServiceName=SLR_SERVICE_NAME)["Role"]["Arn"]


def ensure_manager_role(iam, account: str, manager_name: str) -> str:
    # Scoped to this account and to payment managers created with this name.
    source_arn = f"arn:aws:bedrock-agentcore:*:{account}:payment-manager/{manager_name}-*"
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": account}, "ArnLike": {"aws:SourceArn": source_arn}},
            }
        ],
    }
    # AWS Billing has no credential provider, so the role only needs workload identity access.
    wid = f"arn:aws:bedrock-agentcore:*:{account}:workload-identity-directory/default"
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "WorkloadIdentityManagement",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:CreateWorkloadIdentity", "bedrock-agentcore:DeleteWorkloadIdentity"],
                "Resource": [wid, f"{wid}/workload-identity/*"],
            },
            {
                "Sid": "WorkloadIdentityAccess",
                "Effect": "Allow",
                "Action": ["bedrock-agentcore:GetWorkloadAccessToken"],
                "Resource": [wid, f"{wid}/workload-identity/{manager_name}-*"],
            },
        ],
    }
    try:
        role = iam.get_role(RoleName=ROLE_NAME)["Role"]
        print(f"  reusing {ROLE_NAME}")
        # The existing role isn't changed, because other payment managers may rely on it.
        doc = role["AssumeRolePolicyDocument"]
        if source_arn not in (unquote(doc) if isinstance(doc, str) else json.dumps(doc)):
            print(
                f"  warning: its trust policy doesn't mention payment managers named '{manager_name}-*', "
                "so creating a new manager may fail. Use the --manager-name the role was created for, "
                "or pass --role-arn with a role that trusts this name."
            )
        return role["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
    print(f"  creating {ROLE_NAME}")
    arn = iam.create_role(
        RoleName=ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(trust),
        Description="Service role for AgentCore payments (AWS Billing)",
    )["Role"]["Arn"]
    iam.put_role_policy(
        RoleName=ROLE_NAME, PolicyName="AgentCorePaymentsWorkloadIdentity", PolicyDocument=json.dumps(policy)
    )
    time.sleep(10)  # let IAM propagate before the service assumes the role
    return arn


def ensure_manager(acp: AgentCorePaymentsClient, name: str, role_arn: str) -> dict:
    for m in acp.list_payment_managers():
        if m["name"] == name and m.get("authorizerType") == "AWS_IAM" and m["status"] == "READY":
            print(f"  reusing payment manager {m['paymentManagerId']}")
            return acp.get_payment_manager(m["paymentManagerId"])
    for attempt in range(5):
        try:
            mgr = acp.create_payment_manager(name, role_arn, "Agents paying AWS Marketplace sellers with AWS Billing")
            break
        except PaymentsApiError as e:
            if e.status == 400 and "role" in e.message.lower() and attempt < 4:
                time.sleep(10)  # role still propagating
                continue
            raise
    while mgr["status"] not in ("READY", "CREATE_FAILED"):
        time.sleep(3)
        mgr = acp.get_payment_manager(mgr["paymentManagerId"])
    if mgr["status"] != "READY":
        sys.exit(f"Payment manager failed: {mgr}")
    print(f"  created payment manager {mgr['paymentManagerId']}")
    return mgr


def ensure_connector(acp: AgentCorePaymentsClient, manager_id: str) -> dict:
    for c in acp.list_payment_connectors(manager_id):
        if c["type"] == "AwsBilling":
            print(f"  reusing AWS Billing connector {c['paymentConnectorId']}")
            return c
    try:
        conn = acp.create_aws_billing_connector(manager_id, "awsBilling")
    except PaymentsApiError as e:
        if "service-linked role" in e.message:
            sys.exit(
                f"\n{e.message}\n\nAWS Billing needs the {SLR_ROLE_NAME} service-linked role. Step 1 creates it "
                "unless you passed --skip-slr. If the role exists and you still see this error, contact "
                "AWS Support with the message above."
            )
        raise
    print(f"  created AWS Billing connector {conn['paymentConnectorId']}")
    return conn


SESSION_ACTIONS = [
    "bedrock-agentcore:CreatePaymentSession",
    "bedrock-agentcore:GetPaymentSession",
    "bedrock-agentcore:ListPaymentSessions",
    "bedrock-agentcore:DeletePaymentSession",
]
SPEND_ACTIONS = ["bedrock-agentcore:ProcessPayment"]


def put_role(iam, name: str, account: str, description: str, policy: dict) -> str:
    """Create the role if it's missing, and always (re)apply its inline policy."""
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{account}:root"}, "Action": "sts:AssumeRole"}
        ],
    }
    try:
        arn = iam.get_role(RoleName=name)["Role"]["Arn"]
        print(f"  reusing {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
        arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust), Description=description)[
            "Role"
        ]["Arn"]
        print(f"  created {name}")
    iam.put_role_policy(RoleName=name, PolicyName="MarketplaceBuyer", PolicyDocument=json.dumps(policy))
    return arn


def ensure_buyer_roles(iam, account: str, manager_arn: str) -> tuple[str, str]:
    manager = [manager_arn, f"{manager_arn}/*"]
    session_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Sid": "ManageSessions", "Effect": "Allow", "Action": SESSION_ACTIONS, "Resource": manager},
            {"Sid": "NeverSpend", "Effect": "Deny", "Action": SPEND_ACTIONS, "Resource": "*"},
        ],
    }
    agent_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "SpendWithinSession",
                "Effect": "Allow",
                "Action": SPEND_ACTIONS + ["bedrock-agentcore:GetPaymentSession"],
                "Resource": manager,
            },
            {
                "Sid": "NeverSetBudgets",
                "Effect": "Deny",
                "Action": [
                    "bedrock-agentcore:CreatePaymentSession",
                    "bedrock-agentcore:DeletePaymentSession",
                    "bedrock-agentcore:UpdatePaymentManager",
                    "bedrock-agentcore:CreatePaymentConnector",
                ],
                "Resource": "*",
            },
            {
                "Sid": "InvokeModel",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                "Resource": [
                    "arn:aws:bedrock:*::foundation-model/anthropic.*",
                    f"arn:aws:bedrock:*:{account}:inference-profile/*",
                ],
            },
        ],
    }
    print("5. Session role")
    session_arn = put_role(
        iam,
        SESSION_ROLE_NAME,
        account,
        "Creates AgentCore payment sessions with spend limits. Can't make payments.",
        session_policy,
    )
    print("6. Agent role")
    agent_arn = put_role(
        iam,
        AGENT_ROLE_NAME,
        account,
        "Runs the Marketplace buyer agent. Pays within a session; can't create sessions.",
        agent_policy,
    )
    return session_arn, agent_arn


def write_env(values: dict) -> None:
    existing = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                if "=" in line and not line.startswith("#"):
                    k, v = line.rstrip("\n").split("=", 1)
                    existing[k] = v
    existing.update(values)
    with open(ENV_FILE, "w") as f:
        f.writelines(f"{k}={v}\n" for k, v in existing.items())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    p.add_argument("--manager-name", default="marketplacebuyer", help="lowercase letters and digits")
    p.add_argument("--role-arn", help="use an existing payment manager service role")
    p.add_argument("--skip-slr", action="store_true", help="don't check or create the service-linked role")
    args = p.parse_args()

    session = boto3.Session(region_name=args.region)
    account = session.client("sts").get_caller_identity()["Account"]
    iam = session.client("iam")
    acp = AgentCorePaymentsClient(args.region, session)
    print(f"Account {account}, Region {args.region}")

    print("1. AWS Marketplace service-linked role")
    print("  skipped" if args.skip_slr else f"  {ensure_service_linked_role(iam)}")
    print("2. Payment manager service role")
    role_arn = args.role_arn or ensure_manager_role(iam, account, args.manager_name)
    print("3. Payment manager")
    mgr = ensure_manager(acp, args.manager_name, role_arn)
    print("4. AWS Billing connector")
    conn = ensure_connector(acp, mgr["paymentManagerId"])
    session_role, agent_role = ensure_buyer_roles(iam, account, mgr["paymentManagerArn"])

    write_env(
        {
            "AWS_REGION": args.region,
            "PAYMENT_MANAGER_ARN": mgr["paymentManagerArn"],
            "PAYMENT_MANAGER_ID": mgr["paymentManagerId"],
            "PAYMENT_CONNECTOR_ID": conn["paymentConnectorId"],
            "SESSION_ROLE_ARN": session_role,
            "AGENT_ROLE_ARN": agent_role,
        }
    )
    print(
        f"\nDone. Wrote {ENV_FILE}.\nNext: run python setup_gateway.py so the agent can discover sellers, "
        "or set SELLER_URL in .env. Then run python strands_marketplace_agent.py."
    )


if __name__ == "__main__":
    main()
