"""
AgentCore Browser Tool — Web Bot Auth Signing Demo.

Demonstrates how enabling browserSigning on a custom AgentCore Browser causes
Chrome to automatically attach cryptographic Web Bot Auth headers
(Signature, Signature-Input, Signature-Agent) to every outgoing HTTP request.
This lets AI agents pass Cloudflare's Web Bot Auth challenge without CAPTCHAs.

The script creates two Strands agents:
  1. SIGNED  — uses a custom browser with browserSigning.enabled=True
  2. UNSIGNED — uses the default managed browser (no signing)

Both agents visit https://crawltest.com/cdn-cgi/web-bot-auth and report
whether Cloudflare's challenge was passed.

Usage:
    python web_bot_auth.py [--region REGION] [--skip-cleanup]

Prerequisites:
    pip install -r ../requirements.txt
    AWS credentials configured (aws sts get-caller-identity)

IAM permissions required:
    bedrock-agentcore:StartBrowserSession
    bedrock-agentcore:StopBrowserSession
    bedrock-agentcore:ConnectBrowserAutomationStream
    bedrock-agentcore:CreateBrowser
    bedrock-agentcore:DeleteBrowser
    bedrock:InvokeModel
    iam:CreateRole / iam:DeleteRole / iam:AttachRolePolicy / ...
"""

import argparse
import asyncio
import json
import time
import uuid

import boto3
from strands import Agent
from strands.tools.executors import SequentialToolExecutor
from strands_tools.browser import AgentCoreBrowser

# ── Configuration ─────────────────────────────────────────────────────────────

REGION = "us-west-2"
MODEL_ID = "global.anthropic.claude-haiku-4-5-20251001-v1:0"

SYSTEM_PROMPT = """You are a website analyst with browser signing capabilities.
1. Use the browser tool to visit and interact with the website EFFICIENTLY
2. Focus on extracting key information QUICKLY and within 2-3 browser interactions.
3. Review browser requests for signatures related to Web Bot Auth's Signature and
   Signature-Agent http headers, to verify if browser signing is configured."""

TEST_URL = "https://crawltest.com/cdn-cgi/web-bot-auth"
TEST_PROMPT = (
    f"Review the output and status code at {TEST_URL} and provide 3 to 4 concise "
    "key insights, based on https://developers.cloudflare.com/bots/reference/"
    "bot-verification/web-bot-auth/"
)


# ── IAM role helper ────────────────────────────────────────────────────────────


def create_browser_execution_role(role_name: str) -> str:
    """Create an IAM execution role for AgentCore Browser with Web Bot Auth."""
    iam = boto3.client("iam")
    sts = boto3.client("sts")

    account_id = sts.get_caller_identity()["Account"]
    session = boto3.Session()
    region = session.region_name or REGION

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

    inline_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:StartBrowserSession",
                    "bedrock-agentcore:StopBrowserSession",
                    "bedrock-agentcore:ConnectBrowserAutomationStream",
                    "bedrock:InvokeModel",
                ],
                "Resource": "*",
            }
        ],
    }

    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="Execution role for AgentCore Browser Web Bot Auth demo",
        )
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="BrowserWebBotAuthPolicy",
            PolicyDocument=json.dumps(inline_policy),
        )
        role_arn = role["Role"]["Arn"]
        print(f"Created IAM role: {role_arn}")
        # Wait for propagation
        time.sleep(10)
        return role_arn
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        print(f"Reusing existing IAM role: {role_arn}")
        return role_arn


def delete_browser_execution_role(role_name: str) -> None:
    iam = boto3.client("iam")
    try:
        iam.delete_role_policy(RoleName=role_name, PolicyName="BrowserWebBotAuthPolicy")
    except Exception:
        pass
    try:
        iam.delete_role(RoleName=role_name)
        print(f"Deleted IAM role: {role_name}")
    except Exception as e:
        print(f"Could not delete role {role_name}: {e}")


# ── Browser setup ──────────────────────────────────────────────────────────────


def create_signed_browser(cp_client, execution_role_arn: str) -> str:
    """Create a custom browser with Web Bot Auth signing enabled."""
    browser_name = f"web_bot_auth_{uuid.uuid4().hex[:8]}"
    response = cp_client.create_browser(
        name=browser_name,
        description="Browser configured for Web Bot Auth signing",
        networkConfiguration={"networkMode": "PUBLIC"},
        executionRoleArn=execution_role_arn,
        browserSigning={"enabled": True},
    )
    browser_id = response["browserId"]
    print(f"Created signed browser: {browser_id}")
    print("  browserSigning.enabled = True — all HTTP requests will be signed")
    return browser_id


# ── Agent helpers ──────────────────────────────────────────────────────────────


def create_agent(browser_id: str | None = None) -> Agent:
    """Create a Strands agent with SequentialToolExecutor to avoid async conflicts."""
    kwargs = {"region": REGION}
    if browser_id:
        kwargs["identifier"] = browser_id
    browser_tool = AgentCoreBrowser(**kwargs)
    return Agent(
        tools=[browser_tool.browser],
        tool_executor=SequentialToolExecutor(),
        model=MODEL_ID,
        system_prompt=SYSTEM_PROMPT,
    )


async def run_agent(agent: Agent, prompt: str) -> str | None:
    """Invoke agent asynchronously and return result string."""
    try:
        result = await agent.invoke_async(prompt)
        return str(result)
    except Exception as e:
        print(f"Agent error: {e}")
        return None


# ── Main ───────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="AgentCore Browser Web Bot Auth Signing demo")
    parser.add_argument("--region", default=REGION)
    parser.add_argument(
        "--skip-cleanup",
        action="store_true",
        help="Keep browser and IAM role after the demo",
    )
    return parser.parse_args()


async def async_main(args):
    global REGION
    REGION = args.region

    cp_client = boto3.client("bedrock-agentcore-control", region_name=REGION)
    role_name = f"WebBotAuthBrowserRole_{uuid.uuid4().hex[:6]}"

    print("=" * 70)
    print("AgentCore Browser — Web Bot Auth Signing Demo")
    print("=" * 70)

    # Step 1: Create IAM role + signed browser
    execution_role_arn = create_browser_execution_role(role_name)
    browser_id = create_signed_browser(cp_client, execution_role_arn)

    # Step 2: Create agents
    print("\nInitialising agents...")
    signed_agent = create_agent(browser_id=browser_id)
    unsigned_agent = create_agent()  # default managed browser

    # Step 3: Run signed agent
    print("\n" + "-" * 70)
    print("TEST 1 — SIGNED browser (browserSigning.enabled=True)")
    print("-" * 70)
    result_signed = await run_agent(signed_agent, TEST_PROMPT)
    if result_signed:
        print(result_signed)

    # Step 4: Run unsigned agent
    print("\n" + "-" * 70)
    print("TEST 2 — UNSIGNED browser (default managed, no signing)")
    print("-" * 70)
    result_unsigned = await run_agent(unsigned_agent, TEST_PROMPT)
    if result_unsigned:
        print(result_unsigned)

    # Step 5: Compare
    print("\n" + "=" * 70)
    print("COMPARISON")
    print("=" * 70)
    if result_signed and result_unsigned:
        comparison_agent = Agent(
            system_prompt="You are an expert analyst comparing AI agent outputs.",
        )
        comparison = comparison_agent(
            f"Compare these two agent outputs side by side:\n\n"
            f"**SIGNED (Web Bot Auth enabled)**:\n{result_signed}\n\n"
            f"**UNSIGNED (no signing)**:\n{result_unsigned}\n\n"
            "Highlight: (1) differences in HTTP response / status codes, "
            "(2) whether Signature/Signature-Agent headers appear in the signed run, "
            "(3) summary of which agent passed the Web Bot Auth challenge."
        )
        print(comparison)

    # Step 6: Cleanup
    if not args.skip_cleanup:
        print("\nCleaning up...")
        # AgentCoreBrowser manages sessions internally; stop any that are still active
        # before attempting to delete the browser resource.
        dp_client = boto3.client("bedrock-agentcore", region_name=REGION)
        try:
            sessions = dp_client.list_browser_sessions(browserIdentifier=browser_id)
            terminal = {"STOPPED", "DELETED", "FAILED"}
            for s in sessions.get("items", []):
                if s.get("status") not in terminal:
                    dp_client.stop_browser_session(browserIdentifier=browser_id, sessionId=s["sessionId"])
            if sessions.get("items"):
                time.sleep(2)  # brief wait for sessions to terminate
        except Exception:
            pass
        try:
            cp_client.delete_browser(browserId=browser_id)
            print(f"Deleted browser: {browser_id}")
        except Exception as e:
            print(f"Could not delete browser: {e}")
        delete_browser_execution_role(role_name)
    else:
        print(f"\n--skip-cleanup set. Browser ID: {browser_id}, Role: {role_name}")

    print("\nDemo complete.")


def main():
    args = parse_args()
    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
