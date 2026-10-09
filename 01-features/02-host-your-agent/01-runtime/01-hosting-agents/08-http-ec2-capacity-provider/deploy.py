"""
Deploy one Strands agent TWICE onto ONE CapacityProvider — as a zip, and as a
container — to show that the artifact type and the compute type are orthogonal.

What this script does:

  1. IAM      — execution role for the runtimes, if absent
  2. S3       — bucket for the zip artifact
  3. Zip      — vendor arm64 deps, zip, upload
  4. ECR      — repository + image, built on Graviton via CodeBuild
  5. CP       — ONE CapacityProvider: real EC2 instances in your VPC
  6. Runtimes — TWO agent runtimes on that CP, one per artifact type
  7. Config   — write cp_config.json for invoke.py / cleanup.py

PLAIN BOTO3, ONE CLIENT
-----------------------
There is no `scripts/` helper any more, and no service-model file to install.
`boto3.client("bedrock-agentcore-control")` carries everything this sample
needs:

    create_capacity_provider    get_capacity_provider    update_capacity_provider
    list_capacity_providers     delete_capacity_provider
    list_agent_runtime_versions_by_capacity_provider

and `create_agent_runtime` accepts `capacityProviderConfiguration`. The
CapacityProvider APIs and the agent-runtime APIs are the SAME service
(`bedrock-agentcore-control`, API version 2023-06-05), so one client does both —
see `require_capacity_provider_support` for the version floor.

Usage:
    python deploy.py                 # both artifacts (default)
    python deploy.py --only zip      # zip only — fastest, no Docker/CodeBuild
    python deploy.py --only container
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import boto3

HERE = Path(__file__).resolve().parent
AGENT_DIR = HERE / "agent"
CONFIG_FILE = HERE / "cp_config.json"

STAMP = int(time.time())
NAME = f"basic_http_{STAMP}"

# ── Compute shape ──────────────────────────────────────────────────────────
# Graviton (ARM64) by default: cheaper, and the instance type we have verified.
# Switch both values together to run x86 — see README → Choosing a machine.
OPERATING_SYSTEM = os.environ.get("CP_OS", "LINUX_ARM64")
INSTANCE_TYPE = os.environ.get("CP_INSTANCE_TYPE", "m6g.large")

# ARM64 → aarch64 wheels + arm64 CodeBuild; X86_64 → x86_64 equivalents.
_ARCH = {
    "LINUX_ARM64": {
        "pip_platform": "aarch64-manylinux2014",
        "docker_platform": "linux/arm64",
        "codebuild_image": "aws/codebuild/amazonlinux2-aarch64-standard:3.0",
        "codebuild_type": "ARM_CONTAINER",
    },
    "LINUX_X86_64": {
        "pip_platform": "x86_64-manylinux2014",
        "docker_platform": "linux/amd64",
        "codebuild_image": "aws/codebuild/amazonlinux2-x86_64-standard:5.0",
        "codebuild_type": "LINUX_CONTAINER",
    },
}[OPERATING_SYSTEM]

PYTHON_RUNTIME = "PYTHON_3_12"
PYTHON_VERSION = "3.12"
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")

# Lifecycle. maxLifetime's ceiling is 1209600s (14 days) — verified against the
# live API, which rejects 1209601 with an explicit range message. The service
# requires maxLifetime >= idleInstanceTimeout.
IDLE_TIMEOUT = 900  # 15 min with no work → instance stops
MAX_LIFETIME = 86400  # 1 day; raise up to 1209600 for long-lived sessions

CONTROL_SERVICE = "bedrock-agentcore-control"

# The service principal that assumes both the execution role and the CP operator
# role. Defined once — it appears in two trust policies.
SERVICE_PRINCIPAL = "bedrock-agentcore.amazonaws.com"


def log(msg: str) -> None:
    print(msg, flush=True)


def step(msg: str) -> None:
    print(f"\n{'─' * 70}\n{msg}\n{'─' * 70}", flush=True)


# ══════════════════════════════════════════════════════════════════════════
# 0. Region and client
# ══════════════════════════════════════════════════════════════════════════
def resolve_region(explicit: str | None = None) -> str:
    """
    Resolve the AWS region from the environment, exactly like any other AWS tool.

    Precedence: explicit argument, AWS_REGION, AWS_DEFAULT_REGION, the active
    profile's region. There is deliberately NO default — a sample that silently
    launches EC2 instances in a region you did not choose is a worse outcome than
    one that refuses to start.
    """
    region = (
        explicit or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or boto3.Session().region_name
    )
    if not region:
        sys.exit(
            "No AWS region configured. Set one before running:\n"
            "    export AWS_REGION=<your-region>\n"
            "or configure it in your AWS profile (`aws configure`)."
        )
    return region


def require_capacity_provider_support(client) -> None:
    """
    Fail fast, and legibly, on a boto3 too old to know about CapacityProviders.

    Without this the script dies much later with
    `'BedrockAgentCoreControl' object has no attribute 'create_capacity_provider'`,
    which does not tell you what to do about it.
    """
    missing = [
        op
        for op in ("CreateCapacityProvider", "DeleteCapacityProvider")
        if op not in client.meta.service_model.operation_names
    ]
    if missing:
        sys.exit(
            f"This boto3 ({boto3.__version__}) has no {', '.join(missing)}.\n"
            "The CapacityProvider APIs need a newer one:\n"
            "    uv pip install --upgrade boto3 botocore"
        )


def enable_managed_resource_visibility(region: str) -> None:
    """
    Make the CapacityProvider's EC2 instances visible in the console and in
    `describe-instances`.

    Since EC2 Managed Resource Visibility shipped (April 2026), instances that an
    AWS service provisions on your behalf are **hidden by default** from console
    views and from `DescribeInstances`. A CapacityProvider's instances are exactly
    that: AgentCore launches them, so EC2 classes them as managed.

    The default is a good one in general — it keeps EKS, ECS and Lambda fleets out
    of your instance list. It is the wrong default *here*, for one specific
    reason: **hidden instances are still running and still billing.** This sample
    launches real instances in your account, and the thing you most need to be
    able to check is whether any survived cleanup. A confirmation step you cannot
    see is worse than no confirmation step.

    So this turns visibility on and, deliberately, never turns it back off —
    cleanup.py leaves it alone. Reverting it at the end of a run would re-hide
    the instances at exactly the moment you want to look for leftovers.

    TWO THINGS TO KNOW BEFORE YOU RUN THIS
    --------------------------------------
    1. The setting is **account-wide** and applies to every IAM principal in the
       account, not just you and not just this sample. Turning it on also
       un-hides any EKS, ECS, Lambda and WorkSpaces managed instances you have.
    2. It is not required. The alternative is to leave the account hidden and
       pass `--include-managed-resources` on every `describe-instances` call.
       Set CP_MANAGED_VISIBILITY=skip to take that route, and see the README.

    Visibility changes nothing operationally: hidden or visible, the instances
    run and bill identically.
    """
    if os.environ.get("CP_MANAGED_VISIBILITY") == "skip":
        log("  Skipping the visibility setting (CP_MANAGED_VISIBILITY=skip).")
        log("  Your CP instances will be HIDDEN from describe-instances. Use")
        log("  `aws ec2 describe-instances --include-managed-resources` instead.")
        return

    ec2 = boto3.client("ec2", region_name=region)

    # Older botocore has neither operation. Not fatal — an account that predates
    # the feature has nothing hidden in the first place.
    if "ModifyManagedResourceVisibility" not in ec2.meta.service_model.operation_names:
        log("  This botocore predates EC2 Managed Resource Visibility — skipping.")
        return

    try:
        current = ec2.get_managed_resource_visibility().get("Visibility", {}).get("DefaultVisibility")
        if current == "visible":
            log("✓ Managed resource visibility: already visible (account-wide)")
            return

        ec2.modify_managed_resource_visibility(DefaultVisibility="visible")
        log("✓ Managed resource visibility: hidden → visible")
        log("  Your CapacityProvider instances will now show up in the EC2")
        log("  console and in `aws ec2 describe-instances`.")
        log("  NOTE: this is an ACCOUNT-WIDE setting affecting all IAM principals,")
        log("  and cleanup.py does NOT revert it. To undo it yourself:")
        log("    aws ec2 modify-managed-resource-visibility \\")
        log(f"      --region {region} --default-visibility hidden")
    except Exception as exc:  # noqa: BLE001
        # Most likely ec2:ModifyManagedResourceVisibility is not granted. Worth a
        # warning, not worth failing a deploy over — the fallback flag works.
        log(f"  Could not set managed resource visibility ({type(exc).__name__}).")
        log("  Not fatal. Your CP instances will be hidden from describe-instances;")
        log("  add --include-managed-resources when you look for them.")


REGION = resolve_region()
ACCOUNT = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
BUCKET = f"agentcore-cp-samples-{ACCOUNT}-{REGION}"
ZIP_KEY = f"{NAME}/agent.zip"
ECR_REPO = "agentcore-cp-samples/basic-http"
IMAGE_TAG = NAME
IMAGE_URI = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{ECR_REPO}:{IMAGE_TAG}"
ROLE_NAME = "agentcore-cp-samples-runtime-role"
CODEBUILD_ROLE = "agentcore-cp-samples-codebuild-role"
CODEBUILD_PROJECT = f"agentcore-cp-samples-{OPERATING_SYSTEM.lower().replace('_', '-')}"


# ══════════════════════════════════════════════════════════════════════════
# 1. IAM
# ══════════════════════════════════════════════════════════════════════════
def ensure_roles() -> tuple[str, str]:
    """
    Resolve the two roles this path needs. They are not the same thing.

    The runtime execution role is what your agent's code runs as (Bedrock +
    CloudWatch Logs + ECR read); it is created here if absent. The operator role
    is what the AgentCore service assumes to create EC2 instances and EBS volumes
    IN YOUR ACCOUNT — the part with no serverless equivalent. Nothing is created
    for that one: it defaults to the role you are already running as. See
    `ensure_operator_role`.
    """
    iam = boto3.client("iam")

    # NOTE ON THE TRUST POLICY
    # `bedrock-agentcore.amazonaws.com` is the principal that assumes your
    # execution role. Get it wrong and CreateAgentRuntime fails with:
    #     "Role validation failed for '<role arn>'. Please verify that the role
    #      exists and its trust policy allows assumption by this service"
    # which reads like the role is missing rather than mis-trusted.
    #
    runtime_trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": SERVICE_PRINCIPAL},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": ACCOUNT}},
            }
        ],
    }
    runtime_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": [
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                "Resource": f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/bedrock-agentcore/*",
            },
            {
                "Effect": "Allow",
                "Action": ["ecr:GetAuthorizationToken", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
                "Resource": "*",
            },
        ],
    }
    created = False
    try:
        iam.create_role(
            RoleName=ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(runtime_trust),
            Description="Execution role for AgentCore CapacityProvider samples",
        )
        created = True
    except iam.exceptions.EntityAlreadyExistsException:
        # Refresh the trust policy too, so a role left over from an older run
        # (or from a different sample) is corrected rather than silently reused.
        iam.update_assume_role_policy(RoleName=ROLE_NAME, PolicyDocument=json.dumps(runtime_trust))
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        PolicyName="runtime-access",
        PolicyDocument=json.dumps(runtime_policy),
    )
    role_arn = f"arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}"
    log(f"✓ Runtime execution role: {role_arn}")

    operator_arn = ensure_operator_role(iam)

    if created:
        log("  (waiting 10s for IAM propagation)")
        time.sleep(10)
    return role_arn, operator_arn


def caller_role_name() -> str:
    """
    The name of the IAM role these credentials are already running as.

    Beats hardcoding a name: `arn:aws:iam::<acct>:role/Admin` was wrong in the
    first account we tried it in, because that account's role is called `admin`
    and role ARNs are case-sensitive. The failure is also badly misleading —
    the service reports

        ValidationException: Role validation failed for the operator role.
        Please verify that the role exists and its trust policy allows
        assumption by this service

    which reads like a trust problem when the role simply is not there.
    """
    arn = boto3.client("sts", region_name=REGION).get_caller_identity()["Arn"]
    # arn:aws:sts::<acct>:assumed-role/<role-name>/<session-name>
    tail = arn.split(":")[-1].split("/")
    if tail[0] == "assumed-role" and len(tail) >= 2:
        return tail[1]
    sys.exit(
        f"These credentials are not a role ({arn}).\n"
        "A CapacityProvider operator role has to be assumable by "
        f"{SERVICE_PRINCIPAL}, which an IAM user cannot be. Run as a role, or "
        "point at one explicitly:\n"
        "    export CP_OPERATOR_ROLE_ARN=arn:aws:iam::<acct>:role/<role>"
    )


def ensure_operator_role(iam) -> str:
    """
    Resolve the CP operator role — the role AgentCore assumes to launch, tag and
    terminate EC2 instances, network interfaces and volumes IN YOUR ACCOUNT.

    By default this is *your own* role, the one these credentials are running
    as, looked up through STS rather than guessed by name. Nothing is created:
    if you are already an admin you already have the EC2, EBS and Auto Scaling
    permissions the service needs.

    The one thing that does have to be true is the trust policy — the service
    assumes this role, so `bedrock-agentcore.amazonaws.com` must be allowed to.
    If it is not, this appends a statement saying so (appends, never replaces,
    so existing trust is left intact) and says what it changed.

    Override with CP_OPERATOR_ROLE_ARN to use a role you have scoped down
    yourself, which is what you should do outside a sample — see the README.
    """
    override = os.environ.get("CP_OPERATOR_ROLE_ARN")
    if override:
        log(f"✓ CP operator role:       {override}  (CP_OPERATOR_ROLE_ARN)")
        return override

    name = caller_role_name()
    # get_role, rather than building the ARN by hand: a role with a path has an
    # ARN of `role/<path>/<name>`, and the assumed-role ARN does not show the
    # path. This also fails loudly if the name does not resolve.
    try:
        role = iam.get_role(RoleName=name)["Role"]
    except iam.exceptions.NoSuchEntityException:
        sys.exit(
            f"Could not find the role you are running as ({name}).\n"
            "Point at an operator role explicitly:\n"
            "    export CP_OPERATOR_ROLE_ARN=arn:aws:iam::<acct>:role/<role>"
        )
    operator_arn = role["Arn"]

    # botocore URL-decodes this into a dict for us.
    trust = role["AssumeRolePolicyDocument"]
    statements = trust.get("Statement", [])

    def trusts_service(stmt: dict) -> bool:
        if stmt.get("Effect") != "Allow":
            return False
        services = stmt.get("Principal", {}).get("Service", [])
        if isinstance(services, str):
            services = [services]
        return SERVICE_PRINCIPAL in services

    if not any(trusts_service(s) for s in statements):
        statements.append(
            {
                "Effect": "Allow",
                "Principal": {"Service": SERVICE_PRINCIPAL},
                "Action": "sts:AssumeRole",
            }
        )
        trust["Statement"] = statements
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
        log(f"  Added {SERVICE_PRINCIPAL} to {name}'s trust policy")
        log("  (this is a change to YOUR role — cleanup.py does not undo it)")

    log(f"✓ CP operator role:       {operator_arn}  (your own role)")
    return operator_arn


# ══════════════════════════════════════════════════════════════════════════
# 2 + 3. S3 bucket and the zip artifact
# ══════════════════════════════════════════════════════════════════════════
def build_and_upload_zip() -> None:
    """
    Vendor dependencies for the TARGET architecture and upload a zip.

    The critical flag is --python-platform: your laptop is probably not the
    same architecture as the instance. Wheels must match the instance, not
    the machine building the zip. Code artifacts do NOT get a pip install on
    the instance — whatever is in the zip is what you get.
    """
    s3 = boto3.client("s3", region_name=REGION)
    try:
        if REGION == "us-east-1":
            s3.create_bucket(Bucket=BUCKET)
        else:
            s3.create_bucket(
                Bucket=BUCKET,
                CreateBucketConfiguration={"LocationConstraint": REGION},
            )
        log(f"✓ Created bucket {BUCKET}")
    except (s3.exceptions.BucketAlreadyOwnedByYou, s3.exceptions.BucketAlreadyExists):
        log(f"✓ Bucket exists  {BUCKET}")

    s3.put_public_access_block(
        Bucket=BUCKET,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )

    build = HERE / ".build"
    if build.exists():
        shutil.rmtree(build)
    build.mkdir()

    log(f"  Vendoring deps for {_ARCH['pip_platform']} / python{PYTHON_VERSION}...")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python-platform",
            _ARCH["pip_platform"],
            "--python-version",
            PYTHON_VERSION,
            "--target",
            str(build),
            "--only-binary",
            ":all:",
            "-r",
            str(AGENT_DIR / "requirements.txt"),
        ],
        check=True,
        capture_output=True,
    )
    shutil.copy(AGENT_DIR / "agent.py", build)

    archive = HERE / ".agent"
    zip_path = shutil.make_archive(str(archive), "zip", root_dir=build)
    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    log(f"  Packaged {size_mb:.1f} MiB")

    s3.upload_file(zip_path, BUCKET, ZIP_KEY)
    log(f"✓ Uploaded s3://{BUCKET}/{ZIP_KEY}")

    os.remove(zip_path)
    shutil.rmtree(build)


# ══════════════════════════════════════════════════════════════════════════
# 4. Container artifact
# ══════════════════════════════════════════════════════════════════════════
DOCKERFILE = """\
FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY agent.py .

# AgentCore sets PORT; the app honours it. 8080 is the documented default
# for the HTTP protocol.
EXPOSE 8080
CMD ["python", "agent.py"]
"""


def build_and_push_image() -> None:
    """
    Build the image for the instance architecture and push it to ECR.

    We build with CodeBuild rather than local `docker build` so this works
    on any laptop, and so the image is built natively for the target
    architecture instead of under emulation.
    """
    ecr = boto3.client("ecr", region_name=REGION)
    try:
        ecr.create_repository(repositoryName=ECR_REPO)
        log(f"✓ Created ECR repo {ECR_REPO}")
    except ecr.exceptions.RepositoryAlreadyExistsException:
        log(f"✓ ECR repo exists  {ECR_REPO}")

    iam = boto3.client("iam")
    cb_trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "codebuild.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    cb_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": "*",
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:GetObjectVersion"],
                "Resource": f"arn:aws:s3:::{BUCKET}/*",
            },
            {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
            {
                "Effect": "Allow",
                "Action": [
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "ecr:PutImage",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                ],
                "Resource": f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/{ECR_REPO}",
            },
        ],
    }
    fresh = False
    try:
        iam.create_role(RoleName=CODEBUILD_ROLE, AssumeRolePolicyDocument=json.dumps(cb_trust))
        fresh = True
    except iam.exceptions.EntityAlreadyExistsException:
        pass
    iam.put_role_policy(
        RoleName=CODEBUILD_ROLE,
        PolicyName="build-access",
        PolicyDocument=json.dumps(cb_policy),
    )
    if fresh:
        time.sleep(10)

    # Upload the build context.
    ctx = HERE / ".ctx"
    if ctx.exists():
        shutil.rmtree(ctx)
    ctx.mkdir()
    shutil.copy(AGENT_DIR / "agent.py", ctx)
    shutil.copy(AGENT_DIR / "requirements.txt", ctx)
    (ctx / "Dockerfile").write_text(DOCKERFILE)
    src_zip = shutil.make_archive(str(HERE / ".ctx-src"), "zip", root_dir=ctx)
    boto3.client("s3", region_name=REGION).upload_file(src_zip, BUCKET, f"{NAME}/context.zip")
    os.remove(src_zip)
    shutil.rmtree(ctx)

    buildspec = "\n".join(
        [
            "version: 0.2",
            "phases:",
            "  pre_build:",
            "    commands:",
            (
                f"      - aws ecr get-login-password --region {REGION} | "
                f"docker login --username AWS --password-stdin "
                f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"
            ),
            "  build:",
            "    commands:",
            (f"      - docker build --platform {_ARCH['docker_platform']} -t {IMAGE_URI} ."),
            "  post_build:",
            "    commands:",
            f"      - docker push {IMAGE_URI}",
        ]
    )

    cbc = boto3.client("codebuild", region_name=REGION)
    project = CODEBUILD_PROJECT
    spec = {
        "source": {
            "type": "S3",
            "location": f"{BUCKET}/{NAME}/context.zip",
            "buildspec": buildspec,
        },
        "artifacts": {"type": "NO_ARTIFACTS"},
        "environment": {
            "type": _ARCH["codebuild_type"],
            "image": _ARCH["codebuild_image"],
            "computeType": "BUILD_GENERAL1_SMALL",
            "privilegedMode": True,
        },
        "serviceRole": f"arn:aws:iam::{ACCOUNT}:role/{CODEBUILD_ROLE}",
    }
    if cbc.batch_get_projects(names=[project])["projects"]:
        cbc.update_project(name=project, **spec)
    else:
        cbc.create_project(name=project, **spec)

    log(f"  Building image on {_ARCH['codebuild_type']} (3–6 min)...")
    build_id = cbc.start_build(projectName=project)["build"]["id"]
    while True:
        status = cbc.batch_get_builds(ids=[build_id])["builds"][0]["buildStatus"]
        if status == "SUCCEEDED":
            break
        if status != "IN_PROGRESS":
            sys.exit(f"CodeBuild {status}. Logs:\n  aws codebuild batch-get-builds --ids {build_id} --region {REGION}")
        time.sleep(10)
    log(f"✓ Pushed {IMAGE_URI}")


# ══════════════════════════════════════════════════════════════════════════
# 5. The CapacityProvider
# ══════════════════════════════════════════════════════════════════════════
def default_network() -> tuple[str, str]:
    """Pick the default VPC's first subnet and its default security group."""
    if os.environ.get("CP_SUBNET_ID") and os.environ.get("CP_SECURITY_GROUP_ID"):
        return os.environ["CP_SUBNET_ID"], os.environ["CP_SECURITY_GROUP_ID"]
    ec2 = boto3.client("ec2", region_name=REGION)
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"]
    if not vpcs:
        sys.exit("No default VPC found. Set CP_SUBNET_ID and CP_SECURITY_GROUP_ID.")
    vpc_id = vpcs[0]["VpcId"]
    subnet = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"][0]["SubnetId"]
    sg = ec2.describe_security_groups(
        Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "group-name", "Values": ["default"]},
        ]
    )["SecurityGroups"][0]["GroupId"]
    return subnet, sg


def create_capacity_provider(agentcore, operator_role_arn: str) -> dict:
    """
    Create the CapacityProvider — the one API with no serverless equivalent.

    This declares the fleet your agents will run on: which instance types are
    allowed, which VPC/subnet they join, and when idle instances shut down.
    Instances are launched into YOUR account, on demand, when a session
    starts. There are none running right after this call returns.

    Required members are exactly three: `name`, `permissionsConfiguration` and
    `computeConfiguration`. `description`, `tags` and `clientToken` are optional.

    Terminal state is READY — not ACTIVE. The full status enum is CREATING,
    CREATE_FAILED, UPDATING, UPDATE_FAILED, READY, DELETING, DELETE_FAILED. There
    is no waiter for CapacityProviders (`client.waiter_names` lists only Memory
    and Policy waiters), so this polls.

    NOTE: a CapacityProvider is immutable except for its description —
    `update_capacity_provider` takes `description` and nothing else. Changing the
    machine shape means creating a new one, by design, so a running fleet's shape
    cannot shift under you.
    """
    subnet, sg = default_network()
    log(f"  VPC: subnet={subnet} sg={sg}")
    log(f"  Fleet: {INSTANCE_TYPE} / {OPERATING_SYSTEM}")

    resp = agentcore.create_capacity_provider(
        name=NAME,
        description="Basic HTTP sample — one CP hosting a zip and a container agent",
        permissionsConfiguration={"capacityProviderOperatorRoleArn": operator_role_arn},
        computeConfiguration={
            "ec2Configuration": {
                "launchTemplateSource": {
                    "launchParameters": {
                        "operatingSystem": OPERATING_SYSTEM,
                        "instanceRequirements": {"allowedInstanceTypes": [INSTANCE_TYPE]},
                    }
                },
                "vpcConfiguration": {
                    "subnets": [subnet],
                    "securityGroups": [sg],
                },
                "lifecycleConfiguration": {
                    "idleInstanceTimeout": IDLE_TIMEOUT,
                    "maxLifetime": MAX_LIFETIME,
                },
            }
        },
    )
    cp_id = resp["capacityProviderId"]
    cp_arn = resp["capacityProviderArn"]

    for _ in range(60):
        got = agentcore.get_capacity_provider(capacityProviderId=cp_id)
        status = got["status"]
        if status == "READY":
            log(f"✓ CapacityProvider READY: {cp_id}")
            return {"id": cp_id, "arn": cp_arn}
        if "FAILED" in status:
            # statusReason/statusCode are on GetCapacityProvider, not on Create.
            sys.exit(
                f"CapacityProvider {status}: {got.get('statusReason') or got.get('statusCode') or 'no reason given'}"
            )
        time.sleep(5)
    sys.exit("CapacityProvider did not become READY in 5 minutes")


# ══════════════════════════════════════════════════════════════════════════
# 6. The agent runtimes
# ══════════════════════════════════════════════════════════════════════════
def create_runtime(agentcore, name: str, artifact: dict, cp_arn: str, role_arn: str, kind: str) -> dict:
    """
    Create one agent runtime bound to the CapacityProvider.

    The ONLY difference from a serverless CreateAgentRuntime call:

        capacityProviderConfiguration={"capacityProviderArn": ...}
        # and NO networkConfiguration — see below

    networkConfiguration and capacityProviderConfiguration are mutually
    exclusive. The live API rejects both together with:
        "NetworkConfiguration is not allowed when
         capacityProviderConfiguration is specified"
    That is because the network is declared once, on the CapacityProvider.
    """
    resp = agentcore.create_agent_runtime(
        agentRuntimeName=name,
        roleArn=role_arn,
        agentRuntimeArtifact=artifact,
        protocolConfiguration={"serverProtocol": "HTTP"},
        # ── The one line that moves this agent onto your own EC2 fleet ──
        capacityProviderConfiguration={"capacityProviderArn": cp_arn},
        lifecycleConfiguration={
            "idleRuntimeSessionTimeout": IDLE_TIMEOUT,
            "maxLifetime": MAX_LIFETIME,
        },
        environmentVariables={"ARTIFACT_KIND": kind, "MODEL_ID": MODEL_ID},
        description=f"Basic HTTP sample ({kind} artifact) on a CapacityProvider",
    )
    rid = resp["agentRuntimeId"]

    for _ in range(60):
        got = agentcore.get_agent_runtime(agentRuntimeId=rid)
        status = got["status"]
        if status == "READY":
            log(f"✓ Runtime READY ({kind}): {rid}")
            return {"id": rid, "arn": got["agentRuntimeArn"]}
        if "FAILED" in status:
            sys.exit(f"Runtime {status}: {got.get('failureReason')}")
        time.sleep(5)
    sys.exit("Runtime did not become READY in 5 minutes")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--only",
        choices=["zip", "container"],
        help="deploy just one artifact type (default: both)",
    )
    args = ap.parse_args()
    want_zip = args.only in (None, "zip")
    want_container = args.only in (None, "container")

    # One client for both the CapacityProvider APIs and the runtime APIs.
    agentcore = boto3.client(CONTROL_SERVICE, region_name=REGION)
    require_capacity_provider_support(agentcore)
    log(f"Region: {REGION}   boto3: {boto3.__version__}")

    step("1. IAM roles")
    role_arn, operator_arn = ensure_roles()

    if want_zip:
        step("2. Zip artifact → S3")
        build_and_upload_zip()

    if want_container:
        step("3. Container artifact → ECR")
        build_and_push_image()

    step("4. CapacityProvider (your own EC2 fleet)")
    # Before the fleet exists, so the instances it later launches are visible
    # from their first moment rather than retroactively.
    enable_managed_resource_visibility(REGION)
    cp_info = create_capacity_provider(agentcore, operator_arn)

    # Everything cleanup.py needs to delete, and everything invoke.py needs to
    # call. Keys are flat and explicit so cleanup cannot silently miss a
    # resource and leave EC2 instances running in your account.
    config = {
        "name": NAME,
        "region": REGION,
        "capacityProviderId": cp_info["id"],
        "capacityProviderArn": cp_info["arn"],
        "instanceType": INSTANCE_TYPE,
        "operatingSystem": OPERATING_SYSTEM,
        "bucket": BUCKET,
        "zipKey": ZIP_KEY,
        "runtimeRole": ROLE_NAME,
        "runtimes": {},
    }
    if want_container:
        config["imageUri"] = IMAGE_URI
        config["ecrRepository"] = ECR_REPO
        config["codeBuildProject"] = CODEBUILD_PROJECT
        config["codeBuildRole"] = CODEBUILD_ROLE

    step("5. Agent runtimes on that CapacityProvider")
    if want_zip:
        config["runtimes"]["zip"] = create_runtime(
            agentcore,
            f"{NAME}_zip",
            {
                "codeConfiguration": {
                    "code": {"s3": {"bucket": BUCKET, "prefix": ZIP_KEY}},
                    "runtime": PYTHON_RUNTIME,
                    "entryPoint": ["agent.py"],
                }
            },
            cp_info["arn"],
            role_arn,
            "zip",
        )
    if want_container:
        config["runtimes"]["container"] = create_runtime(
            agentcore,
            f"{NAME}_container",
            {"containerConfiguration": {"containerUri": IMAGE_URI}},
            cp_info["arn"],
            role_arn,
            "container",
        )

    CONFIG_FILE.write_text(json.dumps(config, indent=2))

    step("Deployed")
    log(f"  CapacityProvider : {cp_info['id']}")
    log(f"  Fleet            : {INSTANCE_TYPE} ({OPERATING_SYSTEM})")
    for kind, rt in config["runtimes"].items():
        log(f"  Runtime ({kind:9s}): {rt['id']}")
    log(f"\n  Config written to {CONFIG_FILE.name}")
    log("\n  Next:  python invoke.py      # invoke both, compare the machines")
    log("         python cleanup.py     # delete everything")
    log("\n  No EC2 instances are running yet — the first invoke starts one.")


if __name__ == "__main__":
    main()
