"""
Utility functions for 05_agentic_consumer_discovery.ipynb
Keeps the notebook focused on API calls and Registry showcase.
"""

import json
import io
import zipfile
import time
import base64
import requests


# ═══════════════════════════════════════════════════════════════════════════════
# Step 1 helpers — Lambda, Cognito, Gateway, A2A agent setup
# ═══════════════════════════════════════════════════════════════════════════════

# Lambda code for order management — handles get_order_status and update_order tools
ORDER_MANAGEMENT_LAMBDA_CODE = """
import json

ORDERS = {
    "123": {
        "order_id": "123",
        "items": [{"name": "Widget Pro", "quantity": 2, "price": 49.99}],
        "total": 99.98,
        "status": "shipped",
        "tracking": "TRK-789456",
        "date": "2026-03-20",
        "customer": "Jane Smith",
        "shipping_address": "123 Main St, New York, NY 10001",
        "return_eligible": True,
        "return_window_days": 30
    },
    "456": {
        "order_id": "456",
        "items": [{"name": "Gadget X", "quantity": 1, "price": 149.99}, {"name": "Cable Pack", "quantity": 3, "price": 9.99}],
        "total": 179.96,
        "status": "processing",
        "tracking": None,
        "date": "2026-03-22",
        "customer": "John Doe",
        "shipping_address": "456 Oak Ave, San Francisco, CA 94102",
        "return_eligible": False,
        "return_window_days": 30
    },
    "789": {
        "order_id": "789",
        "items": [{"name": "Premium Headphones", "quantity": 1, "price": 299.99}],
        "total": 299.99,
        "status": "delivered",
        "tracking": "TRK-321654",
        "date": "2026-03-10",
        "customer": "Alice Johnson",
        "shipping_address": "789 Elm St, Seattle, WA 98101",
        "return_eligible": True,
        "return_window_days": 30
    }
}

DEFAULT_ORDER = {"order_id": "unknown", "items": [], "total": 0, "status": "not_found", "tracking": None, "error": "Order not found"}

def lambda_handler(event, context):
    tool = (context.client_context.custom or {}).get("bedrockAgentCoreToolName", "") if context.client_context else ""
    if "___" in tool:
        tool = tool.split("___", 1)[1]
    if tool == "get_order_status":
        return ORDERS.get(event.get("orderId", ""), DEFAULT_ORDER)
    elif tool == "update_order":
        order_id = event.get("orderId", "")
        action = event.get("action", "")
        order = ORDERS.get(order_id)
        if not order:
            return {"error": f"Order {order_id} not found"}
        if action == "cancel":
            return {"order_id": order_id, "status": "cancelled", "message": f"Order {order_id} cancelled. Refund of ${order['total']} in 3-5 business days."}
        elif action == "change_address":
            return {"order_id": order_id, "status": order["status"], "message": f"Shipping address updated to: {event.get('newAddress', '')}"}
        return {"order_id": order_id, "message": f"Order {order_id} updated with action: {action}"}
    return {"error": f"Unknown tool: {tool}"}
"""


def make_lambda_zip(code_str):
    """Package a Python code string into a Lambda-compatible zip archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("lambda_function.py", code_str)
    buf.seek(0)
    return buf.read()


def fetch_oauth_token(cognito_domain, client_id, client_secret, scopes, region, max_retries=6):
    """Fetch OAuth2 access token from Cognito with retry for DNS propagation."""
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                f"https://{cognito_domain}/oauth2/token",
                headers={
                    "Authorization": f"Basic {creds}",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"grant_type": "client_credentials", "scope": scopes},
                timeout=10,
            )
            token = resp.json()["access_token"]
            print(f"  OAuth2 token obtained (length={len(token)})")
            return token
        except Exception:
            if attempt < max_retries - 1:
                print(f"  Waiting for Cognito DNS (attempt {attempt + 1}/{max_retries})...")
                time.sleep(10)
            else:
                raise
    return None


# Tool schemas for the order management Gateway target
ORDER_TOOL_SCHEMAS = [
    {
        "name": "get_order_status",
        "description": "Get the status and details of an order by order ID, including items, total, shipping, and tracking info",
        "inputSchema": {
            "type": "object",
            "properties": {"orderId": {"type": "string", "description": "The order ID to look up"}},
            "required": ["orderId"],
        },
    },
    {
        "name": "update_order",
        "description": "Update an order - cancel it, change shipping address, or perform other modifications",
        "inputSchema": {
            "type": "object",
            "properties": {
                "orderId": {"type": "string", "description": "The order ID to update"},
                "action": {
                    "type": "string",
                    "description": "Action to perform: cancel, change_address",
                },
                "newAddress": {
                    "type": "string",
                    "description": "New shipping address (for change_address action)",
                },
            },
            "required": ["orderId", "action"],
        },
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Step 2 helpers — A2A agent code and file writing
# ═══════════════════════════════════════════════════════════════════════════════

PRICING_AGENT_CODE = '''
import os
from strands import Agent
from strands.multiagent.a2a import A2AServer
from fastapi import FastAPI
import uvicorn

agent = Agent(
    system_prompt="""
You are a pricing and discount specialist for an e-commerce platform. Analyze orders and provide pricing advice.

PRICING RULES:
- Orders over $100: eligible for 10% bulk discount
- Orders over $200: eligible for 15% bulk discount
- Promo code SAVE15: 15% off any order
- Promo code FREESHIP: free shipping (saves $9.99)
- Promo code WELCOME10: 10% off for first-time customers
- Loyalty members get an additional 5% on top of any discount
- Discounts cannot be stacked (only the best single discount applies, plus loyalty bonus if applicable)

PRICE HISTORY (last 30 days):
- Widget Pro: was $59.99, now $49.99 (17% price drop)
- Gadget X: stable at $149.99
- Cable Pack: was $12.99, now $9.99 (23% price drop)
- Premium Headphones: was $349.99, now $299.99 (14% price drop)

Always provide: applicable discounts, best price calculation, savings amount, and any relevant promo codes.
""",
    tools=[],
    name="Pricing Agent",
    description="Pricing and discount specialist — analyzes orders, calculates discounts, recommends promo codes",
)

app = FastAPI()
a2a = A2AServer(agent=agent, http_url=os.environ.get("AGENTCORE_RUNTIME_URL", "http://127.0.0.1:9000/"), serve_at_root=True)

@app.get("/ping")
def ping():
    return {"status": "healthy"}

app.mount("/", a2a.to_fastapi_app())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)
'''

CUSTOMER_SUPPORT_AGENT_CODE = '''
import os
from strands import Agent
from strands.multiagent.a2a import A2AServer
from fastapi import FastAPI
import uvicorn

agent = Agent(
    system_prompt="""
You are a customer support specialist for an e-commerce platform. Handle returns, refunds, complaints, and escalations.

RETURN POLICY:
- Items can be returned within 30 days of order date
- Items must be unused and in original packaging
- Refunds are processed within 3-5 business days after return is received
- Shipping costs for returns: free for defective items, $7.99 for buyer remorse
- Electronics over $200 require a restocking fee of 10%

REFUND RULES:
- Full refund: defective items, wrong items shipped, items not received
- Partial refund (minus restocking fee): buyer remorse on electronics over $200
- Store credit only: items returned after 30 days but within 60 days
- No returns after 60 days

ESCALATION CRITERIA:
- Order value over $500: requires manager approval for refund
- Repeat complaints (3+ on same order): auto-escalate to senior support
- Defective item reports: trigger quality review notification

Always provide: return eligibility, refund amount calculation, next steps, and estimated timeline.
""",
    tools=[],
    name="Customer Support Agent",
    description="Customer support specialist — handles returns, refunds, complaints, and escalation decisions",
)

app = FastAPI()
a2a = A2AServer(agent=agent, http_url=os.environ.get("AGENTCORE_RUNTIME_URL", "http://127.0.0.1:9000/"), serve_at_root=True)

@app.get("/ping")
def ping():
    return {"status": "healthy"}

app.mount("/", a2a.to_fastapi_app())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)
'''

A2A_REQUIREMENTS = "strands-agents[a2a]\nfastapi\nuvicorn\n"


def write_agent_files():
    """Write agent source files and requirements to disk for the starter toolkit."""
    with open("pricing_agent.py", "w") as f:
        f.write(PRICING_AGENT_CODE)
    with open("customer_support_agent.py", "w") as f:
        f.write(CUSTOMER_SUPPORT_AGENT_CODE)
    with open("a2a_requirements.txt", "w") as f:
        f.write(A2A_REQUIREMENTS)
    print("  Agent files written: pricing_agent.py, customer_support_agent.py, a2a_requirements.txt")


# ═══════════════════════════════════════════════════════════════════════════════
# Step 3 helpers — Registry metadata parsing and dynamic tool creation
# ═══════════════════════════════════════════════════════════════════════════════

import uuid  # noqa: E402
from strands import tool  # noqa: E402
from strands.tools.mcp import MCPClient  # noqa: E402


def parse_server_metadata(record):
    """Extract connection metadata (URL, protocol, tool names) from a Registry record."""
    descriptors = record.get("descriptors", {})
    # Derive protocol from descriptor keys (API no longer returns protocol field)
    if "mcp" in descriptors:
        protocol = "MCP"
    elif "a2a" in descriptors:
        protocol = "A2A"
    else:
        protocol = record.get("descriptorType", "")
    meta = {
        "protocol": protocol,
        "record_name": record.get("name", ""),
        "description": record.get("description", ""),
        "transport_type": None,
        "url": None,
        "tool_names": [],
    }

    if protocol == "MCP":
        mcp = descriptors.get("mcp", {})
        try:
            server = json.loads(mcp.get("server", {}).get("inlineContent", "{}"))
        except Exception:
            server = {}
        url = server.get("websiteUrl", "")
        if url:
            meta["transport_type"] = "streamable_http"
            meta["url"] = url
        try:
            tools = json.loads(mcp.get("tools", {}).get("inlineContent", "{}"))
            meta["tool_names"] = [t["name"] for t in tools.get("tools", [])]
        except Exception:
            pass

    elif protocol == "A2A":
        try:
            card = json.loads(descriptors.get("a2a", {}).get("agentCard", {}).get("inlineContent", "{}"))
            meta["url"] = card.get("url")
        except Exception:
            pass

    return meta


def create_mcp_client_from_metadata(meta, access_token):
    """Build a Strands MCPClient for a Gateway MCP server (OAuth2 Bearer auth)."""
    if meta["protocol"] != "MCP" or meta["transport_type"] != "streamable_http":
        return None
    url = meta.get("url")
    if not url or not access_token:
        return None
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"Authorization": f"Bearer {access_token}"}
    print(f"    [MCP] {meta['record_name']} -> {url[:70]}...")
    return MCPClient(lambda u=url, h=headers: streamablehttp_client(u, headers=h))


def create_a2a_tool_from_metadata(meta, session, region):
    """Wrap an A2A agent as a Strands @tool (SigV4 auth via boto3).

    Args:
        meta: Parsed metadata dict from parse_server_metadata.
        session: boto3.Session for creating the AgentCore data plane client.
        region: AWS region string.
    """
    if meta["protocol"] != "A2A":
        return None
    arn = meta.get("url")
    if not arn:
        return None

    record_name = meta.get("record_name", "a2a_agent")
    description = meta.get("description", "An A2A agent")

    def _invoke(task: str) -> str:
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": str(uuid.uuid4()),
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "role": "user",
                        "parts": [{"kind": "text", "text": task}],
                    }
                },
            }
        )
        try:
            data_client = session.client("bedrock-agentcore")
            response = data_client.invoke_agent_runtime(
                agentRuntimeArn=arn,
                payload=payload,
                contentType="application/json",
            )
            result = json.loads(response["response"].read().decode())
            if "result" in result:
                parts = result["result"].get("artifacts", [{}])[0].get("parts", [])
                texts = [p["text"] for p in parts if p.get("kind") == "text"]
                if texts:
                    return "\n".join(texts)
            return json.dumps(result)
        except Exception as e:
            return f"Error: {e}"

    _invoke.__name__ = record_name.replace("-", "_").lower()
    _invoke.__doc__ = (
        f"{description}\n\nArgs:\n    task: The task or question to send.\nReturns:\n    The agent's response."
    )
    print(f"    [A2A] {record_name} -> {arn[:70]}...")
    return tool(_invoke)


# ═══════════════════════════════════════════════════════════════════════════════
# Step 4 helpers — Orchestrator invocation
# ═══════════════════════════════════════════════════════════════════════════════


def invoke_orchestrator(user_request, data_client, orchestrator_arn, max_retries=3):
    """Invoke the orchestrator agent on Runtime via A2A JSON-RPC protocol.

    Args:
        user_request: The user's question or task.
        data_client: boto3 client for bedrock-agentcore (data plane).
        orchestrator_arn: ARN of the deployed orchestrator agent.
        max_retries: Number of retries for cold-start 502 errors.
    """
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": str(uuid.uuid4()),
                    "role": "user",
                    "parts": [{"kind": "text", "text": user_request}],
                }
            },
        }
    )
    for attempt in range(max_retries):
        try:
            response = data_client.invoke_agent_runtime(
                agentRuntimeArn=orchestrator_arn,
                payload=payload,
                contentType="application/json",
            )
            result = json.loads(response["response"].read().decode())
            parts = result.get("result", {}).get("artifacts", [{}])[0].get("parts", [])
            return "\n".join(p["text"] for p in parts if p.get("kind") == "text")
        except Exception as e:
            if "502" in str(e) and attempt < max_retries - 1:
                print(f"  Cold-start 502, retrying in 15s (attempt {attempt + 1}/{max_retries})...")
                time.sleep(10)
            else:
                raise
