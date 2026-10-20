"""Blog Step 3, enrichment automation (runs as the publisher's pipeline role): add scan, CMDB and
classification results, then submit for approval. --fail-scan makes the scan stub return FAILED."""

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

current = cp.get_registry_record(registryId=registry_id, recordId=record_id)["status"]
if current != "DRAFT":
    print(f"{RECORD_NAME} is {current}, not DRAFT; nothing to submit")
    raise SystemExit(0)

record = cp.get_registry_record(registryId=registry_id, recordId=record_id)
scan = run_security_scan(record)  # Replace this stub with your scanner call.
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
