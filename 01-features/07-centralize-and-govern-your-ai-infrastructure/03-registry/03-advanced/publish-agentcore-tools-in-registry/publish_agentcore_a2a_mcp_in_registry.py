"""
Publishing A2A Agent and MCP Server deployed on AgentCore in Registry

Full publisher workflow: deploy an MCP server and A2A agent for Order Management
to Amazon Bedrock AgentCore Runtime, verify both are working, then register them
in AWS Agent Registry, submit for approval, and verify semantic search.

Usage:
    python publish_agentcore_a2a_mcp_in_registry.py

Prerequisites:
    - boto3 >= 1.42.87
    - bedrock-agentcore-starter-toolkit (pip install -r requirements.txt)
    - strands-agents, strands-agents-tools, mcp
    - AWS credentials configured with permissions for AgentCore Runtime + Registry
    - AWS_DEFAULT_REGION set (or defaults to session region)

Note:
    This script deploys two containers to AgentCore Runtime. Deployment may take
    several minutes. MCP server code is written to agents/mcp/ and A2A agent code
    to agents/a2a/ before deployment.
"""

import json
import os
import time
import uuid

import requests
from bedrock_agentcore_starter_toolkit import Runtime
from boto3.session import Session
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

# ── Configuration ─────────────────────────────────────────────────────────────
boto_session = Session()
AWS_REGION = boto_session.region_name

registry_client = boto_session.client(
    "agent-registry-control",
    region_name=AWS_REGION,
)
search_client = boto_session.client(
    "agent-registry",
    region_name=AWS_REGION,
)

os.makedirs("agents/mcp", exist_ok=True)
os.makedirs("agents/a2a", exist_ok=True)

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


def pretty_print_response(response):
    data = {k: v for k, v in response.items() if k != "ResponseMetadata"}
    print(json.dumps(data, indent=2, default=str))


def wait_for_record_draft(registry_id, record_id, interval=3):
    while True:
        resp = registry_client.get_registry_record(registryId=registry_id, recordId=record_id)
        status = resp["status"]
        if status == "DRAFT":
            return resp
        if status.endswith("_FAILED"):
            raise Exception(f"Record failed: {status}")  # noqa: TRY002
        time.sleep(interval)


def signed_mcp_post(url, payload):
    credentials = boto_session.get_credentials().get_frozen_credentials()
    data = json.dumps(payload)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    req = AWSRequest(method="POST", url=url, data=data, headers=headers)
    SigV4Auth(credentials, "bedrock-agentcore", AWS_REGION).add_auth(req)
    resp = requests.post(url, headers=dict(req.headers), data=data, timeout=30)
    text = resp.text.strip()
    if text.startswith(("{", "[")):
        return json.loads(text)
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise ValueError(f"Could not parse MCP response (status {resp.status_code}): {text[:300]}")


def signed_get(url):
    credentials = boto_session.get_credentials().get_frozen_credentials()
    headers = {"Accept": "*/*"}
    req = AWSRequest(method="GET", url=url, headers=headers)
    SigV4Auth(credentials, "bedrock-agentcore", AWS_REGION).add_auth(req)
    return requests.get(url, headers=dict(req.headers), timeout=30)


def signed_a2a_post(url, payload):
    credentials = boto_session.get_credentials().get_frozen_credentials()
    data = json.dumps(payload)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    req = AWSRequest(method="POST", url=url, data=data, headers=headers)
    SigV4Auth(credentials, "bedrock-agentcore", AWS_REGION).add_auth(req)
    resp = requests.post(url, headers=dict(req.headers), data=data, timeout=30)
    text = resp.text.strip()
    if text.startswith(("{", "[")):
        return json.loads(text)
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:") :].strip())
    raise ValueError(f"Could not parse A2A response (status {resp.status_code}): {text[:300]}")


def wait_for_registry(registry_id, interval=5):
    while True:
        resp = registry_client.get_registry(registryId=registry_id)
        status = resp["status"]
        if status == "READY":
            print(f"  {C.GREEN}✅ Registry Status: {status}{C.RESET}")
            return resp
        if status.endswith("_FAILED"):
            print(f"  {C.RED}❌ Registry Status: {status}{C.RESET}")
            raise Exception(f"Registry failed: {status} - {resp.get('statusReason')}")  # noqa: TRY002
        print(f"  {C.YELLOW}⏳ Registry Status: {status}{C.RESET}")
        time.sleep(interval)


# ── 1. Write and Deploy MCP Server ───────────────────────────────────────────
print(f"\n{C.BOLD}=== 1. Deploy MCP Server to AgentCore Runtime ==={C.RESET}")

MCP_SERVER_CODE = '''"""MCP Order Management Server — exposes order CRUD tools via FastMCP."""
import uuid
from datetime import datetime
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="order-management-tools",
    instructions="A collection of order management tools for creating, updating, and managing orders.",
    host="0.0.0.0",
    stateless_http=True,
)

@mcp.tool()
def create_order(customer_name: str, product: str, quantity: int) -> str:
    """Create a new order for a customer."""
    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    return (f"Order created successfully. Order ID: {order_id}, Customer: {customer_name}, "
            f"Product: {product}, Quantity: {quantity}, Status: PENDING, "
            f"Created: {datetime.now().isoformat()}")

@mcp.tool()
def get_order(order_id: str) -> str:
    """Retrieve details of an existing order by its ID."""
    return (f"Order ID: {order_id}, Customer: Jane Smith, Product: Wireless Headphones, "
            f"Quantity: 2, Status: SHIPPED, Total: $149.98, "
            f"Created: 2025-01-15T10:30:00, Shipped: 2025-01-16T14:00:00")

@mcp.tool()
def update_order(order_id: str, quantity: int = None, product: str = None) -> str:
    """Update an existing order\'s quantity or product."""
    updates = []
    if quantity is not None:
        updates.append(f"Quantity: {quantity}")
    if product is not None:
        updates.append(f"Product: {product}")
    return (f"Order {order_id} updated successfully. "
            f"Changes: {\', \'.join(updates) if updates else \'None\'}, "
            f"Updated: {datetime.now().isoformat()}")

@mcp.tool()
def cancel_order(order_id: str, reason: str) -> str:
    """Cancel an existing order with a reason."""
    return (f"Order {order_id} cancelled successfully. Reason: {reason}, "
            f"Status: CANCELLED, Cancelled: {datetime.now().isoformat()}")

@mcp.tool()
def list_orders(status: str = "ALL") -> str:
    """List orders, optionally filtered by status."""
    return (f"Orders (filter: {status}):\\n"
            f"  1. ORD-A1B2C3D4 | Jane Smith    | Wireless Headphones (2) | SHIPPED\\n"
            f"  2. ORD-E5F6G7H8 | John Doe      | USB-C Cable (5)        | PENDING\\n"
            f"  3. ORD-I9J0K1L2 | Alice Johnson | Laptop Stand (1)       | DELIVERED")

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
'''

with open("agents/mcp/mcp_order_server.py", "w") as f:
    f.write(MCP_SERVER_CODE)
print("Wrote agents/mcp/mcp_order_server.py")

print("Configuring MCP AgentCore Runtime...")
mcp_runtime = Runtime()
mcp_runtime.configure(
    agent_name="mcp_order_server",
    protocol="MCP",
    entrypoint="agents/mcp/mcp_order_server.py",
    auto_create_execution_role=True,
    auto_create_ecr=True,
    requirements_file="agents/mcp/requirements.txt",
    region=AWS_REGION,
)
print(f"  {C.GREEN}✅ Configuration completed{C.RESET}")

print("Launching MCP agent to AgentCore Runtime (may take several minutes)...")
mcp_launch_result = mcp_runtime.launch()

mcp_agent_arn = mcp_launch_result.agent_arn
mcp_agent_id = mcp_launch_result.agent_id

mcp_encoded_arn = mcp_agent_arn.replace(":", "%3A").replace("/", "%2F")
MCP_ENDPOINT_URL = (
    f"https://bedrock-agentcore.{AWS_REGION}.amazonaws.com/runtimes/{mcp_encoded_arn}/invocations?qualifier=DEFAULT"
)

print(f"  {C.GREEN}✅ Launch completed{C.RESET}")
print(f"  {C.BOLD}ARN:{C.RESET}      {C.CYAN}{mcp_agent_arn}{C.RESET}")
print(f"  {C.BOLD}Endpoint:{C.RESET}  {C.CYAN}{MCP_ENDPOINT_URL}{C.RESET}")

# Verify: tools/list
print(f"\n{C.BOLD}1.3 Verify — tools/list{C.RESET}")
mcp_tools_response = signed_mcp_post(MCP_ENDPOINT_URL, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
print(f"{C.BOLD}MCP tools/list response:{C.RESET}")
pretty_print_response(mcp_tools_response)

# Verify: tools/call
print(f"\n{C.BOLD}1.4 Test — tools/call create_order{C.RESET}")
mcp_invoke_response = signed_mcp_post(
    MCP_ENDPOINT_URL,
    {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "create_order",
            "arguments": {
                "customer_name": "Jane Smith",
                "product": "Wireless Headphones",
                "quantity": 2,
            },
        },
    },
)
print(f"{C.BOLD}MCP tools/call response:{C.RESET}")
pretty_print_response(mcp_invoke_response)

# ── 2. Write and Deploy A2A Agent ─────────────────────────────────────────────
print(f"\n{C.BOLD}=== 2. Deploy A2A Agent to AgentCore Runtime ==={C.RESET}")

A2A_AGENT_CODE = '''"""A2A Order Management Agent — deployed to AgentCore Runtime."""
import os
import uuid as _uuid
from datetime import datetime
from strands import Agent, tool
from strands.multiagent.a2a import A2AServer
from a2a.types import AgentSkill
from fastapi import FastAPI
import uvicorn

runtime_url = os.environ.get("AGENTCORE_RUNTIME_URL", "http://127.0.0.1:9000/")
host, port  = "0.0.0.0", 9000

@tool
def create_order(customer_name: str, product: str, quantity: int) -> str:
    """Create a new order for a customer."""
    order_id = f"ORD-{_uuid.uuid4().hex[:8].upper()}"
    return (f"Order created successfully. Order ID: {order_id}, Customer: {customer_name}, "
            f"Product: {product}, Quantity: {quantity}, Status: PENDING, "
            f"Created: {datetime.now().isoformat()}")

@tool
def get_order(order_id: str) -> str:
    """Retrieve details of an existing order by its ID."""
    return (f"Order ID: {order_id}, Customer: Jane Smith, Product: Wireless Headphones, "
            f"Quantity: 2, Status: SHIPPED, Total: $149.98")

@tool
def cancel_order(order_id: str, reason: str) -> str:
    """Cancel an existing order with a reason."""
    return (f"Order {order_id} cancelled successfully. Reason: {reason}, "
            f"Status: CANCELLED, Cancelled: {datetime.now().isoformat()}")

@tool
def list_orders(status: str = "ALL") -> str:
    """List orders, optionally filtered by status."""
    return (f"Orders (filter: {status}):\\n"
            f"  1. ORD-A1B2C3D4 | Jane Smith | Wireless Headphones (2) | SHIPPED")

agent = Agent(
    system_prompt=(
        "You are an order management assistant. Use the available tools to "
        "create, retrieve, cancel, and list orders. Be concise and confirm actions clearly."
    ),
    tools=[create_order, get_order, cancel_order, list_orders],
    name="order-management-agent",
    description="An order management agent that handles order creation, cancellations, and lookups",
)

a2a_server = A2AServer(
    agent=agent,
    http_url=runtime_url,
    serve_at_root=True,
    skills=[
        AgentSkill(
            id="order-management",
            name="Order Management",
            description="Create, retrieve, update, and cancel customer orders",
            examples=["Create an order for 2 headphones for Jane Smith", "Cancel order ORD-A1B2C3D4"],
            tags=[],
        ),
        AgentSkill(
            id="order-tracking",
            name="Order Tracking",
            description="Look up order status and list orders by status",
            examples=["What is the status of order ORD-A1B2C3D4?", "Show me all pending orders"],
            tags=[],
        ),
    ],
)

app = FastAPI()

@app.get("/ping")
def ping():
    return {"status": "healthy"}

app.mount("/", a2a_server.to_fastapi_app())

if __name__ == "__main__":
    uvicorn.run(app, host=host, port=port)
'''

with open("agents/a2a/a2a_order_agent.py", "w") as f:
    f.write(A2A_AGENT_CODE)
print("Wrote agents/a2a/a2a_order_agent.py")

# Clear previous starter toolkit config
for f_path in [".bedrock_agentcore.yaml", "Dockerfile"]:
    if os.path.exists(f_path):
        os.remove(f_path)
        print(f"  {C.YELLOW}⏳ Cleared {f_path}{C.RESET}")

a2a_runtime = Runtime()
print("Configuring A2A AgentCore Runtime...")
a2a_runtime.configure(
    agent_name="a2a_order_agent",
    protocol="A2A",
    entrypoint="agents/a2a/a2a_order_agent.py",
    auto_create_execution_role=True,
    auto_create_ecr=True,
    requirements_file="agents/a2a/requirements.txt",
    region=AWS_REGION,
)
print(f"  {C.GREEN}✅ Configuration completed{C.RESET}")

print("Launching A2A agent to AgentCore Runtime (may take several minutes)...")
a2a_launch_result = a2a_runtime.launch()

a2a_agent_arn = a2a_launch_result.agent_arn
a2a_agent_id = a2a_launch_result.agent_id

a2a_encoded_arn = a2a_agent_arn.replace(":", "%3A").replace("/", "%2F")
A2A_ENDPOINT_URL = (
    f"https://bedrock-agentcore.{AWS_REGION}.amazonaws.com/runtimes/{a2a_encoded_arn}/invocations?qualifier=DEFAULT"
)

print(f"  {C.GREEN}✅ Launch completed{C.RESET}")
print(f"  {C.BOLD}ARN:{C.RESET}      {C.CYAN}{a2a_agent_arn}{C.RESET}")
print(f"  {C.BOLD}Endpoint:{C.RESET}  {C.CYAN}{A2A_ENDPOINT_URL}{C.RESET}")

# Verify: fetch agent card
print(f"\n{C.BOLD}2.3 Verify — Agent Card{C.RESET}")
agent_card_url = (
    f"https://bedrock-agentcore.{AWS_REGION}.amazonaws.com"
    f"/runtimes/{a2a_encoded_arn}/invocations/.well-known/agent-card.json"
)
agent_card_response = signed_get(agent_card_url)
a2a_agent_card = agent_card_response.json()

print(f"{C.BOLD}A2A Agent Card:{C.RESET}")
pretty_print_response(a2a_agent_card)

# Verify: message/send
print(f"\n{C.BOLD}2.4 Test — message/send{C.RESET}")
a2a_invoke_response = signed_a2a_post(
    A2A_ENDPOINT_URL,
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "messageId": str(uuid.uuid4()),
                "parts": [
                    {
                        "kind": "text",
                        "text": "Create an order for 2 Wireless Headphones for Jane Smith",
                    }
                ],
            }
        },
    },
)
print(f"{C.BOLD}A2A message/send response:{C.RESET}")
pretty_print_response(a2a_invoke_response)

# ── 3. Register in Agent Registry ─────────────────────────────────────────────
print(f"\n{C.BOLD}=== 3. Register in the Agent Registry ==={C.RESET}")

create_registry_response = registry_client.create_registry(
    name="agentcore-tools-registry",
    description="Registry to store A2A Agents and MCP Servers deployed on AgentCore",
    approvalConfiguration={"autoApprovalRules": []},
)

REGISTRY_ARN = create_registry_response["registryArn"]
REGISTRY_ID = REGISTRY_ARN.split("/")[-1]

wait_for_registry(REGISTRY_ID)

print(f"  {C.GREEN}✅ Registry created!{C.RESET}")
print(f"  {C.BOLD}ARN:{C.RESET}  {C.CYAN}{REGISTRY_ARN}{C.RESET}")
print(f"  {C.BOLD}ID:{C.RESET}   {C.CYAN}{REGISTRY_ID}{C.RESET}")

# 3.2 Create MCP registry record
print(f"\n{C.BOLD}3.2 Create MCP Registry Record{C.RESET}")

mcp_tools = mcp_tools_response.get("result", {}).get("tools", [])
mcp_tool_schema = json.dumps({"tools": mcp_tools})
mcp_server_schema = json.dumps(
    {
        "name": "io.example/order-management-tools",
        "description": "MCP server exposing order management tools",
        "version": "1.0.0",
        "title": "Order Management MCP Server",
        "packages": [
            {
                "registryType": "pypi",
                "identifier": "order-mcp-server",
                "version": "1.0.0",
                "runtimeHint": "python",
                "transport": {"type": "stdio"},
            }
        ],
    }
)

mcp_record_response = registry_client.create_registry_record(
    registryId=REGISTRY_ID,
    name="order_mcp_server",
    description="MCP server with order management tools",
    recordType="MCP",
    recordVersion="1.0",
    descriptors={
        "mcpServer": {
            "data": mcp_server_schema,
            "additionalData": {"tools": {"data": mcp_tool_schema}},
        }
    },
)

MCP_RECORD_ID = mcp_record_response["recordArn"].split("/")[-1]
print(f"  {C.GREEN}✅ MCP record created: {C.CYAN}{MCP_RECORD_ID}{C.RESET}")

# 3.3 Create A2A registry record
print(f"\n{C.BOLD}3.3 Create A2A Registry Record{C.RESET}")

a2a_agent_card_schema = json.dumps(a2a_agent_card)

a2a_record_response = registry_client.create_registry_record(
    registryId=REGISTRY_ID,
    name="order_a2a_agent",
    description="A2A agent for managing customer orders conversationally",
    recordType="AGENT",
    recordVersion="1.0",
    descriptors={"a2aAgentCard": {"data": a2a_agent_card_schema}},
)

A2A_RECORD_ID = a2a_record_response["recordArn"].split("/")[-1]
print(f"  {C.GREEN}✅ A2A record created: {C.CYAN}{A2A_RECORD_ID}{C.RESET}")

# ── 4. Approval Workflow ──────────────────────────────────────────────────────
print(f"\n{C.BOLD}=== 4. Approval Workflow ==={C.RESET}")

# Verify DRAFT status
records_response = registry_client.list_registry_records(registryId=REGISTRY_ID)
print(f"Found {len(records_response['registryRecords'])} record(s):")
for rec in records_response["registryRecords"]:
    status = rec["status"]
    sc = C.GREEN if status == "APPROVED" else C.YELLOW if status in ("DRAFT", "PENDING_APPROVAL") else C.RED
    print(f"  {sc}[{status}]{C.RESET} {rec['name']} | {C.DIM}{rec['recordId']}{C.RESET}")

# Submit and approve
for record_id, record_name in [(MCP_RECORD_ID, "MCP"), (A2A_RECORD_ID, "A2A")]:
    wait_for_record_draft(REGISTRY_ID, record_id)
    registry_client.submit_registry_record_for_approval(registryId=REGISTRY_ID, recordId=record_id)
    print(f"  {C.YELLOW}⏳ {record_name} record → PENDING_APPROVAL{C.RESET}")

    registry_client.update_registry_record_status(
        registryId=REGISTRY_ID,
        recordId=record_id,
        statusReason="Approved by admin",
        status="APPROVED",
    )
    print(f"  {C.GREEN}✅ {record_name} record → APPROVED{C.RESET}")

# Verify
for record_id, record_name in [(MCP_RECORD_ID, "MCP"), (A2A_RECORD_ID, "A2A")]:
    rec = registry_client.get_registry_record(registryId=REGISTRY_ID, recordId=record_id)
    status = rec["status"]
    sc = C.GREEN if status == "APPROVED" else C.RED
    print(f"  {sc}{record_name} record status: {status}{C.RESET}")
    assert status == "APPROVED", f"Expected APPROVED, got {status}"

# ── 5. Semantic Search ────────────────────────────────────────────────────────
print(f"\n{C.BOLD}=== 5. Semantic Search ==={C.RESET}")
print(f"  {C.YELLOW}⏳ Waiting for search index to update (100s)...{C.RESET}")
time.sleep(100)

# Search for order management tools
search_response = search_client.search_discoverable_registry_records(
    registryIds=[REGISTRY_ARN], searchQuery="order management", maxResults=5
)
search_response.pop("ResponseMetadata", None)
records = search_response.get("registryRecords", [])

print(f"{C.BOLD}Search results for 'order management':{C.RESET}")
print(f"Found {len(records)} record(s):\n")

for rec in records:
    status = rec.get("status", "N/A")
    sc = C.GREEN if status == "APPROVED" else C.YELLOW
    print(f"  {sc}[{status}]{C.RESET} {C.CYAN}{rec.get('name', 'N/A')}{C.RESET} ({rec.get('recordType', 'N/A')})")
    print(f"    {rec.get('description', '')}")

    mcp_desc = rec.get("descriptors", {}).get("mcpServer", {})
    tool_schema = mcp_desc.get("additionalData", {}).get("tools", {}).get("data", "")
    if tool_schema:
        try:
            tools = json.loads(tool_schema).get("tools", [])
            print(f"    {C.BOLD}Tools:{C.RESET}")
            for t in tools:
                print(f"      • {t['name']}: {t.get('description', '')}")
        except (json.JSONDecodeError, TypeError):
            pass

    a2a_desc = rec.get("descriptors", {}).get("a2aAgentCard", {})
    card_content = a2a_desc.get("data", "")
    if card_content:
        try:
            card = json.loads(card_content)
            skills = card.get("skills", [])
            if skills:
                print(f"    {C.BOLD}Skills:{C.RESET}")
                for s in skills:
                    print(f"      • {s.get('name', s.get('id', '?'))}: {s.get('description', '')}")
        except (json.JSONDecodeError, TypeError):
            pass

# Multiple queries
for query in [
    "cancel an order",
    "track shipment status",
    "create new order for customer",
]:
    response = search_client.search_discoverable_registry_records(
        registryIds=[REGISTRY_ARN], searchQuery=query, maxResults=3
    )
    records = response.get("registryRecords", [])
    print(f"{C.BOLD}'{query}'{C.RESET} → {len(records)} result(s)")
    for rec in records:
        print(f"  • {rec.get('name', 'N/A')} ({rec.get('recordType', 'N/A')})")
    print()

# ── 6. Cleanup ────────────────────────────────────────────────────────────────
print(f"\n{C.BOLD}=== 6. Cleanup ==={C.RESET}")

agentcore_client = boto_session.client("bedrock-agentcore-control", region_name=AWS_REGION)

for agent_id, agent_name in [(mcp_agent_id, "MCP"), (a2a_agent_id, "A2A")]:
    try:
        agentcore_client.delete_agent_runtime(agentRuntimeId=agent_id)
        print(f"  {C.GREEN}✅ Deleted {agent_name} runtime: {C.DIM}{agent_id}{C.RESET}")
    except Exception as e:  # noqa: BLE001
        print(f"  {C.RED}❌ Failed to delete {agent_name} runtime: {e}{C.RESET}")

records = registry_client.list_registry_records(registryId=REGISTRY_ID)
for rec in records.get("registryRecords", []):
    registry_client.delete_registry_record(registryId=REGISTRY_ID, recordId=rec["recordId"])
    print(f"  {C.GREEN}✅ Deleted record: {C.DIM}{rec['recordId']}{C.RESET}")

registry_client.delete_registry(registryId=REGISTRY_ID)
print(f"  {C.GREEN}✅ Deleted registry: {C.DIM}{REGISTRY_ID}{C.RESET}")

print(f"\n  {C.GREEN}✅ Publish AgentCore Tools demo complete!{C.RESET}")
