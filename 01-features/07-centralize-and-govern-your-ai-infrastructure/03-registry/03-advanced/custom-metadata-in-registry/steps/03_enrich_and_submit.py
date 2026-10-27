"""Step 3 (enrichment automation in the publisher's pipeline): add tool results, submit, and wait
for the approval function's decisions.

For each draft record the pipeline reads the record, adds the security scan result, CMDB ID and
data classification, writes the full metadata map back, and submits the record for approval. The
three lookups are stubs to replace with your own tools; their results are fixed per record so the
run is repeatable (refunds-mcp fails its scan, customer-pii-mcp holds restricted data).

Submitting emits a pending-approval event. The EventBridge rule from step 0 sends it to the
approval function (AWS Lambda), which approves or rejects the record from its custom metadata.
Nobody approves by hand: this script only waits for each decision and prints it.
"""

from common import FUNCTION_NAME, LOG_GROUP, client, need, wait_for_record

# What the stub tools return for each record.
STUB_RESULTS = {
    "payments-ledger-mcp": {
        "cmdbId": "CI-20417",
        "dataClassification": "internal",
        "scan": "PASSED",
        "report_url": "https://scanner.example.com/reports/payments-ledger-mcp-1042",
    },
    "payments-ledger-sandbox-mcp": {
        "cmdbId": "CI-20418",
        "dataClassification": "internal",
        "scan": "PASSED",
        "report_url": "https://scanner.example.com/reports/payments-ledger-sandbox-mcp-1043",
    },
    "marketing-analytics-mcp": {
        "cmdbId": "CI-31502",
        "dataClassification": "internal",
        "scan": "PASSED",
        "report_url": "https://scanner.example.com/reports/marketing-analytics-mcp-1044",
    },
    "refunds-mcp": {
        "cmdbId": "CI-20433",
        "dataClassification": "internal",
        "scan": "FAILED",
        "report_url": "https://scanner.example.com/reports/refunds-mcp-1045",
    },
    "customer-pii-mcp": {
        "cmdbId": "CI-20460",
        "dataClassification": "restricted",
        "scan": "PASSED",
        "report_url": "https://scanner.example.com/reports/customer-pii-mcp-1046",
    },
}


def run_security_scan(record):  # Replace with your security scanner.
    stub = STUB_RESULTS[record["name"]]
    return {"status": stub["scan"], "report_url": stub["report_url"]}


def get_cmdb_id(record):  # Replace with a lookup in your CMDB.
    return STUB_RESULTS[record["name"]]["cmdbId"]


def get_classification(record):  # Replace with your data classification tool.
    return STUB_RESULTS[record["name"]]["dataClassification"]


registry_id = need("registry_id", "01_define_schema")
record_ids = need("record_ids", "02_register")
cp = client("publisher", "agent-registry-control")
print("   (enrichment automation in the publisher pipeline)")

for name, record_id in record_ids.items():
    record = cp.get_registry_record(registryId=registry_id, recordId=record_id)
    # Rerun guard: only a DRAFT record can be enriched and submitted.
    if record["status"] != "DRAFT":
        print(f"{name}: already {record['status']}; nothing to submit")
        continue
    # UpdateRegistryRecord replaces the whole customMetadata map, so write the publisher's values
    # back together with the new ones.
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
    print(f"{name}: submitted")

# Don't wait for PENDING_APPROVAL first: the approval function can decide within seconds.
print(f"\nwaiting for {FUNCTION_NAME} to decide")
decisions = {}
for name, record_id in record_ids.items():
    try:
        decisions[name] = wait_for_record(cp, registry_id, record_id, "APPROVED", "REJECTED", timeout=120)
    except SystemExit as e:
        raise SystemExit(f"{name}: {e}; check the function's log: aws logs tail {LOG_GROUP}") from None

print(f"\n{'record':28} | {'status':8} | statusReason")
for name, record in decisions.items():
    print(f"{name:28} | {record['status']:8} | {record.get('statusReason')}")
