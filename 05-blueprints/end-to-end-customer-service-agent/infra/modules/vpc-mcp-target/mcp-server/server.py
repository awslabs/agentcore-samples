"""
Private MCP Server for Customer Service Operations.

Runs inside a VPC and provides tools for:
- Customer profile lookup
- Order status tracking
- Support ticket creation and escalation

Configure via environment variables:
  MCP_PORT                 - Port to listen on (default: 8000)
  CRM_API_ENDPOINT         - Internal CRM API base URL
  TICKETING_API_ENDPOINT   - Internal ticketing system base URL
"""

import os
import json
import urllib.request
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(host="0.0.0.0", stateless_http=True)

CRM_API = os.environ.get("CRM_API_ENDPOINT", "")
TICKETING_API = os.environ.get("TICKETING_API_ENDPOINT", "")


@mcp.tool()
def lookup_customer(customer_id: str) -> dict:
    """Look up a customer profile by customer ID or email address."""
    if CRM_API:
        try:
            req = urllib.request.Request(
                f"{CRM_API}/customers/{customer_id}",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"error": f"CRM lookup failed: {str(e)}"}

    # Mock response when no CRM is configured
    return {
        "customer_id": customer_id,
        "name": "Jane Smith",
        "email": "jane.smith@example.com",
        "tier": "premium",
        "account_status": "active",
        "created_at": "2024-03-15",
        "total_orders": 47,
    }


@mcp.tool()
def get_order_status(order_id: str) -> dict:
    """Check the current status of a customer order."""
    if CRM_API:
        try:
            req = urllib.request.Request(
                f"{CRM_API}/orders/{order_id}",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"error": f"Order lookup failed: {str(e)}"}

    # Mock response
    return {
        "order_id": order_id,
        "status": "shipped",
        "tracking_number": "1Z999AA10123456784",
        "estimated_delivery": "2026-06-25",
        "items": [
            {"name": "Wireless Headphones", "quantity": 1, "price": 79.99},
            {"name": "USB-C Cable", "quantity": 2, "price": 12.99},
        ],
    }


@mcp.tool()
def create_support_ticket(
    customer_id: str, subject: str, description: str, priority: str = "medium"
) -> dict:
    """Create a new support ticket for a customer."""
    if TICKETING_API:
        try:
            data = json.dumps({
                "customer_id": customer_id,
                "subject": subject,
                "description": description,
                "priority": priority,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{TICKETING_API}/tickets",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"error": f"Ticket creation failed: {str(e)}"}

    # Mock response
    return {
        "ticket_id": "TKT-20260620-001",
        "customer_id": customer_id,
        "subject": subject,
        "priority": priority,
        "status": "open",
        "created_at": "2026-06-20T10:30:00Z",
        "assigned_to": "support-queue",
    }


@mcp.tool()
def escalate_ticket(ticket_id: str, reason: str) -> dict:
    """Escalate a support ticket to a human agent."""
    if TICKETING_API:
        try:
            data = json.dumps({
                "ticket_id": ticket_id,
                "reason": reason,
                "escalation_level": "human_agent",
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{TICKETING_API}/tickets/{ticket_id}/escalate",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            return {"error": f"Escalation failed: {str(e)}"}

    # Mock response
    return {
        "ticket_id": ticket_id,
        "status": "escalated",
        "escalation_reason": reason,
        "assigned_to": "human-agent-pool",
        "estimated_response_time": "15 minutes",
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
