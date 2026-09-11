"""
Tear down every resource this demo created — safe & idempotent.

Order (matches AWS dependency graph so nothing gets stuck):
  1. Terminate every RUNNING MicroVM launched from our image.
  2. Deactivate then delete each image version, then delete the image.
  3. Delete the two IAM roles (drop inline policies first).
  4. Empty and delete the S3 bucket (drops all versions if versioned).
  5. Optionally delete the CloudWatch log group + resource policy
     (skipped by default so you can inspect telemetry after tear-down;
     pass --wipe-logs to remove them too).

We never touch account-wide toggles like CloudWatch Transaction Search
(you probably want that on for other work) and we never touch resources
that don't carry the demo PREFIX.

Run:
    python3 scripts/cleanup.py           # keep the log group
    python3 scripts/cleanup.py --wipe-logs
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

import boto3
import botocore.exceptions

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import config

# --------------------------------------------------------------------------

def step(msg: str) -> None:
    print(f"\n\033[1;36m▶ {msg}\033[0m")

def ok(msg: str) -> None:
    print(f"  \033[32m✓\033[0m {msg}")

def info(msg: str) -> None:
    print(f"  · {msg}")

def warn(msg: str) -> None:
    print(f"  \033[33m!\033[0m {msg}")


# --------------------------------------------------------------------------
# 1. Terminate MicroVMs from our image
# --------------------------------------------------------------------------

def terminate_microvms() -> None:
    step("1/5 Terminate MicroVMs launched from our image")
    mvm = boto3.client("lambda-microvms", region_name=config.REGION)
    target_image_arn = config.image_arn()

    to_terminate: list[str] = []
    try:
        paginator = mvm.get_paginator("list_microvms")
        for page in paginator.paginate():
            for item in page.get("items", []):
                if item.get("imageArn") == target_image_arn:
                    to_terminate.append(item["microvmId"])
    except botocore.exceptions.ClientError as e:
        warn(f"list_microvms failed: {e.response['Error']['Code']}")
        return

    if not to_terminate:
        info("no MicroVMs from this image")
        return

    for vm_id in to_terminate:
        info(f"terminating {vm_id} …")
        try:
            mvm.terminate_microvm(microvmIdentifier=vm_id)
        except botocore.exceptions.ClientError as e:
            warn(f"  {e.response['Error']['Code']}: {e.response['Error'].get('Message')}")

    # Wait until they're actually gone (terminated MicroVMs can block image deletion).
    deadline = time.time() + 300
    remaining = list(to_terminate)
    while remaining and time.time() < deadline:
        still = []
        for vm_id in remaining:
            try:
                r = mvm.get_microvm(microvmIdentifier=vm_id)
                if r["state"] != "TERMINATED":
                    still.append(vm_id)
            except botocore.exceptions.ClientError as e:
                if e.response["Error"]["Code"] not in ("ResourceNotFoundException",):
                    warn(f"  {vm_id}: {e.response['Error']['Code']}")
        if not still:
            break
        info(f"waiting for {len(still)} MicroVM(s) to terminate …")
        time.sleep(10)
        remaining = still
    ok(f"terminated {len(to_terminate)} MicroVM(s)")


# --------------------------------------------------------------------------
# 2. Delete image versions + image
# --------------------------------------------------------------------------

def delete_image() -> None:
    step("2/5 Delete MicroVM image")
    mvm = boto3.client("lambda-microvms", region_name=config.REGION)

    # Does the image exist?
    try:
        mvm.get_microvm_image(imageIdentifier=config.IMAGE_NAME)
    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            info("image does not exist")
            return
        raise

    # Delete versions first
    try:
        paginator = mvm.get_paginator("list_microvm_image_versions")
        versions: list[str] = []
        for page in paginator.paginate(imageIdentifier=config.IMAGE_NAME):
            for item in page.get("items", []):
                versions.append(item["imageVersion"])
        info(f"found {len(versions)} version(s): {versions}")
        for v in versions:
            try:
                mvm.delete_microvm_image_version(
                    imageIdentifier=config.IMAGE_NAME, imageVersion=v,
                )
                info(f"delete_microvm_image_version {v}")
            except botocore.exceptions.ClientError as e:
                warn(f"  {v}: {e.response['Error']['Code']}: {e.response['Error'].get('Message')}")
    except botocore.exceptions.ClientError as e:
        warn(f"list_microvm_image_versions: {e.response['Error']['Code']}")

    # Now delete the image itself
    try:
        mvm.delete_microvm_image(imageIdentifier=config.IMAGE_NAME)
        ok(f"delete_microvm_image {config.IMAGE_NAME}")
    except botocore.exceptions.ClientError as e:
        warn(f"delete_microvm_image: {e.response['Error']['Code']}: {e.response['Error'].get('Message')}")


# --------------------------------------------------------------------------
# 3. IAM roles
# --------------------------------------------------------------------------

def delete_role(role_name: str) -> None:
    iam = boto3.client("iam")
    try:
        # inline policies
        for p in iam.list_role_policies(RoleName=role_name).get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=p)
            info(f"  removed inline policy {p} from {role_name}")
        # attached managed policies (defensive; we didn't attach any but be safe)
        for att in iam.list_attached_role_policies(RoleName=role_name).get("AttachedPolicies", []):
            iam.detach_role_policy(RoleName=role_name, PolicyArn=att["PolicyArn"])
            info(f"  detached {att['PolicyArn']}")
        iam.delete_role(RoleName=role_name)
        ok(f"deleted role {role_name}")
    except iam.exceptions.NoSuchEntityException:
        info(f"role does not exist: {role_name}")
    except botocore.exceptions.ClientError as e:
        warn(f"{role_name}: {e.response['Error']['Code']}: {e.response['Error'].get('Message')}")


def delete_roles() -> None:
    step("3/5 Delete IAM roles")
    delete_role(config.BUILD_ROLE_NAME)
    delete_role(config.EXEC_ROLE_NAME)


# --------------------------------------------------------------------------
# 4. S3 bucket
# --------------------------------------------------------------------------

def delete_bucket() -> None:
    step("4/5 Empty and delete S3 bucket")
    bucket = config.bucket_name()
    s3 = boto3.client("s3", region_name=config.REGION)
    try:
        s3.head_bucket(Bucket=bucket)
    except botocore.exceptions.ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("404", "NoSuchBucket", "NotFound"):
            info(f"bucket does not exist: {bucket}")
            return
        warn(f"head_bucket: {code}")
        return

    # Delete all objects (and versions if versioning was ever on)
    resource = boto3.resource("s3", region_name=config.REGION)
    b = resource.Bucket(bucket)
    try:
        b.object_versions.delete()
    except botocore.exceptions.ClientError:
        pass
    try:
        b.objects.delete()
    except botocore.exceptions.ClientError:
        pass

    try:
        s3.delete_bucket(Bucket=bucket)
        ok(f"deleted bucket {bucket}")
    except botocore.exceptions.ClientError as e:
        warn(f"delete_bucket: {e.response['Error']['Code']}: {e.response['Error'].get('Message')}")


# --------------------------------------------------------------------------
# 5. Log group + resource policy (optional)
# --------------------------------------------------------------------------

def delete_logs() -> None:
    step("5/5 Delete log groups + resource policy (--wipe-logs)")
    logs = boto3.client("logs", region_name=config.REGION)
    for group in (config.AGENT_LOG_GROUP, config.BUILD_LOG_GROUP):
        try:
            logs.delete_log_group(logGroupName=group)
            ok(f"deleted log group {group}")
        except logs.exceptions.ResourceNotFoundException:
            info(f"log group does not exist: {group}")
        except botocore.exceptions.ClientError as e:
            warn(f"{group}: {e.response['Error']['Code']}")

    try:
        logs.delete_resource_policy(policyName=f"{config.PREFIX}-xray-span-delivery")
        ok(f"deleted resource policy {config.PREFIX}-xray-span-delivery")
    except botocore.exceptions.ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "ResourceNotFoundException":
            info("resource policy does not exist")
        else:
            warn(f"delete_resource_policy: {code}")


# --------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--wipe-logs", action="store_true",
                   help="Also delete the CloudWatch log groups + resource policy.")
    args = p.parse_args()

    print("\033[1mMicroVM + AgentCore Observability demo — cleanup\033[0m")
    print(f"account={config.account_id()}  region={config.REGION}  prefix={config.PREFIX}")

    terminate_microvms()
    delete_image()
    delete_roles()
    delete_bucket()
    if args.wipe_logs:
        delete_logs()
    else:
        print("\n  (log groups preserved. Re-run with --wipe-logs to remove them.)")

    print("\n\033[1;32mCleanup complete.\033[0m")


if __name__ == "__main__":
    main()
