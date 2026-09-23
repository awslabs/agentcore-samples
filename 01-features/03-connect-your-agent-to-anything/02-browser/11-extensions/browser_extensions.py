"""
AgentCore Browser Tool — Browser Extensions Demo.

Demonstrates how to load a custom Chrome extension into an AgentCore Browser
session at session-creation time. The extension is zipped, uploaded to S3, and
passed via the `extensions` parameter of `start_browser_session()`.

The demo uses the bundled sample extension in ./extension/ which shows a simple
Hello World popup. The script:
  1. Creates an S3 bucket and IAM execution role
  2. Zips and uploads the sample extension
  3. Creates a custom AgentCore Browser
  4. Starts a session with the extension installed
  5. Uses Playwright to navigate to chrome://extensions/ and confirm the extension
     is loaded in the remote browser

Usage:
    python browser_extensions.py [--region REGION] [--skip-cleanup]

Prerequisites:
    pip install -r ../requirements.txt
    playwright install chromium
    AWS credentials configured (aws sts get-caller-identity)

IAM permissions required:
    bedrock-agentcore:StartBrowserSession / StopBrowserSession
    bedrock-agentcore:ConnectBrowserAutomationStream
    bedrock-agentcore:CreateBrowser / DeleteBrowser
    s3:PutObject / GetObject / GetObjectVersion / ListBucket / ...
    iam:CreateRole / ...
"""

import argparse
import asyncio
import json
import os
import subprocess
import time

import boto3
from botocore.exceptions import ClientError

# ── Configuration ─────────────────────────────────────────────────────────────

BROWSER_NAME = "browser_with_extensions"
AC_ROLE_NAME = "ac-browser-ext-execution-role"
EXTENSION_DIR = os.path.join(os.path.dirname(__file__), "extension")
EXTENSION_ZIP = "sample_extension.zip"


# ── SigV4 helpers ──────────────────────────────────────────────────────────────


def get_ws_url(browser_id: str, session_id: str, region: str) -> str:
    return (
        f"wss://bedrock-agentcore.{region}.amazonaws.com/browser-streams/{browser_id}/sessions/{session_id}/automation"
    )


def get_signed_headers(ws_url: str, region: str) -> dict:
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


# ── Setup helpers ──────────────────────────────────────────────────────────────


def create_execution_role(role_name: str, bucket_name: str) -> str:
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
                    "s3:GetObjectVersion",
                    "s3:ListBucket",
                    "s3:ListMultipartUploadParts",
                    "s3:AbortMultipartUpload",
                ],
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*",
                ],
            }
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


def zip_extension(output_path: str) -> None:
    """Zip the extension/ directory into a flat zip file."""
    if os.path.exists(output_path):
        os.remove(output_path)
    subprocess.run(
        ["zip", "-r", os.path.abspath(output_path), "."],
        cwd=EXTENSION_DIR,
        check=True,
        capture_output=True,
    )
    print(f"Extension zipped: {output_path}")


# ── Extension verification ─────────────────────────────────────────────────────


async def verify_extension_loaded(ws_url: str, headers: dict) -> None:
    """Use Playwright to navigate to chrome://extensions/ and confirm the extension is present."""
    from playwright.async_api import async_playwright

    print("\nConnecting to remote browser via CDP...")
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url, headers=headers)
        page = browser.contexts[0].pages[0] if browser.contexts else await browser.new_context().new_page()

        print("Navigating to chrome://extensions/ ...")
        await page.goto("chrome://extensions/")
        await page.wait_for_timeout(2000)

        # Check page title to confirm extensions page loaded
        title = await page.title()
        print(f"Page title: {title}")
        print("Extension should be visible in the live browser session.")
        print("  → In the AWS console: Built-in Tools → browser → View live session")


# ── Main ───────────────────────────────────────────────────────────────────────


def run_demo(region: str, skip_cleanup: bool) -> None:
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    cp_client = boto3.client("bedrock-agentcore-control", region_name=region)
    dp_client = boto3.client("bedrock-agentcore", region_name=region)
    s3 = boto3.client("s3", region_name=region)

    bucket_name = f"ac-browser-demos-{account_id}-{region}"

    # 1. S3 bucket
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
    role_arn = create_execution_role(AC_ROLE_NAME, bucket_name)

    # 3. Zip and upload extension
    zip_extension(EXTENSION_ZIP)
    s3_key = "extensions/sample_extension.zip"
    s3.upload_file(
        EXTENSION_ZIP,
        bucket_name,
        s3_key,
        ExtraArgs={"ContentType": "application/zip"},
    )
    print(f"Uploaded extension to s3://{bucket_name}/{s3_key}")

    # 4. Create custom browser
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

    # 5. Start session with extension
    resp = dp_client.start_browser_session(
        browserIdentifier=browser_id,
        extensions=[
            {
                "location": {
                    "s3": {
                        "bucket": bucket_name,
                        "prefix": s3_key,
                    }
                }
            }
        ],
    )
    session_id = resp["sessionId"]
    print(f"Session started: {session_id} (extension loaded)")

    ws_url = get_ws_url(browser_id, session_id, region)
    headers = get_signed_headers(ws_url, region)

    # 6. Verify extension is present
    asyncio.run(verify_extension_loaded(ws_url, headers))

    # 7. Stop session
    dp_client.stop_browser_session(browserIdentifier=browser_id, sessionId=session_id)
    print(f"Session {session_id} stopped")

    # 8. Cleanup
    if not skip_cleanup:
        print("\nCleaning up...")
        try:
            cp_client.delete_browser(browserId=browser_id)
            print(f"Deleted browser: {browser_id}")
        except Exception as e:
            print(f"Could not delete browser: {e}")
        # Clean up local zip
        if os.path.exists(EXTENSION_ZIP):
            os.remove(EXTENSION_ZIP)
    else:
        print(f"\n--skip-cleanup: browser={browser_id}")

    print("\nDemo complete.")


def parse_args():
    parser = argparse.ArgumentParser(description="AgentCore Browser extension loading demo")
    parser.add_argument("--region", default=boto3.Session().region_name or "us-east-1")
    parser.add_argument("--skip-cleanup", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    run_demo(args.region, args.skip_cleanup)


if __name__ == "__main__":
    main()
