"""
Synchronize metadata in AWS Agent Registry from MCP Server

Demonstrates URL-based synchronization for three scenarios:
  1. List available registries
  2. Create a registry (IAM auth, no auto-approval rules)
  3. Synchronize a public unprotected MCP server (AWS Knowledge MCP)
  4. Synchronize an OAuth-protected MCP server deployed on AgentCore Runtime
  5. Synchronize an IAM-protected MCP server deployed on AgentCore Runtime
  6. List all records in the registry
  7. Cleanup

After synchronization, the record starts in CREATING, transitions to DRAFT,
and contains server + tools descriptors extracted from the MCP server endpoint.

Usage:
    python registry_synchronize_mcpserver.py

Prerequisites:
    - boto3 >= 1.42.87
    - bedrock-agentcore-starter-toolkit (for sections 4 and 5 which deploy to Runtime)
    - AWS credentials configured
    - AWS_DEFAULT_REGION set (default: us-west-2)

Note:
    Sections 4 and 5 deploy MCP servers to AgentCore Runtime and require
    additional IAM permissions for ECR, CodeBuild, and AgentCore Runtime.
    The Cognito OAuth setup in section 4 creates real AWS resources.
"""

import json
import os
import time

import boto3

# ── Configuration ─────────────────────────────────────────────────────────────
os.environ["AWS_SDK_LOAD_CONFIG"] = "1"

AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")

session = boto3.Session(region_name=AWS_REGION)
registry_client = session.client("agent-registry-control")

# AgentCore Identity client (for OAuth credential providers and runtime management)
identity_client = session.client("bedrock-agentcore-control")
iam_client = session.client("iam")

ACCOUNT_ID = session.client("sts").get_caller_identity()["Account"]

TIMESTAMP = int(time.time())

print(f"Session ready | Account: {ACCOUNT_ID} | Region: {AWS_REGION}")


def pp(response):
    data = {k: v for k, v in response.items() if k != "ResponseMetadata"}
    print(json.dumps(data, indent=2, default=str))


# ── Polling helpers ───────────────────────────────────────────────────────────


def wait_for_registry(registry_id, interval=5):
    while True:
        resp = registry_client.get_registry(registryId=registry_id)
        status = resp["status"]
        print(f"  Registry Status: {status}")
        if status == "READY":
            resp.pop("ResponseMetadata", None)
            print(json.dumps(resp, indent=2, default=str))
            return resp
        if status.endswith("_FAILED"):
            raise Exception(f"Registry failed: {status} - {resp.get('statusReason')}")  # noqa: TRY002
        time.sleep(interval)


def wait_for_record(registry_id, record_id, interval=10, max_wait=120):
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval
        record = registry_client.get_registry_record(registryId=registry_id, recordId=record_id)
        status = record["status"]
        print(f"  Record Status: {status}")
        if status in ("DRAFT", "PENDING_APPROVAL", "APPROVED", "ACTIVE"):
            pp(record)
            return record
        if "FAILED" in status:
            print(f"  Reason: {record.get('statusReason', 'Unknown')}")
            pp(record)
            return record
    print(f"  Timed out after {max_wait}s")
    return record


# ── 1. List Registries ────────────────────────────────────────────────────────
print("\n=== 1. List Registries ===")

registries = registry_client.list_registries()
print(f"Found {len(registries['registries'])} registries:\n")
for reg in registries["registries"]:
    print(f"  [{reg['status']}] {reg['name']} ({reg['registryId']})")

# ── 2. Create Registry ────────────────────────────────────────────────────────
print("\n=== 2. Create Registry with IAM permissions ===")

create_resp = registry_client.create_registry(
    name=f"RegistryforMCPServer_{TIMESTAMP}",
    description="Registry to publish MCP server records",
    approvalConfiguration={"autoApprovalRules": []},
)

REGISTRY_ARN = create_resp["registryArn"]
REGISTRY_ID = REGISTRY_ARN.split("/")[-1]

wait_for_registry(REGISTRY_ID)

print("Registry created!")
print(f"  Registry ARN: {REGISTRY_ARN}")
print(f"  Registry ID:  {REGISTRY_ID}")

# ── 3. Synchronize from Public MCP Server ────────────────────────────────────
print("\n=== 3. Synchronize record from Public MCP server ===")

MCP_PUBLIC_URL = "https://knowledge-mcp.global.api.aws"

print("Creating registry record from public AWS Knowledge MCP server...")
record_response = registry_client.create_registry_record(
    registryId=REGISTRY_ID,
    name="aws_knowledge_mcp_server",
    recordType="MCP",
    descriptors={"mcpServer": {"source": {"fromUrl": {"url": MCP_PUBLIC_URL}}}},
)

print("Registry record created successfully!")
print(f"Record ARN: {record_response.get('recordArn')}")
pp(record_response)

MCP_RECORD_ID = record_response["recordArn"].split("/")[-1]
print(f"\nStored MCP_RECORD_ID: {MCP_RECORD_ID}")

wait_for_record(REGISTRY_ID, MCP_RECORD_ID)

# ── 4. Synchronize from OAuth-Protected MCP Server on AgentCore Runtime ───────
print("\n=== 4. Synchronize from OAuth-Protected MCP Server ===")
print("Note: This section deploys an MCP server to AgentCore Runtime and creates Cognito resources.")

IT_OPS_TOOLKIT_CODE = '''"""IT Operations MCP Server - plain JSON, no SSE, no FastMCP."""
import json
import random
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

def get_service_health(service_name):
    """Check service health and uptime metrics."""
    services = {
        "payment-api":    {"status": "healthy",  "uptime": "99.97%", "latency_ms": 45,  "error_rate": "0.02%"},
        "auth-service":   {"status": "healthy",  "uptime": "99.99%", "latency_ms": 12,  "error_rate": "0.01%"},
        "order-service":  {"status": "degraded", "uptime": "98.5%",  "latency_ms": 230, "error_rate": "1.2%"},
        "inventory-db":   {"status": "healthy",  "uptime": "99.95%", "latency_ms": 8,   "error_rate": "0.03%"},
    }
    data = services.get(service_name.lower(), {
        "status": random.choice(["healthy", "degraded", "down"]),
        "uptime": f"{random.uniform(95, 99.99):.2f}%",
        "latency_ms": random.randint(5, 500),
        "error_rate": f"{random.uniform(0, 5):.2f}%"
    })
    return {"service": service_name, **data, "checked_at": datetime.now(timezone.utc).isoformat()}

def create_incident(title, severity, service_name, description=""):
    """Create an incident ticket in the ITSM system."""
    incident_id = f"INC-{random.randint(10000, 99999)}"
    return {"incident_id": incident_id, "title": title, "severity": severity,
            "service": service_name, "status": "OPEN", "assigned_team": "SRE-OnCall",
            "created_at": datetime.now(timezone.utc).isoformat()}

TOOLS = [
    {"name": "get_service_health", "description": "Check service health and uptime metrics",
     "inputSchema": {"type": "object", "properties": {"service_name": {"type": "string"}}, "required": ["service_name"]}},
    {"name": "create_incident", "description": "Create an incident ticket in the ITSM system",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}, "severity": {"type": "string"}, "service_name": {"type": "string"}}, "required": ["title", "severity", "service_name"]}},
]
TOOL_FNS = {"get_service_health": get_service_health, "create_incident": create_incident}

def handle_jsonrpc(request):
    method, req_id, params = request.get("method"), request.get("id"), request.get("params", {})
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "it-ops-toolkit", "version": "1.0.0"}}}
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments", {})
        if name in TOOL_FNS:
            result = TOOL_FNS[name](**args)
            return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}}
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}

class MCPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        resp   = handle_jsonrpc(body)
        if resp is None:
            self.send_response(204); self.end_headers()
        else:
            payload = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers(); self.wfile.write(payload)
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(b\'{"status":"ok"}\')
    def log_message(self, format, *args): pass

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8000), MCPHandler)
    print("IT Operations MCP server running on http://0.0.0.0:8000")
    server.serve_forever()
'''

with open("it_ops_toolkit.py", "w") as f:
    f.write(IT_OPS_TOOLKIT_CODE)

with open("server_requirements.txt", "w") as f:
    f.write("# No external dependencies - uses Python stdlib only\n")

# 4.3 Create Cognito OAuth Provider
print("\n4.3 Create Cognito OAuth Provider...")
cognito = session.client("cognito-idp")

pool_resp = cognito.create_user_pool(
    PoolName=f"mcp-json-pool-{TIMESTAMP}",
    Policies={"PasswordPolicy": {"MinimumLength": 8}},
)
USER_POOL_ID = pool_resp["UserPool"]["Id"]
print(f"✓ User Pool: {USER_POOL_ID}")

cognito.create_resource_server(
    UserPoolId=USER_POOL_ID,
    Identifier="mcp-server",
    Name="MCP Server",
    Scopes=[{"ScopeName": "invoke", "ScopeDescription": "Invoke MCP tools"}],
)

app_resp = cognito.create_user_pool_client(
    UserPoolId=USER_POOL_ID,
    ClientName="mcp-m2m-client",
    GenerateSecret=True,
    AllowedOAuthFlows=["client_credentials"],
    AllowedOAuthScopes=["mcp-server/invoke"],
    AllowedOAuthFlowsUserPoolClient=True,
)
CLIENT_ID = app_resp["UserPoolClient"]["ClientId"]
CLIENT_SECRET = app_resp["UserPoolClient"]["ClientSecret"]
print(f"✓ Client ID: {CLIENT_ID}")

cognito.create_user_pool_domain(Domain=f"mcp-json-{TIMESTAMP}", UserPoolId=USER_POOL_ID)
COGNITO_DISCOVERY_URL = (
    f"https://cognito-idp.{AWS_REGION}.amazonaws.com/{USER_POOL_ID}/.well-known/openid-configuration"
)
print(f"✓ Discovery URL: {COGNITO_DISCOVERY_URL}")

# 4.4 Create AgentCore OAuth2 Credential Provider
print("\n4.4 Create AgentCore OAuth2 Credential Provider...")
oauth_resp = identity_client.create_oauth2_credential_provider(
    name=f"mcp_json_provider_{TIMESTAMP}",
    credentialProviderVendor="CustomOauth2",
    oauth2ProviderConfigInput={
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {"discoveryUrl": COGNITO_DISCOVERY_URL},
            "clientId": CLIENT_ID,
            "clientSecret": CLIENT_SECRET,
        }
    },
)
OAUTH_PROVIDER_ARN = oauth_resp["credentialProviderArn"]
print(f"✓ OAuth Provider ARN: {OAUTH_PROVIDER_ARN}")  # codeql[py/clear-text-logging-sensitive-data]

# 4.5 Deploy MCP server to AgentCore Runtime with OAuth
print("\n4.5 Deploy MCP server to AgentCore Runtime with OAuth...")
from bedrock_agentcore_starter_toolkit import Runtime

if os.path.exists("Dockerfile"):
    os.remove("Dockerfile")

auth_config = {
    "customJWTAuthorizer": {
        "allowedClients": [CLIENT_ID],
        "discoveryUrl": COGNITO_DISCOVERY_URL,
    }
}

runtime = Runtime()
runtime.configure(
    entrypoint="it_ops_toolkit.py",
    auto_create_execution_role=True,
    auto_create_ecr=True,
    requirements_file="server_requirements.txt",
    region=AWS_REGION,
    authorizer_configuration=auth_config,
    protocol="MCP",
    agent_name=f"it_ops_oauth{TIMESTAMP}",
)
print("✓ Configured")

print("Deploying... (this may take several minutes)")
launch_result = runtime.launch(auto_update_on_conflict=True)
RUNTIME_ARN = launch_result.agent_arn
RUNTIME_ID = launch_result.agent_id
ENCODED_ARN = RUNTIME_ARN.replace(":", "%3A").replace("/", "%2F")
MCP_SERVER_URL = (
    f"https://bedrock-agentcore.{AWS_REGION}.amazonaws.com/runtimes/{ENCODED_ARN}/invocations?qualifier=DEFAULT"
)

print(f"✓ Runtime ARN: {RUNTIME_ARN}")
print(f"✓ MCP Server URL: {MCP_SERVER_URL}")

# 4.6 Publish to Registry with OAuth synchronization
print("\n4.6 Publish to Registry with OAuth synchronization...")
record_response = registry_client.create_registry_record(
    registryId=REGISTRY_ID,
    name=f"mcp_json_oauth_{TIMESTAMP}",
    recordType="MCP",
    descriptors={
        "mcpServer": {
            "source": {
                "fromUrl": {
                    "url": MCP_SERVER_URL,
                    "credentialProviderConfigurations": [
                        {
                            "credentialProviderType": "OAUTH",
                            "credentialProvider": {
                                "oauthCredentialProvider": {
                                    "providerArn": OAUTH_PROVIDER_ARN,
                                    "grantType": "CLIENT_CREDENTIALS",
                                    "scopes": ["mcp-server/invoke"],
                                }
                            },
                        }
                    ],
                }
            }
        }
    },
)

OAUTH_RECORD_ID = record_response["recordArn"].split("/")[-1]
print(
    f"✓ Record: {OAUTH_RECORD_ID} - Status: {record_response['status']}"
)  # codeql[py/clear-text-logging-sensitive-data]
pp(record_response)

wait_for_record(REGISTRY_ID, OAUTH_RECORD_ID)

# ── 5. Synchronize from IAM-Protected MCP Server ──────────────────────────────
print("\n=== 5. Synchronize from IAM-Protected MCP Server ===")

ECOMMERCE_SERVER_CODE = '''"""E-Commerce Order Management MCP Server - plain JSON."""
import json
import random
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

def get_order_details(order_id):
    """Retrieve order information by order ID."""
    return {"order_id": order_id, "status": random.choice(["PENDING", "PROCESSING", "SHIPPED", "DELIVERED"]),
            "items": [{"sku": "SKU-1001", "name": "Wireless Headphones", "qty": 1, "price": 79.99}], "total": 79.99}

def track_shipment(order_id):
    """Get real-time shipment tracking status."""
    return {"order_id": order_id, "carrier": "UPS", "status": random.choice(["IN_TRANSIT", "DELIVERED"]),
            "estimated_delivery": "2026-04-10", "updated_at": datetime.now(timezone.utc).isoformat()}

TOOLS = [
    {"name": "get_order_details", "description": "Retrieve order information by order ID",
     "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}},
    {"name": "track_shipment", "description": "Get real-time shipment tracking status",
     "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}},
]
TOOL_FNS = {"get_order_details": get_order_details, "track_shipment": track_shipment}

def handle_jsonrpc(request):
    method, req_id, params = request.get("method"), request.get("id"), request.get("params", {})
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "ecommerce-order-toolkit", "version": "1.0.0"}}}
    elif method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments", {})
        if name in TOOL_FNS:
            result = TOOL_FNS[name](**args)
            return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": json.dumps(result)}]}}
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}

class MCPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}
        resp   = handle_jsonrpc(body)
        if resp is None:
            self.send_response(204); self.end_headers()
        else:
            payload = json.dumps(resp).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers(); self.wfile.write(payload)
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(b\'{"status":"ok"}\')
    def log_message(self, format, *args): pass

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8000), MCPHandler)
    print("E-Commerce Order MCP server running on http://0.0.0.0:8000")
    server.serve_forever()
'''

with open("ecommerce_order_toolkit.py", "w") as f:
    f.write(ECOMMERCE_SERVER_CODE)

# 5.3 Deploy IAM-protected MCP server
print("\n5.3 Deploy IAM-protected MCP server to AgentCore Runtime...")
if os.path.exists("Dockerfile"):
    os.remove("Dockerfile")

runtime2 = Runtime()
runtime2.configure(
    entrypoint="ecommerce_order_toolkit.py",
    auto_create_execution_role=True,
    auto_create_ecr=True,
    requirements_file="server_requirements.txt",
    region=AWS_REGION,
    protocol="MCP",
    agent_name=f"ecom_order_iam_{TIMESTAMP}",
)
print("✓ Configured (IAM-protected, no JWT authorizer)")

print("Deploying... (this may take several minutes)")
launch_result2 = runtime2.launch(auto_update_on_conflict=True)
RUNTIME_ARN2 = launch_result2.agent_arn
RUNTIME_ID2 = launch_result2.agent_id
ENCODED_ARN2 = RUNTIME_ARN2.replace(":", "%3A").replace("/", "%2F")
MCP_IAM_URL = (
    f"https://bedrock-agentcore.{AWS_REGION}.amazonaws.com/runtimes/{ENCODED_ARN2}/invocations?qualifier=DEFAULT"
)

print(f"✓ Runtime ARN: {RUNTIME_ARN2}")
print(f"✓ MCP IAM URL: {MCP_IAM_URL}")

# 5.4 Create IAM role for Registry to invoke Runtime
print("\n5.4 Create IAM role for Registry to invoke Runtime...")
IAM_ROLE_NAME = f"RegistrySyncRole_{TIMESTAMP}"

trust_policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "agent-registry.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}
invoke_policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:InvokeAgentRuntime",
                "bedrock-agentcore:InvokeAgent",
            ],
            "Resource": [RUNTIME_ARN2, f"{RUNTIME_ARN2}/*"],
        }
    ],
}

try:
    role_resp = iam_client.create_role(
        RoleName=IAM_ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(trust_policy),
        Description="Role for Registry to invoke AgentCore Runtime MCP server",
    )
    IAM_ROLE_ARN = role_resp["Role"]["Arn"]
    print(f"✓ Created IAM role: {IAM_ROLE_ARN}")
except iam_client.exceptions.EntityAlreadyExistsException:
    IAM_ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/{IAM_ROLE_NAME}"
    print(f"✓ Role already exists: {IAM_ROLE_ARN}")

iam_client.put_role_policy(
    RoleName=IAM_ROLE_NAME,
    PolicyName="InvokeAgentCoreRuntime",
    PolicyDocument=json.dumps(invoke_policy),
)
print("✓ Policy attached. Waiting 10s for IAM propagation...")
time.sleep(10)

# 5.5 Synchronize record with IAM auth
print("\n5.5 Synchronize records to Registry with IAM authentication...")
record_response = registry_client.create_registry_record(
    registryId=REGISTRY_ID,
    name=f"mcp_iam_record_{TIMESTAMP}",
    recordType="MCP",
    descriptors={
        "mcpServer": {
            "source": {
                "fromUrl": {
                    "url": MCP_IAM_URL,
                    "credentialProviderConfigurations": [
                        {
                            "credentialProviderType": "IAM",
                            "credentialProvider": {
                                "iamCredentialProvider": {
                                    "roleArn": IAM_ROLE_ARN,
                                    "service": "bedrock-agentcore",
                                    "region": AWS_REGION,
                                }
                            },
                        }
                    ],
                }
            }
        }
    },
)

IAM_RECORD_ID = record_response["recordArn"].split("/")[-1]
print(f"✓ Record: {IAM_RECORD_ID} - Status: {record_response['status']}")
pp(record_response)

wait_for_record(REGISTRY_ID, IAM_RECORD_ID)

# ── 6. List all records ───────────────────────────────────────────────────────
print("\n=== 6. List all records ===")
records = registry_client.list_registry_records(registryId=REGISTRY_ID)
print(f"Found {len(records['registryRecords'])} records:\n")
for rec in records["registryRecords"]:
    print(f"  [{rec['status']}] {rec['name']} | {rec['recordId']}")

# ── 7. Cleanup ────────────────────────────────────────────────────────────────
print("\n=== 7. Cleanup ===")

# Delete registry records
try:
    records = registry_client.list_registry_records(registryId=REGISTRY_ID)
    for rec in records["registryRecords"]:
        registry_client.delete_registry_record(registryId=REGISTRY_ID, recordId=rec["recordId"])
        print(f"✓ Deleted record: {rec['recordId']}")
except Exception as e:  # noqa: BLE001
    print(f"  Records cleanup: {e}")

# Delete registry
try:
    registry_client.delete_registry(registryId=REGISTRY_ID)
    print(f"✓ Deleted registry: {REGISTRY_ID}")
except Exception as e:  # noqa: BLE001
    print(f"  Registry cleanup: {e}")

# Delete AgentCore Runtimes
for rid, rname in [(RUNTIME_ID, "OAuth"), (RUNTIME_ID2, "IAM")]:
    try:
        identity_client.delete_agent_runtime(agentRuntimeId=rid)
        print(f"✓ Deleted {rname} runtime: {rid}")
    except Exception as e:  # noqa: BLE001
        print(f"  {rname} runtime cleanup: {e}")

# Delete OAuth2 Credential Provider
try:
    identity_client.delete_oauth2_credential_provider(name=f"mcp_json_provider_{TIMESTAMP}")
    print("✓ Deleted OAuth provider")
except Exception as e:  # noqa: BLE001
    print(f"  OAuth provider cleanup: {e}")

# Delete Cognito resources
try:
    cognito.delete_user_pool_domain(Domain=f"mcp-json-{TIMESTAMP}", UserPoolId=USER_POOL_ID)
    print("✓ Deleted Cognito domain")
    cognito.delete_user_pool(UserPoolId=USER_POOL_ID)
    print(f"✓ Deleted Cognito pool: {USER_POOL_ID}")
except Exception as e:  # noqa: BLE001
    print(f"  Cognito cleanup: {e}")

# Delete IAM role
try:
    iam_client.delete_role_policy(RoleName=IAM_ROLE_NAME, PolicyName="InvokeAgentCoreRuntime")
    iam_client.delete_role(RoleName=IAM_ROLE_NAME)
    print(f"✓ Deleted IAM role: {IAM_ROLE_NAME}")
except Exception as e:  # noqa: BLE001
    print(f"  IAM role cleanup: {e}")

# Delete local server files
for f_path in [
    "ecommerce_order_toolkit.py",
    "it_ops_toolkit.py",
    "server_requirements.txt",
    "Dockerfile",
]:
    if os.path.exists(f_path):
        os.remove(f_path)
        print(f"✓ Deleted local file: {f_path}")

print("\n✓ Cleanup complete!")
