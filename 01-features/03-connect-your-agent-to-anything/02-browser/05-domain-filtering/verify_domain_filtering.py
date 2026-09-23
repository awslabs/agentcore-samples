import os
import asyncio
import boto3
from urllib.parse import urlparse

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest


BROWSER_ID = os.getenv("BROWSER_ID")
if not BROWSER_ID:
    raise ValueError(
        "BROWSER_ID environment variable not set.\n"
        "Export it from CloudFormation outputs:\n"
        "  export BROWSER_ID=$(aws cloudformation describe-stacks "
        "--stack-name agentcore-browser-firewall "
        "--query 'Stacks[0].Outputs[?OutputKey==`BrowserToolCustomOutput`].OutputValue' "
        "--output text)"
    )

session = boto3.Session()
REGION = session.region_name
browser_session = boto3.client("bedrock-agentcore")


def get_url_and_session():
    response = browser_session.start_browser_session(browserIdentifier=BROWSER_ID)
    session_id = response.get("sessionId")
    ws_url = (
        f"wss://bedrock-agentcore.{REGION}.amazonaws.com/browser-streams/{BROWSER_ID}/sessions/{session_id}/automation"
    )
    return ws_url, session_id


def stop_session(session_id):
    response = browser_session.stop_browser_session(browserIdentifier=BROWSER_ID, sessionId=session_id)
    return response


def get_signed_headers(ws_url):
    """Get SigV4 signed headers for WebSocket connection."""
    credentials = session.get_credentials()
    https_url = ws_url.replace("wss://", "https://")
    parsed = urlparse(https_url)

    request = AWSRequest(method="GET", url=https_url, headers={"host": parsed.netloc})
    SigV4Auth(credentials, "bedrock-agentcore", REGION).add_auth(request)
    return {k: v for k, v in request.headers.items()}


async def main(ws_url, session_id):
    from playwright.async_api import async_playwright

    headers = get_signed_headers(ws_url)

    # Test cases: (url, category, should_allow)
    # URLs must match the AllowedDomains/DeniedDomains in the CloudFormation template
    tests = [
        ("https://example.com", "ALLOWLIST", True),
        ("https://github.com", "ALLOWLIST", True),
        ("https://wikipedia.org", "ALLOWLIST", True),
        ("https://facebook.com", "DENYLIST", False),
        ("https://twitter.com", "DENYLIST", False),
        ("https://randomsite12345.com", "UNLISTED", False),
    ]

    async with async_playwright() as p:
        print("Connecting to browser...")
        browser = await p.chromium.connect_over_cdp(ws_url, headers=headers)
        page = browser.contexts[0].pages[0] if browser.contexts else await browser.new_context().new_page()

        print("=" * 60)
        print("FIREWALL TEST RESULTS")
        print("=" * 60)

        results = []

        for url, category, should_allow in tests:
            try:
                response = await page.goto(url, timeout=10000, wait_until="domcontentloaded")
                allowed = response is not None and response.status < 400
                passed = allowed == should_allow
                status_str = f"HTTP {response.status}" if response else "No response"
                result = "PASS" if passed else "FAIL"
                print(f"{result}: {url} ({category}) - {'Allowed' if allowed else 'Blocked'} [{status_str}]")

                results.append(passed)
            except Exception as e:
                passed = not should_allow
                result = "PASS" if passed else "FAIL"
                print(f"{result}: {url} ({category}) - Blocked ({type(e).__name__})")
                results.append(passed)

        print("=" * 60)
        passed_count = sum(results)
        print(f"Results: {passed_count}/{len(results)} tests passed")

        await browser.close()


if __name__ == "__main__":
    ws_url, session_id = get_url_and_session()
    try:
        asyncio.run(main(ws_url, session_id))
    finally:
        stop_session(session_id)
        print(f"Session {session_id} stopped")
