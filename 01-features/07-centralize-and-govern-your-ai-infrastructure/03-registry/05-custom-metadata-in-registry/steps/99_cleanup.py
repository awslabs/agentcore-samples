"""Delete everything the sample created: the record, the registry, the EventBridge rule, the SQS
queue and the four IAM roles."""

import time

from botocore.exceptions import ClientError
from common import BASE_PROFILE, PERSONAS, QUEUE_NAME, REGISTRY_NAME, RULE_NAME, STATE_FILE, base_session, role_name

NOT_FOUND = {
    "ResourceNotFoundException",
    "NoSuchEntity",
    "AWS.SimpleQueueService.NonExistentQueue",
    "QueueDoesNotExist",
}


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
cp, events, sqs, iam = (session.client(s) for s in ("agent-registry-control", "events", "sqs", "iam"))

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

if ignore_missing(events.remove_targets, Rule=RULE_NAME, Ids=["approver-queue"]):
    events.delete_rule(Name=RULE_NAME)
    print(f"deleted rule {RULE_NAME}")
try:
    sqs.delete_queue(QueueUrl=sqs.get_queue_url(QueueName=QUEUE_NAME)["QueueUrl"])
    print(f"deleted queue {QUEUE_NAME}")
except ClientError as e:
    if e.response["Error"]["Code"] not in NOT_FOUND:
        raise

for persona in PERSONAS:
    role = role_name(persona)
    ignore_missing(iam.delete_role_policy, RoleName=role, PolicyName=f"{role}-policy")
    if ignore_missing(iam.delete_role, RoleName=role):
        print(f"deleted role {role}")

STATE_FILE.unlink(missing_ok=True)
print("cleanup done")
