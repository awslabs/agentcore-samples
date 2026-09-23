"""
Browser Observability and Session Replay with AgentCore Browser Tool.

Demonstrates how to enable recording and observability on an AgentCore Browser
session:
  1. Create an S3 bucket to store browser recordings
  2. Create an IAM execution role with browser + S3 + CloudWatch permissions
  3. Create a custom Browser resource with recording enabled
  4. Start a session using the custom browser resource
  5. Run a Nova Act task while recording
  6. View the recording in the AgentCore console
  7. Clean up all created resources

Prerequisites:
    pip install -r ../requirements.txt
    export NOVA_ACT_API_KEY=<your-nova-act-api-key>

IAM permissions required (caller):
    bedrock-agentcore:CreateBrowser
    bedrock-agentcore:DeleteBrowser
    bedrock-agentcore:StartBrowserSession
    bedrock-agentcore:StopBrowserSession
    bedrock-agentcore:ConnectBrowserAutomationStream
    iam:CreateRole, iam:PutRolePolicy, iam:DeleteRole, iam:DeleteRolePolicy
    s3:CreateBucket, s3:DeleteBucket, s3:ListBucket

Usage:
    python browser_observability.py \\
        --prompt "Search for macbooks and extract the details of the first one" \\
        --starting-page "https://www.amazon.com/" \\
        --nova-act-key $NOVA_ACT_API_KEY
"""

import argparse
import json
import os
import time
import uuid

import boto3
from boto3.session import Session
from nova_act import NovaAct
from rich.console import Console

from bedrock_agentcore.tools.browser_client import BrowserClient

console = Console()


# ── IAM helpers ───────────────────────────────────────────────────────────────


def create_browser_execution_role(role_name: str, bucket_name: str, region: str, account_id: str) -> str:
    """Create an IAM role that lets AgentCore Browser write recordings to S3."""
    iam = boto3.client("iam")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }

    permissions = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "BrowserPermissions",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:ConnectBrowserAutomationStream",
                    "bedrock-agentcore:ConnectBrowserLiveViewStream",
                    "bedrock-agentcore:UpdateBrowserStream",
                    "bedrock-agentcore:GetBrowserSession",
                    "bedrock-agentcore:StartBrowserSession",
                    "bedrock-agentcore:StopBrowserSession",
                ],
                "Resource": "*",
            },
            {
                "Sid": "S3Permissions",
                "Effect": "Allow",
                "Action": ["s3:PutObject", "s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*",
                ],
            },
            {
                "Sid": "CloudWatchPermissions",
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                "Resource": "*",
            },
        ],
    }

    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
        )
        console.print(f"  Created IAM role: {role_name}")
    except iam.exceptions.EntityAlreadyExistsException:
        console.print(f"  Role {role_name} already exists — deleting and recreating.")
        for policy_name in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        iam.delete_role(RoleName=role_name)
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
        )

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=f"{role_name}-policy",
        PolicyDocument=json.dumps(permissions),
    )
    # Allow IAM to propagate
    time.sleep(10)
    return role["Role"]["Arn"]


def delete_browser_execution_role(role_name: str) -> None:
    iam = boto3.client("iam")
    try:
        for policy_name in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        iam.delete_role(RoleName=role_name)
        console.print(f"  Deleted IAM role: {role_name}")
    except Exception as exc:
        console.print(f"  Warning during role cleanup: {exc}")


# ── Main ───────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="AgentCore Browser observability demo")
    parser.add_argument(
        "--prompt",
        default="Search for macbooks and extract the details of the first one",
        help="Nova Act browser instruction",
    )
    parser.add_argument(
        "--starting-page",
        default="https://www.amazon.com/",
        help="Starting URL",
    )
    parser.add_argument(
        "--nova-act-key",
        default=os.getenv("NOVA_ACT_API_KEY"),
        help="Nova Act API key (env: NOVA_ACT_API_KEY)",
    )
    parser.add_argument(
        "--region",
        default=Session().region_name or "us-west-2",
        help="AWS region",
    )
    parser.add_argument(
        "--skip-cleanup",
        action="store_true",
        help="Skip cleanup so you can review the recording in the console",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if not args.nova_act_key:
        console.print("[red]ERROR:[/red] --nova-act-key is required (or set NOVA_ACT_API_KEY).")
        raise SystemExit(1)

    boto_session = Session()
    region = args.region or boto_session.region_name or "us-west-2"
    account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]

    console.print("=" * 60)
    console.print("AgentCore Browser Tool — Observability Demo")
    console.print("=" * 60)
    console.print(f"  Region:  {region}")
    console.print(f"  Account: {account_id}")

    # ── 1. Create S3 bucket ────────────────────────────────────────────────────
    bucket_name = f"agentcore-browser-recordings-{uuid.uuid4().hex[:8]}"
    console.print(f"\n[1] Creating S3 bucket: {bucket_name}")
    s3 = boto3.client("s3", region_name=region)
    if region == "us-east-1":
        s3.create_bucket(Bucket=bucket_name)
    else:
        s3.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={"LocationConstraint": region},
        )
    console.print(f"  Created bucket: {bucket_name}")

    # ── 2. Create execution role ───────────────────────────────────────────────
    role_name = f"agentcore-browser-obs-{uuid.uuid4().hex[:6]}"
    console.print(f"\n[2] Creating execution role: {role_name}")
    role_arn = create_browser_execution_role(role_name, bucket_name, region, account_id)
    console.print(f"  Role ARN: {role_arn}")

    # ── 3. Create custom Browser resource with recording ──────────────────────
    cp_client = boto3.client("bedrock-agentcore-control", region_name=region)
    browser_name = f"obs-browser-{int(time.time()) % 100000}"
    console.print(f"\n[3] Creating custom browser resource: {browser_name}")
    try:
        resp = cp_client.create_browser(
            name=browser_name,
            description="Browser with recording enabled for observability demo",
            networkConfiguration={"networkMode": "PUBLIC"},
            executionRoleArn=role_arn,
            clientToken=str(uuid.uuid4()),
            recording={
                "enabled": True,
                "s3Location": {"bucket": bucket_name, "prefix": "replay-data"},
            },
        )
        browser_id = resp["browserId"]
        console.print(f"  Browser ID: {browser_id}")
    except Exception as exc:
        console.print(f"[red]Failed to create browser resource:[/red] {exc}")
        raise

    browser_client = None
    try:
        # ── 4. Start a session using the custom browser resource ───────────────
        console.print("\n[4] Starting browser session...")
        browser_client = BrowserClient(region)
        browser_client.start(identifier=browser_id)
        ws_url, headers = browser_client.generate_ws_headers()
        console.print("  Session started.")

        # ── 5. Run Nova Act task ───────────────────────────────────────────────
        console.print(f"\n[5] Running task: {args.prompt}")
        with NovaAct(
            cdp_endpoint_url=ws_url,
            cdp_headers=headers,
            nova_act_api_key=args.nova_act_key,
            starting_page=args.starting_page,
        ) as nova_act:
            result = nova_act.act(args.prompt)
        console.print(f"  Result: {result.response if result else 'No result'}")

    finally:
        if browser_client:
            console.print("\n[6] Stopping browser session...")
            browser_client.stop()
            console.print("  Session stopped.")

        console.print(
            f"\n[bold cyan]View recording:[/bold cyan]\n"
            f"  AWS Console → AgentCore → Browser use tools → {browser_name}\n"
            f"  Region URL: https://{region}.console.aws.amazon.com/bedrock-agentcore/builtInTools"
        )

        if not args.skip_cleanup:
            console.print("\n[7] Cleaning up resources...")
            try:
                cp_client.delete_browser(browserId=browser_id)
                console.print(f"  Deleted browser resource: {browser_name}")
            except Exception as exc:
                console.print(f"  Warning during browser cleanup: {exc}")

            delete_browser_execution_role(role_name)

            try:
                objs = s3.list_objects_v2(Bucket=bucket_name).get("Contents", [])
                for obj in objs:
                    s3.delete_object(Bucket=bucket_name, Key=obj["Key"])
                s3.delete_bucket(Bucket=bucket_name)
                console.print(f"  Deleted S3 bucket: {bucket_name}")
            except Exception as exc:
                console.print(f"  Warning during S3 cleanup: {exc}")
        else:
            console.print("\n[yellow]Skipping cleanup (--skip-cleanup). Resources remain for review.[/yellow]")

    console.print("\n" + "=" * 60)
    console.print("Demo complete!")
    console.print("=" * 60)


if __name__ == "__main__":
    main()
