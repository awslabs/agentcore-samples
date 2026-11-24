"""Local ownership records and bounded waits for SDK-owned lab resources.

Records contain identifiers, never credential values. Keep this directory on
partial failure: it is separate from CLI state and is needed to resume cleanup.
"""

import json
import os
import re
import tempfile
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

STATE_DIR = Path(__file__).resolve().parents[1] / ".vpc-state"


def account_for(session):
    if not session.region_name:
        raise ValueError("Set an explicit AWS region before managing lab resources")
    account = session.client("sts").get_caller_identity()["Account"]
    if not re.fullmatch(r"\d{12}", account):
        raise ValueError("Invalid caller account")
    return account


def verified_session(record):
    # NotFound in the wrong account is not evidence that our resource is gone.
    if not re.fullmatch(r"\d{12}", record.get("accountId", "")):
        raise ValueError(
            "Ownership record lacks accountId; reconcile it before cleanup/resume"
        )
    session = boto3.Session(region_name=record["region"])
    if account_for(session) != record["accountId"]:
        raise RuntimeError(
            "Caller account differs from the ownership record; no resource operation attempted"
        )
    return session


def record_path(kind, name):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", name):
        raise ValueError("Resource name must contain only letters, digits, _ or -")
    return STATE_DIR / f"{kind}-{name}.json"


def save(path, record):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".record-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(record, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def reserve(path, record):
    """Never overwrite an earlier run or silently adopt a same-name resource."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(record, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def load(path):
    return json.loads(path.read_text())


def begin(record, path, operation):
    """Journal an operation whose response may be lost, before submitting it."""
    record["pendingOperation"] = operation
    save(path, record)


def complete(record, path, **identifiers):
    record.update(identifiers)
    record.pop("pendingOperation", None)
    save(path, record)


def require_reconciled(record):
    if record.get("pendingOperation"):
        raise RuntimeError(
            f"Reconcile the uncertain {record['pendingOperation']} response before cleanup; keep the ownership record"
        )
    if record.get("status") == "CREATE_PENDING" and not any(
        record.get(key)
        for key in ("resourceGatewayId", "securityGroupId", "aliasCreated")
    ):
        raise RuntimeError(
            "No creation response recorded; reconcile ownership before cleanup"
        )


def not_found(error):
    return (
        isinstance(error, ClientError)
        and error.response["Error"]["Code"] == "ResourceNotFoundException"
    )


def wait_target(control, record, path, *, deleting=False, attempts=30, delay=10):
    for attempt in range(attempts):
        try:
            response = control.get_gateway_target(
                gatewayIdentifier=record["gatewayId"], targetId=record["targetId"]
            )
        except ClientError as error:
            if deleting and not_found(error):
                record["status"] = "DELETED"
                save(path, record)
                return
            raise
        record["status"] = response["status"]
        record["statusReasons"] = response.get("statusReasons", [])
        save(path, record)
        if not deleting and record["status"] == "READY":
            return
        if record["status"] in {"FAILED", "UPDATE_UNSUCCESSFUL", "DELETE_UNSUCCESSFUL"}:
            raise RuntimeError(
                f"Target {record['targetId']}: {record['status']}; see {path}"
            )
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise TimeoutError(
        f"Target {record['targetId']} timed out ({record['status']}); keep {path}"
    )
