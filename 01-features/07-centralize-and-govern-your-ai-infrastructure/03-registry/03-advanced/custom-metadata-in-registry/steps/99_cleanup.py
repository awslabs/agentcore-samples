"""Delete everything the sample created: the records, the registry, the EventBridge rule and its
target, the approval function (AWS Lambda) and its log group, and the four IAM roles (three personas
plus the approval function's role)."""

import time

from botocore.exceptions import ClientError
from common import (
    BASE_PROFILE,
    FUNCTION_NAME,
    LOG_GROUP,
    PERSONAS,
    REGISTRY_NAME,
    RULE_NAME,
    STATE_FILE,
    base_session,
    role_name,
)

NOT_FOUND = {"ResourceNotFoundException", "NoSuchEntity"}


def ignore_missing(call, **kwargs):
    try:
        call(**kwargs)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] not in NOT_FOUND:
            raise
        return False


def wait_gone(get, what, timeout=300):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            get()
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                return print(f"deleted {what}")
            raise
        time.sleep(5)
    raise SystemExit(f"timed out waiting for {what} to be deleted")


print(f">> acting as base credentials (profile {BASE_PROFILE})")
session = base_session()
cp, events, lam, logs, iam = (session.client(s) for s in ("agent-registry-control", "events", "lambda", "logs", "iam"))

for registry in [r for r in cp.list_registries()["registries"] if r["name"] == REGISTRY_NAME]:
    reg_id = registry["registryId"]
    for rec in cp.list_registry_records(registryId=reg_id)["registryRecords"]:
        cp.delete_registry_record(registryId=reg_id, recordId=rec["recordId"])
        wait_gone(
            lambda r=reg_id, i=rec["recordId"]: cp.get_registry_record(registryId=r, recordId=i),
            f"record {rec['name']} ({rec['status']})",
        )
    cp.delete_registry(registryId=reg_id)
    wait_gone(lambda r=reg_id: cp.get_registry(registryId=r), f"registry {REGISTRY_NAME}")

if ignore_missing(events.remove_targets, Rule=RULE_NAME, Ids=["approval-function"]):
    events.delete_rule(Name=RULE_NAME)
    print(f"deleted rule {RULE_NAME}")
if ignore_missing(lam.delete_function, FunctionName=FUNCTION_NAME):
    print(f"deleted function {FUNCTION_NAME}")
if ignore_missing(logs.delete_log_group, logGroupName=LOG_GROUP):
    print(f"deleted log group {LOG_GROUP}")

for persona in PERSONAS + ("approval-function",):
    role = role_name(persona)
    ignore_missing(iam.delete_role_policy, RoleName=role, PolicyName=f"{role}-policy")
    if ignore_missing(iam.delete_role, RoleName=role):
        print(f"deleted role {role}")

STATE_FILE.unlink(missing_ok=True)
print("cleanup done")
