"""Step 2 (publisher): register an MCP server record with custom metadata.

The first attempt sends environment=production, which isn't an allowed value, so the registry
rejects it with a ValidationException. The second attempt sends prod and the record is created
as a COMPLIANT draft. securityScanStatus starts as PENDING because no scan has run yet.
"""

import json
import uuid

from botocore.exceptions import ClientError
from common import RECORD_NAME, client, need, save_state, wait_for_record

registry_id = need("registry_id", "01_define_schema")
cp = client("publisher", "agent-registry-control")

existing = cp.list_registry_records(registryId=registry_id, filters=[{"name": "name", "values": [RECORD_NAME]}])[
    "registryRecords"
]
if existing:
    save_state(record_id=existing[0]["recordId"])
    print(f"{RECORD_NAME} already registered ({existing[0]['status']}); run ./99_cleanup.sh to start over")
    raise SystemExit(0)

tools = [
    {"name": "get_ledger_entries", "description": "Read ledger entries for an account"},
    {"name": "get_account_balance", "description": "Read the current balance for an account"},
]


def register(custom_metadata):
    return cp.create_registry_record(
        registryId=registry_id,
        name=RECORD_NAME,
        recordType="MCP",
        recordVersion="1.0.0",
        description="Read-only MCP server for payments ledger lookups",
        descriptors={"custom": {"data": json.dumps({"tools": tools})}},
        customMetadata=custom_metadata,
        clientToken=str(uuid.uuid4()),
    )


first_try = {"environment": "production", "businessUnit": "payments", "securityScanStatus": "PENDING"}
print("customMetadata sent by the pipeline:\n" + json.dumps(first_try, indent=2))
try:
    register(first_try)
    raise SystemExit("expected a ValidationException: the schema did not reject environment=production")
except ClientError as e:
    if e.response["Error"]["Code"] != "ValidationException":
        raise
    print(f"ValidationException: {e.response['Error']['Message']}\n")

fixed = {
    "environment": "prod",
    "businessUnit": "payments",
    "ownerTeam": "payments-platform",
    "securityScanStatus": "PENDING",
    "networkEgress": "vpc-only",
}
print("customMetadata sent after the fix:\n" + json.dumps(fixed, indent=2))
record_id = register(fixed)["recordArn"].rsplit("/", 1)[1]
save_state(record_id=record_id)
record = wait_for_record(cp, registry_id, record_id, "DRAFT")
print(
    f"{RECORD_NAME}: status={record['status']} "
    f"customMetadataSchemaComplianceStatus={record['customMetadataSchemaComplianceStatus']}"
)
