"""
Deploy a long-running async agent onto a CapacityProvider.

  1. IAM      — execution role for the runtime, plus the CP operator role
  2. S3       — bucket for the zip artifact
  3. Zip      — vendor arm64 deps, zip, upload
  4. CP       — a CapacityProvider with a DELIBERATELY SHORT idle timeout
  5. Runtime  — one agent runtime bound to it
  6. Config   — write cp_config.json for invoke.py / cleanup.py

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

ZIP ONLY
--------
No container path here, so no ECR repository, no CodeBuild project and no Docker.
`serverProtocol` is `HTTP`, exactly as in Sample 1 — the subject of this sample is
the *lifecycle*, not the protocol.

THE POINT OF THE SHORT IDLE TIMEOUT
-----------------------------------
`idleInstanceTimeout` is set to 60 seconds — the minimum the API accepts
(`min: 60, max: 1209600`). That is absurdly low for production, and chosen on
purpose: it makes the HealthyBusy contract observable in a couple of minutes
instead of a couple of hours.

With a 60s idle timeout, an instance whose agents are all idle is reclaimed
almost immediately. So if a long job keeps running across many minutes with no
invokes at all, the ONLY thing keeping its instance alive is the agent's own
`/ping` handler returning HealthyBusy. invoke.py demonstrates exactly that.

In production, set this to a realistic value (900s is the service default) and
raise `maxLifetime` toward its 1209600s (14 day) ceiling if your jobs are long.

Usage:
    python deploy.py
"""

from __future__ import annotations

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
NAME = f"async_job_{STAMP}"

# ── Compute shape ──────────────────────────────────────────────────────────
# Graviton (ARM64) by default: cheaper, and the instance type this sample was
# measured on. Switch both values together to run x86.
OPERATING_SYSTEM = os.environ.get("CP_OS", "LINUX_ARM64")
INSTANCE_TYPE = os.environ.get("CP_INSTANCE_TYPE", "m6g.large")

_ARCH = {
    "LINUX_ARM64": {"pip_platform": "aarch64-manylinux2014"},
    "LINUX_X86_64": {"pip_platform": "x86_64-manylinux2014"},
}[OPERATING_SYSTEM]

PYTHON_RUNTIME = "PYTHON_3_12"
PYTHON_VERSION = "3.12"
MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-4-5-20250929-v1:0")

# ── Lifecycle: the knobs this sample is about ─────────────────────────────────
# Verified against the service model: both fields are min 60, max 1209600.
# 60s is the floor, used here only to make the demo fast. maxLifetime must be
# >= idleInstanceTimeout.
IDLE_INSTANCE_TIMEOUT = int(os.environ.get("IDLE_INSTANCE_TIMEOUT", "60"))
IDLE_SESSION_TIMEOUT = int(os.environ.get("IDLE_SESSION_TIMEOUT", "60"))
MAX_LIFETIME = int(os.environ.get("MAX_LIFETIME", "86400"))

# The job shape, passed to the agent as environment variables. 10 x 30s = 5min,
# which is 5x the idle timeout — long enough to prove HealthyBusy is what keeps
# the instance alive.
TOTAL_STEPS = os.environ.get("TOTAL_STEPS", "10")
SECONDS_PER_STEP = os.environ.get("SECONDS_PER_STEP", "30")

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
        explicit
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or boto3.Session().region_name
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

    This sample needs it more than the others do. The whole experiment is
    "stop invoking and watch EC2 from the outside" — `invoke.py` calls
    `DescribeInstances` on a loop and reports whether the instance survived. On a
    hidden account those calls return nothing, and an instance kept alive by
    HealthyBusy is indistinguishable from one that was reclaimed. The
    demonstration silently inverts its own conclusion.

    (`invoke.py` also passes `IncludeManagedResources=True` on every
    `DescribeInstances` call, so it stays correct even with
    CP_MANAGED_VISIBILITY=skip. This setting is what makes the EC2 *console* and
    your own `aws ec2 describe-instances` agree with it.)

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
        log("  invoke.py still works — it passes IncludeManagedResources itself —")
        log("  but `aws ec2 describe-instances` will show you nothing unless you")
        log("  add --include-managed-resources.")
        return

    ec2 = boto3.client("ec2", region_name=region)

    # Older botocore has neither operation. Not fatal — an account that predates
    # the feature has nothing hidden in the first place.
    if "ModifyManagedResourceVisibility" not in ec2.meta.service_model.operation_names:
        log("  This botocore predates EC2 Managed Resource Visibility — skipping.")
        return

    try:
        current = (
            ec2.get_managed_resource_visibility()
            .get("Visibility", {})
            .get("DefaultVisibility")
        )
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
        # warning, not worth failing a deploy over — invoke.py asks for managed
        # resources explicitly, so the experiment still reads correctly.
        log(f"  Could not set managed resource visibility ({type(exc).__name__}).")
        log("  Not fatal. Your CP instances will be hidden from the console and")
        log("  from `aws ec2 describe-instances` unless you pass")
        log("  --include-managed-resources.")


REGION = resolve_region()
ACCOUNT = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
BUCKET = f"agentcore-cp-samples-{ACCOUNT}-{REGION}"
ZIP_KEY = f"{NAME}/agent.zip"
ROLE_NAME = "agentcore-cp-samples-runtime-role"


# ══════════════════════════════════════════════════════════════════════════
# 1. IAM
# ══════════════════════════════════════════════════════════════════════════
def ensure_roles() -> tuple[str, str]:
    """
    Resolve the two roles this path needs. They are not the same thing.

    The runtime execution role is what your agent's code runs as (Bedrock +
    CloudWatch Logs); it is created here if absent. The operator role is what the
    AgentCore service assumes to create EC2 instances and EBS volumes IN YOUR
    ACCOUNT — the part with no serverless equivalent. Nothing is created for that
    one: it defaults to the role you are already running as. See
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
    # No aws:SourceAccount condition here, to keep the sample minimal. Add one in
    # a real deployment — it is the standard confused-deputy guard.
    runtime_trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": SERVICE_PRINCIPAL},
                "Action": "sts:AssumeRole",
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
                # A global inference profile ("global.anthropic...") fans out to
                # foundation models in several regions, so the resource cannot be
                # pinned to one region's ARN. Narrow this in a real deployment.
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
                "Resource": "arn:aws:logs:*:*:*",
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
        iam.update_assume_role_policy(
            RoleName=ROLE_NAME, PolicyDocument=json.dumps(runtime_trust)
        )
    iam.put_role_policy(
        RoleName=ROLE_NAME,
        # The same policy name Sample 1 and Sample 3 use, on purpose: the role is
        # shared across the samples and this policy grants the same things they
        # do (Bedrock + Logs, minus Sample 1's ECR read, which only the container
        # path needs). Whichever sample runs last leaves an equivalent policy in
        # place. Sample 2 deliberately uses a different name — see its README.
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
        iam.update_assume_role_policy(
            RoleName=name, PolicyDocument=json.dumps(trust)
        )
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

    build = HERE / ".build"
    if build.exists():
        shutil.rmtree(build)
    build.mkdir()

    log(f"  Vendoring deps for {_ARCH['pip_platform']} / python{PYTHON_VERSION}...")
    subprocess.run(
        [
            "uv", "pip", "install",
            "--python-platform", _ARCH["pip_platform"],
            "--python-version", PYTHON_VERSION,
            "--target", str(build),
            "--only-binary", ":all:",
            "-r", str(AGENT_DIR / "requirements.txt"),
        ],
        check=True,
        capture_output=True,
    )
    shutil.copy(AGENT_DIR / "agent.py", build)

    zip_path = shutil.make_archive(str(HERE / ".agent"), "zip", root_dir=build)
    log(f"  Packaged {os.path.getsize(zip_path) / (1024 * 1024):.1f} MiB")
    s3.upload_file(zip_path, BUCKET, ZIP_KEY)
    log(f"✓ Uploaded s3://{BUCKET}/{ZIP_KEY}")
    os.remove(zip_path)
    shutil.rmtree(build)


# ══════════════════════════════════════════════════════════════════════════
# 4. The CapacityProvider
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
    subnet = ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )["Subnets"][0]["SubnetId"]
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

    `lifecycleConfiguration.idleInstanceTimeout` is the field this whole sample
    is about, and it is set to the API floor of 60s — see the module docstring.

    Terminal state is READY — not ACTIVE. The full status enum is CREATING,
    CREATE_FAILED, UPDATING, UPDATE_FAILED, READY, DELETING, DELETE_FAILED. There
    is no waiter for CapacityProviders (`client.waiter_names` lists only Memory
    and Policy waiters), so this polls.

    NOTE: a CapacityProvider is immutable except for its description —
    `update_capacity_provider` takes `description` and nothing else. So the idle
    timeout cannot be raised after the fact: to run this experiment with
    different numbers, deploy a new one.
    """
    subnet, sg = default_network()
    log(f"  VPC: subnet={subnet} sg={sg}")
    log(f"  Fleet: {INSTANCE_TYPE} / {OPERATING_SYSTEM}")

    resp = agentcore.create_capacity_provider(
        name=NAME,
        description="Async long-running sample — short idle timeout on purpose",
        permissionsConfiguration={
            "capacityProviderOperatorRoleArn": operator_role_arn
        },
        computeConfiguration={
            "ec2Configuration": {
                "launchTemplateSource": {
                    "launchParameters": {
                        "operatingSystem": OPERATING_SYSTEM,
                        "instanceRequirements": {
                            "allowedInstanceTypes": [INSTANCE_TYPE]
                        },
                    }
                },
                "vpcConfiguration": {"subnets": [subnet], "securityGroups": [sg]},
                "lifecycleConfiguration": {
                    # 60s: the API minimum. Short on purpose — see module docstring.
                    "idleInstanceTimeout": IDLE_INSTANCE_TIMEOUT,
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
                f"CapacityProvider {status}: "
                f"{got.get('statusReason') or got.get('statusCode') or 'no reason given'}"
            )
        time.sleep(5)
    sys.exit("CapacityProvider did not become READY in 5 minutes")


# ══════════════════════════════════════════════════════════════════════════
# 5. The agent runtime
# ══════════════════════════════════════════════════════════════════════════
def create_runtime(agentcore, cp_arn: str, role_arn: str) -> dict:
    """
    Create the agent runtime bound to the CapacityProvider.

    The ONLY difference from a serverless CreateAgentRuntime call:

        capacityProviderConfiguration={"capacityProviderArn": ...}
        # and NO networkConfiguration — see below

    networkConfiguration and capacityProviderConfiguration are mutually
    exclusive. The live API rejects both together with:
        "NetworkConfiguration is not allowed when
         capacityProviderConfiguration is specified"
    That is because the network is declared once, on the CapacityProvider.

    Note the second `lifecycleConfiguration`, which is NOT the same one the
    CapacityProvider got. `idleRuntimeSessionTimeout` governs how long an idle
    *session* survives; `idleInstanceTimeout` on the CP governs how long an idle
    *instance* survives. An instance hosts sessions, so they are different
    clocks — see the README.

    The job shape (`TOTAL_STEPS`, `SECONDS_PER_STEP`) goes in as environment
    variables, so the experiment can be re-run at a different length without
    rebuilding the zip.
    """
    resp = agentcore.create_agent_runtime(
        agentRuntimeName=NAME,
        roleArn=role_arn,
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {"s3": {"bucket": BUCKET, "prefix": ZIP_KEY}},
                "runtime": PYTHON_RUNTIME,
                "entryPoint": ["agent.py"],
            }
        },
        protocolConfiguration={"serverProtocol": "HTTP"},
        # ── The one line that moves this agent onto your own EC2 fleet ──
        capacityProviderConfiguration={"capacityProviderArn": cp_arn},
        lifecycleConfiguration={
            "idleRuntimeSessionTimeout": IDLE_SESSION_TIMEOUT,
            "maxLifetime": MAX_LIFETIME,
        },
        environmentVariables={
            "MODEL_ID": MODEL_ID,
            "TOTAL_STEPS": TOTAL_STEPS,
            "SECONDS_PER_STEP": SECONDS_PER_STEP,
        },
        description="Async long-running agent on a CapacityProvider",
    )
    rid = resp["agentRuntimeId"]

    for _ in range(60):
        got = agentcore.get_agent_runtime(agentRuntimeId=rid)
        status = got["status"]
        if status == "READY":
            log(f"✓ Runtime READY: {rid}")
            return {"id": rid, "arn": got["agentRuntimeArn"]}
        if "FAILED" in status:
            sys.exit(f"Runtime {status}: {got.get('failureReason')}")
        time.sleep(5)
    sys.exit("Runtime did not become READY in 5 minutes")


def main() -> None:
    # One client for both the CapacityProvider APIs and the runtime APIs.
    agentcore = boto3.client(CONTROL_SERVICE, region_name=REGION)
    require_capacity_provider_support(agentcore)
    log(f"Region: {REGION}   boto3: {boto3.__version__}")

    step("1. IAM roles")
    role_arn, operator_arn = ensure_roles()

    step("2. Zip artifact → S3")
    build_and_upload_zip()

    step("3. CapacityProvider (idle timeout 60s — deliberately short)")
    # Before the fleet exists, so the instances it later launches are visible
    # from their first moment rather than retroactively. This matters more here
    # than in the other samples: the experiment IS an EC2 observation.
    enable_managed_resource_visibility(REGION)
    cp_info = create_capacity_provider(agentcore, operator_arn)

    step("4. Agent runtime")
    runtime = create_runtime(agentcore, cp_info["arn"], role_arn)

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
        "idleInstanceTimeout": IDLE_INSTANCE_TIMEOUT,
        "idleRuntimeSessionTimeout": IDLE_SESSION_TIMEOUT,
        "maxLifetime": MAX_LIFETIME,
        "totalSteps": int(TOTAL_STEPS),
        "secondsPerStep": int(SECONDS_PER_STEP),
        "runtimes": {"zip": runtime},
    }
    CONFIG_FILE.write_text(json.dumps(config, indent=2))

    step("Deployed")
    log(f"  CapacityProvider     : {cp_info['id']}")
    log(f"  Runtime              : {runtime['id']}")
    log(f"  Fleet                : {INSTANCE_TYPE} ({OPERATING_SYSTEM})")
    log(f"  idleInstanceTimeout  : {IDLE_INSTANCE_TIMEOUT}s")
    log(f"  Job                  : {TOTAL_STEPS} steps x {SECONDS_PER_STEP}s")
    log(f"\n  Config written to {CONFIG_FILE.name}")
    log("\n  Next:  python invoke.py       # start a job, watch it survive the idle timeout")
    log("         python cleanup.py      # delete everything")
    log("\n  No EC2 instances are running yet — the first invoke starts one.")


if __name__ == "__main__":
    main()
