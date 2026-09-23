"""
Mount S3 as the Harness Filesystem

Every Harness session runs in an isolated microVM with its own ephemeral disk —
when the session ends, that disk is gone. To keep artifacts around, you can mount
an **S3 Files access point** into the VM. The agent then reads and writes a normal
POSIX path (e.g. /mnt/data) that is backed by S3, so files survive session
termination and are shared across sessions.

An S3 Files mount requires the Harness to run in **VPC network mode** — the
microVM reaches the access point's NFS mount target over your VPC. So the
environment carries both a `networkConfiguration` (VPC + subnets + security
groups) and the `filesystemConfigurations`:

    environment={
        "agentCoreRuntimeEnvironment": {
            "networkConfiguration": {
                "networkMode": "VPC",
                "networkModeConfig": {
                    "subnets": ["subnet-..."],
                    "securityGroups": ["sg-..."],
                },
            },
            "filesystemConfigurations": [
                {
                    "s3FilesAccessPoint": {
                        "accessPointArn": "<S3 Files access point ARN>",
                        "mountPath": "/mnt/data",
                    }
                }
            ],
        }
    }

This sample demonstrates persistence across the session boundary:

    1. Create a Harness with the S3 mount (in your VPC)
    2. Session A — ask the agent to WRITE a file under the mount path
    3. Session B (fresh microVM) — ask the agent to READ that same file back
       The file is still there because it lives in S3, not on the VM disk.

Prerequisites
-------------
* An S3 Files access point backed by a bucket, with a mount target in the subnet
  you pass below. Its ARN looks like:
      arn:aws:s3files:<region>:<account>:file-system/fs-xxxx/access-point/fsap-xxxx
  Provide it with --access-point-arn.
* The subnet(s) and security group(s) that reach the mount target. The Harness
  must be in the same VPC as the mount target, the subnet(s) you pass must be in an
  Availability Zone that has a mount target, and the security group(s) must allow
  NFS (port 2049). Use private subnets with egress (a route to a NAT gateway) —
  VPC-mode Harnesses run in private networking and public subnets won't connect.
* The Harness execution role must be allowed to mount the access point. If this
  script creates the role (the default), it attaches the required `s3files`
  permissions for you. If you pass --role-arn, make sure it already has them.

Usage:
    # Mount an existing S3 Files access point at /mnt/data and run the demo
    python s3_filesystem.py \\
        --access-point-arn arn:aws:s3files:us-west-2:111122223333:file-system/fs-abc/access-point/fsap-def \\
        --subnet-ids subnet-0abc1234 \\
        --security-group-ids sg-0def5678

    # Choose a different mount path and filename
    python s3_filesystem.py \\
        --access-point-arn arn:aws:s3files:... \\
        --subnet-ids subnet-0abc1234 --security-group-ids sg-0def5678 \\
        --mount-path /mnt/shared \\
        --filename trip-notes.md

    # Keep the harness after the demo
    python s3_filesystem.py --access-point-arn ... --subnet-ids ... --security-group-ids ... --skip-cleanup

    # See all options
    python s3_filesystem.py --help
"""

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.client import get_agentcore_client, get_agentcore_control_client
from utils.harness import poll_harness_status
from utils.iam import ROLE_NAME, create_harness_role, delete_harness_role

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_MOUNT_PATH = "/mnt/data"
DEFAULT_FILENAME = "harness-note.md"
S3_FILES_POLICY_NAME = "HarnessS3FilesAccess"

# mountPath must match /mnt/<name> (see the service model: MountPath)
MOUNT_PATH_PATTERN = re.compile(r"^/mnt/[a-zA-Z0-9._-]+/?$")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(
    description="Mount an S3 Files access point into a Harness and prove artifacts persist across sessions.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
parser.add_argument(
    "--access-point-arn",
    required=True,
    metavar="ARN",
    help="S3 Files access point ARN to mount (arn:aws:s3files:...:access-point/fsap-...)",
)
parser.add_argument(
    "--subnet-ids",
    required=True,
    nargs="+",
    metavar="SUBNET",
    help="VPC subnet(s) that can reach the access point's mount target (NFS/2049)",
)
parser.add_argument(
    "--security-group-ids",
    required=True,
    nargs="+",
    metavar="SG",
    help="Security group(s) allowing NFS (2049) to the mount target",
)
parser.add_argument(
    "--mount-path",
    default=DEFAULT_MOUNT_PATH,
    metavar="PATH",
    help=f"Where to mount it inside the VM (default: {DEFAULT_MOUNT_PATH})",
)
parser.add_argument(
    "--filename",
    default=DEFAULT_FILENAME,
    metavar="NAME",
    help=f"File the agent writes/reads under the mount (default: {DEFAULT_FILENAME})",
)
parser.add_argument(
    "--model",
    default=DEFAULT_MODEL,
    metavar="MODEL_ID",
    help=f"Bedrock model ID (default: {DEFAULT_MODEL})",
)
parser.add_argument(
    "--role-arn",
    default=None,
    metavar="ARN",
    help="Use an existing IAM execution role (must already allow the access point)",
)
parser.add_argument(
    "--skip-cleanup",
    action="store_true",
    help="Keep the harness after the demo",
)
parser.add_argument(
    "--raw-events",
    action="store_true",
    help="Print raw JSON streaming events from invoke",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def attach_s3_files_policy(role_name, access_point_arn):
    """Allow the execution role to validate and mount the S3 Files access point.

    * `s3files:GetAccessPoint` **and `s3files:ListMountTargets`** on `*` — the
      runtime validates both while creating the harness, so both must be
      unscoped. `ListMountTargets` is easy to miss: it takes a file-system ID
      rather than an access point, and without it CreateHarness accepts the
      request and then lands in CREATE_FAILED with "Execution role is missing
      required permissions. Ensure the role has s3files:ListMountTargets".
    * `s3files:ClientMount`/`ClientWrite` — used when the microVM mounts the
      access point; scoped to the file system with an AccessPointArn condition.
    """
    fs_arn = access_point_arn.split("/access-point/")[0]
    iam = boto3.client("iam")
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "S3FilesValidate",
                "Effect": "Allow",
                "Action": ["s3files:GetAccessPoint", "s3files:ListMountTargets"],
                "Resource": "*",
            },
            {
                "Sid": "S3FilesClientMount",
                "Effect": "Allow",
                "Action": ["s3files:ClientMount", "s3files:ClientWrite"],
                "Resource": fs_arn,
                "Condition": {"ArnEquals": {"s3files:AccessPointArn": access_point_arn}},
            },
        ],
    }
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=S3_FILES_POLICY_NAME,
        PolicyDocument=json.dumps(policy),
    )
    print(f"  Attached S3 Files access policy: {S3_FILES_POLICY_NAME}")


def stream_response(client, harness_arn, session_id, message, model_id, raw=False):
    """Invoke a Harness and stream the response to stdout."""
    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": message}]}],
        model={"bedrockModelConfig": {"modelId": model_id}},
    )

    full_text = ""
    try:
        for event in response["stream"]:
            if raw:
                print(json.dumps(event, default=str))
                # Accumulate in raw mode too, so the guard below can tell a
                # stream that produced content from one that produced none.
                delta = event.get("contentBlockDelta", {}).get("delta", {})
                if "text" in delta:
                    full_text += delta["text"]
                continue

            if "contentBlockStart" in event:
                start = event["contentBlockStart"].get("start", {})
                if "toolUse" in start:
                    print(f"\n  [Tool: {start['toolUse'].get('name', '?')}]", flush=True)
            elif "contentBlockDelta" in event:
                delta = event["contentBlockDelta"].get("delta", {})
                if "text" in delta:
                    print(delta["text"], end="", flush=True)
                    full_text += delta["text"]
            elif "messageStop" in event:
                print()
            elif "internalServerException" in event:
                print(f"\n  Error: {event['internalServerException']}")
    # A mid-stream failure is raised out of the iterator, not delivered as an
    # event: a modeled service error (EventStreamError/ClientError — throttling,
    # access denied) or a transport error (read timeout, connection reset — a
    # BotoCoreError). Catching only EventStreamError let a read timeout abort the
    # demo even after the answer had streamed, with a traceback that named
    # nothing useful. Re-raise only when nothing arrived.
    except (BotoCoreError, ClientError) as e:
        print(f"\n  Stream error: {e}")
        if not full_text:
            raise

    return full_text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(args=None):
    if args is None:
        args = parser.parse_args()

    if not MOUNT_PATH_PATTERN.match(args.mount_path):
        parser.error(f"--mount-path must look like /mnt/<name> (got: {args.mount_path})")

    control = get_agentcore_control_client()
    client = get_agentcore_client()

    mount = args.mount_path.rstrip("/")
    remote_file = f"{mount}/{args.filename}"
    harness_id = None
    created_role = False

    try:
        # ── Step 0: IAM role (with S3 access) ─────────────────────────
        print("=" * 60)
        print("Step 0: IAM execution role")
        print("=" * 60)
        if args.role_arn:
            role_arn = args.role_arn
            print(f"  Using provided role: {role_arn}")
            print("  (ensure it can access the S3 Files access point)")
        else:
            role_arn = create_harness_role()
            created_role = True
            attach_s3_files_policy(ROLE_NAME, args.access_point_arn)
            print("  Waiting for IAM propagation...")
            time.sleep(10)

        # ── Step 1: Create Harness with S3 mount (VPC network mode) ───
        print("\n" + "=" * 60)
        print("Step 1: Create Harness with S3 mounted at " + mount)
        print("=" * 60)
        # S3 Files mounts require VPC network mode so the microVM can reach the
        # access point's mount target over your VPC.
        network = {
            "networkMode": "VPC",
            "networkModeConfig": {
                "subnets": args.subnet_ids,
                "securityGroups": args.security_group_ids,
            },
        }
        filesystem = [
            {
                "s3FilesAccessPoint": {
                    "accessPointArn": args.access_point_arn,
                    "mountPath": mount,
                }
            }
        ]
        print(f"  networkConfiguration = {json.dumps(network)}")
        print(f"  filesystemConfigurations = {json.dumps(filesystem)}")
        harness_name = f"S3Mount_{uuid.uuid4().hex[:8]}"
        resp = control.create_harness(
            harnessName=harness_name,
            executionRoleArn=role_arn,
            environment={
                "agentCoreRuntimeEnvironment": {
                    "networkConfiguration": network,
                    "filesystemConfigurations": filesystem,
                }
            },
            systemPrompt=[
                {"text": f"You are a helpful assistant. A persistent S3-backed directory is mounted at {mount}."}
            ],
        )
        harness_id = resp["harness"]["harnessId"]
        harness_arn = resp["harness"]["arn"]
        print(f"  Harness ID:  {harness_id}")
        print(f"  Harness ARN: {harness_arn}")
        poll_harness_status(control, harness_id)

        # ── Step 2: Session A — write a file to the mount ─────────────
        print("\n" + "=" * 60)
        print("Step 2: Session A — write a file to the S3 mount")
        print("=" * 60)
        session_a = str(uuid.uuid4()).upper()
        print(f"  Session A: {session_a}\n")
        stream_response(
            client,
            harness_arn,
            session_a,
            f"Write a short markdown travel note about Amsterdam to {remote_file}. "
            f"Confirm the absolute path you saved it to.",
            args.model,
            raw=args.raw_events,
        )

        # Give the S3-backed write a moment to flush before the next session.
        time.sleep(5)

        # ── Step 3: Session B — read it back from a fresh VM ──────────
        print("\n" + "=" * 60)
        print("Step 3: Session B (fresh microVM) — read the file back")
        print("=" * 60)
        session_b = str(uuid.uuid4()).upper()
        print(f"  Session B: {session_b}")
        print("  Different session = different VM disk. If the agent can still")
        print("  read the file, it's because the mount is backed by S3.\n")
        stream_response(
            client,
            harness_arn,
            session_b,
            f"Read the file {remote_file} and show me its contents verbatim.",
            args.model,
            raw=args.raw_events,
        )

        print("\n" + "=" * 60)
        print("Done! The file written in Session A was readable in Session B.")
        print("=" * 60)

    finally:
        if not args.skip_cleanup:
            print("\nCleaning up...")
            if harness_id:
                try:
                    control.delete_harness(harnessId=harness_id)
                    print(f"  Deleted harness: {harness_id}")
                except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
                    print(f"  Warning: failed to delete harness: {e}")
            # The role and its two inline policies were left behind on every run,
            # including successful ones — and the role name is shared by every
            # sample in this folder, so the leftovers broke the next sample too.
            # Only delete the role when this run created it; a --role-arn passed
            # in by the caller belongs to them.
            if created_role:
                delete_harness_role()
            print("  Note: the S3 bucket and access point are left intact.")


if __name__ == "__main__":
    main()
