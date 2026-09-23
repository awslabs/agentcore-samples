#!/usr/bin/env python3
"""Deploy the music production agents onto an AgentCore Runtime Instances capacity provider.

Creates, in order: two IAM roles, an S3 bucket for rendered audio, two ECR images,
one zip artifact, one GPU capacity provider with two persistent EBS volumes, and
three agent runtimes bound to it.

Writes deployment_state.json for invoke.py and cleanup.py.

Cost warning: this launches a real GPU EC2 instance in your account on the first
invoke, billed while it runs. A g6.xlarge is roughly 13x a general-purpose
instance of the same size. Run cleanup.py when you are finished, and note that
deleting the SESSION is what deprovisions the instance and its volumes -- stopping
a runtime does not.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

PROJECT = Path(__file__).resolve().parent.parent
STATE_FILE = PROJECT / "deployment_state.json"

# us-east-2 by default: measured GPU capacity there when us-west-2 had none in
# any Availability Zone for g6.xlarge.
REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")

# Every AgentCore-supported accelerator family (g4dn, g5, g6, g6e, gr6, g6f,
# gr6f, g7e, inf2) is x86_64, so a GPU capacity provider must be LINUX_X86_64
# and its images must be amd64.
CP_OS = "LINUX_X86_64"

# ONE instance type on purpose. Measured behaviour: AgentCore picks the CHEAPEST
# entry in allowedInstanceTypes and only ever attempts that one -- given six GPU
# types it never tried anything but g6.xlarge, and given four CPU types it chose
# c5.large. So a longer list buys no fallback; it just hides which type you get.
# It DOES try every subnet/AZ within a placement attempt. Capacity is handled by
# retrying invoke.py on a fresh session id.
CP_INSTANCE_TYPE = os.environ.get("CP_INSTANCE_TYPE", "g6.xlarge")

BUILD_PLATFORM = "linux/amd64"
# manylinux_2_28, not manylinux2014: numpy 2.5 and scipy 1.18 no longer publish
# glibc-2.17 wheels, so a 2014 baseline resolves to "no usable wheels" the moment
# --only-binary is enforced. Both runtime targets are newer than 2.28 anyway --
# the container base (Debian bookworm) is glibc 2.36 and the zip runtime
# (Amazon Linux 2023) is glibc 2.34.
WHEEL_PLATFORM = "x86_64-manylinux_2_28"
PYTHON_RUNTIME = "PYTHON_3_12"
PYTHON_VERSION = "3.12"

TRACKS_VOLUME, TRACKS_MOUNT = "tracks", "/mnt/tracks"
MODELS_VOLUME, MODELS_MOUNT = "models", "/mnt/models"
TRACKS_SIZE_GIB = int(os.environ.get("TRACKS_SIZE_GIB", "20"))
# The generative stack is ~6.3 GB installed plus ~8.3 GB of weights. 60 GiB
# leaves room for renders and a second model.
MODELS_SIZE_GIB = int(os.environ.get("MODELS_SIZE_GIB", "60"))

# rootVolume.freeSpaceGiB defaults to 8, which is what produced the "7.6G free"
# a previous version of this sample measured and mistook for a limit. AgentCore
# adds OS overhead on top of whatever is set here.
ROOT_FREE_GIB = int(os.environ.get("ROOT_FREE_GIB", "30"))

# A GPU instance idling is the most expensive mistake available here, so this is
# deliberately shorter than the service default of 900.
IDLE_INSTANCE_TIMEOUT = int(os.environ.get("IDLE_INSTANCE_TIMEOUT", "600"))
IDLE_SESSION_TIMEOUT = int(os.environ.get("IDLE_SESSION_TIMEOUT", "600"))
# Ceiling on one compute lifecycle; max 1209600 s (14 days) and must be >= the
# idle timeouts. A runtime's maxLifetime must also be <= the capacity provider's.
MAX_LIFETIME = int(os.environ.get("MAX_LIFETIME", "86400"))

# Optional: an EBS snapshot of an already-prepared models volume. Verified to
# work -- a snapshot-backed volume is NOT reformatted, so its contents survive
# into new sessions and mode=prepare becomes unnecessary. Requires an extra IAM
# statement; see grant_snapshot_restore below.
MODELS_SNAPSHOT_ID = os.environ.get("MODELS_SNAPSHOT_ID")

# One model for all three agents, which is deliberate after measuring the
# alternative. Cheaper models were tried per agent and the allocation came out
# backwards: the weakest model held the hardest task (remediation -- read a
# rejected brief plus an avoid-list, then diverge in key, tempo, genre and
# harmony), while the most capable held the most constrained one (pick EQ bands
# from a table of numbers). A smaller compliance model also wrote a false claim
# into a delivery report: "0.0892 remains below the automatic fail threshold of
# 0.045", conflating the fail and review thresholds it had been handed by name.
#
# Cost is not the tradeoff it looks like. A full --with-catalogue run is eight
# model calls, which is a rounding error beside a g6.xlarge and 122 GiB of EBS
# running for the length of the session.
#
# The per-agent overrides stay so you can right-size each task yourself -- that
# is a real practice, and this is a good place to measure it rather than a good
# place to assume it.
DEFAULT_MODEL_ID = "global.anthropic.claude-sonnet-4-6"

MODELS = {
    "composition": os.environ.get("COMPOSITION_MODEL_ID", DEFAULT_MODEL_ID),
    "mastering": os.environ.get("MASTERING_MODEL_ID", DEFAULT_MODEL_ID),
    "compliance": os.environ.get("COMPLIANCE_MODEL_ID", DEFAULT_MODEL_ID),
}

OPERATOR_ROLE_NAME = "MusicProductionCapacityProviderOperatorRole"
EXECUTION_ROLE_NAME = "MusicProductionRuntimeExecutionRole"
OPERATOR_MANAGED_POLICY = "arn:aws:iam::aws:policy/BedrockAgentCoreRuntimeInstancesOperatorRolePolicy"


def log(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"    ! {msg}", flush=True)


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def run(cmd: list[str], **kwargs) -> None:
    print(f"    $ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True, **kwargs)


def preflight() -> tuple[str, str]:
    # Refuse to overwrite a recorded deployment. The state file is the only
    # record of a session id, and a session's EBS volumes keep billing after its
    # instance is gone -- deleting the session is what deprovisions them.
    if STATE_FILE.exists():
        die(
            f"{STATE_FILE.name} already exists, so a deployment is still recorded.\n"
            "    Tear it down first:        python scripts/cleanup.py\n"
            f"    Or, if it is already gone: mv {STATE_FILE.name} {STATE_FILE.name}.old"
        )

    if not REGION:
        die(
            "Set AWS_REGION (or AWS_DEFAULT_REGION). Nothing is defaulted, so that "
            "GPU instances are never launched in a Region you did not choose."
        )

    if MAX_LIFETIME < max(IDLE_INSTANCE_TIMEOUT, IDLE_SESSION_TIMEOUT):
        die(
            f"MAX_LIFETIME ({MAX_LIFETIME}) must be >= IDLE_INSTANCE_TIMEOUT "
            f"({IDLE_INSTANCE_TIMEOUT}) and IDLE_SESSION_TIMEOUT ({IDLE_SESSION_TIMEOUT})."
        )
    if not 60 <= MAX_LIFETIME <= 1209600:
        die(f"MAX_LIFETIME ({MAX_LIFETIME}) must be between 60 and 1209600 seconds.")

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    if not hasattr(control, "create_capacity_provider"):
        die(
            "This boto3 has no capacity provider APIs. Upgrade:\n"
            "    pip install --upgrade 'boto3>=1.43.72' 'botocore>=1.43.72'"
        )

    requested = os.environ.get("CONTAINER_CLI")
    if requested:
        if not shutil.which(requested):
            die(f"CONTAINER_CLI={requested} is not on PATH.")
        cli = requested
    else:
        cli = next((c for c in ("finch", "docker", "nerdctl", "podman") if shutil.which(c)), None)
    if not cli:
        die("No container CLI found. Install Finch, Docker, nerdctl or Podman.")

    # Prove the engine can build BEFORE creating any AWS resource. Being on PATH
    # is not the same as being usable.
    probe = subprocess.run([cli, "info"], capture_output=True, text=True, check=False)
    if probe.returncode != 0:
        hint = {
            "finch": "Finch runs containers in a Linux VM. Create it once:\n"
            "        finch vm init      # then: finch vm start",
            "docker": "Start Docker Desktop (or the docker daemon) and retry.",
            "podman": "podman machine init && podman machine start",
            "nerdctl": "Ensure containerd is running and reachable.",
        }.get(cli, "Start the container engine and retry.")
        detail = (probe.stderr or probe.stdout or "").strip().splitlines()
        die(
            f"{cli} is installed but not usable yet.\n    {hint}\n"
            f"    Or select another engine: CONTAINER_CLI=docker python scripts/deploy.py\n"
            + (f"    {cli} said: {detail[-1][:160]}" if detail else "")
        )

    if not shutil.which("uv"):
        die(
            "uv is required to vendor Linux wheels for the agent artifacts.\n"
            "    Install: https://docs.astral.sh/uv/getting-started/installation/"
        )

    # A GPU type that the Region does not offer will fail only at first invoke,
    # minutes later, so check now.
    ec2 = boto3.client("ec2", region_name=REGION)
    offered = ec2.describe_instance_type_offerings(
        LocationType="availability-zone", Filters=[{"Name": "instance-type", "Values": [CP_INSTANCE_TYPE]}]
    )["InstanceTypeOfferings"]
    if not offered:
        die(f"{CP_INSTANCE_TYPE} is not offered in {REGION}. Set CP_INSTANCE_TYPE.")
    log(f"{CP_INSTANCE_TYPE} offered in {len(offered)} AZ(s): {', '.join(sorted(o['Location'] for o in offered))}")

    account = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    log(f"region={REGION} account={account} os={CP_OS} instance={CP_INSTANCE_TYPE}")
    log(f"container CLI: {cli}")
    return account, cli


# ------------------------------------------------------------------------- IAM


def artifact_bucket(account: str) -> str:
    return f"music-production-artifacts-{account}-{REGION}"


def ensure_roles(account: str) -> tuple[str, str]:
    """Create the operator (infrastructure) role and the runtime execution role.

    Both are assumed by bedrock-agentcore.amazonaws.com, and getting the trust
    policy wrong produces a message that reads as if the role does not exist.
    """
    iam = boto3.client("iam")
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                # Confused-deputy guard.
                "Condition": {"StringEquals": {"aws:SourceAccount": account}},
            }
        ],
    }

    def upsert(name: str, description: str) -> str:
        try:
            arn = iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust), Description=description)[
                "Role"
            ]["Arn"]
            log(f"created role {name}")
            return arn
        except iam.exceptions.EntityAlreadyExistsException:
            iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
            log(f"reusing role {name}")
            return iam.get_role(RoleName=name)["Role"]["Arn"]

    operator_arn = upsert(OPERATOR_ROLE_NAME, "AgentCore provisions EC2 for music production capacity providers")
    iam.attach_role_policy(RoleName=OPERATOR_ROLE_NAME, PolicyArn=OPERATOR_MANAGED_POLICY)
    if MODELS_SNAPSHOT_ID:
        grant_snapshot_restore(MODELS_SNAPSHOT_ID)

    execution_arn = upsert(EXECUTION_ROLE_NAME, "Music production agent runtime execution role")
    put_execution_policy(account, composition_arn=None)
    log("IAM ready - waiting 10s for propagation")
    time.sleep(10)
    return operator_arn, execution_arn


def grant_snapshot_restore(snapshot_id: str) -> None:
    """Let the operator role create a volume FROM a snapshot.

    The AWS managed operator policy grants ec2:CreateVolume on `volume/*` only.
    Restoring a snapshot is also authorised against the SNAPSHOT resource, so
    without this the placement fails with an opaque
    "Failed to provision compute resources for the agent", and only CloudTrail
    reveals `UnauthorizedOperation ... on resource .../snapshot/snap-...`.
    """
    boto3.client("iam").put_role_policy(
        RoleName=OPERATOR_ROLE_NAME,
        PolicyName="music-production-snapshot-restore",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "CreateVolumeFromModelSnapshot",
                        "Effect": "Allow",
                        "Action": "ec2:CreateVolume",
                        "Resource": f"arn:aws:ec2:{REGION}::snapshot/{snapshot_id}",
                    }
                ],
            }
        ),
    )
    log(f"granted the operator role CreateVolume on {snapshot_id}")


def put_execution_policy(account: str, composition_arn: str | None) -> None:
    """Write the runtime execution policy.

    Called twice. A runtime ARN carries a random 10-character suffix, so it
    cannot be known before CreateAgentRuntime returns: the first call omits the
    cross-agent grant entirely rather than opening it to every runtime in the
    account, and the second adds it scoped to the composition runtime. Nothing
    invokes anything in between.
    """
    bucket = artifact_bucket(account)
    statements: list[dict] = [
        {
            # An inference profile ARN is not sufficient on its own: the
            # foundation model in every Region the profile routes to must also be
            # allowed, and `global.` profiles route to a Region-less ARN.
            "Sid": "InvokeModels",
            "Effect": "Allow",
            "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            "Resource": ["arn:aws:bedrock:*::foundation-model/*", f"arn:aws:bedrock:*:{account}:inference-profile/*"],
        },
        {
            "Sid": "PullImages",
            "Effect": "Allow",
            "Action": ["ecr:GetAuthorizationToken", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
            "Resource": "*",
        },
        {
            # Read the zip artifact, and write rendered audio. The volume is
            # inside a managed instance with no shell and dies with the session,
            # so S3 is the only way a WAV reaches the caller.
            "Sid": "Artifacts",
            "Effect": "Allow",
            "Action": ["s3:GetObject", "s3:PutObject", "s3:ListBucket"],
            "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
        },
        {
            "Sid": "Telemetry",
            "Effect": "Allow",
            "Action": [
                "logs:CreateLogGroup",
                "logs:CreateLogStream",
                "logs:PutLogEvents",
                "logs:DescribeLogStreams",
                "logs:DescribeLogGroups",
                "cloudwatch:PutMetricData",
                "xray:PutTraceSegments",
                "xray:PutTelemetryRecords",
            ],
            "Resource": "*",
        },
    ]
    if composition_arn:
        # Only the compliance agent uses this, and only to reach one runtime.
        # The second resource covers the qualifier (endpoint) sub-resource.
        statements.append(
            {
                "Sid": "CrossAgentInvoke",
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeAgentRuntime",
                "Resource": [composition_arn, f"{composition_arn}/*"],
            }
        )

    boto3.client("iam").put_role_policy(
        RoleName=EXECUTION_ROLE_NAME,
        PolicyName="music-production-runtime-access",
        PolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": statements}),
    )


# ------------------------------------------------------------------- artifacts


def vendor_agent_deps() -> Path:
    """Vendor the agent-side wheels for the target platform.

    Shared by both container images, which is why it is built once. `--only-binary`
    guarantees nothing is compiled on this machine for a different architecture.
    """
    target = PROJECT / "build" / "agentdeps"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    log(f"vendoring agent dependencies for {WHEEL_PLATFORM} / cp{PYTHON_VERSION}")
    run(
        [
            "uv",
            "pip",
            "install",
            "--python-platform",
            WHEEL_PLATFORM,
            "--python-version",
            PYTHON_VERSION,
            "--target",
            str(target),
            "--only-binary",
            ":all:",
            "-r",
            str(PROJECT / "requirements.txt"),
        ]
    )
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    log(f"agent dependencies: {size / 1e6:.0f} MB unpacked")
    return target


def ensure_bucket(account: str) -> str:
    bucket = artifact_bucket(account)
    s3 = boto3.client("s3", region_name=REGION)
    try:
        kwargs: dict = {"Bucket": bucket}
        if REGION != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": REGION}
        s3.create_bucket(**kwargs)
        log(f"created bucket {bucket}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise
        log(f"reusing bucket {bucket}")
    return bucket


def build_and_push_image(agent: str, account: str, cli: str, tag: str) -> str:
    registry = f"{account}.dkr.ecr.{REGION}.amazonaws.com"
    repo = f"music-production/{agent}-agent"
    uri = f"{registry}/{repo}:{tag}"

    ecr = boto3.client("ecr", region_name=REGION)
    try:
        ecr.create_repository(repositoryName=repo)
        log(f"created ECR repo {repo}")
    except ecr.exceptions.RepositoryAlreadyExistsException:
        log(f"reusing ECR repo {repo}")

    token = ecr.get_authorization_token()["authorizationData"][0]["authorizationToken"]
    password = base64.b64decode(token).decode().split(":", 1)[1]
    subprocess.run(
        [cli, "login", "--username", "AWS", "--password-stdin", registry],
        input=password.encode(),
        check=True,
        stdout=subprocess.DEVNULL,
    )

    log(f"building {agent} image for {BUILD_PLATFORM}")
    run(
        [
            cli,
            "build",
            "--platform",
            BUILD_PLATFORM,
            "-f",
            str(PROJECT / f"Dockerfile.{agent}"),
            "-t",
            uri,
            str(PROJECT),
        ]
    )
    log(f"pushing {uri}")
    run([cli, "push", uri])
    size = subprocess.run(
        [cli, "images", "--format", "{{.Size}}", uri], capture_output=True, text=True, check=False
    ).stdout.strip()
    # The hard cap on an AgentCore Runtime image is 2 GB.
    log(f"{agent} image size: {size or '<unknown>'} (limit 2 GB)")
    return uri


def build_and_upload_zip(account: str, bucket: str, key: str) -> tuple[str, str]:
    """Vendor dependencies for the target platform and upload the compliance zip.

    There is no pip install on the instance: whatever is in the zip is what the
    agent gets, so wheels must be built for Linux x86_64 rather than this laptop.
    Limits are 250 MB compressed and 750 MB uncompressed, and the uncompressed one
    is the easier to hit.
    """
    build_dir = PROJECT / "build" / "compliance"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    log(f"vendoring compliance dependencies for {WHEEL_PLATFORM}")
    run(
        [
            "uv",
            "pip",
            "install",
            "--python-platform",
            WHEEL_PLATFORM,
            "--python-version",
            PYTHON_VERSION,
            "--target",
            str(build_dir),
            "--only-binary",
            ":all:",
            "-r",
            str(PROJECT / "requirements.txt"),
        ]
    )
    shutil.copy(PROJECT / "compliance_agent.py", build_dir)
    shutil.copy(PROJECT / "audio_dsp.py", build_dir)

    archive = PROJECT / "build" / "compliance_agent.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(build_dir.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, path.relative_to(build_dir))
    size_mb = archive.stat().st_size / 1e6
    raw_mb = sum(p.stat().st_size for p in build_dir.rglob("*") if p.is_file()) / 1e6
    log(f"zip built: {size_mb:.1f} MB compressed, {raw_mb:.1f} MB uncompressed (limits 250 MB / 750 MB)")
    if size_mb > 250 or raw_mb > 750:
        die("compliance zip exceeds the direct-code-deploy limits")

    boto3.client("s3", region_name=REGION).upload_file(
        str(archive), bucket, key, ExtraArgs={"ExpectedBucketOwner": account}
    )
    log(f"uploaded s3://{bucket}/{key}")
    return bucket, key


# ------------------------------------------------------- capacity provider


def vpc_configuration() -> dict:
    """Use explicit subnets/security group if given, else every default-VPC subnet
    in an AZ that offers the instance type.

    Pass more than one. AgentCore does try every subnet within a single placement
    attempt -- CloudTrail shows four RunInstances calls across four AZs in four
    seconds -- but the error it surfaces names only one, which makes a
    single-subnet capacity provider look like a service fault instead of an AZ
    with no capacity. A capacity provider's configuration cannot be edited after
    creation, so getting this wrong means rebuilding it.

    The supported-AZ list in the VPC documentation governs the microVM ENI path,
    not capacity providers: a subnet in an AZ absent from that list was accepted
    and is where capacity was found.
    """
    subnet = os.environ.get("CP_SUBNET_ID")
    group = os.environ.get("CP_SECURITY_GROUP_ID")
    if subnet and group:
        chosen = [s.strip() for s in subnet.split(",") if s.strip()][:16]
        if len(chosen) == 1:
            warn("one subnet means one AZ; pass several to survive an Insufficient EC2 capacity error in that AZ")
        return {"subnets": chosen, "securityGroups": [group]}
    if subnet or group:
        die("Set CP_SUBNET_ID and CP_SECURITY_GROUP_ID together, or neither.")

    ec2 = boto3.client("ec2", region_name=REGION)
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        die("No default VPC. Set CP_SUBNET_ID and CP_SECURITY_GROUP_ID.")
    vpc_id = vpcs[0]["VpcId"]
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
    groups = ec2.describe_security_groups(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}, {"Name": "group-name", "Values": ["default"]}]
    )["SecurityGroups"]
    if not subnets or not groups:
        die(f"Default VPC {vpc_id} has no subnet or no default security group.")

    offered = {
        o["Location"]
        for o in ec2.describe_instance_type_offerings(
            LocationType="availability-zone", Filters=[{"Name": "instance-type", "Values": [CP_INSTANCE_TYPE]}]
        )["InstanceTypeOfferings"]
    }
    usable = [s for s in subnets if s["AvailabilityZone"] in offered][:16]
    if not usable:
        die(f"No default-VPC subnet is in an AZ offering {CP_INSTANCE_TYPE}.")
    zones = sorted({s["AvailabilityZone"] for s in usable})
    log(f"using default VPC {vpc_id}: {len(usable)} subnet(s) across {', '.join(zones)}")
    return {"subnets": [s["SubnetId"] for s in usable], "securityGroups": [groups[0]["GroupId"]]}


def create_capacity_provider(control, name: str, operator_arn: str) -> tuple[str, str]:
    models_ebs: dict = {
        "name": MODELS_VOLUME,
        "sizeGiB": MODELS_SIZE_GIB,
        "volumeType": "gp3",
        "encrypted": True,
        # The model stack is read-heavy on first load; the gp3
        # default of 125 MiB/s makes a cold start noticeably slower.
        "throughput": 500,
    }
    if MODELS_SNAPSHOT_ID:
        # Verified: a snapshot-backed volume is NOT reformatted, so a prepared
        # stack survives into every new session and mode=prepare is unnecessary.
        models_ebs["snapshotId"] = MODELS_SNAPSHOT_ID
        log(f"models volume will be restored from {MODELS_SNAPSHOT_ID}")

    log(f"creating capacity provider {name}")
    resp = control.create_capacity_provider(
        name=name,
        description="Music production agent fleet (GPU)",
        permissionsConfiguration={"capacityProviderOperatorRoleArn": operator_arn},
        computeConfiguration={
            "ec2Configuration": {
                "launchTemplateSource": {
                    "launchParameters": {
                        "operatingSystem": CP_OS,
                        "instanceRequirements": {"allowedInstanceTypes": [CP_INSTANCE_TYPE]},
                    }
                },
                "vpcConfiguration": vpc_configuration(),
                "volumes": [
                    # The shared workspace, mounted by all three agents.
                    {
                        "ebsConfiguration": {
                            "name": TRACKS_VOLUME,
                            "sizeGiB": TRACKS_SIZE_GIB,
                            "volumeType": "gp3",
                            "encrypted": True,
                        }
                    },
                    # The generative stack, mounted only by the composition agent.
                    {"ebsConfiguration": models_ebs},
                ],
                "rootVolume": {"freeSpaceGiB": ROOT_FREE_GIB, "volumeType": "gp3"},
                "lifecycleConfiguration": {"idleInstanceTimeout": IDLE_INSTANCE_TIMEOUT, "maxLifetime": MAX_LIFETIME},
            }
        },
    )
    cp_id, cp_arn = resp["capacityProviderId"], resp["capacityProviderArn"]

    # The terminal state is READY (the API enum has no ACTIVE, whatever the
    # documentation prose says), and there is no waiter, so poll. This launches
    # no instances -- the fleet is a declaration until the first invoke.
    log(f"waiting for {cp_id} to become READY")
    while True:
        got = control.get_capacity_provider(capacityProviderId=cp_id)
        status = got["status"]
        if status == "READY":
            break
        if "FAILED" in status:
            die(f"{status}: {got.get('statusReason')} ({got.get('statusCode')})")
        time.sleep(5)
    log(f"capacity provider READY: {cp_arn}")
    return cp_id, cp_arn


def create_runtime(
    control, name: str, artifact: dict, execution_arn: str, cp_arn: str, env: dict, volumes: list[tuple[str, str]]
) -> dict:
    log(f"creating runtime {name} (mounts {', '.join(m for _, m in volumes)})")
    resp = control.create_agent_runtime(
        agentRuntimeName=name,
        roleArn=execution_arn,
        agentRuntimeArtifact=artifact,
        protocolConfiguration={"serverProtocol": "HTTP"},
        # Binds the runtime to the fleet. Mutually exclusive with
        # networkConfiguration: the VPC belongs to the capacity provider.
        capacityProviderConfiguration={"capacityProviderArn": cp_arn},
        # Only the volumes a runtime declares are mounted for it, so the mastering
        # and compliance agents never see the model stack.
        filesystemConfigurations=[{"capacityProviderVolume": {"volumeName": v, "mountPath": m}} for v, m in volumes],
        lifecycleConfiguration={"idleRuntimeSessionTimeout": IDLE_SESSION_TIMEOUT, "maxLifetime": MAX_LIFETIME},
        environmentVariables=env,
    )
    runtime_id, arn = resp["agentRuntimeId"], resp["agentRuntimeArn"]
    while True:
        status = control.get_agent_runtime(agentRuntimeId=runtime_id)["status"]
        if status == "READY":
            break
        if "FAILED" in status:
            die(f"runtime {name} is {status}")
        time.sleep(5)
    log(f"runtime READY: {arn}")
    return {"name": name, "id": runtime_id, "arn": arn}


def main() -> None:
    account, cli = preflight()
    suffix = str(int(time.time()))
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    operator_arn, execution_arn = ensure_roles(account)
    bucket = ensure_bucket(account)
    vendor_agent_deps()

    composition_image = build_and_push_image("composition", account, cli, f"v1-{suffix}")
    mastering_image = build_and_push_image("mastering", account, cli, f"v1-{suffix}")
    _, key = build_and_upload_zip(account, bucket, f"compliance/{suffix}/compliance_agent.zip")

    cp_id, cp_arn = create_capacity_provider(control, f"music_production_capacity_{suffix}", operator_arn)

    base_env = {"AWS_REGION": REGION, "WORKSPACE_DIR": TRACKS_MOUNT, "ARTIFACT_BUCKET": bucket}

    # Composition first: the compliance agent needs its ARN, which cannot be
    # constructed by hand because of the random 10-character suffix.
    composition = create_runtime(
        control,
        f"music_production_composition_{suffix}",
        {"containerConfiguration": {"containerUri": composition_image}},
        execution_arn,
        cp_arn,
        {**base_env, "MODEL_ID": MODELS["composition"], "MODELS_DIR": MODELS_MOUNT},
        [(TRACKS_VOLUME, TRACKS_MOUNT), (MODELS_VOLUME, MODELS_MOUNT)],
    )

    # Now that the composition runtime exists, scope the cross-agent grant to
    # that one ARN instead of leaving it open to every runtime in the account.
    put_execution_policy(account, composition_arn=composition["arn"])
    log(f"scoped CrossAgentInvoke to {composition['arn']}")

    mastering = create_runtime(
        control,
        f"music_production_mastering_{suffix}",
        {"containerConfiguration": {"containerUri": mastering_image}},
        execution_arn,
        cp_arn,
        {**base_env, "MODEL_ID": MODELS["mastering"]},
        [(TRACKS_VOLUME, TRACKS_MOUNT)],
    )

    compliance = create_runtime(
        control,
        f"music_production_compliance_{suffix}",
        {
            "codeConfiguration": {
                "code": {"s3": {"bucket": bucket, "prefix": key}},
                "runtime": PYTHON_RUNTIME,
                "entryPoint": ["compliance_agent.py"],
            }
        },
        execution_arn,
        cp_arn,
        {
            **base_env,
            "MODEL_ID": MODELS["compliance"],
            "COMPOSITION_RUNTIME_ARN": composition["arn"],
            "COMPOSITION_QUALIFIER": "DEFAULT",
        },
        [(TRACKS_VOLUME, TRACKS_MOUNT)],
    )

    state = {
        "region": REGION,
        "account": account,
        "suffix": suffix,
        "instance_type": CP_INSTANCE_TYPE,
        "capacity_provider": {"id": cp_id, "arn": cp_arn},
        "runtimes": {"composition": composition, "mastering": mastering, "compliance": compliance},
        "ecr_repositories": ["music-production/composition-agent", "music-production/mastering-agent"],
        "s3": {"bucket": bucket, "key": key},
        "iam_roles": [OPERATOR_ROLE_NAME, EXECUTION_ROLE_NAME],
        "volumes": {TRACKS_VOLUME: TRACKS_MOUNT, MODELS_VOLUME: MODELS_MOUNT},
        "models_snapshot_id": MODELS_SNAPSHOT_ID,
        "sessions": [],
    }
    STATE_FILE.write_text(json.dumps(state, indent=2))
    log(f"wrote {STATE_FILE}")
    print("\nDeployed. Next: python scripts/invoke.py   (then scripts/cleanup.py)")
    print(f"Note: no EC2 instance exists yet - the first invoke provisions a {CP_INSTANCE_TYPE}.")
    if not MODELS_SNAPSHOT_ID:
        print(
            "The first invoke also builds the generative stack onto the models "
            "volume (a few minutes, once per session)."
        )


if __name__ == "__main__":
    main()
