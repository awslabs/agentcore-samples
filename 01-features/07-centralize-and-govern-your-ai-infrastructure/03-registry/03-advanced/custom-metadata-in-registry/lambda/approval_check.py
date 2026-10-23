"""Approval function (AWS Lambda): approve or reject a record from its custom metadata.

The EventBridge rule anycompany-registry-pending-approval invokes this function for every AWS Agent Registry
"Registry Record State changed to Pending Approval" event in the account. The event's detail holds
plain IDs (registryId, registryRecordId); its resources list holds the record ARN.

The function:
1. Skips events from other registries (it only decides for the registry named in REGISTRY_NAME)
   and records that are no longer PENDING_APPROVAL, so duplicate events and retries are harmless.
2. Runs four checks on the record: schema compliance COMPLIANT, securityScanStatus PASSED, a cmdbId,
   and dataClassification public or internal (anything else needs a person to review it).
3. Calls UpdateRegistryRecordStatus with APPROVED or REJECTED and a reason that names the values
   it checked or the checks that failed.

Its role can read records and set their status but can't call UpdateRegistryRecord, so it can't
change the evidence it judges. The role's policy covers every registry in the account (the registry
doesn't exist when 00_setup creates the role), so the REGISTRY_NAME check in step 1 is what limits
the function to the sample's registry.
"""

import json
import os

import boto3

cp = boto3.client("agent-registry-control")
REGISTRY_NAME = os.environ["REGISTRY_NAME"]


def failed_checks(record):
    meta, compliance = (record.get("customMetadata") or {}), record.get("customMetadataSchemaComplianceStatus")
    failed = []
    if compliance != "COMPLIANT":
        failed.append(f"customMetadataSchemaComplianceStatus={compliance}")
    if meta.get("securityScanStatus") != "PASSED":
        failed.append(f"securityScanStatus={meta.get('securityScanStatus')}")
    if not meta.get("cmdbId"):
        failed.append("cmdbId missing")
    if meta.get("dataClassification") not in {"public", "internal"}:
        failed.append(f"dataClassification={meta.get('dataClassification')} (needs manual review)")
    return failed


def reason_for(record, failed):
    if failed:
        return "Automated check failed: " + ", ".join(failed)
    m = record.get("customMetadata") or {}
    return (
        f"{m['environment']}, {m['businessUnit']}, {m['dataClassification']} data, "
        f"CMDB {m['cmdbId']}, scan {m['securityScanStatus']}"
    )


# No try/except: an exception fails the invocation, and Lambda retries asynchronous invocations
# (EventBridge invokes asynchronously) twice by default.
def handler(event, context):
    # Never print the event itself: its envelope includes the account ID.
    registry_id, record_id = event["detail"]["registryId"], event["detail"]["registryRecordId"]
    registry_name = cp.get_registry(registryId=registry_id)["name"]
    if registry_name != REGISTRY_NAME:
        print(json.dumps({"skipped": record_id, "why": f"registry {registry_name} is not {REGISTRY_NAME}"}))
        return
    record = cp.get_registry_record(registryId=registry_id, recordId=record_id)
    if record["status"] != "PENDING_APPROVAL":
        print(json.dumps({"skipped": record["name"], "why": f"status is {record['status']}"}))
        return

    failed = failed_checks(record)
    decision, reason = ("REJECTED" if failed else "APPROVED"), reason_for(record, failed)[:255]
    cp.update_registry_record_status(registryId=registry_id, recordId=record_id, status=decision, statusReason=reason)
    meta = record.get("customMetadata") or {}
    print(
        json.dumps(
            {
                "record": record["name"],
                "decision": decision,
                "reason": reason,
                "customMetadataSchemaComplianceStatus": record.get("customMetadataSchemaComplianceStatus"),
                "securityScanStatus": meta.get("securityScanStatus"),
                "cmdbId": meta.get("cmdbId"),
                "dataClassification": meta.get("dataClassification"),
            }
        )
    )
