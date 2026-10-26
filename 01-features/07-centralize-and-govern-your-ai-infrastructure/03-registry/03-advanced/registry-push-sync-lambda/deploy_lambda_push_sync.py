"""
AWS Agent Registry Push Sync Lambda — Deployment

Sets up an automated push-based sync pipeline using Lambda that detects
AgentCore Runtime updates (UpdateAgentRuntime CloudTrail events via EventBridge)
and keeps the registry MCP tool records up to date automatically.

Steps:
  1. Configuration — set AWS region, Lambda name, registry/MCP server details
  2. Create Registry and MCP Server Record
  3. Approve the registry record
  4. Create AgentCore Identity credential providers (OAuth2)
  5. Create IAM role for the Lambda
  6. Build and create Lambda function
  7. Create EventBridge rule to trigger on UpdateAgentRuntime events
  8. Cross-account event forwarding setup (optional)
  9. Test Lambda manually with a synthetic CloudTrail event
  10. Check Lambda logs
  11. Cleanup

Usage:
    python deploy_lambda_push_sync.py

Prerequisites:
    - boto3 >= 1.42.87
    - AWS credentials configured with Lambda, IAM, EventBridge, and Registry permissions
    - handler.py in the same directory
    - Edit configuration section below before running
"""

import json
import os
import subprocess
import tempfile
import time
import zipfile

import boto3

# ── Edit these values ──────────────────────────────────────────────────────────
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
LAMBDA_NAME = "registry-push-sync-lambda"

REGISTRY_NAME = os.environ.get("REGISTRY_NAME", "<your-registry-name>")
REGISTRY_ID = None  # Set by section 2 after creating the registry
MCP_SERVER_NAME = os.environ.get("MCP_SERVER_NAME", "<mcp-server-name>")
MCP_SERVER_DESCRIPTION = os.environ.get("MCP_SERVER_DESCRIPTION", "<description>")
MCP_RUNTIME_ARN = os.environ.get(
    "MCP_RUNTIME_ARN",
    "arn:aws:bedrock-agentcore:<region>:<account-id>:runtime/<runtime-id>",
)

# AgentCore Identity credential provider per MCP server account.
# Keys are AWS account IDs hosting MCP servers on AgentCore Runtime.
ACCOUNT_CONFIGS: dict = {
    # "<account-a-id>": {
    #     "provider_name": "cognito-provider-AcctA",
    #     "scope": "<resource-server>/access",
    # },
}

# Cross-account: account IDs allowed to forward EventBridge events. Empty for single-account.
CROSS_ACCOUNT_IDS: list = []
# ─────────────────────────────────────────────────────────────────────────────

session = boto3.Session(region_name=AWS_REGION)
iam = session.client("iam")
lambda_client = session.client("lambda")
events_client = session.client("events")
sts = session.client("sts")

ACCOUNT_ID = sts.get_caller_identity()["Account"]
print(f"Account: {ACCOUNT_ID} | Region: {AWS_REGION}")

# ── 2. Create Registry and MCP Server Record ──────────────────────────────────
print("\n=== 2. Create Registry and MCP Server Record ===")

registry_cp = session.client("agent-registry-control", region_name=AWS_REGION)

try:
    reg_resp = registry_cp.create_registry(
        name=REGISTRY_NAME,
        description=f"Agent Registry for push sync — {REGISTRY_NAME}",
        discoveryConfiguration={"authorizerType": "AWS_IAM"},
        approvalConfiguration={"autoApprovalRules": []},
    )
    REGISTRY_ID = reg_resp["registryArn"].split("/")[-1]
    print(f"Created registry: {REGISTRY_NAME} → ID: {REGISTRY_ID}")
except Exception as e:
    if "already exists" in str(e).lower() or "conflict" in str(e).lower():
        regs = registry_cp.list_registries()
        for r in regs.get("registries", []):
            if r.get("name") == REGISTRY_NAME:
                REGISTRY_ID = r.get("registryId") or r.get("registryArn", "").split("/")[-1]
                break
        print(f"Registry already exists: {REGISTRY_NAME} → ID: {REGISTRY_ID}")
    else:
        raise

if not REGISTRY_ID:
    raise ValueError("Failed to create or find registry.")

print(f"Waiting for registry {REGISTRY_ID} to become READY...")
for _ in range(24):
    reg_status = registry_cp.get_registry(registryId=REGISTRY_ID).get("status", "")
    if reg_status == "READY":
        break
    if reg_status.endswith("_FAILED"):
        raise Exception(f"Registry failed: {reg_status}")  # noqa: TRY002
    print(f"  Registry status: {reg_status} — waiting...")
    time.sleep(5)
print(f"Using REGISTRY_ID: {REGISTRY_ID} (status: {reg_status})")

# ── 3. Create Registry Record for MCP Server ──────────────────────────────────
print("\n=== 3. Create Registry Record for MCP Server ===")

encoded_arn = MCP_RUNTIME_ARN.replace(":", "%3A").replace("/", "%2F")
mcp_server_url = f"https://bedrock-agentcore.{AWS_REGION}.amazonaws.com/runtimes/{encoded_arn}/invocations"

server_schema = json.dumps(
    {
        "name": f"io.example/{MCP_SERVER_NAME.lower().replace(' ', '-').replace('_', '-')}",
        "description": MCP_SERVER_DESCRIPTION,
        "version": "1.0.0",
        "title": MCP_SERVER_NAME,
        "websiteUrl": mcp_server_url,
        "packages": [
            {
                "registryType": "pip",
                "identifier": MCP_SERVER_NAME.lower().replace(" ", "-").replace("_", "-"),
                "version": "1.0.0",
                "registryBaseUrl": "https://pypi.org",
                "runtimeHint": "uvx",
                "transport": {"type": "stdio"},
            }
        ],
    }
)

try:
    rec_resp = registry_cp.create_registry_record(
        registryId=REGISTRY_ID,
        name=MCP_SERVER_NAME,
        description=MCP_SERVER_DESCRIPTION,
        recordType="MCP",
        recordVersion="1.0",
        descriptors={
            "mcpServer": {
                "data": server_schema,
            }
        },
    )
    RECORD_ID = (
        rec_resp.get("recordId") or rec_resp.get("registryRecordId") or rec_resp.get("recordArn", "").split("/")[-1]
    )
    print(f"Created record: {MCP_SERVER_NAME} → ID: {RECORD_ID} (status: DRAFT)")
except Exception as e:
    if "already exists" in str(e).lower() or "conflict" in str(e).lower():
        recs = registry_cp.list_registry_records(registryId=REGISTRY_ID)
        for r in recs.get("registryRecords", []):
            if r.get("name") == MCP_SERVER_NAME:
                RECORD_ID = r.get("recordId") or r.get("registryRecordId", "")
                break
        print(f"Record already exists: {MCP_SERVER_NAME} → ID: {RECORD_ID}")
    else:
        raise

print(f"Using RECORD_ID: {RECORD_ID}")

# Approve the record — wait for DRAFT first
print(f"Waiting for record {RECORD_ID} to reach DRAFT...")
while True:
    record = registry_cp.get_registry_record(registryId=REGISTRY_ID, recordId=RECORD_ID)
    current_status = record.get("status", "UNKNOWN")
    if current_status in ("DRAFT", "APPROVED", "PENDING_APPROVAL"):
        break
    if current_status.endswith("_FAILED"):
        raise Exception(f"Record failed: {current_status}")  # noqa: TRY002
    time.sleep(3)
print(f"Record {RECORD_ID} ({record.get('name', '?')}) — status: {current_status}")

if current_status == "DRAFT":
    registry_cp.submit_registry_record_for_approval(registryId=REGISTRY_ID, recordId=RECORD_ID)
    print("Submitted for approval (DRAFT → PENDING_APPROVAL)")
    time.sleep(3)

record = registry_cp.get_registry_record(registryId=REGISTRY_ID, recordId=RECORD_ID)
current_status = record.get("status", "UNKNOWN")

if current_status == "PENDING_APPROVAL":
    registry_cp.update_registry_record_status(
        registryId=REGISTRY_ID,
        recordId=RECORD_ID,
        status="APPROVED",
        statusReason="Approved via deployment script",
    )
    print("Approved (PENDING_APPROVAL → APPROVED)")
elif current_status == "APPROVED":
    print("Already APPROVED — nothing to do")
else:
    print(f"Unexpected status: {current_status}")

# ── 4. Create AgentCore Identity Credential Providers ────────────────────────
print("\n=== 4. Create AgentCore Identity Credential Providers ===")

WORKLOAD_IDENTITY_NAME = "registry-push-sync-agent"

# Edit CREDENTIAL_PROVIDERS to match your Cognito setup before running
CREDENTIAL_PROVIDERS: dict = {
    # "cognito-provider-AcctA": {
    #     "token_endpoint": "https://<domain>.auth.<region>.amazoncognito.com/oauth2/token",
    #     "authorization_endpoint": "https://<domain>.auth.<region>.amazoncognito.com/oauth2/authorize",
    #     "issuer": "https://cognito-idp.<region>.amazonaws.com/<pool-id>",
    #     "client_id": "<client-id>",
    #     "client_secret": "<client-secret>",
    # },
}

acps_client = session.client("bedrock-agentcore-control", region_name=AWS_REGION)

try:
    wi_resp = acps_client.create_workload_identity(name=WORKLOAD_IDENTITY_NAME)
    print(f"Created workload identity: {WORKLOAD_IDENTITY_NAME} → {wi_resp.get('workloadIdentityArn', '?')}")
except Exception as e:
    if "already exists" in str(e).lower() or "conflict" in str(e).lower():
        print(f"Workload identity already exists: {WORKLOAD_IDENTITY_NAME}")
    else:
        raise

for provider_name, config in CREDENTIAL_PROVIDERS.items():
    try:
        resp = acps_client.create_oauth2_credential_provider(
            name=provider_name,
            credentialProviderVendor="CustomOauth2",
            oauth2ProviderConfigInput={
                "customOauth2ProviderConfig": {
                    "oauthDiscovery": {
                        "authorizationServerMetadata": {
                            "issuer": config["issuer"],
                            "authorizationEndpoint": config["authorization_endpoint"],
                            "tokenEndpoint": config["token_endpoint"],
                            "responseTypes": ["token"],
                        }
                    },
                    "clientId": config["client_id"],
                    "clientSecret": config["client_secret"],
                }
            },
        )
        print(f"Created credential provider: {provider_name} → {resp.get('credentialProviderArn', '?')}")
    except Exception as e:
        if "already exists" in str(e).lower() or "conflict" in str(e).lower():
            print(f"Credential provider already exists: {provider_name}")
        else:
            raise

# ── 5. Create IAM Role for Lambda ─────────────────────────────────────────────
print("\n=== 5. Create IAM Role for Lambda ===")

ROLE_NAME = f"{LAMBDA_NAME}-role"

trust_policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

try:
    role = iam.create_role(
        RoleName=ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(trust_policy),
        Description="Role for AWS Agent Registry push sync Lambda",
    )
    ROLE_ARN = role["Role"]["Arn"]
    print(f"Created role: {ROLE_ARN}")
except iam.exceptions.EntityAlreadyExistsException:
    ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/{ROLE_NAME}"
    print(f"Role already exists: {ROLE_ARN}")

iam.attach_role_policy(
    RoleName=ROLE_NAME,
    PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)

iam.put_role_policy(
    RoleName=ROLE_NAME,
    PolicyName="RegistryAccess",
    PolicyDocument=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "agent-registry:ListRegistryRecords",
                        "agent-registry:GetRegistryRecord",
                        "agent-registry:UpdateRegistryRecord",
                        "bedrock-agentcore:GetResourceOauth2Token",
                        "bedrock-agentcore:GetWorkloadAccessToken",
                        "secretsmanager:GetSecretValue",
                    ],
                    "Resource": "*",
                }
            ],
        }
    ),
)
print("Attached policies")

print("Waiting 10s for IAM propagation...")
time.sleep(10)

# ── 6. Build and Create Lambda Function ──────────────────────────────────────
print("\n=== 6. Build and Create Lambda Function ===")

ZIP_PATH = "handler.zip"

script_dir = os.path.dirname(os.path.abspath(__file__))
handler_path = os.path.join(script_dir, "handler.py")

with tempfile.TemporaryDirectory() as tmpdir:
    subprocess.run(
        [
            "pip",
            "install",
            "boto3>=1.42.87",
            "requests",
            "-t",
            tmpdir,
            "--quiet",
            "--no-warn-conflicts",
        ],
        check=True,
    )
    print("Bundled: boto3, botocore, requests")

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(handler_path, "handler.py")
        for root, dirs, files in os.walk(tmpdir):
            for f in files:
                full_path = os.path.join(root, f)
                arcname = os.path.relpath(full_path, tmpdir)
                zf.write(full_path, arcname)

zip_size = os.path.getsize(ZIP_PATH)
print(f"Built {ZIP_PATH} ({zip_size:,} bytes)")

env_vars = {
    "REGISTRY_ID": REGISTRY_ID,
    "WORKLOAD_IDENTITY_NAME": WORKLOAD_IDENTITY_NAME,
}
for acct_id, config in ACCOUNT_CONFIGS.items():
    env_vars[f"CREDENTIAL_PROVIDER_{acct_id}"] = config["provider_name"]
    if config.get("scope"):
        env_vars[f"CREDENTIAL_SCOPE_{acct_id}"] = config["scope"]

with open(ZIP_PATH, "rb") as f:
    zip_bytes = f.read()

try:
    resp = lambda_client.create_function(
        FunctionName=LAMBDA_NAME,
        Runtime="python3.12",
        Role=ROLE_ARN,
        Handler="handler.handler",
        Code={"ZipFile": zip_bytes},
        Timeout=30,
        MemorySize=128,
        Environment={"Variables": env_vars},
        Description="Syncs MCP server tools to AWS Agent Registry on runtime updates",
    )
    LAMBDA_ARN = resp["FunctionArn"]
    print(f"Created Lambda: {LAMBDA_ARN}")
except lambda_client.exceptions.ResourceConflictException:
    lambda_client.update_function_code(FunctionName=LAMBDA_NAME, ZipFile=zip_bytes)
    time.sleep(5)
    lambda_client.update_function_configuration(FunctionName=LAMBDA_NAME, Environment={"Variables": env_vars})
    LAMBDA_ARN = f"arn:aws:lambda:{AWS_REGION}:{ACCOUNT_ID}:function:{LAMBDA_NAME}"
    print(f"Updated existing Lambda: {LAMBDA_ARN}")

# ── 7. Create EventBridge Rule ────────────────────────────────────────────────
print("\n=== 7. Create EventBridge Rule ===")

RULE_NAME = f"{LAMBDA_NAME}-trigger"
event_pattern = {
    "source": ["aws.bedrock-agentcore"],
    "detail-type": ["AWS API Call via CloudTrail"],
    "detail": {"eventName": ["UpdateAgentRuntime"]},
}

events_client.put_rule(
    Name=RULE_NAME,
    EventPattern=json.dumps(event_pattern),
    State="ENABLED",
    Description="Triggers push sync Lambda on AgentCore runtime updates",
)
print(f"Created EventBridge rule: {RULE_NAME}")

events_client.put_targets(
    Rule=RULE_NAME,
    Targets=[{"Id": "push-sync-lambda", "Arn": LAMBDA_ARN}],
)
print("Added Lambda target")

RULE_ARN = f"arn:aws:events:{AWS_REGION}:{ACCOUNT_ID}:rule/{RULE_NAME}"
try:
    lambda_client.add_permission(
        FunctionName=LAMBDA_NAME,
        StatementId="eventbridge-invoke",
        Action="lambda:InvokeFunction",
        Principal="events.amazonaws.com",
        SourceArn=RULE_ARN,
    )
    print("Added Lambda invoke permission for EventBridge")
except lambda_client.exceptions.ResourceConflictException:
    print("Lambda invoke permission already exists")

print("\n=== Deployment Complete ===")
print("Registry, MCP record, Lambda, IAM role, and EventBridge rule are deployed.")
print("When UpdateAgentRuntime CloudTrail events fire, EventBridge will trigger the Lambda.")
print("The Lambda connects to the MCP server, discovers tools, and updates the registry record.")

# ── 8. Cross-Account Setup (Optional) ────────────────────────────────────────
if CROSS_ACCOUNT_IDS:
    print("\n=== 8. Cross-Account Setup ===")
    for acct_id in CROSS_ACCOUNT_IDS:
        try:
            events_client.put_permission(
                EventBusName="default",
                Action="events:PutEvents",
                Principal=acct_id,
                StatementId=f"AllowAccount{acct_id}",
            )
            print(f"Allowed account {acct_id} to send events to this bus")
        except events_client.exceptions.ResourceAlreadyExistsException:
            print(f"Permission for account {acct_id} already exists")
else:
    print("\n(Cross-account setup skipped — no CROSS_ACCOUNT_IDS configured)")

# ── 9. Test Lambda (Optional) ─────────────────────────────────────────────────
print("\n=== 9. Test Lambda (Manual Invocation) ===")
print("To test, set TEST_RUNTIME_ID to a valid runtime ID and uncomment the block below.")
TEST_RUNTIME_ID = os.environ.get("TEST_RUNTIME_ID", "")
TEST_ACCOUNT_ID = CROSS_ACCOUNT_IDS[0] if CROSS_ACCOUNT_IDS else ACCOUNT_ID

if TEST_RUNTIME_ID:
    test_event = {
        "detail-type": "AWS API Call via CloudTrail",
        "source": "aws.bedrock-agentcore",
        "detail": {
            "eventName": "UpdateAgentRuntime",
            "awsRegion": AWS_REGION,
            "requestParameters": {"agentRuntimeId": TEST_RUNTIME_ID},
            "responseElements": {
                "agentRuntimeArn": f"arn:aws:bedrock-agentcore:{AWS_REGION}:{TEST_ACCOUNT_ID}:runtime/{TEST_RUNTIME_ID}",
                "agentRuntimeId": TEST_RUNTIME_ID,
                "status": "UPDATING",
            },
        },
    }

    response = lambda_client.invoke(
        FunctionName=LAMBDA_NAME,
        Payload=json.dumps(test_event),
    )

    result = json.loads(response["Payload"].read())
    if "FunctionError" in response:
        print(f"ERROR: {json.dumps(result, indent=2)}")
    else:
        body = json.loads(result.get("body", "{}"))
        print(f"MCP URL:      {body.get('mcp_url', '?')}")
        print(f"Tools found:  {body.get('tool_count', 0)}")
        print(f"Tools:        {body.get('tools', [])}")
        print(f"Sync result:  {body.get('sync', {})}")
else:
    print("Set TEST_RUNTIME_ID env var to test Lambda invocation.")

# ── 10. Check Lambda Logs ─────────────────────────────────────────────────────
print("\n=== 10. Check Lambda Logs ===")
logs_client = session.client("logs")
log_group = f"/aws/lambda/{LAMBDA_NAME}"

try:
    streams = logs_client.describe_log_streams(
        logGroupName=log_group, orderBy="LastEventTime", descending=True, limit=1
    )
    if streams["logStreams"]:
        stream_name = streams["logStreams"][0]["logStreamName"]
        events = logs_client.get_log_events(logGroupName=log_group, logStreamName=stream_name, limit=20)
        for e in events["events"]:
            msg = e["message"].strip()
            if msg and not msg.startswith("REPORT") and not msg.startswith("END"):
                print(msg)
    else:
        print("No log streams found (Lambda has not been invoked yet)")
except Exception as e:  # noqa: BLE001
    print(f"Could not fetch logs: {e}")

# ── 11. Cleanup ───────────────────────────────────────────────────────────────
print("\n=== 11. Cleanup ===")
print("Uncomment the cleanup block below to delete all created resources.")

# ─ Uncomment to cleanup ─────────────────────────────────────────────────────
# ROLE_NAME = f"{LAMBDA_NAME}-role"
# RULE_NAME = f"{LAMBDA_NAME}-trigger"
#
# try:
#     events_client.remove_targets(Rule=RULE_NAME, Ids=["push-sync-lambda"])
#     events_client.delete_rule(Name=RULE_NAME)
#     print(f"Deleted EventBridge rule: {RULE_NAME}")
# except Exception as e:
#     print(f"Rule cleanup: {e}")
#
# for acct_id in CROSS_ACCOUNT_IDS:
#     try:
#         events_client.remove_permission(EventBusName="default", StatementId=f"AllowAccount{acct_id}")
#         print(f"Removed permission for account {acct_id}")
#     except Exception as e:
#         print(f"Permission cleanup: {e}")
#
# try:
#     lambda_client.delete_function(FunctionName=LAMBDA_NAME)
#     print(f"Deleted Lambda: {LAMBDA_NAME}")
# except Exception as e:
#     print(f"Lambda cleanup: {e}")
#
# try:
#     iam.detach_role_policy(RoleName=ROLE_NAME, PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole")
#     iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName="RegistryAccess")
#     iam.delete_role(RoleName=ROLE_NAME)
#     print(f"Deleted IAM role: {ROLE_NAME}")
# except Exception as e:
#     print(f"Role cleanup: {e}")
#
# try:
#     reg_cleanup = session.client("agent-registry-control", region_name=AWS_REGION)
#     if RECORD_ID:
#         reg_cleanup.delete_registry_record(registryId=REGISTRY_ID, recordId=RECORD_ID)
#         print(f"Deleted registry record: {RECORD_ID}")
#     reg_cleanup.delete_registry(registryId=REGISTRY_ID)
#     print(f"Deleted registry: {REGISTRY_ID}")
# except Exception as e:
#     print(f"Registry cleanup: {e}")
#
# for provider_name in CREDENTIAL_PROVIDERS.keys():
#     try:
#         acps_client.delete_oauth2_credential_provider(name=provider_name)
#         print(f"Deleted credential provider: {provider_name}")
#     except Exception as e:
#         print(f"Credential provider cleanup ({provider_name}): {e}")
#
# try:
#     acps_client.delete_workload_identity(name=WORKLOAD_IDENTITY_NAME)
#     print(f"Deleted workload identity: {WORKLOAD_IDENTITY_NAME}")
# except Exception as e:
#     print(f"Workload identity cleanup: {e}")
#
# print("Cleanup complete.")
# ─────────────────────────────────────────────────────────────────────────────

print("\n✅ Push sync deployment complete.")
