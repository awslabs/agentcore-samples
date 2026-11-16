"""
End-to-end smoke test through the FRONT DOOR, exactly as a non-AWS MCP client would:

  1. Sign in to Cognito as the demo user (authorization-code + PKCE, no client secret)
  2. Call the front-door Gateway over MCP with the resulting bearer token
  3. tools/list  -> expect the agent's single tool
  4. tools/call  -> run the expense agent end to end

No AWS credentials are used for the MCP calls themselves; they are only used to
read the Gateway URL from the CloudFormation stack outputs for convenience.

Usage:
  python3 scripts/test_frontdoor.py --region us-east-1 [--report "..."] [--list-only]
"""

import argparse
import asyncio
import base64
import datetime
import hashlib
import os
import pathlib
import secrets
import sys
import urllib.parse

import boto3
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

STACK = "AgentCore-agentAsMcpServer-dev"
PROJECT_DIR = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_REPORT = "Employee: Kaan Yilmaz. Bought a second monitor for the home office, 4000 TRY. No receipt available."


def load_env() -> dict:
    env = dict(os.environ)
    env_file = PROJECT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip())
    return env


def stack_output(region: str, key: str) -> str:
    cfn = boto3.client("cloudformation", region_name=region)
    outputs = cfn.describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]
    return next(o["OutputValue"] for o in outputs if o["OutputKey"] == key)


def cognito_user_token(env: dict) -> str:
    """Authorization-code + PKCE flow, driven headlessly against the Cognito hosted UI."""
    hosted_ui = env["COGNITO_HOSTED_UI"]
    client_id = env["FRONTDOOR_USER_CLIENT_ID"]
    username, password = env["DEMO_USERNAME"], env["DEMO_PASSWORD"]
    redirect_uri = "http://localhost:53280/callback"

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    with httpx.Client(follow_redirects=False, timeout=30) as http:
        # 1) GET /oauth2/authorize -> redirects to /login with a CSRF cookie
        r = http.get(
            f"{hosted_ui}/oauth2/authorize",
            params={
                "client_id": client_id,
                "response_type": "code",
                "scope": "openid email profile agentcore/invoke",
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        login_url = r.headers.get("location", "")
        if not login_url.startswith("http"):
            login_url = hosted_ui + login_url
        http.get(login_url)
        csrf = http.cookies.get("XSRF-TOKEN", "")

        # 2) POST credentials to /login -> redirects to redirect_uri?code=...
        r = http.post(
            login_url,
            data={"_csrf": csrf, "username": username, "password": password},
            headers={"Cookie": f"XSRF-TOKEN={csrf}"},
        )
        location = r.headers.get("location", "")
        code = urllib.parse.parse_qs(urllib.parse.urlparse(location).query).get("code", [None])[0]
        if not code:
            raise RuntimeError(
                f"Login did not return an auth code (status {r.status_code}). Check DEMO_USERNAME/DEMO_PASSWORD."
            )

        # 3) Exchange code for tokens (PKCE, public client -> no secret)
        r = http.post(
            f"{hosted_ui}/oauth2/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": verifier,
            },
        )
        r.raise_for_status()
        return r.json()["access_token"]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--list-only", action="store_true")
    args = parser.parse_args()

    env = load_env()
    url = stack_output(args.region, "FrontDoorMcpUrl")
    print(f"front door : {url}")
    print(f"signing in : {env['DEMO_USERNAME']} (authorization code + PKCE)")
    token = cognito_user_token(env)
    print("token      : OK\n")

    headers = {"Authorization": f"Bearer {token}"}
    async with (
        streamablehttp_client(url, headers=headers) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        print("== tools/list ==")
        for t in tools.tools:
            print(f"  - {t.name}: {(t.description or '').strip().splitlines()[0]}")
        if args.list_only:
            return

        tool_name = next(t.name for t in tools.tools if t.name.endswith("process_expense_report"))
        print(f"\n== tools/call {tool_name} ==\nreport: {args.report}\n")
        result = await session.call_tool(
            tool_name, {"report": args.report}, read_timeout_seconds=datetime.timedelta(seconds=180)
        )
        for block in result.content:
            if block.type == "text":
                print(block.text)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (RuntimeError, KeyError, StopIteration, httpx.HTTPError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
