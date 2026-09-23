"""
AgentCore Browser Tool — Browser Profile Persistence Demo.

Demonstrates how to persist browser session state (cookies, localStorage) across
multiple browser sessions using AgentCore Browser Profiles.

Workflow:
  1. Create a custom browser with S3 recording and a browser profile
  2. Session A — connect via Playwright, add two items to the e-commerce cart,
     and save the session state to the profile
  3. Session B — start a NEW session loading the saved profile and verify the
     cart still contains the previously selected items

The sample uses a CloudFront-hosted demo e-commerce site. Deploy it first:
  cd sample-ecommerce && bash deploy.sh

Usage:
    python browser_profile.py --cfn-url https://xxxx.cloudfront.net [--region REGION] [--skip-cleanup]

Prerequisites:
    pip install -r ../requirements.txt
    playwright install chromium
    Deploy sample-ecommerce/cloudformation.yaml first and set CFN_URL

IAM permissions required:
    bedrock-agentcore:StartBrowserSession / StopBrowserSession
    bedrock-agentcore:SaveBrowserSessionProfile
    bedrock-agentcore:CreateBrowser / DeleteBrowser
    bedrock-agentcore:CreateBrowserProfile / DeleteBrowserProfile
    s3:PutObject / GetObject / ListBucket (recordings bucket)
    iam:CreateRole / ...
"""

import argparse
import asyncio
import json
import os
import time

import boto3
from botocore.exceptions import ClientError

# ── Configuration ─────────────────────────────────────────────────────────────

BROWSER_NAME = "browser_with_profiles"
BROWSER_PROFILE_NAME = "profile_sample"
AC_ROLE_NAME = "ac-browser-profiles-execution-role"


# ── SigV4 helpers ──────────────────────────────────────────────────────────────


def get_ws_url(browser_id: str, session_id: str, region: str) -> str:
    return (
        f"wss://bedrock-agentcore.{region}.amazonaws.com/browser-streams/{browser_id}/sessions/{session_id}/automation"
    )


def get_signed_headers(ws_url: str, region: str) -> dict:
    """Return SigV4-signed HTTP headers for the CDP WebSocket connection."""
    from urllib.parse import urlparse

    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest

    session = boto3.Session()
    credentials = session.get_credentials()
    https_url = ws_url.replace("wss://", "https://")
    parsed = urlparse(https_url)
    request = AWSRequest(method="GET", url=https_url, headers={"host": parsed.netloc})
    SigV4Auth(credentials, "bedrock-agentcore", region).add_auth(request)
    return {k: v for k, v in request.headers.items()}


# ── IAM role ───────────────────────────────────────────────────────────────────


def create_execution_role(
    role_name: str, account_id: str, region: str, bucket_name: str, profile_name: str, browser_name: str
) -> str:
    iam = boto3.client("iam")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    inline_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "s3:PutObject",
                    "s3:GetObject",
                    "s3:ListBucket",
                    "s3:ListMultipartUploadParts",
                    "s3:AbortMultipartUpload",
                ],
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*",
                ],
            },
            {
                "Sid": "BedrockAgentCoreBrowserProfileUsageAccess",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:StartBrowserSession",
                    "bedrock-agentcore:SaveBrowserSessionProfile",
                ],
                "Resource": [
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:browser-profile/{profile_name}",
                    f"arn:aws:bedrock-agentcore:{region}:{account_id}:browser-custom/{browser_name}",
                ],
            },
        ],
    }

    try:
        role = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
        )
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="ac_custom_policies",
            PolicyDocument=json.dumps(inline_policy),
        )
        iam.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/AmazonBedrockFullAccess",
        )
        print(f"Created IAM role: {role['Role']['Arn']}")
        time.sleep(10)
        return role["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
            print(f"Reusing existing IAM role: {arn}")
            return arn
        raise


# ── Main demo ──────────────────────────────────────────────────────────────────


async def session_a_add_to_cart(ws_url: str, headers: dict, cfn_url: str) -> None:
    """Session A: add items to cart and save state to localStorage."""
    from playwright.async_api import async_playwright

    print("\n[Session A] Adding items to cart...")
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url, headers=headers)
        page = browser.contexts[0].pages[0] if browser.contexts else await browser.new_context().new_page()
        try:
            await page.goto(f"{cfn_url}/#home", wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            # Add first item (index 2)
            btn = page.locator('button[onclick="addToCart(2)"]')
            await btn.wait_for(state="visible")
            await btn.click()
            await page.wait_for_timeout(1000)

            # Add second item (index 4)
            btn = page.locator('button[onclick="addToCart(4)"]')
            await btn.wait_for(state="visible")
            await btn.click()
            await page.wait_for_timeout(1000)

            # View cart to confirm
            view_cart = page.locator("#viewCart")
            await view_cart.wait_for(state="visible")
            await view_cart.click()
            await page.wait_for_timeout(2000)
            print("[Session A] Cart updated — 2 items added")

            # Return to products
            back_btn = page.locator("#backToProducts")
            await back_btn.wait_for(state="visible")
            await back_btn.click()

            # Persist cart to localStorage
            await page.evaluate("localStorage.setItem('cart', JSON.stringify(cart))")
            await page.wait_for_timeout(500)
            print("[Session A] Cart state saved to localStorage")
        except Exception as e:
            print(f"[Session A] Error: {e}")
            raise


async def session_b_verify_cart(ws_url: str, headers: dict, cfn_url: str) -> None:
    """Session B: load with saved profile and verify cart is already populated."""
    from playwright.async_api import async_playwright

    print("\n[Session B] Verifying cart persists from saved profile...")
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url, headers=headers)
        page = browser.contexts[0].pages[0] if browser.contexts else await browser.new_context().new_page()
        try:
            await page.goto(f"{cfn_url}/#home", wait_until="domcontentloaded")
            await page.wait_for_timeout(5000)

            view_cart = page.locator("#viewCart")
            await view_cart.wait_for(state="visible")
            await view_cart.click()
            await page.wait_for_timeout(2000)
            print("[Session B] Cart loaded — items from previous session should be present")
        except Exception as e:
            print(f"[Session B] Error: {e}")
            raise


def run_demo(cfn_url: str, region: str, skip_cleanup: bool) -> None:
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    cp_client = boto3.client("bedrock-agentcore-control", region_name=region)
    dp_client = boto3.client("bedrock-agentcore", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    bucket_name = f"ac-browser-demos-{account_id}-{region}"

    # 1. Create S3 bucket
    try:
        s3.head_bucket(Bucket=bucket_name)
        print(f"Bucket {bucket_name} already exists")
    except ClientError:
        create_params = {"Bucket": bucket_name}
        if region != "us-east-1":
            create_params["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3.create_bucket(**create_params)
        print(f"Created bucket: {bucket_name}")

    # 2. IAM role
    role_arn = create_execution_role(AC_ROLE_NAME, account_id, region, bucket_name, BROWSER_PROFILE_NAME, BROWSER_NAME)

    # 3. Custom browser
    created_browser = cp_client.create_browser(
        name=BROWSER_NAME,
        executionRoleArn=role_arn,
        networkConfiguration={"networkMode": "PUBLIC"},
        recording={
            "enabled": True,
            "s3Location": {"bucket": bucket_name, "prefix": "browser_recordings/"},
        },
    )
    browser_id = created_browser["browserId"]
    print(f"Created browser: {browser_id}")

    # 4. Browser profile
    created_profile = cp_client.create_browser_profile(
        name=BROWSER_PROFILE_NAME, description="Demo profile for cart persistence"
    )
    profile_id = created_profile["profileId"]
    print(f"Created profile: {profile_id}")

    # ── Session A ──────────────────────────────────────────────────────────────
    resp = dp_client.start_browser_session(browserIdentifier=browser_id)
    session_id_a = resp["sessionId"]
    ws_url_a = get_ws_url(browser_id, session_id_a, region)
    headers_a = get_signed_headers(ws_url_a, region)

    asyncio.run(session_a_add_to_cart(ws_url_a, headers_a, cfn_url))

    # Save session to profile
    dp_client.save_browser_session_profile(
        profileIdentifier=profile_id,
        browserIdentifier=browser_id,
        sessionId=session_id_a,
    )
    print("Profile saved successfully")

    # Stop Session A
    dp_client.stop_browser_session(browserIdentifier=browser_id, sessionId=session_id_a)
    print("Session A stopped")

    # ── Session B ──────────────────────────────────────────────────────────────
    resp = dp_client.start_browser_session(
        browserIdentifier=browser_id,
        profileConfiguration={"profileIdentifier": profile_id},
    )
    session_id_b = resp["sessionId"]
    ws_url_b = get_ws_url(browser_id, session_id_b, region)
    headers_b = get_signed_headers(ws_url_b, region)

    asyncio.run(session_b_verify_cart(ws_url_b, headers_b, cfn_url))

    # Stop Session B
    dp_client.stop_browser_session(browserIdentifier=browser_id, sessionId=session_id_b)
    print("Session B stopped")

    # ── Cleanup ────────────────────────────────────────────────────────────────
    if not skip_cleanup:
        print("\nCleaning up...")
        try:
            cp_client.delete_browser(browserId=browser_id)
            print(f"Deleted browser: {browser_id}")
        except Exception as e:
            print(f"Could not delete browser: {e}")
        try:
            cp_client.delete_browser_profile(profileId=profile_id)
            print(f"Deleted profile: {profile_id}")
        except Exception as e:
            print(f"Could not delete profile: {e}")
    else:
        print(f"\n--skip-cleanup: browser={browser_id}, profile={profile_id}")

    print("\nDemo complete.")


def parse_args():
    parser = argparse.ArgumentParser(description="AgentCore Browser Profile persistence demo")
    parser.add_argument(
        "--cfn-url",
        default=os.getenv("CFN_URL", ""),
        help="CloudFront URL of the sample e-commerce site (env: CFN_URL)",
    )
    parser.add_argument("--region", default=boto3.Session().region_name or "us-east-1")
    parser.add_argument("--skip-cleanup", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.cfn_url:
        print("ERROR: --cfn-url is required. Deploy sample-ecommerce/ first.")
        print("  cd sample-ecommerce && bash deploy.sh")
        raise SystemExit(1)
    run_demo(args.cfn_url, args.region, args.skip_cleanup)


if __name__ == "__main__":
    main()
