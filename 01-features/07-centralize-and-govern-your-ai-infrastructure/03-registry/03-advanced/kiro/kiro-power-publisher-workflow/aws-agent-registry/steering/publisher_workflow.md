# AWS Agent Registry Publisher Workflow

Below are the sample code snippets to carry out the operations for AWS Agent Registry for the publisher persona. Generate necessary python scripts, including a `utils.py` where necessary, to execute the below operations if the API operation is too long.

---

## 1. Assume publisher_persona role

```python
import boto3
import json
import time
import utils
import os
from botocore.exceptions import ClientError

AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "<AWS_DEFAULT_ACCOUNT>")

# Auto-detect account ID from current credentials
sts = boto3.client("sts", region_name=AWS_REGION)
ACCOUNT_ID = sts.get_caller_identity()["Account"]
CALLER_ARN = sts.get_caller_identity()["Arn"]

PUBLISHER_ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/publisher_persona"

print(f"Account:  {PUBLISHER_ROLE_ARN}")

# Assume the Admin role
creds = utils.assume_role(
    role_arn=PUBLISHER_ROLE_ARN,
    session_name="publisher_session",
)
session = boto3.Session(
    aws_access_key_id=creds["AccessKeyId"],
    aws_secret_access_key=creds["SecretAccessKey"],
    aws_session_token=creds["SessionToken"],
    region_name=AWS_REGION,
)


```

## 2. List Registries

Create or update src/list_registries.py

```python
registries = cp_client.list_registries()
print(f"Publisher can see {len(registries.get('registries', []))} registries:\n")
for reg in registries.get("registries", []):
    utils.pp(reg)
```

---

## 3. Publish MCP Record to Registry

Create or update src/create_registry_record_mcp.py

Define the MCP server and tools schema, then create the registry record.

```python
mcp_server_schema = json.dumps({
    "name": "io.novacorp/payment-processing-server",
    "description": "A payment processing MCP server for handling transactions, refunds, and payment status queries",
    "version": "1.0.0",
    "title": "Payment Processing Server",
    "packages": [
        {
            "registryType": "npm",
            "identifier": "@novacorp/payment-processing-mcp",
            "version": "1.0.0",
            "registryBaseUrl": "https://registry.npmjs.org",
            "runtimeHint": "npx",
            "transport": {"type": "stdio"},
        }
    ],
})

mcp_tool_schema = json.dumps({
    "tools": [
        {
            "name": "process_payment",
            "description": "Process a new payment transaction for a given amount and currency",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "amount":         {"type": "number", "description": "Payment amount"},
                    "currency":       {"type": "string", "description": "ISO 4217 currency code (e.g. USD, EUR)"},
                    "customer_id":    {"type": "string", "description": "Unique customer identifier"},
                    "payment_method": {
                        "type": "string",
                        "description": "Payment method type",
                        "enum": ["credit_card", "debit_card", "bank_transfer", "digital_wallet"],
                    },
                    "description":    {"type": "string", "description": "Optional payment description"},
                },
                "required": ["amount", "currency", "customer_id", "payment_method"],
            },
        },
        {
            "name": "get_payment_status",
            "description": "Retrieve the current status of a payment by transaction ID",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string", "description": "Unique transaction identifier"},
                },
                "required": ["transaction_id"],
            },
        },
        {
            "name": "process_refund",
            "description": "Initiate a full or partial refund for a completed transaction",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "transaction_id": {"type": "string", "description": "Original transaction ID to refund"},
                    "amount":         {"type": "number", "description": "Refund amount (omit for full refund)"},
                    "reason":         {"type": "string", "description": "Reason for the refund"},
                },
                "required": ["transaction_id", "reason"],
            },
        },
    ]
})
```

```python
MCP_RECORD_ID = None
try:
    mcp_resp = cp_client.create_registry_record(
        registryId=REGISTRY_ID,
        name="mcp_payment_processing_server",
        description="MCP server for processing payments, refunds, and transaction queries",
        descriptorType="MCP",
        descriptors={
            "mcp": {
                "server": {
                    "schemaVersion": "2025-12-11",
                    "inlineContent": mcp_server_schema,
                },
                "tools": {
                    "inlineContent": mcp_tool_schema,
                },
            }
        },
        recordVersion="1.0",
    )
    MCP_RECORD_ID = mcp_resp["recordArn"].split("/")[-1]
    print(f"Created MCP record: {MCP_RECORD_ID}")
    record = utils.wait_for_record_ready(cp_client, REGISTRY_ID, MCP_RECORD_ID)
    print(f"Status: {record.get('status', 'UNKNOWN')}")

except ClientError as e:
    if e.response["Error"]["Code"] == "ConflictException":
        print("Record 'mcp_payment_processing_server' already exists — looking it up...")
        records = cp_client.list_registry_records(registryId=REGISTRY_ID)
        for rec in records.get("registryRecords", []):
            if rec["name"] == "mcp_payment_processing_server":
                MCP_RECORD_ID = rec["registryRecordId"]
                break
        print(f"  Using existing record: {MCP_RECORD_ID}")
    else:
        raise

print(f"\nMCP_RECORD_ID = {MCP_RECORD_ID}")
```

---

## 4. Publish Agent Card to Registry

Create or update src/create_registry_record_a2a.py

Fetch the agent card and pass it as `inlineContent` in the descriptors.

```python
a2a_agent_card = json.dumps({
    "protocolVersion": "0.3.0",
    "name": "Payment Processing Agent",
    "description": "Handles payment transactions, refunds, and billing inquiries",
    "url": "https://example.com/agents/payment",
    "version": "1.0.0",
    "capabilities": {"streaming": True},
    "defaultInputModes": ["text"],
    "defaultOutputModes": ["text"],
    "preferredTransport": "JSONRPC",
    "skills": [
        {"id": "payment_processing", "name": "Payment Processing", "description": "Process payments", "tags": []},
        {"id": "refund_handling",    "name": "Refund Handling",    "description": "Handle refunds",   "tags": []},
        {"id": "billing_inquiry",    "name": "Billing Inquiry",    "description": "Answer billing questions", "tags": []},
    ],
})
```

```python
REGISTRY_ID = ""  # Replace with your registry ID

resp2 = cp_client.create_registry_record(
    registryId=REGISTRY_ID,
    name=AGENT_NAME,
    descriptorType="A2A",
    recordVersion="1.0",
    description="Test agent",
    descriptors={
        "a2a": {
            "agentCard": {
                "schemaVersion": "0.3",
                "inlineContent": a2a_agent_card,
            }
        }
    },
)

A2A_RECORD_ID = resp2["recordArn"].split("/")[-1]
print(f"Creating A2A registry record: {A2A_RECORD_ID}")

record = utils.wait_for_record_ready(cp_client, REGISTRY_ID, A2A_RECORD_ID)
print(f"\nName: {record['name']} | Status: {record['status']}")
```

---

## 5. List Registry Records

Create or update src/list_registry_records.py

```python
all_records = []
resp = cp_client.list_registry_records(registryId=REGISTRY_ID)
all_records.extend(resp.get("registryRecords", []))
while resp.get("nextToken"):
    resp = cp_client.list_registry_records(registryId=REGISTRY_ID, nextToken=resp["nextToken"])
    all_records.extend(resp.get("registryRecords", []))

print(f"Found {len(all_records)} record(s) in registry {REGISTRY_ID}\n")
print(f"{'#':<4} {'Name':<35} {'Record ID':<20} {'Status'}")
print("-" * 85)
for i, r in enumerate(all_records, 1):
    print(f"{i:<4} {r.get('name','N/A'):<35} {r.get('recordId','N/A'):<20} {r.get('status','N/A')}")
```

---

## 6. Submit for Approval

Create or update src/submit_registry_record_for_approval.py

```python
draft_resp = cp_client.list_registry_records(
    registryId=REGISTRY_ID,
    status="DRAFT",
)
draft_records = draft_resp.get("registryRecords", [])
print(f"Found {len(draft_records)} DRAFT records\n")

for rec in draft_records:
    record_id = rec["recordId"]
    try:
        submit_resp = cp_client.submit_registry_record_for_approval(
            registryId=REGISTRY_ID,
            recordId=record_id,
        )
        print(f"Submitted: {rec['name']} ({rec['descriptorType']}) — {record_id}")
        utils.wait_for_record_ready(cp_client, REGISTRY_ID, record_id)
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        error_msg = e.response["Error"].get("Message", "")
        print(f"Failed: {rec['name']} ({record_id}) — {error_code}: {error_msg}")
    print()
```

---

## 7. Deleting Records

### Option A: Delete records by status

Create or update src/delete_registry_record.py

```python
STATUS_TO_DELETE = "CREATE_FAILED"
targets = [r for r in all_records if r.get("status") == STATUS_TO_DELETE]
print(f"{len(targets)} record(s) with status '{STATUS_TO_DELETE}'\n")
for r in targets:
    print(f"  {r.get('name')} — {r.get('recordId')}")
for r in targets:
    try:
        cp_client.delete_registry_record(registryId=REGISTRY_ID, recordId=r["recordId"])
        print(f"  Deleted: {r['name']} ({r['recordId']})")
    except Exception as e:
        print(f"  FAILED:  {r['name']} ({r['recordId']}) — {e}")
```

### Option B: Delete specific record IDs

Create or update src/delete_registry_record.py

```python
IDS_TO_DELETE = [
    "DWEXC7BNKJzq",
    "GRtJFOsueLll",
]
targets = [r for r in all_records if r.get("recordId") in IDS_TO_DELETE]
print(f"{len(targets)} record(s) matched\n")
for r in targets:
    print(f"  {r.get('name')} — {r.get('recordId')}")
for r in targets:
    try:
        cp_client.delete_registry_record(registryId=REGISTRY_ID, recordId=r["recordId"])
        print(f"  Deleted: {r['name']} ({r['recordId']})")
    except Exception as e:
        print(f"  FAILED:  {r['name']} ({r['recordId']}) — {e}")
```

### Option C: Delete all records

Create or update src/delete_registry_record.py

```python
print(f"This will delete ALL {len(all_records)} record(s) in registry {REGISTRY_ID}\n")
for r in all_records:
    try:
        cp_client.delete_registry_record(registryId=REGISTRY_ID, recordId=r["recordId"])
        print(f"  Deleted: {r['name']} ({r['recordId']})")
    except Exception as e:
        print(f"  FAILED:  {r['name']} ({r['recordId']}) — {e}")
```
