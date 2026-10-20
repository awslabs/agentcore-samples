"""Step 4 (approver): decide on the record using its custom metadata.

1. Wait for the pending-approval event on the SQS queue (sent by the EventBridge rule).
2. Show that the approver role can't edit the metadata, so it can't fake a scan result.
3. Automated check: COMPLIANT, scan PASSED, cmdbId present, data public or internal.
   If any check fails, reject with a reason that names the failed checks.
4. Otherwise ask the approver y/N. --yes or --no answers without prompting.
"""

import json
import sys
import time

from botocore.exceptions import ClientError
from common import QUEUE_NAME, RECORD_NAME, RULE_NAME, client, need, wait_for_record

registry_id = need("registry_id", "01_define_schema")
queue_url = need("queue_url", "00_setup")
cp = client("approver", "agent-registry-control")
sqs = client("approver", "sqs")


def next_event():
    """Returns (event, receipt_handle). The caller deletes the message only after the decision is
    recorded; if the status update fails, the message becomes visible again and nothing is lost."""
    deadline = time.time() + 90
    while time.time() < deadline:
        # Hide the message for 10 minutes so it stays ours while the approver decides.
        for msg in sqs.receive_message(
            QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=20, VisibilityTimeout=600
        ).get("Messages", []):
            event = json.loads(msg["Body"])
            if event["detail"]["registryId"] == registry_id:
                return event, msg["ReceiptHandle"]
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
            print(f"   skipped event for another registry ({event['detail']['registryId']})")
    raise SystemExit(f"no pending-approval event in 90 s; check rule {RULE_NAME} and queue {QUEUE_NAME}")


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


# Rerun guard: if the record was already decided, there is no event left to wait for.
current = cp.get_registry_record(registryId=registry_id, recordId=need("record_id", "02_register"))
if current["status"] != "PENDING_APPROVAL":
    print(f"{current['name']} is {current['status']} ({current.get('statusReason')}); nothing to approve")
    raise SystemExit(0)

print(f"waiting for a pending-approval event on {QUEUE_NAME} ...")
event, receipt_handle = next_event()
record_id = event["detail"]["registryRecordId"]
# Print only the event's detail; the full envelope includes your account ID.
print(f"event: {event['detail-type']}")
print(json.dumps(event["detail"], indent=2))

record = cp.get_registry_record(registryId=registry_id, recordId=record_id)
meta, compliance = record["customMetadata"], record["customMetadataSchemaComplianceStatus"]
print(f"\n{record['name']}: status={record['status']} customMetadataSchemaComplianceStatus={compliance}")
print(json.dumps(meta, indent=2))

print("\napprover tries to change the scan result itself:")
try:
    cp.update_registry_record(
        registryId=registry_id,
        recordId=record_id,
        customMetadata={"optionalValue": {**meta, "securityScanStatus": "PASSED"}},
    )
    raise SystemExit("expected AccessDenied: the approver role can edit metadata")
except ClientError as e:
    if e.response["Error"]["Code"] not in ("AccessDeniedException", "AccessDenied"):
        raise
    print(f"   {e.response['Error']['Code']}: the approver can't edit metadata, only approve or reject")

print("\ncheck 1, automated:")
checks = {
    f"customMetadataSchemaComplianceStatus={compliance}": compliance == "COMPLIANT",
    f"securityScanStatus={meta.get('securityScanStatus')}": meta.get("securityScanStatus") == "PASSED",
    f"cmdbId={meta.get('cmdbId')}": bool(meta.get("cmdbId")),
    f"dataClassification={meta.get('dataClassification')}": meta.get("dataClassification") in ("public", "internal"),
}
for check, ok in checks.items():
    print(f"   {'PASS' if ok else 'FAIL'}  {check}")
failed = [check for check, ok in checks.items() if not ok]

if failed:
    status, reason = "REJECTED", "Automated check failed: " + ", ".join(failed)
else:
    print("automated checks passed\n\ncheck 2, approver:")
    answer = "y" if "--yes" in sys.argv else "n" if "--no" in sys.argv else ask(f"Approve {RECORD_NAME}? [y/N] ")
    if answer.lower() == "y":
        status = "APPROVED"
        reason = (
            f"{meta['environment']}, {meta['businessUnit']}, {meta['dataClassification']} data, "
            f"CMDB {meta['cmdbId']}, scan {meta['securityScanStatus']}"
        )
    else:
        status = "REJECTED"
        reason = ("" if "--no" in sys.argv else ask("Reason for rejecting: ")) or "Rejected by approver"

cp.update_registry_record_status(registryId=registry_id, recordId=record_id, status=status, statusReason=reason[:255])
sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
record = wait_for_record(cp, registry_id, record_id, "APPROVED", "REJECTED")
print(f"{record['name']}: status={record['status']} statusReason={record.get('statusReason')!r}")
