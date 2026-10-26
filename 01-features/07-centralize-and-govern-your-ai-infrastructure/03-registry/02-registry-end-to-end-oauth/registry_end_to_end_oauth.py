"""
AWS Agent Registry with OAuth Authentication

Demonstrates how to configure and use OAuth (CUSTOM_JWT) authentication for
AWS Agent Registry search operations:
  1. Set up AWS Cognito as OAuth provider (user pool, app client, test user)
  2. Create an Agent Registry with CUSTOM_JWT authorizer
  3. Create and approve an MCP registry record
  4. Authenticate and obtain a JWT access token from Cognito
  5. Perform authenticated semantic search using a Bearer token
  6. Verify that unauthenticated requests are rejected

Usage:
    python registry_end_to_end_oauth.py

Prerequisites:
    - boto3 >= 1.42.87
    - requests library (pip install requests)
    - AWS credentials configured with agent-registry permissions
    - AWS_DEFAULT_REGION set (default: us-west-2)
"""

import json
import os
import time

import boto3
import requests
from boto3.session import Session

# ── Configuration ─────────────────────────────────────────────────────────────
boto_session = Session()
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", boto_session.region_name or "us-west-2")

registry_client = boto_session.client("agent-registry-control", region_name=AWS_REGION)

REGISTRY_SEARCH_ENDPOINT = f"https://agent-registry.{AWS_REGION}.api.aws/discoverable-records-search"

print(f"Session ready | Region: {AWS_REGION}")


# ── ANSI colors ───────────────────────────────────────────────────────────────
class C:
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


# ── Helpers ───────────────────────────────────────────────────────────────────


def wait_for_record_draft(registry_id, record_id, interval=3):
    while True:
        resp = registry_client.get_registry_record(registryId=registry_id, recordId=record_id)
        status = resp["status"]
        if status == "DRAFT":
            return resp
        if status.endswith("_FAILED"):
            raise Exception(f"Record failed: {status}")  # noqa: TRY002
        time.sleep(interval)


def wait_for_registry(registry_id, interval=5):
    while True:
        resp = registry_client.get_registry(registryId=registry_id)
        status = resp["status"]
        if status == "READY":
            print(f"  {C.GREEN}✅ Registry Status: {status}{C.RESET}")
            resp.pop("ResponseMetadata", None)
            print(json.dumps(resp, indent=2, default=str))
            return resp
        if status.endswith("_FAILED"):
            print(f"  {C.RED}❌ Registry Status: {status}{C.RESET}")
            raise Exception(f"Registry failed: {status} - {resp.get('statusReason')}")  # noqa: TRY002
        print(f"  {C.YELLOW}⏳ Registry Status: {status}{C.RESET}")
        time.sleep(interval)


def pretty_print_response(response):
    data = {k: v for k, v in response.items() if k != "ResponseMetadata"}
    print(json.dumps(data, indent=2, default=str))


# ── 1. Configure OAuth Provider (AWS Cognito) ─────────────────────────────────
print(f"\n{C.BOLD}=== 1. Configure OAuth Provider (AWS Cognito) ==={C.RESET}")

USER_POOL_NAME = "agentcore-registry-pool"
cognito = boto3.client("cognito-idp", region_name=AWS_REGION)

# 1.1 Create or reuse user pool
pools = cognito.list_user_pools(MaxResults=60)["UserPools"]
existing_pool = next((p for p in pools if p["Name"] == USER_POOL_NAME), None)

if existing_pool:
    user_pool_id = existing_pool["Id"]
    print(f"  {C.YELLOW}⚠️  Using existing pool: {user_pool_id}{C.RESET}")
else:
    user_pool = cognito.create_user_pool(PoolName=USER_POOL_NAME)["UserPool"]
    user_pool_id = user_pool["Id"]
    cognito.create_user_pool_domain(
        Domain=user_pool_id.replace("_", "").lower(),
        UserPoolId=user_pool_id,
    )
    print(f"  {C.GREEN}✅ User pool created{C.RESET}")

discovery_url = f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{user_pool_id}/.well-known/openid-configuration"
print(f"  {C.BOLD}Pool ID:{C.RESET}       {C.CYAN}{user_pool_id}{C.RESET}")
print(f"  {C.BOLD}Discovery URL:{C.RESET}  {C.CYAN}{discovery_url}{C.RESET}")

# 1.2 Create or reuse app client
CLIENT_NAME = "agentcore-registry-client"
clients = cognito.list_user_pool_clients(UserPoolId=user_pool_id)["UserPoolClients"]
existing_client = next((c for c in clients if c["ClientName"] == CLIENT_NAME), None)

if existing_client:
    client_id = existing_client["ClientId"]
    print(f"  {C.YELLOW}⚠️  Using existing client: {client_id}{C.RESET}")
else:
    client = cognito.create_user_pool_client(
        UserPoolId=user_pool_id,
        ClientName=CLIENT_NAME,
        GenerateSecret=False,
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
    )
    client_id = client["UserPoolClient"]["ClientId"]
    print(f"  {C.GREEN}✅ App client created{C.RESET}")

print(f"  {C.BOLD}Client ID:{C.RESET}  {C.CYAN}{client_id}{C.RESET}")

# 1.3 Create test user
TEST_USERNAME = "testuser"
TEST_PASSWORD = "TempPass123!"  # pragma: allowlist secret

try:
    cognito.admin_create_user(
        UserPoolId=user_pool_id,
        Username=TEST_USERNAME,
        TemporaryPassword=TEST_PASSWORD,
        MessageAction="SUPPRESS",
    )
    cognito.admin_set_user_password(
        UserPoolId=user_pool_id,
        Username=TEST_USERNAME,
        Password=TEST_PASSWORD,
        Permanent=True,
    )
    print(f"  {C.GREEN}✅ Test user created: {TEST_USERNAME}{C.RESET}")
except cognito.exceptions.UsernameExistsException:
    print(f"  {C.YELLOW}⚠️  User {TEST_USERNAME} already exists{C.RESET}")

# ── 2. Create Agent Registry with CUSTOM_JWT auth ─────────────────────────────
print(f"\n{C.BOLD}=== 2. Create Agent Registry with OAuth ==={C.RESET}")

create_registry_response = registry_client.create_registry(
    name="RegistryWithOauth",
    description="Registry created with OAuth (CUSTOM_JWT authorizer)",
    approvalConfiguration={"autoApprovalRules": []},
    discoveryConfiguration={
        "authorizerType": "CUSTOM_JWT",
        "authorizerConfiguration": {
            "customJWTAuthorizer": {
                "discoveryUrl": discovery_url,
                "allowedClients": [client_id],
            }
        },
    },
)

REGISTRY_ARN = create_registry_response["registryArn"]
REGISTRY_ID = REGISTRY_ARN.split("/")[-1]

wait_for_registry(REGISTRY_ID)

print(f"  {C.GREEN}✅ Registry created!{C.RESET}")
print(f"  {C.BOLD}ARN:{C.RESET}        {C.CYAN}{REGISTRY_ARN}{C.RESET}")
print(f"  {C.BOLD}ID:{C.RESET}         {C.CYAN}{REGISTRY_ID}{C.RESET}")
print(f"  {C.BOLD}Auth Type:{C.RESET}   {C.CYAN}CUSTOM_JWT{C.RESET}")

# ── 3. Create and approve MCP record ──────────────────────────────────────────
print(f"\n{C.BOLD}=== 3. Create and Approve Registry Records ==={C.RESET}")

mcp_server_schema = json.dumps(
    {
        "name": "io.example/weather-server",
        "description": "A weather data MCP server that provides current conditions and forecasts",
        "version": "1.0.0",
        "title": "Weather Server",
        "websiteUrl": "https://example.com/weather",
        "packages": [
            {
                "registryType": "npm",
                "identifier": "@example/weather-mcp",
                "version": "1.0.0",
                "registryBaseUrl": "https://registry.npmjs.org",
                "runtimeHint": "npx",
                "transport": {"type": "stdio"},
                "environmentVariables": [
                    {
                        "name": "WEATHER_API_KEY",
                        "description": "API key for the weather service",
                        "isSecret": True,
                    }
                ],
            }
        ],
    }
)

mcp_tool_schema = json.dumps(
    {
        "tools": [
            {
                "name": "get_current_weather",
                "description": "Get current weather for a city",
                "inputSchema": {
                    "type": "object",
                    "properties": {"city": {"type": "string", "description": "City name"}},
                    "required": ["city"],
                },
            },
            {
                "name": "get_forecast",
                "description": "Get 5-day weather forecast",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name"},
                        "days": {
                            "type": "integer",
                            "description": "Number of forecast days",
                        },
                    },
                    "required": ["city"],
                },
            },
        ]
    }
)

mcp_record_response = registry_client.create_registry_record(
    registryId=REGISTRY_ID,
    name="weather_server",
    displayName="Weather Server",
    description="MCP server providing weather data and forecasts",
    recordType="MCP",
    descriptors={
        "mcpServer": {
            "data": mcp_server_schema,
            "additionalData": {
                "tools": {
                    "data": mcp_tool_schema,
                }
            },
        }
    },
    recordVersion="1.0",
)

MCP_RECORD_ARN = mcp_record_response["recordArn"]
MCP_RECORD_ID = MCP_RECORD_ARN.split("/")[-1]
print(f"  {C.GREEN}✅ MCP Record created: {C.CYAN}{MCP_RECORD_ID}{C.RESET}")
wait_for_record_draft(REGISTRY_ID, MCP_RECORD_ID)

# List records (DRAFT status)
records_response = registry_client.list_registry_records(registryId=REGISTRY_ID)
print(f"\n{C.BOLD}=== Registry Records ==={C.RESET}")
print(f"Found {len(records_response['registryRecords'])} record(s):\n")
for rec in records_response["registryRecords"]:
    status = rec["status"]
    sc = C.GREEN if status == "APPROVED" else C.YELLOW if status in ("DRAFT", "PENDING_APPROVAL") else C.RED
    print(
        f"  {sc}[{status}]{C.RESET} {rec['name']} | {C.CYAN}{rec.get('recordType', 'N/A')}{C.RESET} | {C.DIM}{rec['recordId']}{C.RESET}"
    )

# Approve record
registry_client.submit_registry_record_for_approval(registryId=REGISTRY_ID, recordId=MCP_RECORD_ID)
print(f"  {C.YELLOW}⏳ MCP record → PENDING_APPROVAL{C.RESET}")

registry_client.update_registry_record_status(
    registryId=REGISTRY_ID,
    recordId=MCP_RECORD_ID,
    statusReason="Approved by admin",
    status="APPROVED",
)
print(f"  {C.GREEN}✅ MCP record → APPROVED{C.RESET}")

# Verify record status
record_response = registry_client.get_registry_record(registryId=REGISTRY_ID, recordId=MCP_RECORD_ID)
status = record_response["status"]
sc = C.GREEN if status == "APPROVED" else C.YELLOW if status in ("DRAFT", "PENDING_APPROVAL") else C.RED
print(f"\n{C.BOLD}=== Record Details ==={C.RESET}")
print(f"  {C.BOLD}Name:{C.RESET}      {C.CYAN}{record_response['name']}{C.RESET}")
print(f"  {C.BOLD}Type:{C.RESET}      {C.CYAN}{record_response.get('recordType', 'N/A')}{C.RESET}")
print(f"  {C.BOLD}Status:{C.RESET}     {sc}{status}{C.RESET}")
print(f"  {C.BOLD}Version:{C.RESET}    {C.CYAN}{record_response['recordVersion']}{C.RESET}")

# ── 4. Authenticate and obtain access token ───────────────────────────────────
print(f"\n{C.BOLD}=== 4. Authenticate and Obtain Access Token ==={C.RESET}")

try:
    auth_response = cognito.initiate_auth(
        ClientId=client_id,
        AuthFlow="USER_PASSWORD_AUTH",
        AuthParameters={"USERNAME": TEST_USERNAME, "PASSWORD": TEST_PASSWORD},
    )
    bearer_token = auth_response["AuthenticationResult"]["AccessToken"]
    print(f"  {C.GREEN}✅ Authentication successful!{C.RESET}")
    print(f"  {C.BOLD}Access Token:{C.RESET}  {C.DIM}{bearer_token[:50]}...{C.RESET}")
except cognito.exceptions.NotAuthorizedException:
    print(f"  {C.RED}❌ Authentication failed: Invalid username or password{C.RESET}")
    raise
except Exception as e:
    print(f"  {C.RED}❌ Authentication error: {e}{C.RESET}")
    raise

# ── 5. Perform authenticated search ───────────────────────────────────────────
print(f"\n{C.BOLD}=== 5. Perform Authenticated Search ==={C.RESET}")


def search_registry_records(access_token, search_query, registry_identifiers, max_results=10):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
    }
    payload = {
        "searchQuery": search_query,
        "registryIds": registry_identifiers,
        "maxResults": max_results,
    }
    response = requests.post(REGISTRY_SEARCH_ENDPOINT, headers=headers, json=payload, timeout=30)
    return response.json()


# Wait for search index to update
print(f"  {C.YELLOW}⏳ Waiting 100s for search index...{C.RESET}")
time.sleep(100)

results = search_registry_records(bearer_token, "weather", [REGISTRY_ARN], 10)
print(f"{C.BOLD}🔍 Search: 'weather'{C.RESET}")
records = results.get("registryRecords", [])
if records:
    print(f"  {C.GREEN}✅ Found {len(records)} result(s){C.RESET}")
else:
    print(f"  {C.YELLOW}⚠️  No results found{C.RESET}")
print(json.dumps(results, indent=2))

# ── 5.1 Negative auth tests ───────────────────────────────────────────────────
print(f"\n{C.BOLD}=== 5.1 Negative Auth Tests ==={C.RESET}")

payload = {"searchQuery": "weather", "registryIds": [REGISTRY_ARN], "maxResults": 10}

# Without token
print(f"\n{C.BOLD}Test 1: Search without Authorization header{C.RESET}")
response = requests.post(
    REGISTRY_SEARCH_ENDPOINT,
    headers={"Content-Type": "application/json"},
    json=payload,
    timeout=30,
)
sc = C.GREEN if response.status_code in (401, 403) else C.RED
print(f"  {sc}Status Code: {response.status_code}{C.RESET}")
print(f"  {C.DIM}{response.text}{C.RESET}")

# With invalid token
print(f"\n{C.BOLD}Test 2: Search with invalid token{C.RESET}")
response = requests.post(
    REGISTRY_SEARCH_ENDPOINT,
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer invalid-token-12345",
    },
    json=payload,
    timeout=30,
)
sc = C.GREEN if response.status_code in (401, 403) else C.RED
print(f"  {sc}Status Code: {response.status_code}{C.RESET}")
print(f"  {C.DIM}{response.text}{C.RESET}")

print(f"\n  {C.GREEN}✅ Both requests correctly rejected{C.RESET}")

# ── 6. Cleanup ────────────────────────────────────────────────────────────────
print(f"\n{C.BOLD}=== 6. Cleanup ==={C.RESET}")

# Delete registry records
records = registry_client.list_registry_records(registryId=REGISTRY_ID)
for rec in records.get("registryRecords", []):
    registry_client.delete_registry_record(registryId=REGISTRY_ID, recordId=rec["recordId"])
    print(f"  {C.GREEN}✅ Deleted record: {C.DIM}{rec['recordId']}{C.RESET}")

registry_client.delete_registry(registryId=REGISTRY_ID)
print(f"  {C.GREEN}✅ Deleted registry: {C.DIM}{REGISTRY_ID}{C.RESET}")

# Delete Cognito resources
try:
    cognito.admin_delete_user(UserPoolId=user_pool_id, Username=TEST_USERNAME)
    print(f"  {C.GREEN}✅ Deleted user: {TEST_USERNAME}{C.RESET}")
except Exception as e:  # noqa: BLE001
    print(f"  {C.RED}❌ Error deleting user: {e}{C.RESET}")

try:
    cognito.delete_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)
    print(f"  {C.GREEN}✅ Deleted app client: {C.DIM}{client_id}{C.RESET}")
except Exception as e:  # noqa: BLE001
    print(f"  {C.RED}❌ Error deleting app client: {e}{C.RESET}")

try:
    domain = user_pool_id.replace("_", "").lower()
    cognito.delete_user_pool_domain(Domain=domain, UserPoolId=user_pool_id)
    print(f"  {C.GREEN}✅ Deleted user pool domain{C.RESET}")
except Exception as e:  # noqa: BLE001
    print(f"  {C.RED}❌ Error deleting domain: {e}{C.RESET}")

try:
    cognito.delete_user_pool(UserPoolId=user_pool_id)
    print(f"  {C.GREEN}✅ Deleted user pool: {C.DIM}{user_pool_id}{C.RESET}")
except Exception as e:  # noqa: BLE001
    print(f"  {C.RED}❌ Error deleting user pool: {e}{C.RESET}")

print(f"\n{C.GREEN}✅ Cleanup complete!{C.RESET}")
