"""
Delete the AWS Billing connector and payment manager created by setup_aws_billing.py.

Leaves the service-linked role and the service role in place: other payment managers may
use them, and AWS Marketplace won't let you delete the service-linked role while payments
are still being metered. --gateway also leaves the gateway's execution role,
AgentCoreGatewayMarketplaceRole, in place.

Usage:
    python cleanup.py                       # manager from .env
    python cleanup.py --manager-id <id>     # any manager in this account and Region
    python cleanup.py --gateway             # also delete the gateway from setup_gateway.py
    python cleanup.py --roles               # also delete the session role and agent role
"""

import argparse
import os
import time

import boto3
from dotenv import load_dotenv
from marketplace_payments import AGENT_ROLE_NAME, SESSION_ROLE_NAME, AgentCorePaymentsClient, PaymentsApiError

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=False)

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
p.add_argument("--manager-id", default=os.environ.get("PAYMENT_MANAGER_ID"))
p.add_argument("--gateway", action="store_true", help="also delete the discovery gateway and its target")
p.add_argument("--roles", action="store_true", help="also delete the session role and agent role")
args = p.parse_args()
if not args.manager_id:
    raise SystemExit("No manager ID: pass --manager-id or run setup_aws_billing.py first")

acp = AgentCorePaymentsClient(args.region, boto3.Session(region_name=args.region))
for c in acp.list_payment_connectors(args.manager_id):
    print(f"Deleting connector {c['paymentConnectorId']} ({c['type']})")
    acp.delete_payment_connector(args.manager_id, c["paymentConnectorId"])

for _ in range(20):  # the manager can't be deleted until its connectors are gone
    if not acp.list_payment_connectors(args.manager_id):
        break
    time.sleep(3)

print(f"Deleting payment manager {args.manager_id}")
try:
    acp.delete_payment_manager(args.manager_id)
except PaymentsApiError as e:
    raise SystemExit(str(e))

if args.gateway and os.environ.get("GATEWAY_ID"):
    gw_region = os.environ.get("GATEWAY_REGION", args.region)
    cp = boto3.Session(region_name=gw_region).client("bedrock-agentcore-control")
    gw = os.environ["GATEWAY_ID"]
    for t in cp.list_gateway_targets(gatewayIdentifier=gw)["items"]:
        print(f"Deleting gateway target {t['targetId']}")
        cp.delete_gateway_target(gatewayIdentifier=gw, targetId=t["targetId"])
    for _ in range(20):
        if not cp.list_gateway_targets(gatewayIdentifier=gw)["items"]:
            break
        time.sleep(3)
    print(f"Deleting gateway {gw}")
    cp.delete_gateway(gatewayIdentifier=gw)
elif args.gateway:
    print("No GATEWAY_ID in .env, so no gateway to delete")
if args.roles:
    iam = boto3.client("iam")
    for name in (SESSION_ROLE_NAME, AGENT_ROLE_NAME):
        try:
            for policy in iam.list_role_policies(RoleName=name)["PolicyNames"]:
                iam.delete_role_policy(RoleName=name, PolicyName=policy)
            iam.delete_role(RoleName=name)
            print(f"Deleted role {name}")
        except iam.exceptions.NoSuchEntityException:
            pass
print("Done.")
