"""Utilities for AWS Agent Registry: create, seed, search, and cleanup.

Seeds the registry with inline MCP records (small, focused tool sets) plus
a public MCP server via URL sync. Requires boto3>=1.42.87.
"""

import boto3
import json
import time
import logging
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REGION = os.getenv("AWS_REGION", "us-west-2")


def get_session():
    return boto3.Session(region_name=REGION)


def get_cp_client():
    return get_session().client("bedrock-agentcore-control", region_name=REGION)


def get_dp_client():
    return get_session().client("bedrock-agentcore", region_name=REGION)


# ── Registry ──────────────────────────────────────────────────────────────────

def create_registry(name="tool-provider-registry", description="Registry for RegistryToolProvider demo"):
    cp = get_cp_client()
    logger.info("Creating registry '%s'...", name)
    resp = cp.create_registry(
        name=name, description=description,
        approvalConfiguration={"autoApproval": False},
    )
    registry_arn = resp["registryArn"]
    registry_id = registry_arn.split("/")[-1]

    for _ in range(30):
        time.sleep(5)
        status = cp.get_registry(registryId=registry_id).get("status", "UNKNOWN")
        logger.info("  status: %s", status)
        if status == "READY":
            break
    logger.info("✅ Registry ready: %s", registry_id)
    return {"registryId": registry_id, "registryArn": registry_arn}


# ── Inline MCP + URL Sync Records ────────────────────────────────────────────

INLINE_RECORDS = [
    {
        "name": "weather_tools_mcp",
        "description": "Weather MCP server — current conditions and multi-day forecasts for any city worldwide",
        "server_info": {
            "name": "io.example/weather-tools",
            "description": "MCP server for weather data",
            "version": "1.0.0",
            "packages": [{"registryType": "npm", "identifier": "@example/weather-mcp", "version": "1.0.0", "transport": {"type": "stdio"}}],
        },
        "tools": [
            {"name": "get_current_weather", "description": "Get current weather conditions for a city including temperature, humidity, and wind speed",
             "inputSchema": {"type": "object", "properties": {"city": {"type": "string", "description": "City name (e.g. Seattle)"}, "units": {"type": "string", "enum": ["celsius", "fahrenheit"]}}, "required": ["city"]}},
            {"name": "get_weather_forecast", "description": "Get a multi-day weather forecast for a city",
             "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}, "days": {"type": "integer", "description": "Number of forecast days (1-5)"}}, "required": ["city"]}},
        ],
    },
    {
        "name": "order_management_mcp",
        "description": "Order management MCP server — track order status, list orders, and create new e-commerce orders",
        "server_info": {
            "name": "io.example/order-management",
            "description": "MCP server for order tracking and management",
            "version": "1.0.0",
            "packages": [{"registryType": "npm", "identifier": "@example/order-mcp", "version": "1.0.0", "transport": {"type": "stdio"}}],
        },
        "tools": [
            {"name": "get_order_status", "description": "Get the current status and details of an order by order ID",
             "inputSchema": {"type": "object", "properties": {"order_id": {"type": "string", "description": "The order identifier"}}, "required": ["order_id"]}},
            {"name": "list_orders", "description": "List orders optionally filtered by status (pending, shipped, delivered)",
             "inputSchema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["pending", "shipped", "delivered", "ALL"]}}}},
            {"name": "create_order", "description": "Create a new order for a customer with product and quantity",
             "inputSchema": {"type": "object", "properties": {"customer_name": {"type": "string"}, "product": {"type": "string"}, "quantity": {"type": "integer"}}, "required": ["customer_name", "product", "quantity"]}},
        ],
    },
    {
        "name": "inventory_tools_mcp",
        "description": "Inventory MCP server — check real-time stock levels across warehouses and search products by name or category",
        "server_info": {
            "name": "io.example/inventory-tools",
            "description": "MCP server for product inventory and stock levels",
            "version": "1.0.0",
            "packages": [{"registryType": "npm", "identifier": "@example/inventory-mcp", "version": "1.0.0", "transport": {"type": "stdio"}}],
        },
        "tools": [
            {"name": "check_inventory", "description": "Check stock levels for a product SKU across warehouses",
             "inputSchema": {"type": "object", "properties": {"sku": {"type": "string", "description": "Product SKU"}, "warehouse": {"type": "string", "description": "Warehouse ID (optional)"}}, "required": ["sku"]}},
            {"name": "search_products", "description": "Search products by name or category",
             "inputSchema": {"type": "object", "properties": {"query": {"type": "string", "description": "Product name or keyword"}, "category": {"type": "string"}}, "required": ["query"]}},
        ],
    },
]

URL_SYNC_SERVER = {
    "name": "aws_knowledge_mcp",
    "url": "https://knowledge-mcp.global.api.aws",
    "description": "AWS Knowledge MCP — search AWS documentation, guides, and best practices",
}


def _wait_for_draft(cp, registry_id, record_id, timeout=120):
    """Poll until record reaches DRAFT (or terminal) status."""
    for _ in range(timeout // 5):
        r = cp.get_registry_record(registryId=registry_id, recordId=record_id)
        status = r.get("status", "UNKNOWN")
        if status in ("DRAFT", "APPROVED", "CREATE_FAILED", "PENDING_APPROVAL"):
            return r
        time.sleep(5)
    return r


def _approve(cp, registry_id, record_id, name=""):
    """Submit for approval and approve."""
    cp.submit_registry_record_for_approval(registryId=registry_id, recordId=record_id)
    cp.update_registry_record_status(
        registryId=registry_id, recordId=record_id,
        status="APPROVED", statusReason="Auto-approved for demo",
    )
    logger.info("  ✅ %s approved", name)


def seed(registry_id, include_url_sync=True):
    """Seed registry with inline MCP records + optional URL sync.

    Returns list of {name, recordId, tool_count} dicts.
    """
    cp = get_cp_client()
    results = []

    # 1. Inline MCP records — uses the same pattern as getting-started-registry-end-to-end
    for rec in INLINE_RECORDS:
        logger.info("Creating '%s' (MCP, %d tools)...", rec["name"], len(rec["tools"]))
        try:
            resp = cp.create_registry_record(
                registryId=registry_id,
                name=rec["name"],
                description=rec["description"],
                descriptorType="MCP",
                descriptors={"mcp": {
                    "server": {"inlineContent": json.dumps(rec["server_info"])},
                    "tools": {"inlineContent": json.dumps({"tools": rec["tools"]})},
                }},
                recordVersion="1.0",
            )
            record_id = resp["recordArn"].split("/")[-1]
            logger.info("  Created: %s", record_id)
        except Exception as e:
            logger.error("  Failed: %s", e)
            continue

        r = _wait_for_draft(cp, registry_id, record_id)
        if r.get("status") == "CREATE_FAILED":
            logger.error("  ❌ %s failed: %s", rec["name"], r.get("statusReason", ""))
            continue

        _approve(cp, registry_id, record_id, rec["name"])
        results.append({"name": rec["name"], "recordId": record_id, "tool_count": len(rec["tools"])})

    # 2. URL sync (AWS Knowledge MCP — may be rate-limited)
    if include_url_sync:
        server = URL_SYNC_SERVER
        logger.info("Registering '%s' via URL sync: %s", server["name"], server["url"])
        try:
            resp = cp.create_registry_record(
                registryId=registry_id,
                name=server["name"],
                description=server.get("description", ""),
                descriptorType="MCP",
                synchronizationType="URL",
                synchronizationConfiguration={"fromUrl": {"url": server["url"]}},
            )
            record_id = resp["recordArn"].split("/")[-1]
            r = _wait_for_draft(cp, registry_id, record_id)
            if r.get("status") == "CREATE_FAILED":
                logger.warning("  ⚠️ URL sync failed: %s — skipping", r.get("statusReason", ""))
                cp.delete_registry_record(registryId=registry_id, recordId=record_id)
            else:
                tools = []
                try:
                    tools = json.loads(r["descriptors"]["mcp"]["tools"]["inlineContent"]).get("tools", [])
                except Exception:
                    pass
                _approve(cp, registry_id, record_id, server["name"])
                results.append({"name": server["name"], "recordId": record_id, "tool_count": len(tools)})
        except Exception as e:
            logger.warning("  ⚠️ URL sync skipped: %s", e)

    return results


def wait_for_search_index(registry_id, expected_count, query="weather orders inventory documentation", max_wait=120):
    dp = get_dp_client()
    logger.info("Waiting for search index (%d records)...", expected_count)
    for _ in range(max_wait // 10):
        time.sleep(10)
        resp = dp.search_registry_records(
            registryIds=[registry_id], searchQuery=query, maxResults=10,
        )
        found = len(resp.get("registryRecords", []))
        if found >= expected_count:
            logger.info("  All %d records indexed.", found)
            return
        logger.info("  %d/%d indexed — waiting...", found, expected_count)


def search(query, registry_id, max_results=10):
    dp = get_dp_client()
    resp = dp.search_registry_records(
        registryIds=[registry_id], searchQuery=query, maxResults=max_results,
    )
    return resp.get("registryRecords", [])


def delete_registry(registry_id):
    cp = get_cp_client()
    records = cp.list_registry_records(registryId=registry_id).get("registryRecords", [])
    for rec in records:
        rid = rec["recordId"]
        logger.info("Deleting record %s (%s)...", rec.get("name", ""), rid)
        cp.delete_registry_record(registryId=registry_id, recordId=rid)
    time.sleep(5)
    logger.info("Deleting registry %s...", registry_id)
    cp.delete_registry(registryId=registry_id)
    logger.info("✅ Registry deleted")
