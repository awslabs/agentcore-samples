"""
Discovering Tools and Agents at Runtime Using AWS Agent Registry

An orchestrator agent that uses AWS Agent Registry for registry-driven discovery:
  1. Deploy infrastructure — MCP servers (AgentCore Gateway) + A2A agents (Runtime)
  2. Create Registry and register MCP/A2A records
  3. Deploy orchestrator agent (searches Registry at runtime to discover tools)
  4. Run 3 end-to-end demos:
     - Demo 1: Order status lookup (MCP tool via Gateway)
     - Demo 2: Pricing & discounts (MCP + A2A multi-agent)
     - Demo 3: Return & refund (A2A customer support agent)

Usage:
    python discovery_and_invocation_at_runtime.py

Prerequisites:
    - boto3 >= 1.42.87
    - strands-agents[a2a], bedrock-agentcore-starter-toolkit, mcp, requests
    - AWS credentials with Bedrock, AgentCore, Lambda, IAM, Cognito, ECR, CodeBuild permissions
    - Amazon Bedrock access enabled for Claude Sonnet 4
    - utils.py and cleanup.py in the same directory

Architecture:
    Consumer → Orchestrator Agent (Runtime, A2A)
                  │ searches Registry at runtime
                  ├─ MCP Server (AgentCore Gateway + Lambda, OAuth2 auth)
                  ├─ Pricing A2A Agent (Runtime, IAM SigV4)
                  └─ Customer Support A2A Agent (Runtime, IAM SigV4)
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote

import boto3

# ── 1.0 Install dependencies ──────────────────────────────────────────────────
subprocess.check_call(
    [
        sys.executable,
        "-m",
        "pip",
        "install",
        "-U",
        "-q",
        "strands-agents>=0.1.0",
        "strands-agents[a2a]>=0.1.0",
        "boto3>=1.42.87",
        "bedrock-agentcore>=1.0.0",
        "bedrock-agentcore-starter-toolkit>=0.1.24",
        "mcp>=1.0.0",
        "requests>=2.31.0",
    ]
)

import boto3 as _b3

assert tuple(int(x) for x in _b3.__version__.split(".")) >= (1, 42, 87), (
    f"boto3 >= 1.42.87 required, got {_b3.__version__}"
)
print(f"boto3 {_b3.__version__} — native AgentCore Registry support ✓")

# ── Imports ───────────────────────────────────────────────────────────────────
from bedrock_agentcore_starter_toolkit import Runtime
from utils import (
    ORDER_MANAGEMENT_LAMBDA_CODE,
    ORDER_TOOL_SCHEMAS,
    make_lambda_zip,
    write_agent_files,
)

# ── Configuration ─────────────────────────────────────────────────────────────
session = boto3.Session()
region = session.region_name or "us-west-2"
os.environ["AWS_DEFAULT_REGION"] = region

sts_client = session.client("sts")
account_id = sts_client.get_caller_identity()["Account"]
iam_client = session.client("iam")
lambda_client = session.client("lambda")
cognito_client = session.client("cognito-idp")
sm_client = session.client("secretsmanager")

cp_client = session.client("bedrock-agentcore-control")
registry_client = session.client("agent-registry-control")
agentcore_client = session.client("bedrock-agentcore")
dp_client = session.client("agent-registry")  # search_discoverable_registry_records

timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d%H%M%S")
MODEL_ID = "us.anthropic.claude-sonnet-4-6"
print(f"Account: {account_id} | Region: {region} | Timestamp: {timestamp}")

# ── 1. Deploy Infrastructure ──────────────────────────────────────────────────
print("\n=== 1. Deploy Infrastructure ===")

# 1a. Lambda — order management backend
print("1a. Creating Lambda function...")
lambda_role_name = f"LambdaMCPRole-{timestamp}"
lambda_role_resp = iam_client.create_role(
    RoleName=lambda_role_name,
    AssumeRolePolicyDocument=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    ),
)
lambda_role_arn = lambda_role_resp["Role"]["Arn"]
iam_client.attach_role_policy(
    RoleName=lambda_role_name,
    PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
)
time.sleep(10)

fname = f"order-management-mcp-{timestamp}"
resp = lambda_client.create_function(
    FunctionName=fname,
    Runtime="python3.13",
    Role=lambda_role_arn,
    Handler="lambda_function.lambda_handler",
    Code={"ZipFile": make_lambda_zip(ORDER_MANAGEMENT_LAMBDA_CODE)},
    Timeout=30,
)
lambda_arns = {"order-management-mcp": resp["FunctionArn"]}
print(f"✓ Lambda: {fname}")

# 1b. Cognito — OAuth2 token provider for Gateway auth
print("1b. Creating Cognito user pool...")
pool_resp = cognito_client.create_user_pool(
    PoolName=f"gateway-pool-{timestamp}",
    Policies={
        "PasswordPolicy": {
            "MinimumLength": 8,
            "RequireUppercase": False,
            "RequireLowercase": False,
            "RequireNumbers": False,
            "RequireSymbols": False,
        }
    },
)
user_pool_id = pool_resp["UserPool"]["Id"]
resource_server_id = f"gateway-api-{timestamp}"

cognito_client.create_resource_server(
    UserPoolId=user_pool_id,
    Identifier=resource_server_id,
    Name=f"Gateway API {timestamp}",
    Scopes=[
        {"ScopeName": "read", "ScopeDescription": "Read"},
        {"ScopeName": "write", "ScopeDescription": "Write"},
    ],
)

app_client_resp = cognito_client.create_user_pool_client(
    UserPoolId=user_pool_id,
    ClientName=f"gateway-client-{timestamp}",
    GenerateSecret=True,
    AllowedOAuthFlows=["client_credentials"],
    AllowedOAuthFlowsUserPoolClient=True,
    AllowedOAuthScopes=[f"{resource_server_id}/read", f"{resource_server_id}/write"],
)
client_id = app_client_resp["UserPoolClient"]["ClientId"]

secret_name = f"gateway-client-secret-{timestamp}"
_client_secret = cognito_client.describe_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)[
    "UserPoolClient"
]["ClientSecret"]
sm_client.create_secret(Name=secret_name, SecretString=_client_secret)
del _client_secret

domain_name = f"gateway-{timestamp}"
cognito_client.create_user_pool_domain(Domain=domain_name, UserPoolId=user_pool_id)
cognito_domain = f"{domain_name}.auth.{region}.amazoncognito.com"
scopes = f"{resource_server_id}/read {resource_server_id}/write"
print(f"✓ Cognito: pool={user_pool_id}")

# 1c. AgentCore Gateway — managed MCP server fronting Lambda
print("1c. Creating AgentCore Gateway...")
gateway_role_name = f"AgentCoreGatewayRole-{timestamp}"
iam_client.create_role(
    RoleName=gateway_role_name,
    AssumeRolePolicyDocument=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    ),
)
gateway_role_arn = f"arn:aws:iam::{account_id}:role/{gateway_role_name}"
iam_client.put_role_policy(
    RoleName=gateway_role_name,
    PolicyName="LambdaInvoke",
    PolicyDocument=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "lambda:InvokeFunction",
                    "Resource": f"arn:aws:lambda:{region}:{account_id}:function:*",
                }
            ],
        }
    ),
)
time.sleep(10)

gateway_resp = cp_client.create_gateway(
    name=f"demo-gateway-{timestamp}",
    roleArn=gateway_role_arn,
    protocolType="MCP",
    protocolConfiguration={"mcp": {"supportedVersions": ["2025-03-26", "2025-06-18"]}},
    authorizerType="CUSTOM_JWT",
    authorizerConfiguration={
        "customJWTAuthorizer": {
            "discoveryUrl": f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration",
            "allowedClients": [client_id],
        }
    },
)
gateway_id = gateway_resp["gatewayId"]
print(f"  Gateway creating: {gateway_id} ...")
while True:
    status = cp_client.get_gateway(gatewayIdentifier=gateway_id)
    if status.get("status") == "READY":
        gateway_url = status.get("gatewayUrl")
        break
    time.sleep(5)
print(f"✓ Gateway ready: {gateway_url}")

resp = cp_client.create_gateway_target(
    gatewayIdentifier=gateway_id,
    name=f"order-management-target-{timestamp}",
    targetConfiguration={
        "mcp": {
            "lambda": {
                "lambdaArn": lambda_arns["order-management-mcp"],
                "toolSchema": {"inlinePayload": ORDER_TOOL_SCHEMAS},
            }
        }
    },
    credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
)
tid = resp["targetId"]
while True:
    if cp_client.get_gateway_target(gatewayIdentifier=gateway_id, targetId=tid).get("status") == "READY":
        break
    time.sleep(10)
print(f"✓ Gateway target ready: {tid}")

# 1d. A2A Agents — deploy to AgentCore Runtime
print("1d. Deploying A2A agents to AgentCore Runtime...")
write_agent_files()

if os.path.exists(".bedrock_agentcore.yaml"):
    os.remove(".bedrock_agentcore.yaml")

pricing_rt = Runtime()
pricing_rt.configure(
    entrypoint="pricing_agent.py",
    auto_create_execution_role=True,
    auto_create_ecr=True,
    requirements_file="a2a_requirements.txt",
    region=region,
    agent_name="pricing_agent",
    protocol="A2A",
)
pricing_launch = pricing_rt.launch(auto_update_on_conflict=True)
pricing_agent_id = pricing_launch.agent_id
print(f"✓ Pricing Agent deployed: {pricing_agent_id}")

support_rt = Runtime()
support_rt.configure(
    entrypoint="customer_support_agent.py",
    auto_create_execution_role=True,
    auto_create_ecr=True,
    requirements_file="a2a_requirements.txt",
    region=region,
    agent_name="customer_support_agent",
    protocol="A2A",
)
support_launch = support_rt.launch(auto_update_on_conflict=True)
support_agent_id = support_launch.agent_id
print(f"✓ Customer Support Agent deployed: {support_agent_id}")

agent_arns = {}
for name, aid in [
    ("pricing_agent", pricing_agent_id),
    ("customer_support_agent", support_agent_id),
]:
    resp = cp_client.get_agent_runtime(agentRuntimeId=aid)
    agent_arns[name] = resp["agentRuntimeArn"]

registry_records = {
    "order_management_mcp": {
        "protocol": "MCP",
        "description": "Order data tools - get order status, tracking, items, shipping details, cancel or change address",
        "tools": ORDER_TOOL_SCHEMAS,
    },
    "pricing_agent": {
        "protocol": "AGENT",
        "description": "Pricing only - discount tiers, promo codes, price history. Never handles returns or refunds",
    },
    "customer_support_agent": {
        "protocol": "AGENT",
        "description": "Returns and refunds only - return eligibility, refund calculation, complaints, escalations",
    },
}

print("\n" + "═" * 70)
print("DEPLOYED RESOURCES")
print("═" * 70)
print(f"  Lambda ARN:            {lambda_arns['order-management-mcp']}")
print(f"  Gateway URL:           {gateway_url}")
print(f"  Cognito User Pool:     {user_pool_id}")
print(f"  Secret Name:           {secret_name}")  # codeql[py/clear-text-logging-sensitive-data]
print(f"  Pricing Agent ARN:     {agent_arns['pricing_agent']}")
print(f"  Support Agent ARN:     {agent_arns['customer_support_agent']}")
print("═" * 70)

# ── 2. Create AWS Agent Registry & Register Records ───────────────────────────
print("\n=== 2. Create Registry and Register Records ===")

# 2a. Create Registry
reg = registry_client.create_registry(
    name="OrderManagementRegistry",
    description="Registry for Order Management & Customer Service — agent discovers tools and agents via semantic search",
    approvalConfiguration={"autoApprovalRules": []},
)
REGISTRY_ARN = reg["registryArn"]
REGISTRY_ID = REGISTRY_ARN.split("/")[-1]
print(f"Registry: {REGISTRY_ID}")

while True:
    r = registry_client.get_registry(registryId=REGISTRY_ID)
    if r["status"] == "READY":
        print("Registry status: READY")
        break
    print(f"Registry status: {r['status']} - waiting...")
    time.sleep(5)

# 2b. Register Records


def build_mcp_descriptors(name, description, gateway_url, tools):
    server_desc = description[:100] if len(description) > 100 else description
    server_content = json.dumps(
        {
            "name": f"gateway-mcp-server/{name}",
            "description": server_desc,
            "version": "1.0.0",
            "websiteUrl": gateway_url,
        }
    )
    tools_content = json.dumps({"tools": tools})
    return {
        "mcpServer": {
            "data": server_content,
            "additionalData": {"tools": {"data": tools_content}},
        }
    }


def build_a2a_descriptors(name, description, agent_arn):
    escaped_arn = quote(agent_arn, safe="")
    agent_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{escaped_arn}/invocations/"
    agent_card = {
        "protocolVersion": "0.3.0",
        "name": name.replace("_", " ").title(),
        "description": description,
        "url": agent_url,
        "version": "1.0.0",
        "preferredTransport": "JSONRPC",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [
            {
                "id": name,
                "name": name.replace("_", " ").title(),
                "description": description,
                "tags": [],
            }
        ],
    }
    return {"a2aAgentCard": {"data": json.dumps(agent_card)}}


record_ids = []
for name, cfg in registry_records.items():
    if cfg["protocol"] == "MCP":
        descriptors = build_mcp_descriptors(name, cfg["description"], gateway_url, cfg["tools"])
    else:
        descriptors = build_a2a_descriptors(name, cfg["description"], agent_arns[name])

    resp = registry_client.create_registry_record(
        registryId=REGISTRY_ID,
        name=name,
        description=cfg["description"],
        recordType=cfg["protocol"],
        descriptors=descriptors,
        recordVersion="1.0",
    )
    rid = resp["recordArn"].split("/record/")[-1]
    record_ids.append(rid)
    print(f"Created {cfg['protocol']}: {name} -> {rid}")

print(f"\nTotal records: {len(record_ids)}")

# 2c. Approve All Records
print("\nWaiting for records to be ready for approval...")
for rid in record_ids:
    while True:
        rec = registry_client.get_registry_record(registryId=REGISTRY_ID, recordId=rid)
        status = rec.get("status", "UNKNOWN")
        if status in ("DRAFT", "PENDING_APPROVAL"):
            break
        print(f"  {rec['name']}: {status} - waiting...")
        time.sleep(5)
    print(f"  {rec['name']}: {status}")

for rid in record_ids:
    rec = registry_client.get_registry_record(registryId=REGISTRY_ID, recordId=rid)
    status = rec.get("status")
    if status == "DRAFT":
        registry_client.submit_registry_record_for_approval(registryId=REGISTRY_ID, recordId=rid)
    if status in ("DRAFT", "PENDING_APPROVAL"):
        registry_client.update_registry_record_status(
            registryId=REGISTRY_ID,
            recordId=rid,
            status="APPROVED",
            statusReason="Approved",
        )
    print(f"Approved: {rid}")

# Wait for search index propagation
print("\nWaiting for search index to propagate all records...")
for attempt in range(12):
    time.sleep(10)
    resp = dp_client.search_discoverable_registry_records(
        registryIds=[REGISTRY_ARN],
        searchQuery="order pricing support",
        maxResults=10,
    )
    found = len(resp.get("registryRecords", []))
    if found >= len(record_ids):
        print(f"  All {found} records indexed.")
        break
    print(f"  {found}/{len(record_ids)} records indexed - waiting...")
print("Ready.")

# 2d. Verify Semantic Search
print("\n2d. Verify Semantic Search")
for query in [
    "order status tracking",
    "pricing discount promo code",
    "return refund customer support",
    "cancel order update",
]:
    resp = dp_client.search_discoverable_registry_records(registryIds=[REGISTRY_ARN], searchQuery=query, maxResults=3)
    hits = resp.get("registryRecords", [])
    print(f"\n'{query}' -> {len(hits)} results:")
    for h in hits:
        descriptors = h.get("descriptors", {})
        dtype = (
            "MCP"
            if "mcpServer" in descriptors
            else "AGENT"
            if "a2aAgentCard" in descriptors
            else h.get("recordType", "?")
        )
        print(f"  - {h['name']} ({dtype})")

# ── 3. Deploy Orchestrator Agent ──────────────────────────────────────────────
print("\n=== 3. Deploy Orchestrator Agent ===")

ORCHESTRATOR_AGENT_CODE = '''
import os
import json
import boto3
from strands import Agent, tool
from strands.models import BedrockModel
from strands.multiagent.a2a import A2AServer
from fastapi import FastAPI
import uvicorn
from utils import (
    parse_server_metadata,
    create_mcp_client_from_metadata,
    create_a2a_tool_from_metadata,
    fetch_oauth_token,
)

REGION       = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
REGISTRY_ARN = os.environ["REGISTRY_ARN"]
COGNITO_DOMAIN = os.environ["COGNITO_DOMAIN"]
CLIENT_ID    = os.environ["CLIENT_ID"]
SCOPES       = os.environ["SCOPES"]
MODEL_ID     = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-4-6")

session = boto3.Session()

_sm = session.client("secretsmanager", region_name=REGION)
CLIENT_SECRET = _sm.get_secret_value(SecretId=os.environ["CLIENT_SECRET_NAME"])["SecretString"]

dp_client = session.client("agent-registry", region_name=REGION)


@tool
def discover_and_execute(request: str) -> str:
    """Search the AWS Agent Registry for relevant tools and agents, then execute the request.

    Args:
        request: The user request to process.

    Returns:
        The response from executing the request with dynamically discovered tools.
    """
    access_token = fetch_oauth_token(COGNITO_DOMAIN, CLIENT_ID, CLIENT_SECRET, SCOPES, REGION)

    search_queries = [
        request,
        "order management status tracking cancel update",
        "pricing discount promo code savings",
        "customer support returns refunds complaints",
    ]
    all_records = {}
    for q in search_queries:
        results = dp_client.search_discoverable_registry_records(
            registryIds=[REGISTRY_ARN], searchQuery=q, maxResults=5,
        ).get("registryRecords", [])
        for rec in results:
            name = rec.get("name", "")
            if name not in all_records:
                all_records[name] = rec

    records = list(all_records.values())
    if not records:
        return "No tools found in registry for this request."

    mcp_clients, a2a_tools, seen_urls = [], [], set()
    for rec in records:
        meta = parse_server_metadata(rec)
        if meta["protocol"] == "MCP":
            url = meta.get("url")
            if url and url not in seen_urls:
                c = create_mcp_client_from_metadata(meta, access_token)
                if c:
                    mcp_clients.append(c)
                    seen_urls.add(url)
        elif meta["protocol"] == "A2A":
            fn = create_a2a_tool_from_metadata(meta, session, REGION)
            if fn:
                a2a_tools.append(fn)

    if not mcp_clients and not a2a_tools:
        return "No tools could be instantiated from registry results."

    model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
    started_clients = []
    try:
        mcp_tools = []
        for c in mcp_clients:
            c.start()
            started_clients.append(c)
            mcp_tools.extend(c.list_tools_sync())

        sub_agent = Agent(
            model=model,
            tools=mcp_tools + a2a_tools,
            system_prompt=(
                "You are an Order Management & Customer Service assistant. "
                "Use the RIGHT tool for each request:\\n"
                "- Order lookups, status, cancellations, address changes: use MCP tools\\n"
                "- Pricing, discounts, promo codes: use the pricing_agent tool\\n"
                "- Returns, refunds, complaints, escalations: use the customer_support_agent tool\\n"
                "Always use the most specific tool for the task."
            ),
        )
        return str(sub_agent(request))
    finally:
        for c in started_clients:
            try:
                c.stop()
            except Exception:
                pass


model = BedrockModel(model_id=MODEL_ID, region_name=REGION)
agent = Agent(
    model=model,
    name="Orchestrator Agent",
    description="Order management orchestrator that discovers and invokes tools and agents from the AWS Agent Registry",
    system_prompt=(
        "You are an orchestrator agent. For every user request, use the "
        "discover_and_execute tool to search the registry and process it. "
        "Always pass the full user request to the tool."
    ),
    tools=[discover_and_execute],
)

app   = FastAPI()
a2a   = A2AServer(
    agent=agent,
    http_url=os.environ.get("AGENTCORE_RUNTIME_URL", "http://127.0.0.1:9000/"),
    serve_at_root=True,
)

@app.get("/ping")
def ping():
    return {"status": "healthy"}

app.mount("/", a2a.to_fastapi_app())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)
'''

with open("orchestrator_agent.py", "w") as f:
    f.write(ORCHESTRATOR_AGENT_CODE)

with open("orchestrator_requirements.txt", "w") as f:
    f.write("strands-agents[a2a]\nfastapi\nuvicorn\nmcp\nrequests\n")

print("✓ Orchestrator agent code written: orchestrator_agent.py")

if os.path.exists("Dockerfile"):
    os.remove("Dockerfile")

orchestrator_rt = Runtime()
orchestrator_rt.configure(
    entrypoint="orchestrator_agent.py",
    auto_create_execution_role=True,
    auto_create_ecr=True,
    requirements_file="orchestrator_requirements.txt",
    region=region,
    agent_name="orchestrator_agent",
    protocol="A2A",
)
orchestrator_launch = orchestrator_rt.launch(
    auto_update_on_conflict=True,
    env_vars={
        "REGISTRY_ARN": REGISTRY_ARN,
        "COGNITO_DOMAIN": cognito_domain,
        "CLIENT_ID": client_id,
        "CLIENT_SECRET_NAME": secret_name,
        "SCOPES": scopes,
        "MODEL_ID": MODEL_ID,
        "AWS_DEFAULT_REGION": region,
    },
)
orchestrator_agent_id = orchestrator_launch.agent_id
orchestrator_arn = orchestrator_launch.agent_arn
print(f"✓ Orchestrator deployed: {orchestrator_agent_id}")

# Grant orchestrator role permissions for Secrets Manager and Registry
agent_info = cp_client.get_agent_runtime(agentRuntimeId=orchestrator_agent_id)
orch_role_arn = agent_info.get("roleArn") or agent_info.get("agentRuntimeRoleArn", "")
if orch_role_arn:
    orch_role_name = orch_role_arn.split("/")[-1]

    iam_client.put_role_policy(
        RoleName=orch_role_name,
        PolicyName="SecretsManagerReadAccess",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "secretsmanager:GetSecretValue",
                        "Resource": f"arn:aws:secretsmanager:{region}:{account_id}:secret:{secret_name}*",
                    }
                ],
            }
        ),
    )
    iam_client.put_role_policy(
        RoleName=orch_role_name,
        PolicyName="RegistrySearchAccess",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": "agent-registry:SearchDiscoverableRegistryRecords",
                        "Resource": f"arn:aws:agent-registry:*:{account_id}:registry/*",
                    }
                ],
            }
        ),
    )
    print(f"✓ Secrets Manager + Registry search access granted to: {orch_role_name}")

print(f"✓ Orchestrator status: {agent_info['status']}")

# ── 4. End-to-End Demos ───────────────────────────────────────────────────────
print("\n=== 4. End-to-End Demos ===")
print("Each demo triggers the orchestrator to: search Registry → instantiate tools → execute\n")

from utils import invoke_orchestrator

# Demo 1: Order Status (MCP Tool)
print("─── Demo 1: Order Status — MCP Tool Invocation ───")
result = invoke_orchestrator(
    "What is the current status and tracking info for order 123?",
    agentcore_client,
    orchestrator_arn,
)
print(f"\n── Response ──\n{result}")

print("\n─── Demo 2: Pricing & Discounts — MCP + A2A Multi-Agent ───")
result = invoke_orchestrator(
    "Order 123 has 2x Widget Pro at $99.98. What discount tiers or promo codes can reduce the price?",
    agentcore_client,
    orchestrator_arn,
)
print(f"\n── Response ──\n{result}")

print("\n─── Demo 3: Return & Refund — Customer Support Decision ───")
result = invoke_orchestrator(
    "I want to return order 789 (Premium Headphones, delivered March 10). Am I eligible for a return and what is the refund amount?",
    agentcore_client,
    orchestrator_arn,
)
print(f"\n── Response ──\n{result}")

# ── Cleanup ───────────────────────────────────────────────────────────────────
print("\n=== Cleanup ===")
print("Running cleanup.py to delete all created resources...")

# cleanup.py uses variables from this scope — import and run it
# Alternatively, run as subprocess:
# subprocess.run([sys.executable, "cleanup.py"], check=False)
#
# The cleanup.py script in this directory handles all resource deletion.
# Run it separately if you want to preserve resources for further testing.
print("To clean up all resources, run: python cleanup.py")
print("(Edit cleanup.py to set the resource IDs captured in this session.)")
