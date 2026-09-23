"""
AgentCore Browser Tool — OS-Level Actions (InvokeBrowser API).

Demonstrates the InvokeBrowser REST API which lets clients send raw OS-level
input events — mouse clicks/moves/drags, scroll, keyboard typing/shortcuts, and
screenshots — directly to the browser sandbox VM, bypassing the CDP/Playwright
automation layer entirely.

Use OS-level actions to interact with:
  - OS-native dialogs (file upload, print, authentication pop-ups)
  - Browser chrome elements (address bar, extension popups)
  - Keyboard shortcuts (Ctrl+S, Ctrl+P, Alt+Tab)
  - Canvas / WebGL content without DOM selectors

The script exercises the full action surface:
  1. Mouse: click (left/right/middle/double), move, drag
  2. Scroll: vertical, horizontal, combined
  3. Keyboard: type text, press keys (Enter/Tab/Escape/Backspace/arrows), shortcuts
  4. Screenshot: PNG capture

All requests are signed with SigV4 using the bedrock-agentcore service name.

Usage:
    python os_actions.py [--region REGION] [--skip-cleanup]

Prerequisites:
    pip install -r ../requirements.txt
    AWS credentials configured (aws sts get-caller-identity)

IAM permissions required:
    bedrock-agentcore:InvokeBrowser
    bedrock-agentcore:StartBrowserSession
    bedrock-agentcore:StopBrowserSession
    bedrock-agentcore:CreateBrowser / DeleteBrowser
    iam:CreateRole / ...
"""

import argparse
import base64
import json
import time

import boto3
import requests
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.session import Session as BotocoreSession

# ── Configuration ─────────────────────────────────────────────────────────────

SERVICE = "bedrock-agentcore"
SESSION_HEADER = "x-amzn-browser-session-id"
BROWSER_NAME = "browser_with_os_actions"


# ── SigV4 helpers ──────────────────────────────────────────────────────────────


def get_credentials(region: str):
    session = BotocoreSession()
    return session.get_credentials().get_frozen_credentials(), region


def signed_request(method: str, url: str, *, headers=None, body=None, region: str, credentials):
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    data = json.dumps(body) if body else ""
    req = AWSRequest(method=method, url=url, headers=hdrs, data=data)
    SigV4Auth(credentials, SERVICE, region).add_auth(req)
    return requests.request(method, url, headers=dict(req.headers), data=data)


def invoke(base_url: str, session_id: str, action: dict, *, region: str, credentials, browser_id: str):
    url = f"{base_url}/browsers/{browser_id}/sessions/invoke"
    hdrs = {SESSION_HEADER: session_id}
    return signed_request(
        "POST",
        url,
        headers=hdrs,
        body={"action": action},
        region=region,
        credentials=credentials,
    )


def start_session(base_url: str, browser_id: str, *, region: str, credentials) -> str:
    url = f"{base_url}/browsers/{browser_id}/sessions/start"
    body = {
        "name": "os-actions-demo",
        "sessionTimeoutSeconds": 3600,
        "viewPort": {"width": 1920, "height": 1080},
    }
    resp = signed_request("PUT", url, body=body, region=region, credentials=credentials)
    resp.raise_for_status()
    data = resp.json()
    print(f"Session started: {data['sessionId']}")
    return data["sessionId"]


def stop_session(base_url: str, session_id: str, browser_id: str, *, region: str, credentials):
    url = f"{base_url}/browsers/{browser_id}/sessions/stop?sessionId={session_id}"
    resp = signed_request("PUT", url, body="", region=region, credentials=credentials)
    print(f"Session stop status: {resp.status_code}")


# ── IAM helpers ────────────────────────────────────────────────────────────────


def create_execution_role(role_name: str, region: str) -> str:
    iam = boto3.client("iam")
    account_id = boto3.client("sts").get_caller_identity()["Account"]

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": ["bedrock-agentcore.amazonaws.com"]},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }

    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowAgentToUseBrowser",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:InvokeBrowser",
                    "bedrock-agentcore:StartBrowserSession",
                    "bedrock-agentcore:StopBrowserSession",
                ],
                "Resource": [f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"],
            }
        ],
    }

    try:
        try:
            role = iam.get_role(RoleName=role_name)
            print(f"Reusing existing IAM role: {role['Role']['Arn']}")
            return role["Role"]["Arn"]
        except iam.exceptions.NoSuchEntityException:
            pass

        role_resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
        )
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="BrowserOSActPolicy",
            PolicyDocument=json.dumps(policy_document),
        )
        print(f"Created IAM role: {role_resp['Role']['Arn']}")
        print("Waiting 10 seconds for IAM propagation...")
        time.sleep(10)
        return role_resp["Role"]["Arn"]
    except Exception as e:
        print(f"Error creating IAM role: {e}")
        raise


def delete_execution_role(role_name: str) -> None:
    iam = boto3.client("iam")
    account_id = boto3.client("sts").get_caller_identity()["Account"]
    policy_arn = f"arn:aws:iam::{account_id}:policy/BrowserOSActPolicy"
    try:
        iam.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
    except Exception as e:  # noqa: BLE001
        print(f"Could not detach role policy: {e}")
    try:
        iam.delete_role_policy(RoleName=role_name, PolicyName="BrowserOSActPolicy")
    except Exception as e:  # noqa: BLE001
        print(f"Could not delete inline policy: {e}")
    try:
        iam.delete_role(RoleName=role_name)
        print(f"Deleted IAM role: {role_name}")
    except Exception as e:  # noqa: BLE001
        print(f"Could not delete role: {e}")


# ── Demo ───────────────────────────────────────────────────────────────────────


def check_screenshot(resp) -> None:
    data = resp.json().get("result", {}).get("screenshot", {}).get("data")
    if not data:
        print("  [screenshot] no data returned")
        return
    img_bytes = base64.b64decode(data)
    out_path = "screenshot.png"
    with open(out_path, "wb") as f:
        f.write(img_bytes)
    print(f"  [screenshot] saved to {out_path} ({len(img_bytes)} bytes)")


def run_os_actions(base_url: str, session_id: str, browser_id: str, region: str, credentials) -> None:
    def act(action: dict, label: str) -> None:
        r = invoke(base_url, session_id, action, region=region, credentials=credentials, browser_id=browser_id)
        status_label = "OK" if r.status_code == 200 else f"HTTP {r.status_code}"
        print(f"  [{status_label}] {label}")
        return r

    print("\n── Mouse Actions ──────────────────────────────────────")
    act({"mouseClick": {"x": 600, "y": 370, "button": "LEFT"}}, "left click")
    act({"mouseClick": {"x": 500, "y": 300, "button": "LEFT", "clickCount": 2}}, "double click")
    act({"mouseClick": {"x": 200, "y": 400, "button": "RIGHT", "clickCount": 1}}, "right click")
    act({"mouseClick": {"x": 960, "y": 540, "button": "MIDDLE", "clickCount": 1}}, "middle click")
    act({"mouseMove": {"x": 800, "y": 600}}, "mouse move to (800, 600)")
    act({"mouseMove": {"x": 1, "y": 1}}, "mouse move to (1, 1)")
    act({"mouseDrag": {"startX": 1, "startY": 1, "endX": 705, "endY": 180, "button": "LEFT"}}, "drag left")
    act({"mouseDrag": {"startX": 500, "startY": 300, "endX": 100, "endY": 200, "button": "MIDDLE"}}, "drag middle")

    print("\n── Scroll Actions ─────────────────────────────────────")
    act({"mouseScroll": {"x": 800, "y": 600, "deltaX": 0, "deltaY": -500}}, "scroll up")
    act({"mouseScroll": {"x": 500, "y": 300, "deltaX": 300, "deltaY": 0}}, "scroll right")
    act({"mouseScroll": {"x": 500, "y": 300, "deltaX": -100, "deltaY": -200}}, "scroll up-left")
    act({"mouseScroll": {"x": 500, "y": 300, "deltaX": 1000, "deltaY": 1000}}, "scroll down-right (large)")

    print("\n── Keyboard Actions ───────────────────────────────────")
    act({"keyType": {"text": "Hello World"}}, "type: Hello World")
    act({"keyType": {"text": "user@example.com!#$%^&*()"}}, "type: special chars")
    act({"keyType": {"text": "https://www.example.com"}}, "type: URL")
    act({"keyPress": {"key": "enter"}}, "press: Enter")
    act({"keyPress": {"key": "tab"}}, "press: Tab")
    act({"keyPress": {"key": "escape"}}, "press: Escape")
    act({"keyPress": {"key": "backspace", "presses": 5}}, "press: Backspace x5")
    act({"keyPress": {"key": "ArrowDown", "presses": 10}}, "press: ArrowDown x10")
    act({"keyShortcut": {"keys": ["ctrl", "s"]}}, "shortcut: Ctrl+S")
    act({"keyShortcut": {"keys": ["ctrl", "p"]}}, "shortcut: Ctrl+P")
    act({"keyShortcut": {"keys": ["ctrl", "shift", "i"]}}, "shortcut: Ctrl+Shift+I")

    print("\n── Screenshot ─────────────────────────────────────────")
    r = act({"screenshot": {"format": "PNG"}}, "screenshot PNG")
    check_screenshot(r)
    r = act({"screenshot": {}}, "screenshot (default format)")
    check_screenshot(r)


def parse_args():
    parser = argparse.ArgumentParser(description="AgentCore Browser OS-level actions (InvokeBrowser) demo")
    parser.add_argument("--region", default=boto3.Session().region_name or "us-west-2")
    parser.add_argument("--skip-cleanup", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    region = args.region
    role_name = "BrowserOSActAgentCoreRole"

    print("=" * 60)
    print("AgentCore Browser — OS-Level Actions Demo")
    print("=" * 60)

    # IAM role
    execution_role_arn = create_execution_role(role_name, region)

    # Create browser
    cp_client = boto3.client("bedrock-agentcore-control", region_name=region)
    created_browser = cp_client.create_browser(
        name=BROWSER_NAME,
        executionRoleArn=execution_role_arn,
        networkConfiguration={"networkMode": "PUBLIC"},
    )
    browser_id = created_browser["browserId"]
    print(f"Created browser: {browser_id}")

    # Setup endpoint and credentials
    base_url = f"https://bedrock-agentcore.{region}.amazonaws.com"
    credentials, _ = get_credentials(region)

    # Start session
    session_id = start_session(base_url, browser_id, region=region, credentials=credentials)
    print("Waiting 3 seconds for session to initialize...")
    time.sleep(3)

    # Run OS-level actions
    run_os_actions(base_url, session_id, browser_id, region, credentials)

    # Stop session
    stop_session(base_url, session_id, browser_id, region=region, credentials=credentials)

    # Cleanup
    if not args.skip_cleanup:
        print("\nCleaning up...")
        try:
            cp_client.delete_browser(browserId=browser_id)
            print(f"Deleted browser: {browser_id}")
        except Exception as e:  # noqa: BLE001
            print(f"Could not delete browser: {e}")
        delete_execution_role(role_name)
    else:
        print(f"\n--skip-cleanup: browser={browser_id}, role={role_name}")

    print("\nDemo complete.")


if __name__ == "__main__":
    main()
