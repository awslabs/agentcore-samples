"""Step 3 (enrichment automation in the publisher's pipeline): add tool results, then submit.

Reads the record, adds the security scan result, CMDB ID and data classification, writes the full
metadata map back, and submits the record for approval. The three lookups are stubs to replace
with your own tools. Run with --fail-scan to make the scan return FAILED.
"""

import json
import sys

from common import RECORD_NAME, client, need, wait_for_record

FAIL_SCAN = "--fail-scan" in sys.argv


def run_security_scan(record):  # Replace with your security scanner.
    return {
        "status": "FAILED" if FAIL_SCAN else "PASSED",
        "report_url": "https://scanner.example.com/reports/payments-ledger-mcp-1042",
    }


def get_cmdb_id(record):  # Replace with a lookup in your CMDB.
    return "CI-20417"


def get_classification(record):  # Replace with your data classification tool.
    return "internal"


registry_id = need("registry_id", "01_define_schema")
record_id = need("record_id", "02_register")
cp = client("publisher", "agent-registry-control")
print("   (enrichment automation in the publisher pipeline)")

# Rerun guard: only a DRAFT record can be enriched and submitted.
current = cp.get_registry_record(registryId=registry_id, recordId=record_id)["status"]
if current != "DRAFT":
    print(f"{RECORD_NAME} is {current}, not DRAFT; nothing to submit")
    raise SystemExit(0)

# UpdateRegistryRecord replaces the whole customMetadata map, so read the publisher's values
# first and write them back together with the new ones.
record = cp.get_registry_record(registryId=registry_id, recordId=record_id)
scan = run_security_scan(record)
metadata = {
    **record["customMetadata"],
    "cmdbId": get_cmdb_id(record),
    "dataClassification": get_classification(record),
    "securityScanStatus": scan["status"],
    "securityScanReportUrl": scan["report_url"],
}
cp.update_registry_record(registryId=registry_id, recordId=record_id, customMetadata={"optionalValue": metadata})
wait_for_record(cp, registry_id, record_id, "DRAFT")
cp.submit_registry_record_for_approval(registryId=registry_id, recordId=record_id)

record = wait_for_record(cp, registry_id, record_id, "PENDING_APPROVAL")
print("customMetadata after enrichment:\n" + json.dumps(record["customMetadata"], indent=2))
print(
    f"{RECORD_NAME}: status={record['status']} "
    f"customMetadataSchemaComplianceStatus={record['customMetadataSchemaComplianceStatus']}"
)
