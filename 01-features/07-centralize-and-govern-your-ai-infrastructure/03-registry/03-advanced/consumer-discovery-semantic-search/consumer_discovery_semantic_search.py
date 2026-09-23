"""
Searching the metadata in Agent Registry — Consumer Discovery Journey

Demonstrates semantic and filtered search in AWS Agent Registry using an
e-commerce scenario: a developer discovers 14 registered MCP and A2A tools
via natural-language queries, metadata filters, and drill-down.

Usage:
    python consumer_discovery_semantic_search.py

Prerequisites:
    - boto3 >= 1.42.87
    - AWS credentials configured (IAM user or role with registry permissions)
    - registry-records.json in the same directory
    - AWS_DEFAULT_REGION set (default: us-west-2)

API coverage:
    CreateRegistry, GetRegistry, CreateRegistryRecord,
    SubmitRegistryRecordForApproval, SearchRegistryRecords,
    GetRegistryRecord, DeleteRegistryRecord, DeleteRegistry
"""

import json
import os
import time
from datetime import datetime, timezone

import boto3

# ── Configuration ─────────────────────────────────────────────────────────────
AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "us-west-2")

session = boto3.Session(region_name=AWS_REGION)


cp_client = session.client("agent-registry-control")
dp_client = session.client("agent-registry")

print(f"Session ready | Region: {AWS_REGION}")

# ── 1. Create registry with auto-approval ─────────────────────────────────────
registry_name = f"consumerDiscovery_{datetime.now(tz=timezone.utc).strftime('%Y%m%d%H%M%S')}"

create_resp = cp_client.create_registry(
    name=registry_name,
    description="Registry for consumer discovery journey demo",
    approvalConfiguration={"autoApprovalRules": ["APPROVE_ALL"]},
)

REGISTRY_ARN = create_resp["registryArn"]
REGISTRY_ID = REGISTRY_ARN.split("/")[-1]

print("Registry created!")
print(f"  ARN: {REGISTRY_ARN}")
print(f"  ID:  {REGISTRY_ID}")

while True:
    r = cp_client.get_registry(registryId=REGISTRY_ID)
    if r["status"] == "READY":
        print("  Status: READY")
        break
    print(f"  Status: {r['status']} - please wait...")
    time.sleep(10)

# ── 2. Seed registry from registry-records.json ───────────────────────────────
script_dir = os.path.dirname(os.path.abspath(__file__))
records_file = os.path.join(script_dir, "registry-records.json")

with open(records_file, "r") as f:
    SEED_RECORDS = json.load(f)

print(f"\nLoaded {len(SEED_RECORDS)} seed records")

record_ids = []
for rec in SEED_RECORDS:
    resp = cp_client.create_registry_record(
        registryId=REGISTRY_ID,
        name=rec["name"],
        description=rec["description"],
        recordType=rec["recordType"],
        descriptors=rec["descriptors"],
        recordVersion=rec["recordVersion"],
    )
    record_id = resp["recordArn"].split("/")[-1]
    record_ids.append(record_id)
    print(f"  [{rec['recordType']:6s}] {rec['name']} -> {record_id}")

print(f"\nCreated {len(record_ids)} records.")

# Wait for all records to leave CREATING state
print("Waiting for records to be ready...")
for rid in record_ids:
    while True:
        rec = cp_client.get_registry_record(registryId=REGISTRY_ID, recordId=rid)
        if rec["status"] != "CREATING":
            break
        time.sleep(2)
print("All records ready.")

# Submit for approval (autoApproval=True -> straight to APPROVED)
for rid in record_ids:
    cp_client.submit_registry_record_for_approval(registryId=REGISTRY_ID, recordId=rid)

print(f"Submitted {len(record_ids)} records for approval.")

# Wait for search index propagation
print("Waiting 45s for search index propagation...")
time.sleep(45)
print("Ready for discovery.")

# ── 3. Helper functions ───────────────────────────────────────────────────────


def search_raw(query, max_results=10):
    """Search the registry and print the raw JSON API response."""
    response = dp_client.search_discoverable_registry_records(
        registryIds=[REGISTRY_ARN],
        searchQuery=query,
        maxResults=max_results,
    )
    response.pop("ResponseMetadata", None)
    print(json.dumps(response, indent=2, default=str))
    return response


def search(query, max_results=10, filters=None):
    """Search the registry and display formatted results. Optionally apply metadata filters."""
    params = {"registryIds": [REGISTRY_ARN], "searchQuery": query, "maxResults": max_results}
    if filters:
        params["filters"] = filters
    response = dp_client.search_discoverable_registry_records(**params)
    records = response.get("registryRecords", [])
    header = f'Search: "{query}"'
    if filters:
        header += f"  |  Filter: {json.dumps(filters)}"
    print(header)
    print(f"Found {len(records)} result(s)\n")
    for rec in records:
        print(f"  [{rec['recordType']:6s}]  {rec['name']}")
        print(f"        {rec.get('description', 'N/A')}")
        print()
    return response


# ── 3.1 Controlling result count ──────────────────────────────────────────────
print("=== Controlling result count with maxResults ===")
results = search("order management", max_results=3)

# ── 3.1 Order Management ──────────────────────────────────────────────────────
print("\n=== 3.1 Order Management ===")
results = search("I need to look up, update, and cancel customer orders")

# ── 3.2 Customer Notifications ───────────────────────────────────────────────
print("\n=== 3.2 Customer Notifications ===")
results = search("send notifications to customers via email or SMS")

# ── 3.3 Payment and Refunds ───────────────────────────────────────────────────
print("\n=== 3.3 Payment and Refunds ===")
results = search("check payment status or issue a refund for an order")

# ── 3.4 Inventory Management ─────────────────────────────────────────────────
print("\n=== 3.4 Inventory Management ===")
results = search("check product availability and reserve stock for an order")

# ── 3.5 Shipping and Delivery ─────────────────────────────────────────────────
print("\n=== 3.5 Shipping and Delivery ===")
results = search("track shipments and get delivery estimates")

# ── 3.6 Drill-Down: MCP Tool Connection Details ───────────────────────────────
print("\n=== 3.6 Drill-Down: MCP Tool Connection Details ===")
response = search("look up order details by order ID")
records = response.get("registryRecords", [])
mcp_hits = [r for r in records if r["recordType"] == "MCP"]

if mcp_hits:
    hit = mcp_hits[0]
    record_id = hit["recordId"]
    print(f"Drilling into: {hit['name']} ({record_id})\n")

    full = cp_client.get_registry_record(registryId=REGISTRY_ID, recordId=record_id)
    mcp = full.get("descriptors", {}).get("mcpServer", {})

    server = json.loads(mcp.get("data", "{}"))
    tools = json.loads(mcp.get("additionalData", {}).get("tools", {}).get("data", "{}"))

    print("Server descriptor:")
    print(json.dumps(server, indent=2))
    print("\nTools descriptor:")
    print(json.dumps(tools, indent=2))
else:
    print("No MCP results found.")

# ── 3.7 Drill-Down: A2A Agent Details ────────────────────────────────────────
print("\n=== 3.7 Drill-Down: A2A Agent Details ===")
response = search("issue a refund for a completed order")
records = response.get("registryRecords", [])
a2a_hits = [r for r in records if r["recordType"] == "AGENT"]

if a2a_hits:
    hit = a2a_hits[0]
    record_id = hit["recordId"]
    print(f"Drilling into: {hit['name']} ({record_id})\n")

    full = cp_client.get_registry_record(registryId=REGISTRY_ID, recordId=record_id)
    card = full.get("descriptors", {}).get("a2aAgentCard", {})
    agent_card = json.loads(card.get("data", "{}"))

    print("Agent card:")
    print(json.dumps(agent_card, indent=2))
else:
    print("No A2A results found.")

# ── 3.8 Cross-Type Discovery ──────────────────────────────────────────────────
print("\n=== 3.8 Cross-Type Discovery: Full Fulfillment Workflow ===")
response = search("fulfill an e-commerce order end to end — inventory, payment, and shipping")
records = response.get("registryRecords", [])

by_protocol = {}
for rec in records:
    proto = rec["recordType"]
    by_protocol.setdefault(proto, []).append(rec["name"])

print("\nResults grouped by protocol:")
for proto, names in sorted(by_protocol.items()):
    print(f"  {proto}: {', '.join(names)}")
print(f"\nTotal: {len(records)} tools discovered across {len(by_protocol)} protocol(s)")

# ── 3.9 Searching by Inline Descriptor Content ───────────────────────────────
print("\n=== 3.9 Searching by Inline Descriptor Content ===")
results = search("check_inventory")
results = search("SKU stock availability")
results = search("assign-carrier")

# ── 3.10 Negative Search ─────────────────────────────────────────────────────
print("\n=== 3.10 Negative Search ===")
results = search("quantum computing simulation, molecular dynamics")

# ── 3.11 Raw API Response ────────────────────────────────────────────────────
print("\n=== 3.11 Raw API Response ===")
raw_response = search_raw("order lookup")

# ── 3.12 Metadata-Filtered Search ────────────────────────────────────────────
print("\n=== 3.12 Metadata-Filtered Search ===")

# $eq — MCP only
results = search("payment", filters={"recordType": {"$eq": "MCP"}})

# $ne — exclude MCP
results = search("shipping inventory refund", filters={"recordType": {"$ne": "MCP"}})

# $in — MCP or AGENT
results = search("order management", filters={"recordType": {"$in": ["MCP", "AGENT"]}})

# version filter
results = search("order", filters={"recordVersion": {"$eq": "1.0"}})

# $and — MCP AND version 1.0
results = search(
    "email notification",
    filters={
        "$and": [
            {"recordType": {"$eq": "MCP"}},
            {"recordVersion": {"$eq": "1.0"}},
        ]
    },
)

# $or — MCP OR AGENT
results = search(
    "inventory",
    filters={
        "$or": [
            {"recordType": {"$eq": "MCP"}},
            {"recordType": {"$eq": "AGENT"}},
        ]
    },
)

# filter by exact name
results = search("payment", filters={"name": {"$eq": "payment_status_tool"}})

print("\n✅ Consumer discovery demo complete.")

# ── Cleanup ───────────────────────────────────────────────────────────────────
print("\n=== Cleanup ===")
for rid in record_ids:
    try:
        cp_client.delete_registry_record(registryId=REGISTRY_ID, recordId=rid)
        print(f"  Deleted record: {rid}")
    except Exception as e:  # noqa: BLE001
        print(f"  Error deleting {rid}: {e}")

try:
    cp_client.delete_registry(registryId=REGISTRY_ID)
    print(f"  Deleted registry: {REGISTRY_ID}")
except Exception as e:  # noqa: BLE001
    print(f"  Error deleting registry: {e}")

print("\nCleanup complete!")
