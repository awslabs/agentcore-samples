"""Delete everything deploy.py created: runtime -> bucket contents -> stack. Dry run unless --yes.

    python infra/cleanup.py
    python infra/cleanup.py --yes

To delete a deployment made under other names (for example before NAME in config.py changed), pass them:

    python infra/cleanup.py --stack-name <old stack> --runtime-name <old runtime> --yes
"""

import argparse
import time

from config import RUNTIME_NAME, STACK_NAME, STATE_FILE, client, find_runtime, log, stack_outputs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--yes", action="store_true", help="actually delete")
    ap.add_argument("--stack-name", default=STACK_NAME, help=f"default: {STACK_NAME}")
    ap.add_argument("--runtime-name", default=RUNTIME_NAME, help=f"default: {RUNTIME_NAME}")
    a = ap.parse_args()
    dry = not a.yes
    if dry:
        log("DRY RUN -- pass --yes to delete")

    ctl = client("bedrock-agentcore-control")
    rt = find_runtime(ctl, a.runtime_name)
    if not rt:
        log(f"runtime {a.runtime_name}: not found")
    elif dry:
        log(f"would delete runtime {rt['agentRuntimeId']}")
    else:
        ctl.delete_agent_runtime(agentRuntimeId=rt["agentRuntimeId"])
        while find_runtime(ctl, a.runtime_name):
            time.sleep(10)
        log(f"deleted runtime {rt['agentRuntimeId']}")

    bucket = stack_outputs(a.stack_name).get("DataBucketName")
    if bucket:
        s3 = client("s3")
        keys = [
            {"Key": o["Key"]}
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket)
            for o in page.get("Contents", [])
        ]
        if not dry:
            for i in range(0, len(keys), 1000):
                s3.delete_objects(Bucket=bucket, Delete={"Objects": keys[i : i + 1000], "Quiet": True})
        log(f"{'would empty' if dry else 'emptied'} s3://{bucket} ({len(keys)} objects)")

    cfn = client("cloudformation")
    try:
        cfn.describe_stacks(StackName=a.stack_name)
    except cfn.exceptions.ClientError:
        log(f"stack {a.stack_name}: not found")
        return 0
    if dry:
        log(f"would delete stack {a.stack_name}")
        return 0
    cfn.delete_stack(StackName=a.stack_name)
    cfn.get_waiter("stack_delete_complete").wait(StackName=a.stack_name)
    log(f"deleted stack {a.stack_name}")
    if a.stack_name == STACK_NAME:  # the state file (with the agencies' session IDs) belongs to this deployment
        STATE_FILE.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
