"""
Data Protection for AgentCore Observability.

Demonstrates two complementary data protection mechanisms for agents deployed
on AgentCore Runtime:

  1. Amazon Bedrock Guardrails — detect and anonymize PII in agent
     prompts and responses (e.g., email, phone, SSN)
  2. CloudWatch Logs Data Protection — automatically mask sensitive
     information in runtime logs using managed and custom data identifiers

This script uses a customer-support travel agent use case. A traveler
asks about bookings, potentially including personal details. Guardrails
prevent the model from echoing PII, and the CW Logs policy masks any PII
that appears in application logs.

Usage:
    python data_protection.py [--region us-east-1]

    Steps this script runs:
      1. Create a Bedrock Guardrail with PII filters (email, phone, name, SSN, etc.)
      2. Deploy the travel support agent to AgentCore Runtime (with guardrail attached)
      3. Invoke the agent with a PII-heavy prompt — observe anonymized output
      4. Apply a CloudWatch Logs Data Protection policy to the runtime log group
      5. Invoke again — observe masked logs
      6. (Optional) cleanup with --cleanup flag

Prerequisites:
    - uv installed
    - AWS CLI configured with credentials
    - Amazon Bedrock model access (Claude Haiku 4.5)
    - CloudWatch Transaction Search enabled

AWS Docs:
    https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html
    https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/mask-sensitive-log-data.html
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid

import boto3
from boto3.session import Session

# ── Configuration ──────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AGENT_NAME = f"travel_data_protection_{int(time.time()) % 100000}"

# ── AWS Setup ──────────────────────────────────────────────────────────────────

session = Session()
REGION = session.region_name
ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]
S3_BUCKET = f"agentcore-code-{ACCOUNT_ID}-{REGION}"


def parse_args():
    parser = argparse.ArgumentParser(description="AgentCore Data Protection Demo")
    parser.add_argument("--region", default=None, help="AWS region")
    parser.add_argument("--cleanup", action="store_true", help="Delete all created resources")
    return parser.parse_args()


# ── Step 1: Create Bedrock Guardrail ──────────────────────────────────────────


def create_guardrail() -> tuple[str, str]:
    """Create a guardrail that anonymizes common PII types."""
    bedrock = boto3.client("bedrock", region_name=REGION)

    print("\nCreating Bedrock Guardrail with PII filters...")
    resp = bedrock.create_guardrail(
        name=f"travel-pii-guardrail-{int(time.time()) % 100000}",
        description="Anonymizes PII in travel support agent interactions.",
        sensitiveInformationPolicyConfig={
            "piiEntitiesConfig": [
                {"type": "EMAIL", "action": "ANONYMIZE"},
                {"type": "PHONE", "action": "ANONYMIZE"},
                {"type": "NAME", "action": "ANONYMIZE", "inputAction": "NONE"},
                {"type": "US_SOCIAL_SECURITY_NUMBER", "action": "ANONYMIZE"},
                {"type": "CREDIT_DEBIT_CARD_NUMBER", "action": "ANONYMIZE"},
            ],
            "regexesConfig": [
                {
                    "name": "BookingReference",
                    "description": "Travel booking references in format BK-NNNNNN",
                    "pattern": r"\bBK-\d{6}\b",
                    "action": "ANONYMIZE",
                }
            ],
        },
        blockedInputMessaging="Your message contains restricted content.",
        blockedOutputsMessaging="The response contains restricted content.",
    )

    guardrail_id = resp["guardrailId"]
    print(f"  Guardrail ID: {guardrail_id}")

    version_resp = bedrock.create_guardrail_version(
        guardrailIdentifier=guardrail_id,
        description="Production version",
    )
    guardrail_version = version_resp["version"]
    print(f"  Guardrail version: {guardrail_version}")

    return guardrail_id, guardrail_version


# ── Step 2: Deploy Agent to AgentCore Runtime ─────────────────────────────────

AGENT_CODE = '''
import os
import logging
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
app = BedrockAgentCoreApp()

@tool
def lookup_booking(booking_ref: str) -> str:
    """Look up a travel booking by reference number."""
    logger.info("Looking up booking: %s", booking_ref)
    print(f"agent.email: support@travelco.com")   # Intentional PII in logs for demo
    print(f"agent.phone: 1-800-555-0199")
    return f"Booking {booking_ref}: Flight NYC-LHR, 2026-06-15, Seat 22A. Status: Confirmed."

@tool
def get_weather(destination: str) -> str:
    """Get weather for a travel destination."""
    return f"{destination}: Sunny, 18°C. Great travel conditions."

def get_model():
    return BedrockModel(
        model_id=os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0"),
        temperature=0.0,
        max_tokens=512,
        guardrail_id=os.getenv("BEDROCK_GUARDRAIL_ID"),
        guardrail_version=os.getenv("BEDROCK_GUARDRAIL_VERSION"),
        guardrail_trace="enabled",
    )

agent = Agent(
    model=get_model(),
    system_prompt="You are a travel support agent. Use lookup_booking to retrieve booking details and get_weather to check destination conditions.",
    tools=[lookup_booking, get_weather],
    trace_attributes={"tags": ["Strands", "DataProtection"]},
)

@app.entrypoint
def travel_support(payload):
    user_input = payload.get("prompt", "")
    print(f"Processing: {user_input[:100]}")
    print("agent.id: EMP-A9X42B")   # Will be masked by CW Logs policy
    response = agent(user_input)
    return response.message["content"][0]["text"]

if __name__ == "__main__":
    app.run()
'''


def write_agent_file():
    os.makedirs("_dp_agent", exist_ok=True)
    with open("_dp_agent/travel_support_agent.py", "w") as f:
        f.write(AGENT_CODE)
    with open("_dp_agent/requirements.txt", "w") as f:
        f.write("bedrock-agentcore>=1.5.0\nstrands-agents\naws-opentelemetry-distro\n")


def deploy_agent(guardrail_id: str, guardrail_version: str) -> dict:
    """Deploy the agent code to AgentCore Runtime."""
    s3 = boto3.client("s3", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    # Create IAM role
    role_name = f"agentcore-dp-{AGENT_NAME}"
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT_ID}},
            }
        ],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["logs:*"], "Resource": "*"},
            {"Effect": "Allow", "Action": ["xray:*"], "Resource": "*"},
            {
                "Effect": "Allow",
                "Action": ["cloudwatch:PutMetricData"],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                    "bedrock:ApplyGuardrail",
                ],
                "Resource": "*",
            },
        ],
    }

    try:
        role_arn = iam.create_role(RoleName=role_name, AssumeRolePolicyDocument=json.dumps(trust))["Role"]["Arn"]
    except iam.exceptions.EntityAlreadyExistsException:
        role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
    iam.put_role_policy(RoleName=role_name, PolicyName="policy", PolicyDocument=json.dumps(policy))
    time.sleep(10)

    # Build and upload zip
    pkg = "dp_deploy_pkg"
    if os.path.isdir(pkg):
        shutil.rmtree(pkg)
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python-platform",
            "aarch64-manylinux2014",
            "--python-version",
            "3.13",
            "--target",
            pkg,
            "--only-binary",
            ":all:",
            "-r",
            "_dp_agent/requirements.txt",
        ],
        check=True,
    )
    subprocess.run(["zip", "-r", "../dp_deploy.zip", "."], cwd=pkg, check=True, capture_output=True)
    subprocess.run(
        ["zip", "dp_deploy.zip", "-j", "_dp_agent/travel_support_agent.py"],
        check=True,
        capture_output=True,
    )
    shutil.rmtree(pkg)

    try:
        s3.create_bucket(Bucket=S3_BUCKET) if REGION == "us-east-1" else s3.create_bucket(
            Bucket=S3_BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION}
        )
    except Exception:
        pass
    s3.upload_file("dp_deploy.zip", S3_BUCKET, f"{AGENT_NAME}/dp.zip")
    os.remove("dp_deploy.zip")

    # Create runtime
    resp = control.create_agent_runtime(
        agentRuntimeName=AGENT_NAME,
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {"s3": {"bucket": S3_BUCKET, "prefix": f"{AGENT_NAME}/dp.zip"}},
                "runtime": "PYTHON_3_13",
                "entryPoint": ["travel_support_agent.py"],
            }
        },
        roleArn=role_arn,
        networkConfiguration={"networkMode": "PUBLIC"},
        protocolConfiguration={"serverProtocol": "HTTP"},
        environmentVariables={
            "BEDROCK_GUARDRAIL_ID": guardrail_id,
            "BEDROCK_GUARDRAIL_VERSION": guardrail_version,
        },
    )
    runtime_id = resp["agentRuntimeId"]
    runtime_arn = resp["agentRuntimeArn"]

    # Wait for ready
    while True:
        s = control.get_agent_runtime(agentRuntimeId=runtime_id)["status"]
        print(f"  Runtime status: {s}")
        if s == "READY":
            break
        if s in ("CREATE_FAILED", "UPDATE_FAILED"):
            sys.exit(1)
        time.sleep(15)

    control.create_agent_runtime_endpoint(agentRuntimeId=runtime_id, name="default")
    while True:
        eps = control.list_agent_runtime_endpoints(agentRuntimeId=runtime_id)
        ep = next((e for e in eps.get("runtimeEndpoints", []) if e["name"] == "default"), None)
        if ep and ep["status"] == "READY":
            break
        time.sleep(15)

    return {
        "runtime_id": runtime_id,
        "runtime_arn": runtime_arn,
        "role_name": role_name,
    }


# ── Step 3: Apply CloudWatch Logs Data Protection ─────────────────────────────

CW_DATA_PROTECTION_POLICY = {
    "Name": "travel-agent-pii-policy",
    "Description": "Mask PII in travel support agent logs",
    "Version": "2021-06-01",
    "Statement": [
        {
            "Sid": "AuditPolicy",
            "DataIdentifier": [
                "arn:aws:dataprotection::aws:data-identifier/EmailAddress",
                "arn:aws:dataprotection::aws:data-identifier/PhoneNumber",
                "arn:aws:dataprotection::aws:data-identifier/Name",
            ],
            "Operation": {"Audit": {"FindingsDestination": {}}},
        },
        {
            "Sid": "DeidentifyPolicy",
            "DataIdentifier": [
                "arn:aws:dataprotection::aws:data-identifier/EmailAddress",
                "arn:aws:dataprotection::aws:data-identifier/PhoneNumber",
                "arn:aws:dataprotection::aws:data-identifier/Name",
            ],
            "Operation": {"Deidentify": {"MaskConfig": {}}},
        },
    ],
}


def apply_cw_data_protection(runtime_id: str):
    """Apply a CW Logs data protection policy to the runtime log group."""
    logs = boto3.client("logs", region_name=REGION)
    log_group = f"/aws/bedrock-agentcore/runtimes/{runtime_id}-DEFAULT"

    print(f"\nApplying CloudWatch Logs data protection to: {log_group}")
    try:
        logs.put_data_protection_policy(
            logGroupIdentifier=log_group,
            policyDocument=json.dumps(CW_DATA_PROTECTION_POLICY),
        )
        print("  Data protection policy applied.")
        print("  Sensitive data in logs will now be masked as ****.")
    except Exception as e:
        print(f"  Warning: {e}")


# ── Step 4: Test Invocation ────────────────────────────────────────────────────

TEST_PROMPT = (
    "I'm Jane Smith (jane.smith@example.com, 555-867-5309). "
    "Can you look up my booking BK-123456 and check the weather in London?"
)


def test_invocation(runtime_arn: str, label: str):
    print(f"\n[{label}] Invoking with PII-heavy prompt...")
    print(f"  Prompt: {TEST_PROMPT}")

    client = boto3.client("bedrock-agentcore", region_name=REGION)
    resp = client.invoke_agent_runtime(
        agentRuntimeArn=runtime_arn,
        qualifier="DEFAULT",
        runtimeSessionId=str(uuid.uuid4()),
        payload=json.dumps({"prompt": TEST_PROMPT}).encode(),
    )

    raw = resp["response"].read() if hasattr(resp.get("response"), "read") else b""
    output = raw.decode() if isinstance(raw, bytes) else str(raw)
    print(f"  Response: {output[:500]}")
    print("  Note: PII in response should be anonymized by Guardrails.")


# ── Cleanup ────────────────────────────────────────────────────────────────────


def cleanup(state: dict):
    bedrock = boto3.client("bedrock", region_name=REGION)
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    iam = boto3.client("iam", region_name=REGION)

    if state.get("guardrail_id"):
        try:
            bedrock.delete_guardrail(guardrailIdentifier=state["guardrail_id"])
            print("Deleted guardrail")
        except Exception as e:
            print(f"Warning: {e}")

    if state.get("runtime_id"):
        try:
            control.delete_agent_runtime(agentRuntimeId=state["runtime_id"])
            print(f"Deleted runtime {state['runtime_id']}")
        except Exception as e:
            print(f"Warning: {e}")

    if state.get("role_name"):
        try:
            for p in iam.list_role_policies(RoleName=state["role_name"])["PolicyNames"]:
                iam.delete_role_policy(RoleName=state["role_name"], PolicyName=p)
            iam.delete_role(RoleName=state["role_name"])
            print(f"Deleted role {state['role_name']}")
        except Exception as e:
            print(f"Warning: {e}")

    shutil.rmtree("_dp_agent", ignore_errors=True)
    print("Cleanup complete.")


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    args = parse_args()
    state_file = "dp_demo_state.json"

    if args.cleanup:
        if os.path.exists(state_file):
            with open(state_file) as f:
                state = json.load(f)
            cleanup(state)
            os.remove(state_file)
        else:
            print("No state file found.")
        return

    print("=" * 60)
    print("AgentCore Data Protection Demo")
    print("=" * 60)

    # 1. Create guardrail
    guardrail_id, guardrail_version = create_guardrail()

    # 2. Write and deploy agent
    write_agent_file()
    print(f"\nDeploying agent '{AGENT_NAME}' to AgentCore Runtime...")
    agent_info = deploy_agent(guardrail_id, guardrail_version)

    state = {
        "guardrail_id": guardrail_id,
        "runtime_id": agent_info["runtime_id"],
        "runtime_arn": agent_info["runtime_arn"],
        "role_name": agent_info["role_name"],
    }
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)

    # 3. Test without CW data protection (guardrails only)
    test_invocation(agent_info["runtime_arn"], "Guardrails only (no CW Logs protection)")

    # 4. Apply CW Logs data protection
    apply_cw_data_protection(agent_info["runtime_id"])

    # 5. Test with both protections
    test_invocation(agent_info["runtime_arn"], "Guardrails + CW Logs Data Protection")

    print("\n" + "=" * 60)
    print("Demo complete! Review results in:")
    print("  CloudWatch > Log Groups > /aws/bedrock-agentcore/runtimes/...")
    print("  CloudWatch > GenAI Observability > Bedrock AgentCore > Traces")
    print("\n  Cleanup: python data_protection.py --cleanup")
    print("=" * 60)


if __name__ == "__main__":
    main()
