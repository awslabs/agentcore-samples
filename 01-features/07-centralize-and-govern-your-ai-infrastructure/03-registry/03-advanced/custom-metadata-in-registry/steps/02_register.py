"""Step 2 (publisher): register five MCP server records with custom metadata.

The first attempt sends environment=production, which isn't an allowed value, so the registry
rejects it with a ValidationException. Then the pipeline registers five MCP servers with valid
values, and each is created as a COMPLIANT draft. securityScanStatus starts as PENDING because no
scan has run yet.

There are five so later steps have something to exclude: the approval function rejects
refunds-mcp (its scan fails) and customer-pii-mcp (restricted data), and the consumer's filters
on environment and businessUnit leave out payments-ledger-sandbox-mcp (dev) and
marketing-analytics-mcp (marketing). Only payments-ledger-mcp passes everything.
"""

import json
import uuid

from botocore.exceptions import ClientError
from common import RECORD_NAME, client, need, save_state, wait_for_record

RECORDS = [
    {
        "name": "payments-ledger-mcp",
        "description": "Read-only MCP server for payments ledger lookups",
        "tools": [
            {"name": "get_ledger_entries", "description": "Read ledger entries for an account"},
            {"name": "get_account_balance", "description": "Read the current balance for an account"},
        ],
        "customMetadata": {
            "environment": "prod",
            "businessUnit": "payments",
            "ownerTeam": "payments-platform",
            "networkEgress": "vpc-only",
            "securityScanStatus": "PENDING",
        },
    },
    {
        "name": "payments-ledger-sandbox-mcp",
        "description": "Payments ledger lookups against synthetic sandbox data, for development and testing",
        "tools": [{"name": "get_ledger_entries", "description": "Read synthetic ledger entries for a test account"}],
        "customMetadata": {
            "environment": "dev",
            "businessUnit": "payments",
            "ownerTeam": "payments-platform",
            "networkEgress": "vpc-only",
            "securityScanStatus": "PENDING",
        },
    },
    {
        "name": "marketing-analytics-mcp",
        "description": "Campaign performance and attribution reports for the marketing team",
        "tools": [
            {"name": "get_campaign_metrics", "description": "Read impressions, clicks and conversions for a campaign"},
            {"name": "get_attribution_report", "description": "Read channel attribution for a date range"},
        ],
        "customMetadata": {
            "environment": "prod",
            "businessUnit": "marketing",
            "ownerTeam": "marketing-insights",
            "networkEgress": "allowlisted-internet",
            "securityScanStatus": "PENDING",
        },
    },
    {
        "name": "refunds-mcp",
        "description": "Look up refund requests and refund status for payments",
        "tools": [
            {"name": "get_refund_status", "description": "Read the status of a refund"},
            {"name": "list_refunds_for_account", "description": "List refunds issued to an account"},
        ],
        "customMetadata": {
            "environment": "prod",
            "businessUnit": "payments",
            "ownerTeam": "payments-platform",
            "networkEgress": "vpc-only",
            "securityScanStatus": "PENDING",
        },
    },
    {
        "name": "customer-pii-mcp",
        "description": "Customer profile and cardholder detail lookups for payments support",
        "tools": [
            {"name": "get_customer_profile", "description": "Read a customer's name, address and contact details"},
            {"name": "get_payment_methods", "description": "Read the cards on file for a customer"},
        ],
        "customMetadata": {
            "environment": "prod",
            "businessUnit": "payments",
            "ownerTeam": "customer-data",
            "networkEgress": "none",
            "securityScanStatus": "PENDING",
        },
    },
]

registry_id = need("registry_id", "01_define_schema")
cp = client("publisher", "agent-registry-control")
existing = {r["name"]: r["recordId"] for r in cp.list_registry_records(registryId=registry_id)["registryRecords"]}


def register(spec, custom_metadata):
    return cp.create_registry_record(
        registryId=registry_id,
        name=spec["name"],
        recordType="MCP",
        recordVersion="1.0.0",
        description=spec["description"],
        descriptors={"custom": {"data": json.dumps({"tools": spec["tools"]})}},
        customMetadata=custom_metadata,
        clientToken=str(uuid.uuid4()),
    )


if RECORD_NAME not in existing:
    first_try = {"environment": "production", "businessUnit": "payments", "securityScanStatus": "PENDING"}
    print("customMetadata sent by the pipeline:\n" + json.dumps(first_try, indent=2))
    try:
        register(RECORDS[0], first_try)
        raise SystemExit("expected a ValidationException: the schema did not reject environment=production")
    except ClientError as e:
        if e.response["Error"]["Code"] != "ValidationException":
            raise
        print(f"ValidationException: {e.response['Error']['Message']}\n")
    print(f"customMetadata sent after the fix ({RECORD_NAME}):\n" + json.dumps(RECORDS[0]["customMetadata"], indent=2))

record_ids = {}
for spec in RECORDS:
    if spec["name"] in existing:
        record_ids[spec["name"]] = existing[spec["name"]]
        print(f"{spec['name']}: already registered")
        continue
    record_ids[spec["name"]] = register(spec, spec["customMetadata"])["recordArn"].rsplit("/", 1)[1]
    print(f"{spec['name']}: created")
save_state(record_ids=record_ids)

print()
for name, record_id in record_ids.items():
    record = wait_for_record(cp, registry_id, record_id, "DRAFT", "PENDING_APPROVAL", "APPROVED", "REJECTED")
    print(
        f"{name}: status={record['status']} "
        f"customMetadataSchemaComplianceStatus={record['customMetadataSchemaComplianceStatus']}"
    )
