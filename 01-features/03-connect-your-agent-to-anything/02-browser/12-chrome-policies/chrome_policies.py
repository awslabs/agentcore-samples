"""
AgentCore Browser Tool — Chrome Enterprise Policies and Custom Root CAs.

Demonstrates two complementary browser security features:

Part 1 — Chrome Enterprise Policies:
  - Upload a Chrome policy JSON to S3 that blocks all URLs except docs.aws.amazon.com
  - Create a custom AgentCore Browser with the policy enforced at the MANAGED level
  - Use Playwright to verify: allowed URL loads, blocked URL is rejected by Chrome
  - Optionally run a Strands agent against the restricted browser

Part 2 — Custom Root CA Certificates:
  - Store the BadSSL "untrusted root CA" certificate in Secrets Manager
  - Code Interpreter WITHOUT root CA → SSLCertVerificationError (expected)
  - Code Interpreter WITH root CA (Certificate.from_secret_arn) → HTTP 200 (success)

Usage:
    python chrome_policies.py [--region REGION] [--skip-cleanup] [--skip-root-ca]

Prerequisites:
    pip install -r ../requirements.txt
    playwright install chromium
    AWS credentials configured (aws sts get-caller-identity)

IAM permissions required:
    bedrock-agentcore:StartBrowserSession / StopBrowserSession / ConnectBrowserAutomationStream
    bedrock-agentcore:CreateBrowser / DeleteBrowser
    bedrock-agentcore:CreateCodeInterpreter / DeleteCodeInterpreter / InvokeCodeInterpreter
    s3:PutObject / GetObject / GetObjectVersion / ListBucket / ...
    secretsmanager:CreateSecret / DescribeSecret / DeleteSecret
    iam:CreateRole / ...

Notes:
  - Do NOT set DeveloperToolsAvailability=2 in policies — it disables CDP and breaks all
    Playwright automation silently. Use 0 (allowed) or 1 (extensions only).
  - MANAGED policies are enforced at the browser level and cannot be overridden per-session.
  - RECOMMENDED policies are set at session level and can be overridden by MANAGED policies.
"""

import argparse
import asyncio
import json
import time

import boto3
from botocore.exceptions import ClientError

# ── Configuration ─────────────────────────────────────────────────────────────

BUCKET_NAME_TPL = "ac-browser-policy-demo-{account_id}-{region}"
AC_ROLE_NAME = "ac-browser-policy-execution-role"
BROWSER_NAME = "docs_research_browser"
POLICY_KEY = "browser-policies/docs-only-policy.json"
SECRET_NAME = "demo-badssl-untrusted-root-ca"  # pragma: allowlist secret

# BadSSL untrusted root CA — public test certificate for SSL demo only
BADSSL_ROOT_CA = """-----BEGIN CERTIFICATE-----
MIIGfjCCBGagAwIBAgIJAJeg/PrX5Sj9MA0GCSqGSIb3DQEBCwUAMIGBMQswCQYD
VQQGEwJVUzETMBEGA1UECAwKQ2FsaWZvcm5pYTEWMBQGA1UEBwwNU2FuIEZyYW5j
aXNjbzEPMA0GA1UECgwGQmFkU1NMMTQwMgYDVQQDDCtCYWRTU0wgVW50cnVzdGVk
IFJvb3QgQ2VydGlmaWNhdGUgQXV0aG9yaXR5MB4XDTE2MDcwNzA2MzEzNVoXDTM2
MDcwMjA2MzEzNVowgYExCzAJBgNVBAYTAlVTMRMwEQYDVQQIDApDYWxpZm9ybmlh
MRYwFAYDVQQHDA1TYW4gRnJhbmNpc2NvMQ8wDQYDVQQKDAZCYWRTU0wxNDAyBgNV
BAMMK0JhZFNTTCBVbnRydXN0ZWQgUm9vdCBDZXJ0aWZpY2F0ZSBBdXRob3JpdHkw
ggIiMA0GCSqGSIb3DQEBAQUAA4ICDwAwggIKAoICAQDKQtPMhEH073gis/HISWAi
bOEpCtOsatA3JmeVbaWal8O/5ZO5GAn9dFVsGn0CXAHR6eUKYDAFJLa/3AhjBvWa
tnQLoXaYlCvBjodjLEaFi8ckcJHrAYG9qZqioRQ16Yr8wUTkbgZf+er/Z55zi1yn
CnhWth7kekvrwVDGP1rApeLqbhYCSLeZf5W/zsjLlvJni9OrU7U3a9msvz8mcCOX
fJX9e3VbkD/uonIbK2SvmAGMaOj/1k0dASkZtMws0Bk7m1pTQL+qXDM/h3BQZJa5
DwTcATaa/Qnk6YHbj/MaS5nzCSmR0Xmvs/3CulQYiZJ3kypns1KdqlGuwkfiCCgD
yWJy7NE9qdj6xxLdqzne2DCyuPrjFPS0mmYimpykgbPnirEPBF1LW3GJc9yfhVXE
Cc8OY8lWzxazDNNbeSRDpAGbBeGSQXGjAbliFJxwLyGzZ+cG+G8lc+zSvWjQu4Xp
GJ+dOREhQhl+9U8oyPX34gfKo63muSgo539hGylqgQyzj+SX8OgK1FXXb2LS1gxt
VIR5Qc4MmiEG2LKwPwfU8Yi+t5TYjGh8gaFv6NnksoX4hU42gP5KvjYggDpR+NSN
CGQSWHfZASAYDpxjrOo+rk4xnO+sbuuMk7gORsrl+jgRT8F2VqoR9Z3CEdQxcCjR
5FsfTymZCk3GfIbWKkaeLQIDAQABo4H2MIHzMB0GA1UdDgQWBBRvx4NzSbWnY/91
3m1u/u37l6MsADCBtgYDVR0jBIGuMIGrgBRvx4NzSbWnY/913m1u/u37l6MsAKGB
h6SBhDCBgTELMAkGA1UEBhMCVVMxEzARBgNVBAgMCkNhbGlmb3JuaWExFjAUBgNV
BAcMDVNhbiBGcmFuY2lzY28xDzANBgNVBAoMBkJhZFNTTDE0MDIGA1UEAwwrQmFk
U1NMIFVudHJ1c3RlZCBSb290IENlcnRpZmljYXRlIEF1dGhvcml0eYIJAJeg/PrX
5Sj9MAwGA1UdEwQFMAMBAf8wCwYDVR0PBAQDAgEGMA0GCSqGSIb3DQEBCwUAA4IC
AQBQU9U8+jTRT6H9AIFm6y50tXTg/ySxRNmeP1Ey9Zf4jUE6yr3Q8xBv9gTFLiY1
qW2qfkDSmXVdBkl/OU3+xb5QOG5hW7wVolWQyKREV5EvUZXZxoH7LVEMdkCsRJDK
wYEKnEErFls5WPXY3bOglBOQqAIiuLQ0f77a2HXULDdQTn5SueW/vrA4RJEKuWxU
iD9XPnVZ9tPtky2Du7wcL9qhgTddpS/NgAuLO4PXh2TQ0EMCll5reZ5AEr0NSLDF
c/koDv/EZqB7VYhcPzr1bhQgbv1dl9NZU0dWKIMkRE/T7vZ97I3aPZqIapC2ulrf
KrlqjXidwrGFg8xbiGYQHPx3tHPZxoM5WG2voI6G3s1/iD+B4V6lUEvivd3f6tq7
d1V/3q1sL5DNv7TvaKGsq8g5un0TAkqaewJQ5fXLigF/yYu5a24/GUD783MdAPFv
gWz8F81evOyRfpf9CAqIswMF+T6Dwv3aw5L9hSniMrblkg+ai0K22JfoBcGOzMtB
Ke/Ps2Za56dTRoY/a4r62hrcGxufXd0mTdPaJLw3sJeHYjLxVAYWQq4QKJQWDgTS
dAEWyN2WXaBFPx5c8KIW95Eu8ShWE00VVC3oA4emoZ2nrzBXLrUScifY6VaYYkkR
2O2tSqU8Ri3XRdgpNPDWp8ZL49KhYGYo3R/k98gnMHiY5g==
-----END CERTIFICATE-----"""


# ── IAM role ───────────────────────────────────────────────────────────────────


def create_execution_role(role_name: str, bucket_name: str) -> str:
    iam = boto3.client("iam")

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {},
            }
        ],
    }

    s3_policy = {
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
            Description="Execution role for AgentCore Browser chrome policy demo",
        )
        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="ac_browser_s3_policy",
            PolicyDocument=json.dumps(s3_policy),
        )
        print(f"Created IAM role: {role['Role']['Arn']}")
        print("Waiting 10 seconds for IAM propagation...")
        time.sleep(10)
        return role["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
            print(f"Reusing existing IAM role: {arn}")
            return arn
        raise


# ── Part 1: Chrome Enterprise Policies ────────────────────────────────────────


def create_chrome_policy(s3_client, bucket_name: str) -> None:
    """Upload a Chrome policy that blocks all URLs except docs.aws.amazon.com."""
    policy = {
        "URLBlocklist": ["*"],
        "URLAllowlist": [
            "docs.aws.amazon.com",
            ".aws.amazon.com",
            ".amazonaws.com",
        ],
        "PasswordManagerEnabled": False,
        "DownloadRestrictions": 3,
        # NOTE: Do not set DeveloperToolsAvailability=2; it disables CDP silently.
        "DeveloperToolsAvailability": 0,
        "BookmarkBarEnabled": False,
        "AutofillAddressEnabled": False,
        "AutofillCreditCardEnabled": False,
    }
    s3_client.put_object(
        Bucket=bucket_name,
        Key=POLICY_KEY,
        Body=json.dumps(policy, indent=2),
        ContentType="application/json",
    )
    print(f"Chrome policy uploaded to s3://{bucket_name}/{POLICY_KEY}")
    print(json.dumps(policy, indent=2))


def wait_for_browser_ready(client, browser_id: str) -> None:
    print("Waiting for browser to reach READY status...")
    while True:
        info = client.get_browser(browserId=browser_id)
        status = info["status"]
        if status == "READY":
            print(f"Browser READY: {browser_id}")
            return
        elif status == "CREATE_FAILED":
            raise RuntimeError(f"Browser creation failed: {info.get('failureReason')}")
        print(f"  Status: {status} — waiting...")
        time.sleep(5)


async def test_policy_enforcement(ws_url: str, headers: dict) -> str:
    """Verify allowed URL loads and blocked URL is rejected by Chrome."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(ws_url, headers=headers, timeout=60000)
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else await context.new_page()

        # Test 1: ALLOWED
        print("\n" + "=" * 60)
        print("TEST 1: Navigate to docs.aws.amazon.com (ALLOWED)")
        print("=" * 60)
        await page.goto(
            "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html",
            wait_until="domcontentloaded",
            timeout=60000,
        )
        await asyncio.sleep(3)
        title = await page.title()
        print(f"Page title: {title}")
        scripts = await page.evaluate(
            "() => { document.querySelectorAll('script,style,noscript').forEach(s=>s.remove()); return document.body.innerText; }"
        )
        print(f"Extracted {len(scripts)} chars")

        # Test 2: BLOCKED
        print("\n" + "=" * 60)
        print("TEST 2: Navigate to www.wikipedia.org (BLOCKED)")
        print("=" * 60)
        try:
            await page.goto("https://www.wikipedia.org", wait_until="domcontentloaded", timeout=15000)
            content = await page.evaluate("() => document.documentElement.outerHTML")
            if "blocked" in content.lower() or "ERR_BLOCKED" in content:
                print("Result: CHROME POLICY BLOCKED THIS URL")
            else:
                print(f"Result: page loaded (unexpected) — title: {await page.title()}")
        except Exception:
            print("Result: CHROME POLICY BLOCKED THIS URL")

        await browser.close()
        return title


def run_part1_chrome_policies(region: str, bucket_name: str, execution_role_arn: str) -> str:
    """Create browser with managed Chrome policy, run Playwright tests. Returns browser_id."""
    from bedrock_agentcore.tools import BrowserClient

    s3_client = boto3.client("s3", region_name=region)
    create_chrome_policy(s3_client, bucket_name)

    client = BrowserClient(region)

    try:
        response = client.create_browser(
            name=BROWSER_NAME,
            execution_role_arn=execution_role_arn,
            network_configuration={"networkMode": "PUBLIC"},
            enterprise_policies=[
                {
                    "location": {"s3": {"bucket": bucket_name, "prefix": POLICY_KEY}},
                    "type": "MANAGED",
                }
            ],
            recording={
                "enabled": True,
                "s3Location": {"bucket": bucket_name, "prefix": "policy-demo"},
            },
            description="Browser restricted to AWS docs with Chrome enterprise policies",
        )
        browser_id = response["browserId"]
        print(f"Created browser: {browser_id}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConflictException":
            print(f"Browser '{BROWSER_NAME}' already exists — reusing.")
            browsers = client.list_browsers(browser_type="CUSTOM")
            browser_id = next(
                (b["browserId"] for b in browsers.get("browserSummaries", []) if b.get("name") == BROWSER_NAME),
                None,
            )
            if not browser_id:
                raise RuntimeError(f"Could not find browser '{BROWSER_NAME}'")
        else:
            raise

    wait_for_browser_ready(client.control_plane_client, browser_id)

    client.start(identifier=browser_id, session_timeout_seconds=3600)
    for _ in range(30):
        info = client.get_session()
        if info.get("status") == "READY":
            break
        time.sleep(5)

    ws_url, headers = client.generate_ws_headers()
    asyncio.run(test_policy_enforcement(ws_url, headers))
    client.stop()

    print(
        "\nTo review the session recording:"
        "\n  AWS Console → Bedrock AgentCore → Built-in Tools → docs_research_browser"
        "\n  → Browser sessions → View Recording"
    )
    return browser_id


# ── Part 2: Custom Root CA Certificates ───────────────────────────────────────


def store_root_ca_secret(sm_client) -> str:
    try:
        sm_client.create_secret(
            Name=SECRET_NAME,
            SecretString=BADSSL_ROOT_CA,
            Description="BadSSL untrusted root CA for demo purposes",
        )
        print(f"Created Secrets Manager secret: {SECRET_NAME}")
    except sm_client.exceptions.ResourceExistsException:
        print(f"Secret already exists: {SECRET_NAME}")
    return sm_client.describe_secret(SecretId=SECRET_NAME)["ARN"]


TEST_CODE_NO_CA = (
    "import urllib.request\n"
    "try:\n"
    "    response = urllib.request.urlopen('https://untrusted-root.badssl.com')\n"
    "    print(f'Status: {response.status}')\n"
    "except Exception as e:\n"
    "    print(f'Error: {type(e).__name__}')\n"
    "    print('Connection failed — root CA not trusted.')\n"
)

TEST_CODE_WITH_CA = (
    "import urllib.request\n"
    "response = urllib.request.urlopen('https://untrusted-root.badssl.com')\n"
    "print(f'Status: {response.status}')\n"
    "print(response.read().decode('utf-8')[:200])\n"
)


def run_part2_root_ca(region: str, execution_role_arn: str, skip_root_ca: bool) -> tuple:
    """Run Code Interpreter with and without root CA. Returns (interpreter_id, secret_arn)."""
    if skip_root_ca:
        print("\n[Part 2] Skipped (--skip-root-ca)")
        return None, None

    from bedrock_agentcore.tools import Certificate, CodeInterpreter

    sm_client = boto3.client("secretsmanager", region_name=region)
    secret_arn = store_root_ca_secret(sm_client)

    # Without root CA
    print("\n" + "=" * 60)
    print("Part 2a — Code Interpreter WITHOUT root CA (expect SSL error)")
    print("=" * 60)
    ci_no_ca = CodeInterpreter(region)
    ci_no_ca.start()
    result = ci_no_ca.invoke("executeCode", {"code": TEST_CODE_NO_CA, "language": "python"})
    for event in result.get("stream", []):
        if "result" in event:
            stdout = event["result"].get("structuredContent", {}).get("stdout", "")
            print(f"Output: {stdout}")
    ci_no_ca.stop()

    # With root CA
    print("\n" + "=" * 60)
    print("Part 2b — Code Interpreter WITH root CA (expect HTTP 200)")
    print("=" * 60)
    ci_with_ca = CodeInterpreter(region)
    response = ci_with_ca.create_code_interpreter(
        name="demo_rootca_interpreter",
        execution_role_arn=execution_role_arn,
        network_configuration={"networkMode": "PUBLIC"},
        certificates=[Certificate.from_secret_arn(secret_arn)],
        description="Code interpreter trusting BadSSL untrusted root CA",
    )
    interpreter_id = response["codeInterpreterId"]
    print(f"Created interpreter: {interpreter_id}")

    # Wait for READY
    print("Waiting for interpreter to become ready...")
    while True:
        info = ci_with_ca.get_code_interpreter(interpreter_id)
        if info["status"] == "READY":
            print("Interpreter READY")
            break
        elif info["status"] == "CREATE_FAILED":
            raise RuntimeError(f"Interpreter failed: {info.get('failureReason')}")
        time.sleep(3)

    ci_with_ca.start(identifier=interpreter_id)
    result = ci_with_ca.invoke("executeCode", {"code": TEST_CODE_WITH_CA, "language": "python"})
    for event in result.get("stream", []):
        if "result" in event:
            content = event["result"]
            stdout = content.get("structuredContent", {}).get("stdout", "")
            exit_code = content.get("structuredContent", {}).get("exitCode", -1)
            if exit_code == 0 and "200" in stdout:
                print("Result: SUCCESS — HTTP 200 (root CA trusted)")
                print(f"Output: {stdout[:300]}")
            else:
                print(f"Unexpected result (exit {exit_code}): {stdout[:300]}")
    ci_with_ca.stop()

    return interpreter_id, secret_arn


# ── Cleanup ────────────────────────────────────────────────────────────────────


def cleanup(
    region: str,
    bucket_name: str,
    browser_id: str,
    interpreter_id: str | None,
    secret_arn: str | None,
) -> None:
    from bedrock_agentcore.tools import BrowserClient, CodeInterpreter

    sm_client = boto3.client("secretsmanager", region_name=region)
    iam = boto3.client("iam")
    s3_client = boto3.client("s3", region_name=region)

    # Delete browser
    if browser_id:
        try:
            client = BrowserClient(region)
            client.delete_browser(browser_id)
            print(f"Deleted browser: {browser_id}")
        except Exception as e:
            print(f"Could not delete browser: {e}")

    # Delete interpreter
    if interpreter_id:
        try:
            ci = CodeInterpreter(region)
            ci.delete_code_interpreter(interpreter_id)
            print(f"Deleted interpreter: {interpreter_id}")
        except Exception as e:
            print(f"Could not delete interpreter: {e}")

    # Delete secret
    if secret_arn:
        try:
            sm_client.delete_secret(SecretId=SECRET_NAME, ForceDeleteWithoutRecovery=True)
            print(f"Deleted secret: {SECRET_NAME}")
        except Exception as e:
            print(f"Could not delete secret: {e}")

    # Delete S3 policy file
    try:
        s3_client.delete_object(Bucket=bucket_name, Key=POLICY_KEY)
        print(f"Deleted S3 policy: s3://{bucket_name}/{POLICY_KEY}")
    except Exception as e:
        print(f"Could not delete S3 policy: {e}")

    # Delete IAM role
    try:
        iam.delete_role_policy(RoleName=AC_ROLE_NAME, PolicyName="ac_browser_s3_policy")
        iam.delete_role(RoleName=AC_ROLE_NAME)
        print(f"Deleted IAM role: {AC_ROLE_NAME}")
    except Exception as e:
        print(f"Could not delete IAM role: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="AgentCore Browser Chrome enterprise policies and custom root CA demo")
    parser.add_argument("--region", default=boto3.Session().region_name or "us-west-2")
    parser.add_argument("--skip-cleanup", action="store_true")
    parser.add_argument("--skip-root-ca", action="store_true", help="Skip Part 2 (root CA demo)")
    return parser.parse_args()


def main():
    args = parse_args()
    region = args.region

    account_id = boto3.client("sts").get_caller_identity()["Account"]
    bucket_name = BUCKET_NAME_TPL.format(account_id=account_id, region=region)
    s3_client = boto3.client("s3", region_name=region)

    print("=" * 60)
    print("AgentCore Browser — Chrome Policies + Custom Root CA Demo")
    print("=" * 60)
    print(f"Account: {account_id}  Region: {region}  Bucket: {bucket_name}")

    # Create S3 bucket
    try:
        s3_client.head_bucket(Bucket=bucket_name)
        print(f"Bucket {bucket_name} already exists")
    except ClientError:
        params = {"Bucket": bucket_name}
        if region != "us-east-1":
            params["CreateBucketConfiguration"] = {"LocationConstraint": region}
        s3_client.create_bucket(**params)
        print(f"Created bucket: {bucket_name}")

    # IAM role
    execution_role_arn = create_execution_role(AC_ROLE_NAME, bucket_name)

    # Part 1
    browser_id = run_part1_chrome_policies(region, bucket_name, execution_role_arn)

    # Part 2
    interpreter_id, secret_arn = run_part2_root_ca(region, execution_role_arn, args.skip_root_ca)

    # Cleanup
    if not args.skip_cleanup:
        print("\nCleaning up all resources...")
        cleanup(region, bucket_name, browser_id, interpreter_id, secret_arn)
    else:
        print(f"\n--skip-cleanup: browser={browser_id}, interpreter={interpreter_id}")

    print("\nDemo complete.")


if __name__ == "__main__":
    main()
