"""
AWS Agent Registry Approval — CI/CD and Approval Workflow

Walks a Registry Administrator through the CI/CD and approval workflow:
  1. Create a Registry (governance-first, autoApproval: false)
  2. Deploy CloudFormation infrastructure (EventBridge, Lambda, DynamoDB, S3)
     for automated CI/CD pipeline with Slack notifications
  3. Create A2A, MCP, and CUSTOM records
  4. Submit records for approval (triggers Slack notification via Lambda)
  5. Cleanup

Usage:
    python admin_approval_workflow.py

Prerequisites:
    - boto3 installed (pip install -r requirements.txt)
    - A Slack workspace with an incoming webhook configured
    - AWS CLI configured with a default region (us-west-2)
    - deploy.sh and destroy.sh scripts in the same directory

Configuration:
    Set SLACK_INC_HOOK and SLACK_CHANNEL_NAME below before running.
"""

import json
import os
import subprocess

import boto3
import botocore.exceptions
from utils import wait_for_record_draft, wait_for_registry_ready

print(f"Boto3 version: {boto3.__version__}")

# ── Configuration ──────────────────────────────────────────────────────────────
SLACK_INC_HOOK = os.environ.get("SLACK_INC_HOOK", "<incoming slack hook here: https>")
SLACK_CHANNEL_NAME = os.environ.get("SLACK_CHANNEL_NAME", "<slack channel name here>")

try:
    import sagemaker

    AWS_REGION = sagemaker.Session().boto_region_name
except Exception:  # noqa: BLE001
    AWS_REGION = boto3.session.Session().region_name or "us-west-2"

session = boto3.Session(region_name=AWS_REGION)
cp_client = session.client("agent-registry-control")

# ── Step 1 — Create Registry ───────────────────────────────────────────────────
print("\n=== Step 1 — Create a Registry (Governance-First) ===")

create_resp = cp_client.create_registry(
    name="adminFlowRegistry",
    description="Registry created for Administrator Flow",
    approvalConfiguration={"autoApprovalRules": []},
)

REGISTRY_ARN = create_resp["registryArn"]
REGISTRY_ID = REGISTRY_ARN.split("/")[-1]

print("Registry created!")
print(f"  ARN: {REGISTRY_ARN}")
print(f"  ID:  {REGISTRY_ID}")

# Wait for registry to reach READY status (~2 minutes)
wait_for_registry_ready(cp_client, REGISTRY_ID)

# ── Step 2 — Deploy CloudFormation Infrastructure ─────────────────────────────
print("\n=== Step 2 — Deploy Infrastructure ===")
print("Deploying CloudFormation stack (EventBridge, Lambda, DynamoDB, S3)...")
print("This sets up automated CI/CD pipeline with Slack notifications.")

SKIP_LAYER_BUILD = False
CFN_STACK_NAME = "adminflow-registry"

script_dir = os.path.dirname(os.path.abspath(__file__))
cmd = [
    "bash",
    os.path.join(script_dir, "deploy.sh"),
    "--stack-name",
    CFN_STACK_NAME,
    "--prefix",
    CFN_STACK_NAME,
    "--registry-id",
    REGISTRY_ID,
    "--slack-hook-url",
    SLACK_INC_HOOK,
    "--slack-channel",
    SLACK_CHANNEL_NAME,
]

if SKIP_LAYER_BUILD:
    cfn = session.client("cloudformation")
    layer_key = None
    try:
        response = cfn.describe_stacks(StackName=CFN_STACK_NAME)
        params = response["Stacks"][0].get("Parameters", [])
        for p in params:
            if p["ParameterKey"] == "LambdaLayerKey":
                layer_key = p["ParameterValue"]
                break
    except cfn.exceptions.ClientError:
        pass

    if not layer_key:
        raise ValueError(
            f"Cannot skip layer build: stack '{CFN_STACK_NAME}' does not exist "
            "or does not have a 'LambdaLayerKey' parameter. "
            "Set SKIP_LAYER_BUILD = False to build the layer."
        )
    cmd += ["--skip-layer-build", "--layer-key", layer_key]

cmd += ["--region", AWS_REGION]

process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
for line in process.stdout:
    print(line, end="")
process.wait()
if process.returncode != 0:
    print(f"\nScript exited with code {process.returncode}")

# ── Step 3a — Create A2A Record ───────────────────────────────────────────────
print("\n=== Step 3a — Create A2A Record and Submit for Approval ===")

a2a_agent_card = json.dumps(
    {
        "$schema": "https://a2a-protocol.org/schemas/0.3/agent-card.schema.json",
        "name": "Loan Underwriting Agent",
        "description": "Evaluates loan applications.",
        "version": "1.0.0",
        "url": "http://loan-underwriting-agent.internal.corp.com/agent",
        "protocolVersion": "0.3",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [
            {
                "id": "evaluate-loan-application",
                "name": "Evaluate Loan Application",
                "description": "Assesses a loan application and returns an approval decision with risk score and recommended terms.",
                "tags": ["loan", "underwriting", "credit", "risk", "approval"],
                "examples": [
                    "Evaluate loan application for applicant ID 98234 requesting $250,000 mortgage.",
                    "What is the risk score for a $50,000 personal loan with a 680 credit score?",
                ],
            }
        ],
    }
)

a2a_resp = cp_client.create_registry_record(
    registryId=REGISTRY_ID,
    name="loan_underwriting_agent",
    description="Assesses a loan application and returns an approval decision with risk score and recommended terms.",
    recordType="AGENT",
    descriptors={"a2aAgentCard": {"data": a2a_agent_card}},
    recordVersion="1.0",
)

A2A_RECORD_ARN = a2a_resp["recordArn"]
A2A_RECORD_ID = A2A_RECORD_ARN.split("/")[-1]
metadata = a2a_resp.get("ResponseMetadata", {})
print(
    f"A2A Record created: {A2A_RECORD_ID} "
    f"(RequestId: {metadata['HTTPHeaders']['x-amzn-requestid']}, "
    f"Timestamp: {metadata['HTTPHeaders']['date']})"
)

wait_for_record_draft(cp_client, REGISTRY_ID, A2A_RECORD_ID)

submit_resp = cp_client.submit_registry_record_for_approval(registryId=REGISTRY_ID, recordId=A2A_RECORD_ID)
metadata = submit_resp.get("ResponseMetadata", {})
print(
    f"Record submitted for approval "
    f"(RequestId: {metadata['HTTPHeaders']['x-amzn-requestid']}, "
    f"Timestamp: {metadata['HTTPHeaders']['date']})"
)

# ── Step 3b — Create MCP Record ───────────────────────────────────────────────
print("\n=== Step 3b — Create MCP Record ===")

mcp_record = cp_client.create_registry_record(
    registryId=REGISTRY_ID,
    name="loan_underwriting_mcp",
    description="MCP server for loan underwriting tools",
    recordType="MCP",
    descriptors={
        "mcpServer": {
            "data": json.dumps(
                {
                    "name": "io.enterprise/loan-underwriting",
                    "description": "MCP server for loan underwriting tools",
                    "version": "2.1.0",
                    "packages": [
                        {
                            "registryType": "npm",
                            "identifier": "@enterprise/loan-underwriting-mc",
                            "version": "2.1.0",
                            "transport": {"type": "stdio"},
                        }
                    ],
                }
            ),
            "additionalData": {
                "tools": {
                    "data": json.dumps(
                        {
                            "tools": [
                                {
                                    "name": "check_credit_score",
                                    "description": "Retrieve credit score and credit history summary for an applicant",
                                    "inputSchema": {
                                        "type": "object",
                                        "properties": {"applicant_id": {"type": "string"}},
                                    },
                                }
                            ],
                        }
                    )
                }
            },
        }
    },
    recordVersion="2.1",
)

MCP_RECORD_ID = mcp_record["recordArn"].split("/")[-1]
metadata = mcp_record.get("ResponseMetadata", "")
print(
    f"MCP Record created: {MCP_RECORD_ID} "
    f"(RequestId: {metadata['HTTPHeaders']['x-amzn-requestid']}, "
    f"Timestamp: {metadata['HTTPHeaders']['date']})"
)

wait_for_record_draft(cp_client, REGISTRY_ID, MCP_RECORD_ID)

submit_resp = cp_client.submit_registry_record_for_approval(registryId=REGISTRY_ID, recordId=MCP_RECORD_ID)
metadata = submit_resp.get("ResponseMetadata", {})
print(
    f"Record submitted for approval "
    f"(RequestId: {metadata['HTTPHeaders']['x-amzn-requestid']}, "
    f"Timestamp: {metadata['HTTPHeaders']['date']})"
)

# ── Step 3c — Create CUSTOM Record ────────────────────────────────────────────
print("\n=== Step 3c — Create CUSTOM Record ===")

custom_record = cp_client.create_registry_record(
    registryId=REGISTRY_ID,
    name="loan_decision_engine_custom",
    description="Custom Rest API for integrating with the internal loan decision engine to finalize underwriting outcomes",
    recordType="CUSTOM",
    descriptors={
        "custom": {
            "data": json.dumps(
                {
                    "name": "loan-decision-engine",
                    "description": "Custom Rest API for integrating with the internal loan decision engine to finalize underwriting outcomes",
                    "version": "1.0.0",
                    "endpoint": "https://underwriting.internal.example.com/api/v1",
                }
            )
        }
    },
    recordVersion="1.0",
)

CUSTOM_RECORD_ID = custom_record["recordArn"].split("/")[-1]
metadata = custom_record.get("ResponseMetadata", "")
print(
    f"CUSTOM Record created: {CUSTOM_RECORD_ID} "
    f"(RequestId: {metadata['HTTPHeaders']['x-amzn-requestid']}, "
    f"Timestamp: {metadata['HTTPHeaders']['date']})"
)

wait_for_record_draft(cp_client, REGISTRY_ID, CUSTOM_RECORD_ID)

submit_resp = cp_client.submit_registry_record_for_approval(registryId=REGISTRY_ID, recordId=CUSTOM_RECORD_ID)
metadata = submit_resp.get("ResponseMetadata", {})
print(
    f"Record submitted for approval "
    f"(RequestId: {metadata['HTTPHeaders']['x-amzn-requestid']}, "
    f"Timestamp: {metadata['HTTPHeaders']['date']})"
)

# ── Step 4 — Simulation of Administrator Flow ─────────────────────────────────
print("\n=== Step 4 — Simulation of Administrator Flow ===")
print("Records submitted for approval. The Lambda (ending 'lambda-cicd') will now:")
print("  1. Check for duplicates via semantic search")
print("  2. Scan agent cards using CISCO AI Defense")
print("  3. Store AI scan results in DynamoDB + generate HTML report in S3")
print("  4. Send Slack notification to the Administrator")
print()
print("Check your Slack channel for notifications. Each message includes:")
print("  - Record metadata and duplicate check results")
print("  - AI scan summary with link to detailed HTML report")
print("  - AWS CLI commands to approve/reject the record")
print()
print("Refer to IAM_PERMISSIONS.md for the required permissions.")

# ── Step 5 — Cleanup ──────────────────────────────────────────────────────────
print("\n=== Step 5 — Cleanup ===")
print("Cleaning up the infrastructure...")

process = subprocess.Popen(
    [
        "bash",
        os.path.join(script_dir, "destroy.sh"),
        "--stack-name",
        CFN_STACK_NAME,
        "--prefix",
        CFN_STACK_NAME,
        "--region",
        AWS_REGION,
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1,
)
for line in process.stdout:
    print(line, end="")
process.wait()
if process.returncode != 0:
    print(f"\nScript exited with code {process.returncode}")


def cleanup_registry(registry_id):
    try:
        cp_client.get_registry(registryId=registry_id)
        records_list = cp_client.list_registry_records(registryId=registry_id)["registryRecords"]
        print(f"{len(records_list)} records found in the registry. Deleting all records")
        for record in records_list:
            cp_client.delete_registry_record(registryId=registry_id, recordId=record["recordId"])
        print("Deleting Registry")
        cp_client.delete_registry(registryId=registry_id)
    except cp_client.exceptions.ResourceNotFoundException:
        print(f"Registry {registry_id} not found")
    except botocore.exceptions.ClientError as e:
        print(f"Unexpected error: {e}")


cleanup_registry(REGISTRY_ID)
print("\n✅ Admin approval workflow demo complete.")
