"""Shared helpers for every step.

- Persona sessions: assume the step's IAM role from your base AWS profile.
- Waits: registries and records change state asynchronously, so steps poll until they settle.
- .state/state.json: IDs one step creates and later steps read (registry ID, record IDs, approval
  function ARN, EventBridge rule ARN). It's in .gitignore because it holds IDs from your account.
"""

import json
import os
import time
from functools import lru_cache
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("REGION", "us-east-1")
BASE_PROFILE = os.environ.get("BASE_PROFILE", "default")
MODEL_ID = os.environ.get("MODEL_ID", "us.anthropic.claude-sonnet-5-5")
PREFIX = "cm-demo"
REGISTRY_NAME = "anycompany-governed-registry"
RECORD_NAME = "payments-ledger-mcp"
RULE_NAME = f"{PREFIX}-pending-approval"
FUNCTION_NAME = f"{PREFIX}-approval-function"
LOG_GROUP = f"/aws/lambda/{FUNCTION_NAME}"
PERSONAS = ("admin", "publisher", "consumer")
STATE_FILE = Path(__file__).resolve().parent.parent / ".state" / "state.json"
LAMBDA_DIR = Path(__file__).resolve().parent.parent / "lambda"


@lru_cache
def base_session():
    return boto3.Session(profile_name=BASE_PROFILE, region_name=REGION)


@lru_cache
def account_id():
    return base_session().client("sts").get_caller_identity()["Account"]


def role_name(persona):
    return f"{PREFIX}-{persona}"


FUNCTION_ROLE = role_name("approval-function")


@lru_cache
def session_for(persona):
    arn = f"arn:aws:iam::{account_id()}:role/{role_name(persona)}"
    sts = base_session().client("sts")
    deadline = time.time() + 60
    while True:
        try:
            creds = sts.assume_role(RoleArn=arn, RoleSessionName=role_name(persona))["Credentials"]
            break
        except ClientError as e:
            # A new IAM role takes a few seconds before STS will let anyone assume it.
            if e.response["Error"]["Code"] != "AccessDenied" or time.time() > deadline:
                raise
            time.sleep(5)
    print(f">> acting as {persona} (role {role_name(persona)})")
    return boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=REGION,
    )


def client(persona, service):
    return session_for(persona).client(service)


def wait_for_record(cp, registry_id, record_id, *statuses, timeout=180):
    """Records change state asynchronously (CREATING/UPDATING -> DRAFT, submit -> PENDING_APPROVAL)."""
    last, deadline = None, time.time() + timeout
    while time.time() < deadline:
        record = cp.get_registry_record(registryId=registry_id, recordId=record_id)
        if record["status"] != last:
            last = record["status"]
            print(f"   record status: {last}")
        if last in statuses:
            return record
        if last.endswith("FAILED"):
            raise SystemExit(f"record {record_id} is {last}: {record.get('statusReason')}")
        time.sleep(3)
    raise SystemExit(f"timed out waiting for record {record_id} to reach {statuses}; last {last}")


def wait_for_registry(cp, registry_id, status, timeout=300):
    last, deadline = None, time.time() + timeout
    while time.time() < deadline:
        registry = cp.get_registry(registryId=registry_id)
        if registry["status"] != last:
            last = registry["status"]
            print(f"   registry status: {last}")
        if last == status:
            return registry
        if last.endswith("FAILED"):
            raise SystemExit(f"registry is {last}: {registry.get('statusReason')}")
        time.sleep(5)
    raise SystemExit(f"timed out waiting for registry to reach {status}; last {last}")


def load_state():
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(**values):
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps({**load_state(), **values}, indent=2))


def need(key, step):
    value = load_state().get(key)
    if not value:
        raise SystemExit(f"{key} missing: run ./{step}.sh first")
    return value
