#!/usr/bin/env python
"""Deploys the whole thing: CapacityProvider → Runtime → Gateway, with OTEL.

    client → AgentCore Gateway → AgentRuntime → EC2 (CapacityProvider) → Bedrock
                                                     ↓
                                          OTEL: metrics, logs, spans

What this script does:

  1. IAM       — three roles: operator (AgentCore provisions EC2 in your account),
                 runtime (the agent's own), and gateway. No instance profile — the
                 capacity provider omits it and the service supplies its own
  2. Network   — a private subnet (no public IP), a security group with ZERO
                 ingress, and 7 VPC endpoints: with no NAT, every AWS destination
                 the agent reaches needs its own endpoint
  3. Image     — the agent container, built for the instance architecture, to ECR
  4. CP        — ONE CapacityProvider: real EC2 in your VPC + a 30 GiB encrypted
                 EBS volume. Nothing runs yet — the first invoke launches it
  5. Runtime   — the agent on that CP, EBS mounted at /mnt/data, W3C traceparent
                 allowed through, OTLP endpoints pinned to this region
  6. Gateway   — an AgentCore Gateway in front, as the single entry point
  7. Observ.   — Transaction Search + the resource policy that lets X-Ray write
                 spans into the agent's log group (ACCOUNT config, bills per span)

Idempotent: re-running reuses whatever already exists (roles, subnet, SG, VPC
endpoints, ECR) and only creates what is missing.

    python deploy.py                    # deploy everything
    python deploy.py --skip-build       # do not rebuild the image
    python deploy.py --no-gateway       # CP + runtime only, no Gateway

Environment variables (see README):
    AWS_REGION            REQUIRED unless your AWS profile sets a region
    CP_INSTANCE_TYPE      default m6g.large   (same as the official AWS sample)
    CP_OS                 LINUX_ARM64 | LINUX_X86_64
    BEDROCK_MODEL_ID      default global.anthropic.claude-haiku-4-5-20251001-v1:0
    CP_IDLE_TIMEOUT       default 900   (seconds)
    CP_MAX_LIFETIME       default 3600  (seconds; ceiling 1209600 = 14 days)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


# ─────────────────────────── configuration ───────────────────────────
def resolve_region() -> str:
    """Resolves the region like any other AWS tool: AWS_REGION, then
    AWS_DEFAULT_REGION, then the active profile.

    There is deliberately NO default. A sample that silently launches EC2
    instances in a region you did not pick is worse than one that refuses to run.
    """
    region = (os.environ.get("AWS_REGION")
              or os.environ.get("AWS_DEFAULT_REGION")
              or boto3.Session().region_name)
    if not region:
        sys.exit("No AWS region configured. Either:\n"
                 "    export AWS_REGION=<region>\n"
                 "or set one in your AWS profile (`aws configure`).")
    return region


REGION = resolve_region()
INSTANCE_TYPE = os.environ.get("CP_INSTANCE_TYPE", "m6g.large")
OPERATING_SYSTEM = os.environ.get("CP_OS", "LINUX_ARM64")
# A "global." inference profile is the only prefix that resolves in every region
# this sample can run in. A geographic prefix (us./eu./apac./au.) is valid only
# inside its own geography, so a hardcoded one makes the agent fail with
# "The provided model identifier is invalid" the moment you deploy elsewhere.
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID",
                          "global.anthropic.claude-haiku-4-5-20251001-v1:0")
IDLE_TIMEOUT = int(os.environ.get("CP_IDLE_TIMEOUT", "900"))
MAX_LIFETIME = int(os.environ.get("CP_MAX_LIFETIME", "3600"))

# The image is built by CodeBuild, natively for the instance architecture, so no
# local Docker is needed. docker_platform feeds `docker build --platform`; the
# codebuild_* fields pick a native builder (arm64 image on ARM_CONTAINER).
ARCH = {
    "LINUX_ARM64": {"docker": "linux/arm64", "instance_hint": "m6g/c7g/t4g",
                    "codebuild_image": "aws/codebuild/amazonlinux2-aarch64-standard:3.0",
                    "codebuild_type": "ARM_CONTAINER"},
    "LINUX_X86_64": {"docker": "linux/amd64", "instance_hint": "m5/c5/t3",
                     "codebuild_image": "aws/codebuild/amazonlinux2-x86_64-standard:5.0",
                     "codebuild_type": "LINUX_CONTAINER"},
}[OPERATING_SYSTEM]

PREFIX = "cpsample"
CP_NAME = f"{PREFIX}_cp"
RUNTIME_NAME = f"{PREFIX}_agent"
GATEWAY_NAME = f"{PREFIX}-gw"
TARGET_NAME = "runtime-cp"
ECR_REPO = f"{PREFIX}-agent"
IMAGE_TAG = "v1"

OPERATOR_ROLE = f"{PREFIX}OperatorRole"
RUNTIME_ROLE = f"{PREFIX}RuntimeRole"
GATEWAY_ROLE = f"{PREFIX}GatewayRole"
CODEBUILD_ROLE = f"{PREFIX}CodeBuildRole"
CODEBUILD_PROJECT = f"{PREFIX}-image-build"

SUBNET_NAME = f"{PREFIX}-private"
SG_AGENT_NAME = f"{PREFIX}-noingress"
SG_VPCE_NAME = f"{PREFIX}-vpce"
SUBNET_CIDR = os.environ.get("CP_SUBNET_CIDR", "172.31.200.0/24")

# With no public IP and no NAT, every AWS destination needs its own VPC
# endpoint. Missing xray or bedrock-runtime fails SILENTLY (the
# BatchSpanProcessor swallows the timeout). See README → Private networking.
INTERFACE_ENDPOINTS = ["ecr.api", "ecr.dkr", "logs", "bedrock-agentcore",
                       "xray", "bedrock-runtime"]

TAGS = {"project": "cp-sample", "owner": os.environ.get("USER", "unknown")}
HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / ".deploy-state.json"

# The Dockerfile lives here as a string, not a file: the repo does not version
# Dockerfiles (it builds via CodeBuild). Unlike the plain sample, this one runs
# `opentelemetry-instrument` and sets the OTEL env — the OTLP endpoints are NOT
# pinned here (they carry the region; deploy.py sets them per region on the
# runtime, see create_runtime). ARM64 must match the CP's operatingSystem.
DOCKERFILE = """\
FROM public.ecr.aws/docker/library/python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py .
ENV PYTHONUNBUFFERED=1 \\
    OTEL_TRACES_EXPORTER=otlp \\
    OTEL_LOGS_EXPORTER=otlp \\
    OTEL_METRICS_EXPORTER=none \\
    OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf \\
    OTEL_TRACES_SAMPLER=always_on \\
    OTEL_PYTHON_LOG_CORRELATION=true
EXPOSE 8080
CMD ["opentelemetry-instrument", "python", "agent.py"]
"""


def log(msg: str = "") -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    log(f"\n\033[1m── {msg}\033[0m")


def ok(msg: str) -> None:
    log(f"   ✓ {msg}")


def warn(msg: str) -> None:
    log(f"   ! {msg}")


def save_state(**kw) -> dict:
    st = load_state()
    st.update(kw)
    STATE_FILE.write_text(json.dumps(st, indent=2))
    return st


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def wait_ready(get, label: str, timeout: int = 180, on_fail=None) -> dict:
    """Polls `get()` until status is READY. There is no boto3 waiter for any of
    these resources, so all four (CP, runtime, gateway, target) come through here.

    Matching on "FAILED" catches UPDATE_FAILED and DELETE_FAILED too, not just
    CREATE_FAILED — this script is idempotent, so it does run updates.
    """
    t0 = time.time()
    while True:
        r = get()
        status = r.get("status")
        if status == "READY":
            ok(f"{label}: READY in {time.time()-t0:.1f}s")
            return r
        if "FAILED" in (status or ""):
            detail = (f"{status} / {r.get('statusCode')}\n"
                      f"  reason: {r.get('statusReason') or r.get('statusReasons')}")
            sys.exit(f"\n{label} failed: {detail}\n{on_fail or ''}")
        if time.time() - t0 > timeout:
            sys.exit(f"timed out waiting for {label} (status={status})")
        time.sleep(4)


# ─────────────────────────── prerequisites ───────────────────────────
def require_capacity_provider_support(agentcore) -> None:
    """Fail early, with a clear message, if boto3 lacks the CP APIs."""
    ops = agentcore.meta.service_model.operation_names
    if "CreateCapacityProvider" not in ops:
        import botocore
        sys.exit(
            f"\nThis boto3/botocore does not have the CapacityProvider APIs.\n"
            f"  botocore installed: {botocore.__version__} — needs >= 1.43.66\n"
            f"  fix: pip install -U boto3 botocore\n")
    ok(f"boto3 with {len([o for o in ops if 'Capacity' in o])} CapacityProvider APIs")


# ─────────────────────────── IAM ───────────────────────────
def ensure_role(iam, name: str, trust: dict, managed=(), inline: dict | None = None,
                desc: str = "") -> str:
    try:
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust),
                        Description=desc or f"{PREFIX} sample")
        ok(f"role created: {name}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
        ok(f"role already existed: {name}")
    for arn in managed:
        iam.attach_role_policy(RoleName=name, PolicyArn=arn)
    if inline:
        iam.put_role_policy(RoleName=name, PolicyName="inline",
                            PolicyDocument=json.dumps(inline))
    return f"arn:aws:iam::{ACCOUNT}:role/{name}"


def ensure_roles(iam) -> tuple[str, str, str]:
    step("1. IAM roles")
    svc_trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
        "Action": "sts:AssumeRole",
        "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}}}]}

    operator = ensure_role(
        iam, OPERATOR_ROLE, svc_trust,
        managed=["arn:aws:iam::aws:policy/BedrockAgentCoreRuntimeInstancesOperatorRolePolicy"],
        desc="AgentCore provisions EC2/ASG/EBS/ENI in your account with this role")

    # No instance role: the capacity provider omits instanceProfileArn and uses
    # the service default (see create_capacity_provider). Avoids a fixed-name, account-global role.

    runtime = ensure_role(
        iam, RUNTIME_ROLE, svc_trust,
        inline={"Version": "2012-10-17", "Statement": [
            {"Sid": "Observability", "Effect": "Allow", "Action": [
                "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                "logs:DescribeLogStreams", "logs:DescribeLogGroups",
                "cloudwatch:PutMetricData",
                # xray:PutSpans is required for ADOT to export spans
                "xray:PutSpans", "xray:PutTraceSegments", "xray:PutTelemetryRecords",
                "bedrock-agentcore:PutSystemLogEvents"], "Resource": "*"},
            # AgentCore uses this permission to let X-Ray deliver spans into the
            # AGENT's own log group (unified destination) instead of the shared
            # aws/spans. See docs → observability-configure.html.
            {"Sid": "UnifiedSpanDestination", "Effect": "Allow",
             "Action": ["logs:PutResourcePolicy"], "Resource": [
                 f"arn:aws:logs:*:{ACCOUNT}:log-group:/aws/bedrock-agentcore/runtimes/*"]},
            {"Sid": "PullImage", "Effect": "Allow", "Action": [
                "ecr:GetAuthorizationToken", "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
                "ecr:BatchCheckLayerAvailability"], "Resource": "*"},
            {"Sid": "Bedrock", "Effect": "Allow", "Action": [
                "bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream",
                "bedrock:Converse", "bedrock:ConverseStream"], "Resource": [
                    "arn:aws:bedrock:*::foundation-model/*",
                    f"arn:aws:bedrock:*:{ACCOUNT}:inference-profile/*",
                    f"arn:aws:bedrock:*:{ACCOUNT}:application-inference-profile/*"]}]},
        desc="The agent's execution role")

    gateway = ensure_role(
        iam, GATEWAY_ROLE, svc_trust,
        inline={"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["bedrock-agentcore:InvokeAgentRuntime"],
             "Resource": [f"arn:aws:bedrock-agentcore:*:{ACCOUNT}:runtime/*"]},
            {"Effect": "Allow", "Action": [
                "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
                "xray:PutTraceSegments", "xray:PutSpans",
                "cloudwatch:PutMetricData"], "Resource": "*"}]},
        desc="The Gateway's role for invoking the runtime")

    log("   waiting for IAM propagation (10s)…")
    time.sleep(10)
    return operator, runtime, gateway


# ─────────────────────────── networking ───────────────────────────
def ensure_network(ec2) -> tuple[str, str, str]:
    """Subnet with NO public-IP auto-assign, SG with no ingress, and VPC endpoints."""
    step("2. Networking (private, no public IP)")
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        sys.exit("no default VPC found — create one or adjust this script")
    vpc = vpcs[0]["VpcId"]
    tagspec = [{"Key": k, "Value": v} for k, v in TAGS.items()]

    found = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]},
                                          {"Name": "tag:Name", "Values": [SUBNET_NAME]}])["Subnets"]
    if found:
        subnet = found[0]["SubnetId"]
        ok(f"subnet already existed: {subnet}")
    else:
        az = ec2.describe_availability_zones()["AvailabilityZones"][0]["ZoneName"]
        try:
            subnet = ec2.create_subnet(
                VpcId=vpc, CidrBlock=SUBNET_CIDR, AvailabilityZone=az,
                TagSpecifications=[{"ResourceType": "subnet",
                                    "Tags": tagspec + [{"Key": "Name", "Value": SUBNET_NAME}]}]
            )["Subnet"]["SubnetId"]
        except ClientError as e:
            if e.response["Error"]["Code"] != "InvalidSubnet.Conflict":
                raise
            sys.exit(f"\nCIDR {SUBNET_CIDR} is already taken in {vpc} by another subnet.\n"
                     f"Pick a free range:  export CP_SUBNET_CIDR=172.31.201.0/24\n")
        ok(f"subnet created: {subnet} ({SUBNET_CIDR})")
    ec2.modify_subnet_attribute(SubnetId=subnet, MapPublicIpOnLaunch={"Value": False})
    ok("MapPublicIpOnLaunch=False")

    def ensure_sg(name, desc):
        f = ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [vpc]},
                                                  {"Name": "group-name", "Values": [name]}])["SecurityGroups"]
        if f:
            return f[0]["GroupId"]
        return ec2.create_security_group(
            GroupName=name, VpcId=vpc, Description=desc,
            TagSpecifications=[{"ResourceType": "security-group", "Tags": tagspec}]
        )["GroupId"]

    # The agent's SG: zero ingress. Inbound traffic arrives over the Reverse
    # X-ENI, not the primary ENI — which is why no ingress rule is needed.
    sg_agent = ensure_sg(SG_AGENT_NAME, "agent: no ingress, egress open")
    sg_vpce = ensure_sg(SG_VPCE_NAME, "443 from the agents to the VPC endpoints")
    try:
        ec2.authorize_security_group_ingress(GroupId=sg_vpce, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
            "UserIdGroupPairs": [{"GroupId": sg_agent, "Description": "agents"}]}])
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise
    n_in = len(ec2.describe_security_groups(GroupIds=[sg_agent])["SecurityGroups"][0]["IpPermissions"])
    ok(f"agent SG {sg_agent}: {n_in} ingress rule(s)")

    # An endpoint existing in the VPC is NOT enough: an interface endpoint only
    # serves the subnets it is attached to. Reusing one that lives in a different
    # subnet means the instance cannot reach ECR, and the invoke fails with
    # "The agent artifact could not be downloaded" — nothing points at networking.
    existing = {}
    for e in ec2.describe_vpc_endpoints(
            Filters=[{"Name": "vpc-id", "Values": [vpc]}])["VpcEndpoints"]:
        existing[e["ServiceName"]] = e
    # The S3 gateway endpoint is a ROUTE, so it needs the main route table to attach to.
    # A VPC with no main route table is rare but possible, and a bare IndexError here
    # would say nothing about what to do next.
    main_rt = next((t for t in ec2.describe_route_tables(
        Filters=[{"Name": "vpc-id", "Values": [vpc]}])["RouteTables"]
        if any(a.get("Main") for a in t["Associations"])), None)
    if main_rt is None:
        sys.exit(f"\nVPC {vpc} has no main route table, so the S3 gateway endpoint has nowhere "
                 f"to\nattach — and without S3 the instance cannot pull the agent image layers "
                 f"from ECR.\n\nAssociate a main route table with {vpc}, or run this sample in a "
                 f"VPC that has one.\n")
    rt = main_rt["RouteTableId"]

    pending = []
    s3 = f"com.amazonaws.{REGION}.s3"
    if s3 not in existing:
        e = ec2.create_vpc_endpoint(VpcEndpointType="Gateway", VpcId=vpc, ServiceName=s3,
                                    RouteTableIds=[rt],
                                    TagSpecifications=[{"ResourceType": "vpc-endpoint", "Tags": tagspec}])
        pending.append(e["VpcEndpoint"]["VpcEndpointId"])
        ok("s3 endpoint (gateway) created")
    for svc in INTERFACE_ENDPOINTS:
        full = f"com.amazonaws.{REGION}.{svc}"
        found_ep = existing.get(full)
        if found_ep:
            if subnet in (found_ep.get("SubnetIds") or []):
                continue
            # Right service, wrong subnet. An interface endpoint accepts only ONE
            # subnet per AZ, so it cannot simply be extended to ours — and an
            # endpoint whose SG does not admit our SG is unreachable anyway.
            # Reusing it would fail at invoke time with "The agent artifact could
            # not be downloaded", which points nowhere near networking.
            sys.exit(
                f"\nThe {svc} endpoint ({found_ep['VpcEndpointId']}) exists in {vpc} but "
                f"serves\nsubnet(s) {found_ep.get('SubnetIds')}, not {subnet} — and an "
                f"interface endpoint\ntakes only one subnet per AZ.\n\n"
                f"Either deploy into the subnet that endpoint already serves, or run this "
                f"sample\nin a region/VPC of its own. Sharing a VPC with another "
                f"deployment of this\nsample does not work.\n")
        try:
            e = ec2.create_vpc_endpoint(
                VpcEndpointType="Interface", VpcId=vpc, ServiceName=full,
                SubnetIds=[subnet], SecurityGroupIds=[sg_vpce], PrivateDnsEnabled=True,
                TagSpecifications=[{"ResourceType": "vpc-endpoint", "Tags": tagspec}])
            pending.append(e["VpcEndpoint"]["VpcEndpointId"])
            ok(f"{svc} endpoint created")
        except ClientError as ex:
            warn(f"{svc} endpoint: {ex.response['Error']['Code']}")
    if pending:
        log(f"   waiting for {len(pending)} endpoint(s) to become available…")
        for _ in range(40):
            sts = {e["VpcEndpointId"]: e["State"] for e in
                   ec2.describe_vpc_endpoints(VpcEndpointIds=pending)["VpcEndpoints"]}
            if all(v == "available" for v in sts.values()):
                break
            time.sleep(10)
        ok(f"endpoints: {set(sts.values())}")
    else:
        ok(f"{len(INTERFACE_ENDPOINTS) + 1} endpoints already serve {subnet}")
    return vpc, subnet, sg_agent


# ─────────────────────────── image ───────────────────────────
def build_and_push(sess) -> str:
    """Build the agent image on CodeBuild and push it to ECR.

    CodeBuild instead of local `docker build`: no Docker needed on your machine,
    and the image is built natively for the target architecture. The build context
    (agent.py + requirements.txt + the DOCKERFILE string) goes to S3, CodeBuild
    pulls it, builds and pushes.
    """
    step(f"3. Agent image ({ARCH['docker']}, built on CodeBuild)")
    reg = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"
    img = f"{reg}/{ECR_REPO}:{IMAGE_TAG}"
    ecr = sess.client("ecr")
    iam = sess.client("iam")
    s3 = sess.client("s3")

    try:
        ecr.create_repository(repositoryName=ECR_REPO,
                              tags=[{"Key": k, "Value": v} for k, v in TAGS.items()])
        ok(f"ECR repo created: {ECR_REPO}")
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryAlreadyExistsException":
            raise
        ok(f"ECR repo already existed: {ECR_REPO}")

    # bucket for the build context (create if missing; us-east-1 has no LocationConstraint)
    bucket = f"{PREFIX}-build-{ACCOUNT}-{REGION}"
    try:
        if REGION == "us-east-1":
            s3.create_bucket(Bucket=bucket)
        else:
            s3.create_bucket(Bucket=bucket,
                             CreateBucketConfiguration={"LocationConstraint": REGION})
        ok(f"build bucket created: {bucket}")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou",
                                               "BucketAlreadyExists"):
            raise
        ok(f"build bucket exists: {bucket}")

    # CodeBuild service role
    cb_role_arn = ensure_role(
        iam, CODEBUILD_ROLE,
        {"Version": "2012-10-17", "Statement": [{"Effect": "Allow",
            "Principal": {"Service": "codebuild.amazonaws.com"},
            "Action": "sts:AssumeRole"}]},
        inline={"Version": "2012-10-17", "Statement": [
            {"Effect": "Allow", "Action": ["logs:CreateLogGroup",
                "logs:CreateLogStream", "logs:PutLogEvents"], "Resource": "*"},
            {"Effect": "Allow", "Action": ["s3:GetObject", "s3:GetObjectVersion"],
             "Resource": f"arn:aws:s3:::{bucket}/*"},
            {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
            {"Effect": "Allow", "Action": ["ecr:BatchCheckLayerAvailability",
                "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage", "ecr:PutImage",
                "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload"],
             "Resource": f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/{ECR_REPO}"}]},
        desc="CodeBuild builds the agent image")
    time.sleep(8)   # let the fresh role propagate before CodeBuild assumes it

    # build context → S3 (agent.py + requirements.txt + the Dockerfile string)
    ctx = HERE / ".ctx"
    if ctx.exists():
        shutil.rmtree(ctx)
    ctx.mkdir()
    shutil.copy(HERE / "agent" / "agent.py", ctx)
    shutil.copy(HERE / "agent" / "requirements.txt", ctx)
    (ctx / "Dockerfile").write_text(DOCKERFILE)
    src_zip = shutil.make_archive(str(HERE / ".ctx-src"), "zip", root_dir=ctx)
    s3.upload_file(src_zip, bucket, "context.zip")
    os.remove(src_zip)
    shutil.rmtree(ctx)

    buildspec = "\n".join([
        "version: 0.2", "phases:", "  pre_build:", "    commands:",
        f"      - aws ecr get-login-password --region {REGION} | "
        f"docker login --username AWS --password-stdin {reg}",
        "  build:", "    commands:",
        f"      - docker build --platform {ARCH['docker']} -t {img} .",
        "  post_build:", "    commands:", f"      - docker push {img}"])

    cbc = sess.client("codebuild")
    spec = {
        "source": {"type": "S3", "location": f"{bucket}/context.zip",
                   "buildspec": buildspec},
        "artifacts": {"type": "NO_ARTIFACTS"},
        "environment": {"type": ARCH["codebuild_type"], "image": ARCH["codebuild_image"],
                        "computeType": "BUILD_GENERAL1_SMALL", "privilegedMode": True},
        "serviceRole": cb_role_arn}
    if cbc.batch_get_projects(names=[CODEBUILD_PROJECT])["projects"]:
        cbc.update_project(name=CODEBUILD_PROJECT, **spec)
    else:
        cbc.create_project(name=CODEBUILD_PROJECT, **spec)

    log("   building on CodeBuild (3-6 min)…")
    build_id = cbc.start_build(projectName=CODEBUILD_PROJECT)["build"]["id"]
    while True:
        status = cbc.batch_get_builds(ids=[build_id])["builds"][0]["buildStatus"]
        if status == "SUCCEEDED":
            break
        if status != "IN_PROGRESS":
            sys.exit(f"CodeBuild {status}. Logs:\n"
                     f"  aws codebuild batch-get-builds --ids {build_id} --region {REGION}")
        time.sleep(10)
    ok(f"pushed {ECR_REPO}:{IMAGE_TAG}")
    return img


# ─────────────────────────── CapacityProvider ───────────────────────────
def create_capacity_provider(agentcore, operator_arn: str,
                             subnet: str, sg: str) -> tuple[str, str]:
    step("4. CapacityProvider (your own EC2 fleet)")
    for cp in agentcore.list_capacity_providers(maxResults=50).get("capacityProviders", []):
        if cp.get("name") == CP_NAME:
            ok(f"already existed: {cp['capacityProviderId']} ({cp.get('status')})")
            return cp["capacityProviderId"], cp["capacityProviderArn"]

    # instanceProfileArn is omitted: the service attaches its own default instance
    # profile. Passing one is optional (only operatingSystem + instanceRequirements
    # are required) and ties you to a fixed-name, account-global role — see ensure_roles.
    r = agentcore.create_capacity_provider(
        name=CP_NAME,
        description="Sample: agent on an AWS-managed EC2 instance, no public IP",
        permissionsConfiguration={"capacityProviderOperatorRoleArn": operator_arn},
        computeConfiguration={"ec2Configuration": {
            "launchTemplateSource": {"launchParameters": {
                "operatingSystem": OPERATING_SYSTEM,
                "instanceRequirements": {"allowedInstanceTypes": [INSTANCE_TYPE]},
                "monitoring": "BASIC"}},
            "vpcConfiguration": {"subnets": [subnet], "securityGroups": [sg]},
            "volumes": [{"ebsConfiguration": {
                "name": "data_volume", "sizeGiB": 30, "volumeType": "gp3",
                "iops": 3000, "throughput": 125, "encrypted": True}}],
            "lifecycleConfiguration": {"idleInstanceTimeout": IDLE_TIMEOUT,
                                       "maxLifetime": MAX_LIFETIME}}},
        tags=TAGS)
    cid, carn = r["capacityProviderId"], r["capacityProviderArn"]
    wait_ready(lambda: agentcore.get_capacity_provider(capacityProviderId=cid),
               f"CP {cid}",
               on_fail="If it says 'not authorized to use launch template', the real "
                       "cause\nis iam:PassRole on the instance profile. This sample omits "
                       "instanceProfileArn\nto avoid that; if you added one back, its name "
                       "must start with\n'AmazonBedrockAgentCoreCapacityProviderDefault…'.")
    ok("no EC2 created yet — provisioning is lazy")
    return cid, carn


# ─────────────────────────── Runtime ───────────────────────────
def create_runtime(agentcore, img: str, cp_arn: str,
                   runtime_arn_role: str) -> tuple[str, str]:
    step("5. AgentRuntime on that CapacityProvider")
    existing = None
    for rt in agentcore.list_agent_runtimes(maxResults=50).get("agentRuntimes", []):
        if rt.get("agentRuntimeName") == RUNTIME_NAME:
            existing = rt
            break
    body = dict(
        agentRuntimeArtifact={"containerConfiguration": {"containerUri": img}},
        roleArn=runtime_arn_role,
        # HTTP (not MCP): the Gateway does not forward the Accept header, and an
        # MCP runtime requires "application/json, text/event-stream" — which
        # would return 406 through the Gateway.
        protocolConfiguration={"serverProtocol": "HTTP"},
        capacityProviderConfiguration={"capacityProviderArn": cp_arn},
        filesystemConfigurations=[{"capacityProviderVolume": {
            "volumeName": "data_volume", "mountPath": "/mnt/data"}}],
        # The OTLP endpoints carry the region, and ADOT signs SigV4 with the
        # region it PARSES OUT OF THE ENDPOINT, not with AWS_REGION. Setting them
        # here — from deploy.py, which already knows the region — gives the region
        # a single source. A region baked into the Dockerfile is a second source
        # that wins at runtime, which is how spans silently went to the wrong
        # region before. See README → Observability.
        environmentVariables={"AGENT_STATE_DIR": "/mnt/data",
                              "BEDROCK_MODEL_ID": MODEL_ID,
                              "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT":
                                  f"https://xray.{REGION}.amazonaws.com/v1/traces",
                              "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT":
                                  f"https://logs.{REGION}.amazonaws.com/v1/logs"},
        # lets the W3C traceparent through to the agent (X-Amzn-Trace-Id is restricted)
        requestHeaderConfiguration={"requestHeaderAllowlist": [
            "traceparent", "tracestate", "baggage"]},
        lifecycleConfiguration={"idleRuntimeSessionTimeout": IDLE_TIMEOUT,
                                "maxLifetime": MAX_LIFETIME})
    if existing:
        rid = existing["agentRuntimeId"]
        r = agentcore.update_agent_runtime(agentRuntimeId=rid, **body)
        ok(f"updated: {rid} (v{r.get('agentRuntimeVersion')})")
        arn = existing["agentRuntimeArn"]
    else:
        r = agentcore.create_agent_runtime(agentRuntimeName=RUNTIME_NAME,
                                          description="Sample: HTTP agent on a CapacityProvider",
                                          tags=TAGS, **body)
        rid, arn = r["agentRuntimeId"], r["agentRuntimeArn"]
        ok(f"created: {rid}")
    wait_ready(lambda: agentcore.get_agent_runtime(agentRuntimeId=rid),
               f"runtime {rid}", timeout=240)
    return rid, arn


# ─────────────────────────── Gateway ───────────────────────────
def create_gateway(agentcore, runtime_arn: str,
                   gateway_role: str) -> tuple[str | None, str | None, str | None]:
    step("6. AgentCore Gateway in front")
    gid = gurl = None
    for g in agentcore.list_gateways(maxResults=50).get("items", []):
        if g.get("name") == GATEWAY_NAME:
            gid = g["gatewayId"]
            # list_gateways does NOT return gatewayUrl — only get_gateway does.
            # Without this, the state saves gateway_url=None and invoke.py
            # silently falls back to the direct path, never touching the Gateway.
            gurl = agentcore.get_gateway(gatewayIdentifier=gid).get("gatewayUrl")
            ok(f"already existed: {gid}")
            break
    if not gid:
        # protocolType is OMITTED on purpose: a gateway with protocolType=MCP
        # rejects a runtime target ("AgentCore Runtime targets can be added to
        # gateways that don't have a protocol type set").
        r = agentcore.create_gateway(
            name=GATEWAY_NAME,
            description="Single entry point for the runtime on the CapacityProvider",
            roleArn=gateway_role, authorizerType="AWS_IAM",
            exceptionLevel="DEBUG", tags=TAGS)
        gid, gurl = r["gatewayId"], r.get("gatewayUrl")
        ok(f"created with no protocolType: {gid}")
        wait_ready(lambda: agentcore.get_gateway(gatewayIdentifier=gid),
                   f"gateway {gid}")

    tid = None
    for t in agentcore.list_gateway_targets(gatewayIdentifier=gid).get("items", []):
        if t.get("name") == TARGET_NAME:
            tid = t["targetId"]
            ok(f"target already existed: {tid} ({t.get('status')})")
            break
    if not tid:
        t = agentcore.create_gateway_target(
            gatewayIdentifier=gid, name=TARGET_NAME,
            description="AgentRuntime running on EC2 via a CapacityProvider",
            targetConfiguration={"http": {"agentcoreRuntime": {
                "arn": runtime_arn, "qualifier": "DEFAULT"}}},
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
            metadataConfiguration={"allowedRequestHeaders": [
                "traceparent", "tracestate", "baggage"]})
        tid = t["targetId"]
        wait_ready(lambda: agentcore.get_gateway_target(gatewayIdentifier=gid,
                                                        targetId=tid),
                   f"target {tid}",
                   on_fail="A runtime target cannot be added to a gateway that has "
                           "protocolType set.\nThe 'MCP server did not respond' "
                           "message is misleading — check that.")
    return gid, gurl, tid


# ─────────────────────────── observability ───────────────────────────
RESOURCE_POLICY_NAME = f"{PREFIX}-xray-spans"


def ensure_xray_logs_resource_policy(logs) -> None:
    """Grants X-Ray PutLogEvents on the span log groups.

    A prerequisite for enabling Transaction Search via the API (the console does
    it for you). Without this policy X-Ray cannot deliver spans — neither to
    aws/spans nor to the agent's own log group. See docs →
    observability-configure.html.
    """
    targets = [f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:aws/spans:*",
               f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/application-signals/data:*",
               f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/bedrock-agentcore/runtimes/*"]
    desired = {"Version": "2012-10-17", "Statement": [{
        "Sid": "TransactionSearchXRayAccess", "Effect": "Allow",
        "Principal": {"Service": "xray.amazonaws.com"},
        "Action": "logs:PutLogEvents", "Resource": targets,
        "Condition": {"ArnLike": {"aws:SourceArn": f"arn:aws:xray:{REGION}:{ACCOUNT}:*"},
                      "StringEquals": {"aws:SourceAccount": ACCOUNT}}}]}

    # CONDITIONAL: only writes if absent or different
    try:
        current = logs.describe_resource_policies().get("resourcePolicies", [])
    except ClientError as e:
        warn(f"describe_resource_policies: {e.response['Error']['Code']}")
        current = []

    # The check is about COVERAGE, not existence. A policy covering only
    # `aws/spans` is not enough: with ADOT >= 0.18 the span goes to the AGENT's
    # log group, and without permission X-Ray cannot write there — the spans
    # disappear with no error at all (that is exactly what happened on the first
    # attempt in Oregon).
    covers_spans = covers_runtimes = False
    existing_name = None
    for p in current:
        try:
            doc = json.loads(p.get("policyDocument", "{}"))
        except json.JSONDecodeError:
            continue
        for stm in doc.get("Statement", []):
            if "xray.amazonaws.com" not in json.dumps(stm.get("Principal", {})):
                continue
            res = stm.get("Resource")
            res = [res] if isinstance(res, str) else (res or [])
            if any("aws/spans" in r for r in res):
                covers_spans = True
                existing_name = p.get("policyName")
            if any("bedrock-agentcore/runtimes" in r for r in res):
                covers_runtimes = True

    if covers_spans and covers_runtimes:
        ok(f"resource policy '{existing_name}' already covers aws/spans + the agent log group")
        return

    if covers_spans and not covers_runtimes:
        warn(f"policy '{existing_name}' covers only aws/spans — with ADOT >= 0.18 the "
             f"spans go to the AGENT's log group and would be lost")
    try:
        logs.put_resource_policy(policyName=RESOURCE_POLICY_NAME,
                                 policyDocument=json.dumps(desired))
        ok(f"resource policy '{RESOURCE_POLICY_NAME}' created "
           f"(X-Ray → aws/spans + /aws/bedrock-agentcore/runtimes/*)")
    except ClientError as e:
        warn(f"put_resource_policy: {e.response['Error']['Code']}: "
             f"{e.response['Error']['Message'][:110]}")


def enable_transaction_search(sampling: int = 20) -> None:
    """Transaction Search feeds the spans (aws/spans or the agent's log group)
    and is a prerequisite for the GenAI Observability screen.

    Everything here is CONDITIONAL: it reads the current state and only changes
    what needs changing. This is ACCOUNT-level config — it affects every service
    in the region and it bills per span.
    """
    step(f"7. Observability (Transaction Search, sampling {sampling}%)")
    xr = boto3.client("xray", region_name=REGION)
    logs = boto3.client("logs", region_name=REGION)

    # 1) resource policy: prerequisite for X-Ray to write to the log groups
    ensure_xray_logs_resource_policy(logs)

    # 2) trace segment destination
    try:
        d = xr.get_trace_segment_destination()
        dest, status = d.get("Destination"), d.get("Status")
        if dest == "CloudWatchLogs" and status == "ACTIVE":
            ok("Transaction Search already active (CloudWatchLogs / ACTIVE)")
        elif dest == "CloudWatchLogs" and status == "PENDING":
            warn("Transaction Search is PENDING — this can take a few minutes")
        else:
            xr.update_trace_segment_destination(Destination="CloudWatchLogs")
            ok(f"destination {dest} → CloudWatchLogs (enabling Transaction Search)")
            for _ in range(30):
                s = xr.get_trace_segment_destination().get("Status")
                if s == "ACTIVE":
                    ok("status ACTIVE")
                    break
                time.sleep(10)
            else:
                warn("did not reach ACTIVE — spans only arrive after it does")
    except ClientError as e:
        warn(f"trace segment destination: {e.response['Error']['Code']}")

    # 3) sampling: only changes it if it differs from what we want
    try:
        rules = xr.get_indexing_rules().get("IndexingRules", [])
        current = None
        for r in rules:
            if r.get("Name") == "Default":
                current = r.get("Rule", {}).get("Probabilistic", {}).get(
                    "DesiredSamplingPercentage")
        if current == float(sampling):
            ok(f"sampling is already {sampling}%")
        else:
            xr.update_indexing_rule(Name="Default", Rule={
                "Probabilistic": {"DesiredSamplingPercentage": float(sampling)}})
            ok(f"sampling {current}% → {sampling}%")
        if sampling < 100:
            warn(f"at {sampling}% sampling, ~1 in {round(100/max(sampling,1))} invokes "
                 f"shows up in X-Ray — generate load instead of a single invoke")
    except ClientError as e:
        warn(f"indexing rule: {e.response['Error']['Code']}")

    warn("ACCOUNT-level config: it bills per ingested span")


# ─────────────────────────── main ───────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip-build", action="store_true", help="do not rebuild the image")
    ap.add_argument("--no-gateway", action="store_true", help="CP + runtime only")
    ap.add_argument("--no-observability", action="store_true",
                    help="do not enable Transaction Search")
    ap.add_argument("--sampling", type=int, default=20)
    args = ap.parse_args()

    global ACCOUNT
    sess = boto3.Session(region_name=REGION)
    ACCOUNT = sess.client("sts").get_caller_identity()["Account"]

    log("\033[1mDeploy: agent on an AWS-managed EC2 instance (AgentCore CapacityProvider)\033[0m")
    log(f"  account   : {ACCOUNT}")
    log(f"  region    : {REGION}")
    log(f"  instance  : {INSTANCE_TYPE} ({OPERATING_SYSTEM}, {ARCH['instance_hint']})")
    log(f"  model     : {MODEL_ID}")
    log(f"  idle/max  : {IDLE_TIMEOUT}s / {MAX_LIFETIME}s")

    agentcore = sess.client("bedrock-agentcore-control")
    require_capacity_provider_support(agentcore)

    iam = sess.client("iam")
    ec2 = sess.client("ec2")

    operator, runtime_role, gw_role = ensure_roles(iam)
    vpc, subnet, sg = ensure_network(ec2)

    if args.skip_build:
        img = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{ECR_REPO}:{IMAGE_TAG}"
        step("3. Agent image")
        ok(f"--skip-build: using {ECR_REPO}:{IMAGE_TAG}")
    else:
        img = build_and_push(sess)

    cp_id, cp_arn = create_capacity_provider(agentcore, operator, subnet, sg)
    rt_id, rt_arn = create_runtime(agentcore, img, cp_arn, runtime_role)

    gid = gurl = tid = None
    if not args.no_gateway:
        gid, gurl, tid = create_gateway(agentcore, rt_arn, gw_role)
    if not args.no_observability:
        enable_transaction_search(args.sampling)

    st = save_state(account=ACCOUNT, region=REGION, vpc=vpc, subnet=subnet,
                    sg_agent=sg, image=img, cp_id=cp_id, cp_arn=cp_arn,
                    runtime_id=rt_id, runtime_arn=rt_arn,
                    gateway_id=gid, gateway_url=gurl, target_id=tid,
                    target_name=TARGET_NAME if tid else None)

    step("Deployed")
    log(f"   CapacityProvider : {cp_id}")
    log(f"   AgentRuntime     : {rt_id}")
    if gurl:
        log(f"   Gateway          : {gurl}/{TARGET_NAME}/invocations")
    log(f"\n   state saved to   : {STATE_FILE.name}")
    log("\n   next step:  python invoke.py")
    log("   the 1st invocation takes ~45-60s (it provisions the EC2). See README → Cold start.")
    log(f"   there is cost running: EC2 + EBS + {len(INTERFACE_ENDPOINTS)} interface endpoints.")
    log("       clean up with:  python cleanup.py\n")


if __name__ == "__main__":
    main()
