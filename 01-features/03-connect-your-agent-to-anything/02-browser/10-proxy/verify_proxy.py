"""Verify that the AgentCore Browser routes traffic through the Squid proxy.

Reads CloudFormation outputs, builds a proxyConfiguration, starts a browser
session, and checks that the browser's public IP matches the Squid proxy's IP.

Usage:
    export AWS_DEFAULT_REGION=us-west-2
    python verify_proxy.py
"""

import asyncio
import boto3
from urllib.parse import urlparse
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

STACK_NAME = "agentcore-browser-proxy"

session = boto3.Session()
REGION = session.region_name
print(f"Region: {REGION}")
browser_client = session.client("bedrock-agentcore")


def get_stack_outputs():
    cfn = session.client("cloudformation")
    stacks = cfn.describe_stacks(StackName=STACK_NAME)["Stacks"]
    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0]["Outputs"]}


def get_signed_headers(ws_url):
    credentials = session.get_credentials()
    https_url = ws_url.replace("wss://", "https://")
    parsed = urlparse(https_url)
    request = AWSRequest(method="GET", url=https_url, headers={"host": parsed.netloc})
    SigV4Auth(credentials, "bedrock-agentcore", REGION).add_auth(request)
    return {k: v for k, v in request.headers.items()}


async def main():
    outputs = get_stack_outputs()
    browser_id = outputs["BrowserId"]
    squid_ip = outputs["SquidPrivateIp"]
    squid_public_ip = outputs["SquidPublicIp"]
    secret_arn = outputs["ProxySecretArn"]

    print(f"Browser ID:       {browser_id}")
    print(f"Squid private IP: {squid_ip}")
    print(f"Squid public IP:  {squid_public_ip}")

    proxy_config = {
        "proxies": [
            {
                "externalProxy": {
                    "server": squid_ip,
                    "port": 3128,
                    "credentials": {"basicAuth": {"secretArn": secret_arn}},
                }
            }
        ]
    }

    print("\nStarting browser session with proxy configuration...")
    response = browser_client.start_browser_session(
        browserIdentifier=browser_id,
        proxyConfiguration=proxy_config,
    )
    session_id = response["sessionId"]
    ws_url = (
        f"wss://bedrock-agentcore.{REGION}.amazonaws.com/browser-streams/{browser_id}/sessions/{session_id}/automation"
    )
    print(f"Session ID: {session_id}")

    headers = get_signed_headers(ws_url)

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(ws_url, headers=headers)
            page = browser.contexts[0].pages[0] if browser.contexts else await browser.new_context().new_page()

            print("\nChecking browser's public IP via icanhazip.com...")
            await page.goto("https://icanhazip.com", timeout=15000, wait_until="domcontentloaded")
            observed_ip = (await page.inner_text("body")).strip()

            print(f"\n{'=' * 50}")
            print(f"Expected IP (Squid public): {squid_public_ip}")
            print(f"Observed IP (browser):      {observed_ip}")
            match = observed_ip == squid_public_ip
            print(f"Result: {'PASS' if match else 'FAIL'} — traffic {'is' if match else 'is NOT'} routed through proxy")
            print(f"{'=' * 50}")

            await browser.close()
    finally:
        browser_client.stop_browser_session(browserIdentifier=browser_id, sessionId=session_id)
        print(f"\nSession {session_id} stopped")


if __name__ == "__main__":
    asyncio.run(main())
