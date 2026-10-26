#!/usr/bin/env python3
"""Remove a migration deployment, and optionally the data it staged.

`cdk destroy` on its own is not enough and is not safe by default:

* the stack has termination protection on, so the delete is refused;
* the staging bucket is deliberately RETAINed, so the stack goes away while every staged record,
  report, watermark and old-to-new id map stays (and keeps costing);
* the migrated target records are real resources in your registry and must never be touched by a
  teardown -- they are the point of having run the tool.

So this shows exactly what it would remove, and removes nothing until you say so. It is driven by
the CLI::

    agent-registry-migration destroy                 # plan only: what would go, what stays
    agent-registry-migration destroy --yes           # delete the engine, KEEP the bucket and data
    agent-registry-migration destroy --yes --delete-data   # also empty and delete the bucket

Migrated target records are never deleted, whatever is passed.
"""

from __future__ import annotations

import sys

import boto3
from botocore.exceptions import ClientError

DEFAULT_STACK_NAME = "AgentRegistryMigrationEngine"
DATA_PREFIXES = ("app/", "runs/", "reports/", "state/")
_DELETE_BATCH = 1000


class TeardownError(RuntimeError):
    """Raised when the deployment cannot be inspected or removed."""


def main(argv: list[str], session: object | None = None) -> int:
    options = _parse_arguments(argv)
    if options is None:
        return 1
    if options.get("help"):
        print(__doc__)
        return 0

    session = session or boto3.session.Session(region_name=options.get("region"))
    cloudformation = session.client("cloudformation")  # type: ignore[attr-defined]
    s3 = session.client("s3")  # type: ignore[attr-defined]

    try:
        plan = build_plan(cloudformation, s3, options["stack_name"])
    except TeardownError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    print(render_plan(plan, options))
    if not options["confirmed"]:
        print(
            "\nNothing was deleted. Re-run with --yes to delete the stack"
            + (", or --yes --delete-data to delete the staged data too." if plan["bucket"] else ".")
        )
        return 0

    _execute(cloudformation, s3, plan, options)
    return 0


def build_plan(cloudformation: object, s3: object, stack_name: str) -> dict:
    """Describe what exists: the stack, its resources, and the staging bucket's contents."""
    try:
        described = cloudformation.describe_stacks(StackName=stack_name)["Stacks"][0]  # type: ignore[attr-defined]
    except ClientError as error:
        if _is_missing_stack(error, stack_name):
            raise TeardownError(
                f"No stack named {stack_name}. Nothing to tear down here -- pass --stack-name if "
                "this deployment uses a different name."
            ) from error
        raise TeardownError(f"could not describe {stack_name}: {error}") from error

    outputs = {item.get("OutputKey"): item.get("OutputValue") for item in described.get("Outputs", [])}
    bucket = outputs.get("StagingBucketName")
    resources: dict[str, int] = {}
    paginator = cloudformation.get_paginator("list_stack_resources")  # type: ignore[attr-defined]
    for page in paginator.paginate(StackName=stack_name):
        for resource in page.get("StackResourceSummaries", []):
            resource_type = str(resource.get("ResourceType"))
            resources[resource_type] = resources.get(resource_type, 0) + 1

    return {
        "stackName": stack_name,
        "stackStatus": described.get("StackStatus"),
        "terminationProtection": bool(described.get("EnableTerminationProtection")),
        "parameterPrefix": outputs.get("ConfigurationParameterPrefix"),
        "resources": resources,
        "bucket": bucket,
        "contents": inventory_bucket(s3, bucket) if bucket else None,
    }


def _is_missing_stack(error: ClientError, stack_name: str) -> bool:
    """Whether ``error`` means "there is no such stack" rather than a real failure.

    CloudFormation answers DescribeStacks for an absent stack with ``ValidationError`` and a message
    naming it, so the code is checked first and the message only as a narrowing confirmation. This
    used to be a bare ``"does not exist" in str(error)`` substring test, which conflated an absent
    stack with any other ValidationError -- and would stop recognising it if the wording changed.
    """
    response = getattr(error, "response", None) or {}
    code = str(response.get("Error", {}).get("Code", ""))
    if code not in {"ValidationError", "ResourceNotFoundException"}:
        return False
    message = str(response.get("Error", {}).get("Message", "")) or str(error)
    return "does not exist" in message or stack_name in message


def inventory_bucket(s3: object, bucket: str) -> dict:
    """Count objects (all versions) and bytes per top-level prefix.

    Versions matter: the bucket is versioned, so 'empty' in the console can still be thousands of
    billable versions and delete markers.
    """
    by_prefix: dict[str, dict[str, int]] = {}
    total = {"objects": 0, "versions": 0, "bytes": 0}
    paginator = s3.get_paginator("list_object_versions")  # type: ignore[attr-defined]
    try:
        pages = paginator.paginate(Bucket=bucket)
        for page in pages:
            for version in page.get("Versions", []) + page.get("DeleteMarkers", []):
                key = str(version.get("Key", ""))
                size = int(version.get("Size", 0) or 0)
                prefix = next((p for p in DATA_PREFIXES if key.startswith(p)), "other")
                entry = by_prefix.setdefault(prefix, {"versions": 0, "bytes": 0})
                entry["versions"] += 1
                entry["bytes"] += size
                total["versions"] += 1
                total["bytes"] += size
                if version.get("IsLatest", False):
                    total["objects"] += 1
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in {"NoSuchBucket", "404"}:
            return {"missing": True, "byPrefix": {}, "total": total}
        raise TeardownError(f"could not list s3://{bucket}: {error}") from error
    return {"missing": False, "byPrefix": by_prefix, "total": total}


def render_plan(plan: dict, options: dict) -> str:
    """Human-readable plan: what goes, what stays. Printed in both dry-run and confirmed mode."""
    lines = [
        f"Deployment: {plan['stackName']} ({plan['stackStatus']})",
        "",
        "WILL BE DELETED",
        f"  CloudFormation stack {plan['stackName']}, including:",
    ]
    for resource_type, count in sorted(plan["resources"].items()):
        if resource_type == "AWS::S3::Bucket":
            continue  # retained by design; reported under its own heading below
        lines.append(f"    {count} x {resource_type}")
    if plan["parameterPrefix"]:
        lines.append(f"  Configuration parameters under {plan['parameterPrefix']}")

    contents = plan.get("contents") or {}
    total = contents.get("total", {})
    if plan["bucket"] and options["delete_data"]:
        prefixes = ", ".join(
            f"{prefix} ({entry['versions']} versions)"
            for prefix, entry in sorted(contents.get("byPrefix", {}).items())
            if not (options["keep_reports"] and prefix == "reports/")
        )
        lines += [
            f"  Staging bucket s3://{plan['bucket']} and its contents:",
            (
                f"    {total.get('objects', 0)} objects / {total.get('versions', 0)} versions"
                f" / {_human_bytes(total.get('bytes', 0))}"
            ),
        ]
        if prefixes:
            lines.append(f"    {prefixes}")
        if options["keep_reports"]:
            lines.append("    reports/ is kept, so the bucket itself is kept too")

    lines += ["", "WILL SURVIVE"]
    lines.append("  Every migrated record in the target registries -- a teardown never deletes records")
    lines.append("  The Preview registries and their records")
    if plan["bucket"] and not options["delete_data"]:
        lines += [
            (
                f"  Staging bucket s3://{plan['bucket']} with"
                f" {total.get('objects', 0)} objects / {_human_bytes(total.get('bytes', 0))}"
            ),
            "    (it holds your reports and the old -> new id crosswalks; add --delete-data to remove it)",
        ]
    if plan["bucket"] and options["delete_data"] and options["keep_reports"]:
        lines.append(f"  s3://{plan['bucket']}/reports/ and the bucket that holds it")
    lines.append(
        "  Cross-account access roles you created yourself, and any RegistryAccess-<account> "
        "stack in another account (destroy those where they live)"
    )
    if plan["terminationProtection"]:
        lines += ["", "Termination protection is ON and will be disabled before the stack is deleted."]
    return "\n".join(lines)


def _execute(cloudformation: object, s3: object, plan: dict, options: dict) -> None:
    stack_name = plan["stackName"]
    if plan["terminationProtection"]:
        print(f"\nDisabling termination protection on {stack_name}")
        cloudformation.update_termination_protection(  # type: ignore[attr-defined]
            StackName=stack_name,
            EnableTerminationProtection=False,
        )

    print(f"Deleting stack {stack_name}")
    cloudformation.delete_stack(StackName=stack_name)  # type: ignore[attr-defined]
    waiter = cloudformation.get_waiter("stack_delete_complete")  # type: ignore[attr-defined]
    waiter.wait(StackName=stack_name)
    print(f"Deleted stack {stack_name}")

    bucket = plan["bucket"]
    if bucket and options["delete_data"]:
        prefixes = None if not options["keep_reports"] else ("app/", "runs/", "state/")
        removed = empty_bucket(s3, bucket, prefixes=prefixes)
        print(f"Deleted {removed} object version(s) from s3://{bucket}")
        if prefixes is None:
            s3.delete_bucket(Bucket=bucket)  # type: ignore[attr-defined]
            print(f"Deleted bucket s3://{bucket}")
        else:
            print(f"Kept s3://{bucket}/reports/ and the bucket")
    elif bucket:
        print(f"Kept s3://{bucket} and everything in it")

    print("\nTeardown complete. Migrated target records were not touched.")


def empty_bucket(s3: object, bucket: str, *, prefixes: tuple[str, ...] | None = None) -> int:
    """Delete every object version (and delete marker), optionally only under ``prefixes``."""
    paginator = s3.get_paginator("list_object_versions")  # type: ignore[attr-defined]
    batch: list[dict[str, str]] = []
    removed = 0
    for page in paginator.paginate(Bucket=bucket):
        for version in page.get("Versions", []) + page.get("DeleteMarkers", []):
            key = str(version.get("Key", ""))
            if prefixes is not None and not key.startswith(prefixes):
                continue
            batch.append({"Key": key, "VersionId": str(version.get("VersionId"))})
            if len(batch) == _DELETE_BATCH:
                removed += _delete_batch(s3, bucket, batch)
                batch = []
    if batch:
        removed += _delete_batch(s3, bucket, batch)
    return removed


def _delete_batch(s3: object, bucket: str, batch: list[dict[str, str]]) -> int:
    response = s3.delete_objects(  # type: ignore[attr-defined]
        Bucket=bucket,
        Delete={"Objects": batch, "Quiet": True},
    )
    errors = response.get("Errors") or []
    if errors:
        first = errors[0]
        raise TeardownError(
            f"could not delete {len(errors)} object(s), first: {first.get('Key')} "
            f"({first.get('Code')}: {first.get('Message')})"
        )
    return len(batch)


def _parse_arguments(argv: list[str]) -> dict | None:
    options = {
        "stack_name": DEFAULT_STACK_NAME,
        "region": None,
        "confirmed": False,
        "delete_data": False,
        "keep_reports": False,
        "help": False,
    }
    index = 1
    while index < len(argv):
        argument = argv[index]
        if argument in {"-h", "--help"}:
            options["help"] = True
        elif argument == "--yes":
            options["confirmed"] = True
        elif argument == "--delete-data":
            options["delete_data"] = True
        elif argument == "--keep-reports":
            options["keep_reports"] = True
        elif argument in {"--stack-name", "--region"}:
            index += 1
            if index >= len(argv):
                print(f"error: {argument} needs a value", file=sys.stderr)
                return None
            options["stack_name" if argument == "--stack-name" else "region"] = argv[index]
        elif argument.startswith("--stack-name="):
            options["stack_name"] = argument.split("=", 1)[1]
        elif argument.startswith("--region="):
            options["region"] = argument.split("=", 1)[1]
        else:
            print(f"error: unknown argument {argument}", file=sys.stderr)
            return None
        index += 1
    if options["keep_reports"] and not options["delete_data"]:
        print("error: --keep-reports only means something with --delete-data", file=sys.stderr)
        return None
    return options


def _human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


if __name__ == "__main__":
    sys.exit(main(sys.argv))
