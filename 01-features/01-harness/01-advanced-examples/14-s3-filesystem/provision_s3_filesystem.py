"""
Provision the prerequisites for the S3 filesystem sample (optional)

The two sample scripts in this folder mount an **existing** S3 Files access point;
neither one creates infrastructure. That is deliberate — but it means a fresh
account cannot run them at all, because `--access-point-arn`, `--subnet-ids` and
`--security-group-ids` have nothing to point at yet.

This script fills that gap. It creates the four things the sample needs and then
prints the exact command line to paste into `s3_filesystem.py` or
`s3_llm_wiki.py`:

    1. An S3 bucket (unless you pass --bucket to reuse your own)
    2. An IAM service role the S3 Files service assumes to read/write that bucket
    3. An S3 Files **file system** over the bucket, plus an **access point**
    4. A **mount target** per subnet, and a security group allowing NFS (2049)

Networking: bring your own, by default
--------------------------------------
By default this script does NOT create a VPC. It looks for private subnets that
already have egress through a NAT gateway and uses those, because a NAT gateway
bills by the hour whether or not you are using it (~$0.045/hr plus data
processing, us-west-2) and is the resource people forget to delete.

Pass `--create-vpc` to build a VPC, private subnets, an internet gateway and a
NAT gateway from scratch. The script prints the cost note and what it is about to
create before it does. Use it when the account has no suitable subnets, and run
`--teardown` when you are done.

Everything is tagged and recorded in `provision_state.json`, and `--teardown`
deletes exactly what this script created — nothing else. A bucket or VPC you
brought yourself is never deleted.

`--teardown` is safe to re-run, and usually has to be. AgentCore releases the
harness microVM's network interfaces on its own schedule, long after the harness
itself is gone — over 45 minutes in testing — and the security group cannot be
deleted until they go. Rather than block on that, teardown removes everything
that bills, reports the security group as still held, and exits 0; re-run it
later and it deletes just what is left.

Usage:
    # Discover a private subnet with NAT egress and provision the S3 Files layer
    python provision_s3_filesystem.py

    # Reuse a bucket you already have (it is not deleted on teardown)
    python provision_s3_filesystem.py --bucket my-existing-bucket

    # Use specific subnets instead of discovering them
    python provision_s3_filesystem.py --subnet-ids subnet-0abc1234 subnet-0def5678

    # Nothing suitable in the account: build the VPC and NAT gateway too
    python provision_s3_filesystem.py --create-vpc

    # Show what would be created, without creating anything
    python provision_s3_filesystem.py --dry-run

    # Delete everything this script created (reads provision_state.json)
    python provision_s3_filesystem.py --teardown

    # See all options
    python provision_s3_filesystem.py --help
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.client import REGION

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
STATE_FILE = Path(__file__).parent / "provision_state.json"
TAG_KEY = "CreatedBy"
TAG_VALUE = "agentcore-harness-14-s3-filesystem"

# The access point enforces this POSIX identity on every file the agent writes.
# uid/gid 1000 is the harness microVM's non-root user; with the default (root)
# the agent can mount the export and then fail on the first write.
POSIX_UID = 1000
POSIX_GID = 1000
ROOT_DIR_PERMISSIONS = "755"

NFS_PORT = 2049

# CreateFileSystem/CreateMountTarget return immediately and provision in the
# background. Statuses are free-form strings in the service model (no enum), so
# treat anything that is not a known-good or known-bad value as "still working".
READY_STATUSES = ("available",)
FAILED_STATUSES = ("failed", "error", "deleted", "deleting")
POLL_INTERVAL = 10
POLL_TIMEOUT = 600

# Grace period for AgentCore to reclaim the harness microVM's ENIs, which happens
# after the harness is deleted and is far slower than a mount target's ENI
# release — measured at over 45 minutes. Deliberately *short*: the only resources
# those ENIs hold are a security group and (with --create-vpc) subnets, none of
# which cost anything to leave in place for a while. Blocking the user for the
# full reclamation would be a long wait for nothing, so this waits just long
# enough to catch a quick release and otherwise reports what is left to re-run.
ENI_RELEASE_TIMEOUT = 300

# Teardown is expected to run more than once (see attempt()), so a resource that
# is already gone is a success, not a failure. Each service spells it its own way.
ALREADY_GONE_CODES = (
    "ResourceNotFoundException",
    "NoSuchEntity",
    "NoSuchBucket",
    "InvalidGroup.NotFound",
    "InvalidSubnetID.NotFound",
    "InvalidRouteTableID.NotFound",
    "InvalidAllocationID.NotFound",
    "InvalidAssociationID.NotFound",
    "InvalidVpcID.NotFound",
    "InvalidInternetGatewayID.NotFound",
    "InvalidNatGatewayID.NotFound",
    "NatGatewayNotFound",
)

# VPC layout used only with --create-vpc.
VPC_CIDR = "10.200.0.0/16"
PUBLIC_SUBNET_CIDR = "10.200.0.0/24"  # holds the NAT gateway
PRIVATE_SUBNET_CIDRS = ["10.200.1.0/24", "10.200.2.0/24"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
parser = argparse.ArgumentParser(
    description="Provision (or tear down) the S3 Files prerequisites for this sample.",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
parser.add_argument(
    "--bucket",
    default=None,
    metavar="NAME",
    help="Reuse an existing S3 bucket instead of creating one (never deleted on teardown)",
)
parser.add_argument(
    "--prefix",
    default="harness-sample/",
    metavar="PREFIX",
    help="Key prefix inside the bucket to scope the file system to (default: harness-sample/)",
)
parser.add_argument(
    "--subnet-ids",
    nargs="+",
    default=None,
    metavar="SUBNET",
    help="Use these subnets instead of discovering private subnets with NAT egress",
)
parser.add_argument(
    "--security-group-ids",
    nargs="+",
    default=None,
    metavar="SG",
    help="Use these security groups instead of creating one that allows NFS (2049)",
)
parser.add_argument(
    "--create-vpc",
    action="store_true",
    help="Also create a VPC, private subnets and a NAT gateway (a NAT gateway bills hourly)",
)
parser.add_argument(
    "--mount-path",
    default="/mnt/data",
    metavar="PATH",
    help="Mount path to print in the suggested command line (default: /mnt/data)",
)
parser.add_argument(
    "--dry-run",
    action="store_true",
    help="Report what would be created (and what was discovered) without creating it",
)
parser.add_argument(
    "--teardown",
    action="store_true",
    help="Delete everything recorded in provision_state.json, then remove the state file",
)


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------
# Teardown deletes only what is recorded here, so every resource is written to
# disk the moment it is created rather than at the end. A run that dies halfway
# (or is Ctrl-C'd) still leaves a state file that --teardown can clean up; a
# single save at the end would silently orphan everything created before a crash.
def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"region": REGION, "created": {}, "reused": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str) + "\n")


def record(state: dict, key: str, value) -> None:
    """Record a resource this script created and flush it to disk immediately."""
    state["created"][key] = value
    save_state(state)


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------
def wait_for(describe, label: str) -> dict:
    """Poll `describe()` until its `status` is available, raising on failure.

    `describe` returns the resource dict. Unknown statuses are treated as
    in-progress: the service model types these as plain strings, so a new
    transitional value must not read as a failure.
    """
    deadline = time.time() + POLL_TIMEOUT
    last = None
    while time.time() < deadline:
        resource = describe()
        status = (resource.get("status") or "").lower()
        if status != last:
            print(f"    {label}: {status or '(no status)'}")
            last = status
        if status in READY_STATUSES:
            return resource
        if status in FAILED_STATUSES:
            reason = resource.get("statusMessage") or "(no statusMessage)"
            raise RuntimeError(f"{label} entered {status}: {reason}")
        time.sleep(POLL_INTERVAL)
    raise TimeoutError(f"{label} still {last!r} after {POLL_TIMEOUT}s")


# ---------------------------------------------------------------------------
# Networking discovery
# ---------------------------------------------------------------------------
def route_table_for(ec2, subnet_id: str, vpc_id: str) -> dict | None:
    """Return the route table that governs a subnet.

    A subnet with no explicit association is governed by the VPC's *main* route
    table. Looking only at explicit associations reports "no egress" for
    perfectly usable subnets, which is the single easiest way to wrongly conclude
    an account has nowhere to run.
    """
    explicit = ec2.describe_route_tables(Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}])
    if explicit["RouteTables"]:
        return explicit["RouteTables"][0]
    main = ec2.describe_route_tables(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "association.main", "Values": ["true"]},
        ]
    )
    return main["RouteTables"][0] if main["RouteTables"] else None


def classify_subnet(ec2, subnet: dict) -> str:
    """Classify a subnet's default-route egress: private+NAT, public, or isolated."""
    rt = route_table_for(ec2, subnet["SubnetId"], subnet["VpcId"])
    if rt is None:
        return "no-route-table"
    for route in rt["Routes"]:
        if route.get("DestinationCidrBlock") != "0.0.0.0/0":
            continue
        if route.get("NatGatewayId"):
            return "private-nat"
        if str(route.get("GatewayId", "")).startswith("igw-"):
            return "public"
    return "isolated"


def discover_subnets(ec2) -> list[dict]:
    """Find private subnets with NAT egress, preferring distinct AZs in one VPC.

    VPC-mode harnesses run in private networking, so a subnet whose default route
    is an internet gateway is not a substitute. Reporting what was rejected and
    why matters more than the happy path here: an account that looks empty
    usually has public-only subnets, and saying so points at the fix.
    """
    subnets = ec2.describe_subnets()["Subnets"]
    by_class: dict[str, list[dict]] = {}
    for subnet in subnets:
        by_class.setdefault(classify_subnet(ec2, subnet), []).append(subnet)

    usable = by_class.get("private-nat", [])
    print(f"  Scanned {len(subnets)} subnet(s) in {ec2.meta.region_name}:")
    for kind, group in sorted(by_class.items()):
        print(f"    {kind}: {len(group)}")

    if not usable:
        print("\n  No private subnet with NAT egress found.")
        print("  A VPC-mode harness needs private networking; public subnets are not a substitute.")
        print("  Re-run with --create-vpc to build one (note: a NAT gateway bills hourly),")
        print("  or pass --subnet-ids explicitly if you know a subnet that works.")
        raise SystemExit(2)

    # Group by VPC — a harness gets one network config, so all subnets must share
    # a VPC. Take the VPC with the most usable subnets, then one per AZ.
    per_vpc: dict[str, list[dict]] = {}
    for subnet in usable:
        per_vpc.setdefault(subnet["VpcId"], []).append(subnet)
    vpc_id = max(per_vpc, key=lambda v: len(per_vpc[v]))

    chosen: dict[str, dict] = {}
    for subnet in sorted(per_vpc[vpc_id], key=lambda s: s["AvailabilityZone"]):
        chosen.setdefault(subnet["AvailabilityZone"], subnet)
    picked = list(chosen.values())
    print(f"\n  Using VPC {vpc_id} with {len(picked)} private subnet(s):")
    for subnet in picked:
        print(f"    {subnet['SubnetId']}  ({subnet['AvailabilityZone']})")
    return picked


def create_vpc(ec2, state: dict) -> list[dict]:
    """Create a VPC with a NAT gateway and private subnets for the harness.

    Layout: one public subnet holding the NAT gateway, and one private subnet per
    AZ whose default route points at it. The mount targets and the harness live
    in the private subnets.
    """
    print("  Creating VPC (this includes a NAT gateway — it bills hourly until torn down)")
    tags = [{"Key": TAG_KEY, "Value": TAG_VALUE}, {"Key": "Name", "Value": "harness-s3fs-sample"}]

    vpc_id = ec2.create_vpc(CidrBlock=VPC_CIDR, TagSpecifications=[{"ResourceType": "vpc", "Tags": tags}])["Vpc"][
        "VpcId"
    ]
    record(state, "vpc_id", vpc_id)
    ec2.get_waiter("vpc_available").wait(VpcIds=[vpc_id])
    # The NAT gateway needs public DNS/hostname resolution to reach S3 endpoints.
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={"Value": True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={"Value": True})
    print(f"    VPC: {vpc_id}")

    igw_id = ec2.create_internet_gateway(TagSpecifications=[{"ResourceType": "internet-gateway", "Tags": tags}])[
        "InternetGateway"
    ]["InternetGatewayId"]
    record(state, "igw_id", igw_id)
    ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    print(f"    Internet gateway: {igw_id}")

    # Standard availability zones only. DescribeAvailabilityZones also returns
    # Local Zones and Wavelength Zones (7 and 6 of them in us-west-2), which do
    # not support the resources this sample needs — slicing the raw list can hand
    # `create_subnet` a zone like `us-west-2-wl1-las-wlz-1` and fail there.
    azs = [
        az["ZoneName"]
        for az in ec2.describe_availability_zones(
            Filters=[{"Name": "zone-type", "Values": ["availability-zone"]}, {"Name": "state", "Values": ["available"]}]
        )["AvailabilityZones"]
    ]
    if len(azs) < len(PRIVATE_SUBNET_CIDRS):
        print(f"    only {len(azs)} usable availability zone(s); creating that many private subnets")
    azs = azs[: len(PRIVATE_SUBNET_CIDRS)]

    public_subnet = ec2.create_subnet(
        VpcId=vpc_id,
        CidrBlock=PUBLIC_SUBNET_CIDR,
        AvailabilityZone=azs[0],
        TagSpecifications=[{"ResourceType": "subnet", "Tags": tags}],
    )["Subnet"]
    record(state, "public_subnet_id", public_subnet["SubnetId"])
    print(f"    Public subnet (for NAT): {public_subnet['SubnetId']}")

    public_rt = ec2.create_route_table(VpcId=vpc_id, TagSpecifications=[{"ResourceType": "route-table", "Tags": tags}])[
        "RouteTable"
    ]["RouteTableId"]
    record(state, "public_route_table_id", public_rt)
    ec2.create_route(RouteTableId=public_rt, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw_id)
    assoc_public = ec2.associate_route_table(RouteTableId=public_rt, SubnetId=public_subnet["SubnetId"])[
        "AssociationId"
    ]
    record(state, "public_rt_association_id", assoc_public)

    eip = ec2.allocate_address(Domain="vpc", TagSpecifications=[{"ResourceType": "elastic-ip", "Tags": tags}])
    record(state, "nat_eip_allocation_id", eip["AllocationId"])
    nat_id = ec2.create_nat_gateway(
        SubnetId=public_subnet["SubnetId"],
        AllocationId=eip["AllocationId"],
        TagSpecifications=[{"ResourceType": "natgateway", "Tags": tags}],
    )["NatGateway"]["NatGatewayId"]
    record(state, "nat_gateway_id", nat_id)
    print(f"    NAT gateway: {nat_id} (waiting for it to become available)")
    ec2.get_waiter("nat_gateway_available").wait(NatGatewayIds=[nat_id])

    private_rt = ec2.create_route_table(
        VpcId=vpc_id, TagSpecifications=[{"ResourceType": "route-table", "Tags": tags}]
    )["RouteTable"]["RouteTableId"]
    record(state, "private_route_table_id", private_rt)
    ec2.create_route(RouteTableId=private_rt, DestinationCidrBlock="0.0.0.0/0", NatGatewayId=nat_id)

    # Each subnet and association is recorded as it is created, not after the
    # loop: if the second create_subnet fails, the first one still has to end up
    # in the state file or --teardown will not know to delete it.
    private_subnets = []
    assoc_ids = []
    for cidr, az in zip(PRIVATE_SUBNET_CIDRS, azs):
        subnet = ec2.create_subnet(
            VpcId=vpc_id,
            CidrBlock=cidr,
            AvailabilityZone=az,
            TagSpecifications=[{"ResourceType": "subnet", "Tags": tags}],
        )["Subnet"]
        private_subnets.append(subnet)
        record(state, "private_subnet_ids", [s["SubnetId"] for s in private_subnets])
        assoc_ids.append(
            ec2.associate_route_table(RouteTableId=private_rt, SubnetId=subnet["SubnetId"])["AssociationId"]
        )
        record(state, "private_rt_association_ids", assoc_ids)
        print(f"    Private subnet: {subnet['SubnetId']} ({az})")
    return private_subnets


def create_nfs_security_group(ec2, vpc_id: str, state: dict) -> str:
    """Create a security group allowing NFS (2049) within the VPC.

    Ingress is scoped to the VPC's own CIDR rather than 0.0.0.0/0: the only
    traffic that needs to reach the mount target is the harness microVM inside
    this VPC.
    """
    vpc = ec2.describe_vpcs(VpcIds=[vpc_id])["Vpcs"][0]
    cidr = vpc["CidrBlock"]
    sg_id = ec2.create_security_group(
        GroupName=f"harness-s3fs-nfs-{uuid.uuid4().hex[:8]}",
        Description="NFS (2049) between the AgentCore harness and its S3 Files mount target",
        VpcId=vpc_id,
        TagSpecifications=[{"ResourceType": "security-group", "Tags": [{"Key": TAG_KEY, "Value": TAG_VALUE}]}],
    )["GroupId"]
    record(state, "security_group_id", sg_id)
    ec2.authorize_security_group_ingress(
        GroupId=sg_id,
        IpPermissions=[
            {
                "IpProtocol": "tcp",
                "FromPort": NFS_PORT,
                "ToPort": NFS_PORT,
                "IpRanges": [{"CidrIp": cidr, "Description": "NFS from within the VPC"}],
            }
        ],
    )
    print(f"  Security group: {sg_id} (TCP {NFS_PORT} from {cidr})")
    return sg_id


# ---------------------------------------------------------------------------
# S3 + IAM + S3 Files
# ---------------------------------------------------------------------------
def enable_versioning(s3, bucket: str) -> None:
    """Turn on bucket versioning, which S3 Files requires.

    This is not optional and not merely a "bucket warning": CreateFileSystem
    rejects an unversioned bucket outright with `ValidationException: Your bucket
    must have versioning enabled to create a file system.` — `acceptBucketWarning`
    does not cover it. Versioning also means teardown has to delete object
    *versions* and delete markers, not just objects (see drop_bucket).
    """
    s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})


def create_bucket(s3, region: str, account_id: str, state: dict) -> str:
    """Create a private, encrypted, versioned bucket to back the file system."""
    name = f"harness-s3fs-{account_id}-{region}-{uuid.uuid4().hex[:8]}"
    # us-east-1 is the one region where CreateBucket must NOT carry a location
    # constraint; sending one there fails with InvalidLocationConstraint.
    kwargs = {"Bucket": name}
    if region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    s3.create_bucket(**kwargs)
    record(state, "bucket", name)
    s3.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )
    s3.put_bucket_encryption(
        Bucket=name,
        ServerSideEncryptionConfiguration={
            "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
        },
    )
    enable_versioning(s3, name)
    print(f"  Bucket: {name} (private, SSE-S3, versioning enabled)")
    return name


def create_s3files_role(iam, bucket: str, state: dict) -> str:
    """Create the IAM role the S3 Files service assumes to reach the bucket.

    Two details are easy to get wrong and both fail late:

    * The trust principal is **elasticfilesystem.amazonaws.com**, not
      `s3files.amazonaws.com` — S3 Files is fronted by the EFS service principal.
    * Besides S3 access the role needs EventBridge permissions on
      `DO-NOT-DELETE-S3-Files*` rules, which the service uses to keep the file
      system synchronised with the bucket.
    """
    role_name = f"harness-s3fs-role-{uuid.uuid4().hex[:8]}"
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "elasticfilesystem.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "BucketAccess",
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                    "s3:ListBucket",
                    "s3:ListBucketVersions",
                    "s3:GetBucketLocation",
                    "s3:AbortMultipartUpload",
                ],
                "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
            },
            {
                "Sid": "EventBridgeManage",
                "Effect": "Allow",
                "Action": [
                    "events:PutRule",
                    "events:PutTargets",
                    "events:DeleteRule",
                    "events:RemoveTargets",
                    "events:EnableRule",
                    "events:DisableRule",
                ],
                "Resource": ["arn:aws:events:*:*:rule/DO-NOT-DELETE-S3-Files*"],
                "Condition": {"StringEquals": {"events:ManagedBy": "elasticfilesystem.amazonaws.com"}},
            },
            {
                "Sid": "EventBridgeRead",
                "Effect": "Allow",
                "Action": [
                    "events:DescribeRule",
                    "events:ListRules",
                    "events:ListRuleNamesByTarget",
                    "events:ListTargetsByRule",
                ],
                "Resource": ["arn:aws:events:*:*:rule/*"],
            },
        ],
    }
    role_arn = iam.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(trust),
        Description="S3 Files service role for the AgentCore harness S3 filesystem sample",
        Tags=[{"Key": TAG_KEY, "Value": TAG_VALUE}],
    )["Role"]["Arn"]
    record(state, "s3files_role_name", role_name)
    iam.put_role_policy(RoleName=role_name, PolicyName="s3files-bucket-access", PolicyDocument=json.dumps(policy))
    print(f"  S3 Files service role: {role_arn}")
    return role_arn


def create_filesystem(s3f, bucket: str, prefix: str, role_arn: str, state: dict) -> str:
    """Create the S3 Files file system over the bucket and wait for it."""
    # CreateFileSystem fails on a brand-new role with "cannot assume role" until
    # the role has propagated. Retry rather than sleeping a fixed interval.
    deadline = time.time() + 120
    while True:
        try:
            resp = s3f.create_file_system(
                bucket=f"arn:aws:s3:::{bucket}",
                prefix=prefix,
                roleArn=role_arn,
                # Acknowledges non-fatal bucket configuration warnings. Note this
                # does NOT cover versioning, which is a hard requirement (see
                # enable_versioning).
                acceptBucketWarning=True,
                tags=[{"key": TAG_KEY, "value": TAG_VALUE}],
            )
            break
        except ClientError as e:
            code = e.response["Error"]["Code"]
            message = e.response["Error"]["Message"]
            # Only a role that has not propagated yet is worth retrying, and the
            # error code alone does not identify it: ValidationException also
            # covers permanent problems like an unversioned bucket. Retrying on
            # the code and printing "waiting for IAM role propagation" turned a
            # one-line fix into two minutes of a misattributed cause, so match
            # the message and surface anything else immediately.
            transient = "assume" in message.lower() or "role" in message.lower()
            if code != "ValidationException" or not transient or time.time() > deadline:
                print(f"\n  CreateFileSystem failed ({code}): {message}")
                raise
            print(f"    waiting for IAM role propagation: {message}")
            time.sleep(10)

    fs_id = resp["fileSystemId"]
    record(state, "file_system_id", fs_id)
    print(f"  File system: {fs_id}")
    wait_for(lambda: s3f.get_file_system(fileSystemId=fs_id), f"file system {fs_id}")
    return fs_id


def create_access_point(s3f, fs_id: str, mount_path: str, state: dict) -> str:
    """Create an access point that enforces the microVM's POSIX identity."""
    resp = s3f.create_access_point(
        fileSystemId=fs_id,
        posixUser={"uid": POSIX_UID, "gid": POSIX_GID},
        rootDirectory={
            "path": mount_path,
            "creationPermissions": {
                "ownerUid": POSIX_UID,
                "ownerGid": POSIX_GID,
                "permissions": ROOT_DIR_PERMISSIONS,
            },
        },
        tags=[{"key": TAG_KEY, "value": TAG_VALUE}],
    )
    ap_id = resp["accessPointId"]
    ap_arn = resp["accessPointArn"]
    record(state, "access_point_id", ap_id)
    record(state, "access_point_arn", ap_arn)
    print(f"  Access point: {ap_id} (uid/gid {POSIX_UID}/{POSIX_GID}, root {mount_path})")
    wait_for(lambda: s3f.get_access_point(accessPointId=ap_id), f"access point {ap_id}")
    return ap_arn


def create_mount_targets(s3f, fs_id: str, subnets: list[dict], sg_ids: list[str], state: dict) -> list[str]:
    """Create one mount target per subnet — the harness must run in an AZ that has one."""
    created = []
    for subnet in subnets:
        try:
            mt = s3f.create_mount_target(
                fileSystemId=fs_id,
                subnetId=subnet["SubnetId"],
                securityGroups=sg_ids,
            )
        except ClientError as e:
            # One mount target per AZ is the limit; an existing one is fine.
            if e.response["Error"]["Code"] != "ConflictException":
                raise
            print(f"    mount target already exists for {subnet['SubnetId']}")
            continue
        created.append(mt["mountTargetId"])
        record(state, "mount_target_ids", created)
        print(f"  Mount target: {mt['mountTargetId']} in {subnet['SubnetId']}")

    for mt_id in created:
        wait_for(lambda mt_id=mt_id: s3f.get_mount_target(mountTargetId=mt_id), f"mount target {mt_id}")
    return created


# ---------------------------------------------------------------------------
# Teardown
# ---------------------------------------------------------------------------
def list_enis(ec2, sg_id: str) -> list:
    """Return the network interfaces still attached to a security group."""
    return ec2.describe_network_interfaces(Filters=[{"Name": "group-id", "Values": [sg_id]}])["NetworkInterfaces"]


def format_enis(enis: list) -> str:
    """Describe ENIs for a progress line.

    Worth naming explicitly: `DependencyViolation` only says "has a dependent
    object", which is not enough to tell a teardown that is stuck from one that
    just needs more time.
    """
    return ", ".join(f"{e['NetworkInterfaceId']} ({e.get('InterfaceType', 'interface')})" for e in enis)


def teardown(state: dict) -> None:
    """Delete every resource recorded in the state file, in dependency order.

    Two properties matter more than the ordering itself:

    * Each step is independent. One failure must not strand the resources behind
      it, since a leftover NAT gateway keeps billing — so the things that bill go
      first, and anything that could not be deleted is reported at the end.
    * It is safe to run again, and expected to be. Resources are dropped from the
      state file as they go and an already-absent resource counts as success, so a
      second run retries only what is genuinely left. This is not a nicety: the
      harness ENIs hold the security group for longer than anyone should wait, so
      the normal outcome of a first run is "everything billable is gone, one free
      security group to go".
    """
    # Resources still held by the harness ENIs are reported separately from real
    # failures: they cost nothing, they always clear on their own, and the only
    # action is to re-run later. Folding them into `problems` made the ordinary
    # path exit 1 with a "could not be deleted" warning, which reads as breakage.
    pending = []
    created = state.get("created", {})
    if not created:
        # An empty `created` block means there is nothing left to delete, so the
        # state file has to go too. Leaving it behind deadlocks the script: the
        # provisioning path refuses to start while a state file exists and points
        # at --teardown, and --teardown would keep finding nothing to do.
        STATE_FILE.unlink(missing_ok=True)
        print(f"Nothing recorded in the state file — nothing to delete. Removed {STATE_FILE.name}.")
        return

    region = state.get("region") or REGION
    s3f = boto3.client("s3files", region_name=region)
    ec2 = boto3.client("ec2", region_name=region)
    iam = boto3.client("iam")
    s3 = boto3.client("s3", region_name=region)
    problems = []

    def attempt(label, fn, key=None, item=None, verb="Deleted"):
        """Run one teardown step, then forget it so a re-run does not redo the work.

        Teardown is expected to be run more than once: the harness ENIs can
        outlast a first pass by design (see the wait below). Without forgetting
        what already went, the second run redoes everything, turns each
        already-gone resource into a fresh "could not delete" line, and can never
        exit 0 — so an already-absent resource counts as success, and the state
        file shrinks as the teardown progresses.
        """
        try:
            fn()
            print(f"  {verb} {label}")
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code not in ALREADY_GONE_CODES and "NotFound" not in code:
                print(f"  Warning: {label} failed: {e}")
                problems.append(f"{label}: {e}")
                return
            print(f"  Already gone: {label}")
        except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
            print(f"  Warning: {label} failed: {e}")
            problems.append(f"{label}: {e}")
            return
        if key is None:
            return
        if item is None:
            created.pop(key, None)
        elif item in created.get(key, []):
            created[key].remove(item)
            if not created[key]:
                created.pop(key, None)
        save_state(state)

    print("Tearing down (only resources this script created):\n")

    # Mount targets must be gone before the file system, and their ENIs must be
    # released before the subnets and security group can go. The wait runs inside
    # attempt() as well: raising here would abort the whole teardown and strand
    # everything below — including the NAT gateway, which keeps billing.
    # Every delete is issued before any of the waits, so the mount targets tear
    # down in parallel instead of one POLL_TIMEOUT after another. Iterate over a
    # snapshot: attempt() prunes the list as entries succeed.
    mount_target_ids = list(created.get("mount_target_ids", []))
    for mt_id in mount_target_ids:
        attempt(f"mount target {mt_id}", lambda mt_id=mt_id: s3f.delete_mount_target(mountTargetId=mt_id))

    def await_mount_target_gone(mt_id):
        deadline = time.time() + POLL_TIMEOUT
        while time.time() < deadline:
            try:
                s3f.get_mount_target(mountTargetId=mt_id)
            except ClientError as e:
                if e.response["Error"]["Code"] == "ResourceNotFoundException":
                    return
                raise
            time.sleep(POLL_INTERVAL)
        raise TimeoutError(f"still present after {POLL_TIMEOUT}s")

    for mt_id in mount_target_ids:
        attempt(
            f"mount target {mt_id} finished deleting",
            lambda mt_id=mt_id: await_mount_target_gone(mt_id),
            key="mount_target_ids",
            item=mt_id,
            verb="Confirmed",
        )

    if created.get("access_point_id"):
        attempt(
            f"access point {created['access_point_id']}",
            lambda: s3f.delete_access_point(accessPointId=created["access_point_id"]),
            key="access_point_id",
        )
        created.pop("access_point_arn", None)
        save_state(state)
    if created.get("file_system_id"):
        fs_id = created["file_system_id"]
        attempt(
            f"file system {fs_id}",
            lambda: s3f.delete_file_system(fileSystemId=fs_id, forceDelete=True),
        )

        # DeleteFileSystem returns immediately and finishes in the background,
        # while the service is still using the role and the bucket to flush
        # pending data. Deleting either one out from under it is what turns a
        # clean teardown into a file system stuck in `deleting`.
        def await_filesystem_gone():
            deadline = time.time() + POLL_TIMEOUT
            while time.time() < deadline:
                try:
                    s3f.get_file_system(fileSystemId=fs_id)
                except ClientError as e:
                    if e.response["Error"]["Code"] == "ResourceNotFoundException":
                        return
                    raise
                time.sleep(POLL_INTERVAL)
            raise TimeoutError(f"still present after {POLL_TIMEOUT}s")

        attempt(
            f"file system {fs_id} finished deleting",
            await_filesystem_gone,
            key="file_system_id",
            verb="Confirmed",
        )

    if created.get("s3files_role_name"):
        role = created["s3files_role_name"]

        def drop_role():
            for page in iam.get_paginator("list_role_policies").paginate(RoleName=role):
                for name in page["PolicyNames"]:
                    iam.delete_role_policy(RoleName=role, PolicyName=name)
            iam.delete_role(RoleName=role)

        attempt(f"IAM role {role}", drop_role, key="s3files_role_name")

    if created.get("bucket"):
        bucket = created["bucket"]

        def drop_bucket():
            # The bucket is not empty, and versioning is on, so every *version*
            # and delete marker has to go — deleting only current objects leaves
            # the bucket non-empty and DeleteBucket then fails.
            #
            # It also has to be emptied more than once. S3 Files exports the
            # file system to S3 asynchronously and well behind the writes: a run
            # of this sample was observed finishing with only a directory marker
            # in the bucket, the agent's files arriving later. Anything that
            # lands between the last list and DeleteBucket would fail the delete,
            # so empty-then-delete is retried until the bucket is really empty.
            for _ in range(5):
                emptied = 0
                for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket):
                    to_delete = [
                        {"Key": o["Key"], "VersionId": o["VersionId"]}
                        for o in page.get("Versions", []) + page.get("DeleteMarkers", [])
                    ]
                    if to_delete:
                        s3.delete_objects(Bucket=bucket, Delete={"Objects": to_delete})
                        emptied += len(to_delete)
                try:
                    s3.delete_bucket(Bucket=bucket)
                    return
                except ClientError as e:
                    if e.response["Error"]["Code"] != "BucketNotEmpty":
                        raise
                    print(f"    bucket refilled during teardown ({emptied} deleted); emptying again")
                    time.sleep(POLL_INTERVAL)
            raise RuntimeError("bucket still not empty after 5 attempts (S3 Files may still be exporting)")

        attempt(f"bucket {bucket}", drop_bucket, key="bucket")

    # Everything that bills goes before anything that can block. The NAT gateway
    # is charged by the hour and its Elastic IP is charged while unassociated, and
    # neither is held up by an ENI — whereas the security group and the subnets
    # can be, for a long time (see below).
    if created.get("nat_gateway_id"):
        nat = created["nat_gateway_id"]

        def drop_nat():
            ec2.delete_nat_gateway(NatGatewayId=nat)
            ec2.get_waiter("nat_gateway_deleted").wait(NatGatewayIds=[nat])

        attempt(f"NAT gateway {nat}", drop_nat, key="nat_gateway_id")
    if created.get("nat_eip_allocation_id"):
        attempt(
            f"elastic IP {created['nat_eip_allocation_id']}",
            lambda: ec2.release_address(AllocationId=created["nat_eip_allocation_id"]),
            key="nat_eip_allocation_id",
        )

    # The security group and the created subnets are all held by the same ENIs, so
    # check once here instead of letting each delete below discover it separately.
    #
    # The slow ENI is not the obvious one. The mount targets' ENIs go within a
    # minute or two of DeleteMountTarget. The harness microVM's ENIs (interface
    # type `agentic_ai`, service-managed `ela-attach-...` attachments) are
    # reclaimed by AgentCore on its own schedule *after* the harness is deleted —
    # observed still attached more than 45 minutes later.
    #
    # Nothing they hold costs anything, so this does not out-wait them: it allows
    # a short grace period for a quick release, then reports what is still held
    # and leaves it in the state file for the next run. Naming the interfaces
    # matters because `DependencyViolation` only says "has a dependent object",
    # which cannot distinguish "still settling" from "genuinely stuck".
    sg_id = created.get("security_group_id")
    eni_blocked = False
    if sg_id:
        deadline = time.time() + ENI_RELEASE_TIMEOUT
        announced = False
        while True:
            blockers = list_enis(ec2, sg_id)
            if not blockers:
                if announced:
                    print(f"  Confirmed network interfaces using {sg_id} released")
                break
            if time.time() > deadline:
                eni_blocked = True
                break
            if not announced:
                announced = True
                print(f"  Waiting for {format_enis(blockers)} to be released")
                print(f"    up to {ENI_RELEASE_TIMEOUT // 60} min; safe to Ctrl-C and re-run --teardown later")
            time.sleep(POLL_INTERVAL)

    for assoc in list(created.get("private_rt_association_ids", [])):
        attempt(
            f"route table association {assoc}",
            lambda a=assoc: ec2.disassociate_route_table(AssociationId=a),
            key="private_rt_association_ids",
            item=assoc,
        )
    if created.get("public_rt_association_id"):
        attempt(
            f"route table association {created['public_rt_association_id']}",
            lambda: ec2.disassociate_route_table(AssociationId=created["public_rt_association_id"]),
            key="public_rt_association_id",
        )
    # The security group must go before its subnets, and both need the ENIs gone.
    # If they have not been released yet, skip both rather than issuing deletes
    # that are certain to fail with DependencyViolation: they stay in the state
    # file and the next --teardown picks them up.
    if eni_blocked:
        held = [f"security group {sg_id}"]
        held += [f"subnet {s}" for s in created.get("private_subnet_ids", [])]
        if created.get("public_subnet_id"):
            held.append(f"subnet {created['public_subnet_id']}")
        pending.extend(held)
        for label in held:
            print(f"  Still held by the harness network interfaces: {label}")
    else:
        if sg_id:
            attempt(
                f"security group {sg_id}", lambda: ec2.delete_security_group(GroupId=sg_id), key="security_group_id"
            )
        for subnet_id in list(created.get("private_subnet_ids", [])):
            attempt(
                f"subnet {subnet_id}",
                lambda s=subnet_id: ec2.delete_subnet(SubnetId=s),
                key="private_subnet_ids",
                item=subnet_id,
            )
        if created.get("public_subnet_id"):
            attempt(
                f"subnet {created['public_subnet_id']}",
                lambda: ec2.delete_subnet(SubnetId=created["public_subnet_id"]),
                key="public_subnet_id",
            )
    # The route tables, gateway and VPC all hang off the subnets above, so once
    # those are held there is no point trying these either.
    if not eni_blocked:
        for rt_key in ("private_route_table_id", "public_route_table_id"):
            if created.get(rt_key):
                attempt(
                    f"route table {created[rt_key]}",
                    lambda r=created[rt_key]: ec2.delete_route_table(RouteTableId=r),
                    key=rt_key,
                )
        if created.get("igw_id") and created.get("vpc_id"):
            attempt(
                f"internet gateway {created['igw_id']}",
                lambda: (
                    ec2.detach_internet_gateway(InternetGatewayId=created["igw_id"], VpcId=created["vpc_id"]),
                    ec2.delete_internet_gateway(InternetGatewayId=created["igw_id"]),
                ),
                key="igw_id",
            )
        if created.get("vpc_id"):
            attempt(f"VPC {created['vpc_id']}", lambda: ec2.delete_vpc(VpcId=created["vpc_id"]), key="vpc_id")
    elif created.get("vpc_id"):
        print(f"  Still held (waiting on the subnets above): VPC {created['vpc_id']} and its route tables")

    if problems:
        print("\nSome resources could not be deleted:")
        for p in problems:
            print(f"  - {p}")
        print(f"\nRe-run --teardown to retry just what is left; {STATE_FILE.name} keeps the remaining resources.")
        raise SystemExit(1)

    if pending:
        # Not an error: nothing here bills, and AgentCore always releases the
        # interfaces eventually. Exit 0 so a scripted teardown does not read this
        # as a failure, and say plainly what is left and what to do about it.
        print("\nEverything billable is gone. AgentCore has not released the harness's network")
        print(f"interfaces yet, so {len(pending)} resource(s) that cost nothing are still in place:")
        for item in pending:
            print(f"  - {item}")
        print("\nThis is normal; reclamation has been observed to take well over an hour.")
        print(f"Re-run the same command later to finish up:\n\n    python {Path(__file__).name} --teardown\n")
        return

    STATE_FILE.unlink(missing_ok=True)
    print(f"\nAll done. Removed {STATE_FILE.name}.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(args=None):
    if args is None:
        args = parser.parse_args()

    if args.teardown:
        if not STATE_FILE.exists():
            print(f"No {STATE_FILE.name} found — nothing to tear down.")
            return
        state = load_state()
        # `--teardown --dry-run` is exactly what a cautious reader tries before
        # deleting anything, so it must not delete: list what would go instead.
        if args.dry_run:
            created = state.get("created", {})
            if not created:
                print(f"{STATE_FILE.name} records nothing this script created — teardown would delete nothing.")
                return
            print("Would delete the following (and nothing else):\n")
            for key, value in created.items():
                for item in value if isinstance(value, list) else [value]:
                    print(f"  {key}: {item}")
            reused = state.get("reused", {})
            if reused:
                print("\nWould keep (you brought these):\n")
                for key, value in reused.items():
                    for item in value if isinstance(value, list) else [value]:
                        print(f"  {key}: {item}")
            print("\nDry run — nothing was deleted.")
            return
        teardown(state)
        return

    if STATE_FILE.exists() and not args.dry_run:
        print(f"{STATE_FILE.name} already exists — this account already has resources from a previous run.")
        print("Run --teardown first, or delete the state file if you have cleaned up by hand.")
        raise SystemExit(1)

    session = boto3.Session(region_name=REGION)
    region = session.region_name
    if not region:
        print("No region configured. Set AWS_REGION (or AWS_DEFAULT_REGION), or configure a profile.")
        raise SystemExit(1)

    ec2 = session.client("ec2")
    s3 = session.client("s3")
    s3f = session.client("s3files")
    iam = session.client("iam")
    account_id = session.client("sts").get_caller_identity()["Account"]

    print("=" * 68)
    print(f"Provisioning S3 Files prerequisites in {region} (account {account_id})")
    print("=" * 68)

    # ── Networking ────────────────────────────────────────────────────
    print("\nStep 1: networking")
    state = load_state()
    state["region"] = region

    if args.subnet_ids:
        subnets = ec2.describe_subnets(SubnetIds=args.subnet_ids)["Subnets"]
        vpc_ids = {s["VpcId"] for s in subnets}
        if len(vpc_ids) > 1:
            print(f"  --subnet-ids span multiple VPCs ({', '.join(sorted(vpc_ids))}).")
            print("  A harness gets one network configuration, so all subnets must share a VPC.")
            raise SystemExit(1)
        vpc_id = vpc_ids.pop()
        state["reused"]["subnet_ids"] = args.subnet_ids
        for subnet in subnets:
            kind = classify_subnet(ec2, subnet)
            note = "" if kind == "private-nat" else f"  <-- {kind}; a VPC-mode harness needs private+NAT egress"
            print(f"  {subnet['SubnetId']} ({subnet['AvailabilityZone']}) {kind}{note}")
    elif args.create_vpc:
        if args.dry_run:
            print("  Would create: VPC, internet gateway, public subnet, NAT gateway (bills hourly),")
            print("                elastic IP, 2 private subnets, 2 route tables")
            vpc_id, subnets = None, []
        else:
            subnets = create_vpc(ec2, state)
            vpc_id = state["created"]["vpc_id"]
    else:
        subnets = discover_subnets(ec2)
        vpc_id = subnets[0]["VpcId"]
        state["reused"]["vpc_id"] = vpc_id
        state["reused"]["subnet_ids"] = [s["SubnetId"] for s in subnets]

    if args.security_group_ids:
        sg_ids = args.security_group_ids
        state["reused"]["security_group_ids"] = sg_ids
        print(f"  Using security group(s): {', '.join(sg_ids)}")
    elif args.dry_run:
        sg_ids = ["<created>"]
        print(f"  Would create a security group allowing TCP {NFS_PORT} within the VPC")
    else:
        sg_ids = [create_nfs_security_group(ec2, vpc_id, state)]

    # ── Bucket + service role ─────────────────────────────────────────
    print("\nStep 2: bucket and S3 Files service role")
    if args.bucket:
        bucket = args.bucket
        state["reused"]["bucket"] = bucket
        print(f"  Using existing bucket: {bucket} (not deleted on teardown)")
        if not args.dry_run:
            try:
                s3.head_bucket(Bucket=bucket)
            except ClientError as e:
                print(f"  Cannot access bucket {bucket}: {e}")
                raise SystemExit(1) from e
            # S3 Files refuses an unversioned bucket, so check before spending
            # two minutes creating a role and a file system that cannot attach.
            # Enabling versioning on someone else's bucket is a change to a
            # resource this script does not own, so ask rather than assume.
            status = s3.get_bucket_versioning(Bucket=bucket).get("Status")
            if status != "Enabled":
                print(f"  Bucket versioning is {status or 'not enabled'}, and S3 Files requires it.")
                print("  Enable it yourself with:")
                print(
                    f"    aws s3api put-bucket-versioning --bucket {bucket} --versioning-configuration Status=Enabled"
                )
                print("  then re-run. (This script only enables versioning on buckets it creates.)")
                raise SystemExit(1)
    elif args.dry_run:
        bucket = f"harness-s3fs-{account_id}-{region}-<random>"
        print(f"  Would create bucket: {bucket}")
    else:
        bucket = create_bucket(s3, region, account_id, state)

    if args.dry_run:
        print("  Would create an IAM role trusted by elasticfilesystem.amazonaws.com")
        role_arn = "<created>"
    else:
        role_arn = create_s3files_role(iam, bucket, state)

    # ── File system, access point, mount targets ──────────────────────
    print("\nStep 3: S3 Files file system, access point and mount targets")
    if args.dry_run:
        print(f"  Would create a file system over s3://{bucket}/{args.prefix}")
        print(f"  Would create an access point (uid/gid {POSIX_UID}/{POSIX_GID})")
        print(f"  Would create {len(subnets) or 'N'} mount target(s), one per subnet")
        print("\nDry run — nothing was created.")
        return

    save_state(state)
    fs_id = create_filesystem(s3f, bucket, args.prefix, role_arn, state)
    ap_arn = create_access_point(s3f, fs_id, args.mount_path, state)
    create_mount_targets(s3f, fs_id, subnets, sg_ids, state)

    subnet_ids = [s["SubnetId"] for s in subnets]
    print("\n" + "=" * 68)
    print("Ready. Run the sample with:")
    print("=" * 68)
    print(
        f"\npython s3_filesystem.py \\\n"
        f"    --access-point-arn {ap_arn} \\\n"
        f"    --subnet-ids {' '.join(subnet_ids)} \\\n"
        f"    --security-group-ids {' '.join(sg_ids)}\n"
    )
    print(f"State written to {STATE_FILE.name}. Tear it all down with:")
    print("\n    python provision_s3_filesystem.py --teardown\n")
    if state["created"].get("nat_gateway_id"):
        print("Note: this run created a NAT gateway, which bills hourly until you tear it down.\n")


if __name__ == "__main__":
    main()
