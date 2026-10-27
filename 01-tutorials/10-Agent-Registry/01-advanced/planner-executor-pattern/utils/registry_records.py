"""Utility functions for managing AWS Agent Registry records.

Provides helpers to create, submit for approval, and approve registry records
with polling loops that wait for each status transition to complete.

Usage:
    from util.registry_records import create_and_approve_all_records

    record_ids = create_and_approve_all_records(cp_client, REGISTRY_ID, records)
"""

import time

# ---------------------------------------------------------------------------
# Module-level configuration
# ---------------------------------------------------------------------------
POLL_INTERVAL = 3   # seconds between status checks
MAX_RETRIES = 20    # max polling attempts before raising
DEBUG = False       # set to True to print full record payloads


def _wait_for_status(cp_client, registry_id, record_id, target_status, label=""):
    """Poll get_registry_record until status matches *target_status*."""
    for attempt in range(MAX_RETRIES):
        rec = cp_client.get_registry_record(
            registryId=registry_id, recordId=record_id
        )
        status = rec["status"]
        if status == target_status:
            print(f"  {label}{record_id}: {status}")
            return rec
        print(f"  {label}{record_id}: {status} - waiting for {target_status}...")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(
        f"Record {record_id} did not reach {target_status} after "
        f"{MAX_RETRIES * POLL_INTERVAL}s (last status: {status})"
    )


# ---------------------------------------------------------------------------
# Record creation
# ---------------------------------------------------------------------------
def create_record(cp_client, registry_id, name, description, descriptor_type,
                  descriptors, record_version="1.0"):
    """Create a single registry record and wait for DRAFT status.

    Returns the record ID.
    """
    import json as _json
    record_payload = {
        "registryId": registry_id,
        "name": name,
        "description": description,
        "descriptorType": descriptor_type,
        "descriptors": descriptors,
        "recordVersion": record_version,
    }
    print(f"  Creating: {name} ...")
    if DEBUG:
        print(f"    Payload: {_json.dumps(record_payload, indent=2)}")
    try:
        resp = cp_client.create_registry_record(
            registryId=registry_id,
            name=name,
            description=description,
            descriptorType=descriptor_type,
            descriptors=descriptors,
            recordVersion=record_version,
        )
    except Exception as e:
        print(f"  FAILED creating record: {name}")
        print(f"    Record: {_json.dumps(record_payload, indent=2)}")
        if hasattr(e, "response"):
            meta = e.response.get("ResponseMetadata", {})
            print(f"    Request ID:  {meta.get('RequestId', 'N/A')}")
            print(f"    HTTP Status: {meta.get('HTTPStatusCode', 'N/A')}")
            print(f"    Error:       {e.response.get('Error', {})}")
        raise
    record_id = resp["recordArn"].split("/")[-1]
    _wait_for_status(cp_client, registry_id, record_id, "DRAFT", label="Created ")
    return record_id


def create_all_records(cp_client, registry_id, records):
    """Create multiple registry records and wait for each to reach DRAFT.

    *records* is a list of dicts, each with keys:
        name, description, descriptorType, descriptors, record_version (optional)

    Returns a list of record IDs.
    """
    record_ids = []
    for rec in records:
        rid = create_record(
            cp_client, registry_id,
            name=rec["name"],
            description=rec["description"],
            descriptor_type=rec.get("descriptorType", rec.get("descriptor_type")),
            descriptors=rec["descriptors"],
            record_version=rec.get("record_version", "1.0"),
        )
        record_ids.append(rid)
    return record_ids


# ---------------------------------------------------------------------------
# Approval workflow
# ---------------------------------------------------------------------------
def submit_for_approval(cp_client, registry_id, record_id):
    """Submit a record for approval and wait for PENDING_APPROVAL status."""
    cp_client.submit_registry_record_for_approval(
        registryId=registry_id, recordId=record_id
    )
    _wait_for_status(cp_client, registry_id, record_id, "PENDING_APPROVAL",
                     label="Submitted ")


def approve_record(cp_client, registry_id, record_id):
    """Approve a record and wait for APPROVED status."""
    cp_client.update_registry_record_status(
        registryId=registry_id, recordId=record_id,
        status="APPROVED",
        statusReason="Approved via notebook",
    )
    _wait_for_status(cp_client, registry_id, record_id, "APPROVED",
                     label="Approved ")


def submit_all_for_approval(cp_client, registry_id, record_ids):
    """Submit multiple records for approval, waiting for each."""
    for rid in record_ids:
        submit_for_approval(cp_client, registry_id, rid)


def approve_all_records(cp_client, registry_id, record_ids):
    """Approve multiple records, waiting for each."""
    for rid in record_ids:
        approve_record(cp_client, registry_id, rid)


# ---------------------------------------------------------------------------
# Convenience: create + full approval in one call
# ---------------------------------------------------------------------------
def create_and_approve_record(cp_client, registry_id, name, description,
                              descriptor_type, descriptors, record_version="1.0"):
    """Create a record, submit for approval, and approve it.

    Returns the record ID.
    """
    record_id = create_record(
        cp_client, registry_id, name, description,
        descriptor_type, descriptors, record_version,
    )
    submit_for_approval(cp_client, registry_id, record_id)
    approve_record(cp_client, registry_id, record_id)
    return record_id


def create_and_approve_all_records(cp_client, registry_id, records):
    """Create, submit, and approve multiple records.

    *records* is a list of dicts, each with keys:
        name, description, descriptorType, descriptors, record_version (optional)

    Returns a list of record IDs.
    """
    record_ids = create_all_records(cp_client, registry_id, records)
    submit_all_for_approval(cp_client, registry_id, record_ids)
    approve_all_records(cp_client, registry_id, record_ids)
    return record_ids
