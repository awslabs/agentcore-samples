"""
Deploy an MCP server onto a CapacityProvider.

  1. IAM      — execution role for the runtime, and the CP operator role
  2. S3       — bucket for the zip artifact
  3. Zip      — vendor arm64 deps, zip, upload
  4. CP       — the EC2 fleet
  5. Runtime  — serverProtocol=MCP (the one line that differs from Sample 1)
  6. Config   — write cp_config.json for invoke.py / cleanup.py

THE ONE LINE THAT MAKES IT MCP
------------------------------
    protocolConfiguration={"serverProtocol": "MCP"}

The `serverProtocol` enum accepts MCP, HTTP, A2A and AGUI (verified in the
service model). That single field changes the contract the runtime expects from
your artifact:

    HTTP  →  port 8080, POST /invocations, GET /ping
    MCP   →  port 8000, POST /mcp  (JSON-RPC, streamable-HTTP)
    A2A   →  port 9000, POST /

The ports are fixed by the AgentCore Runtime service contract, not by preference.
Get the port wrong and the runtime cannot reach your server at all.

PLAIN BOTO3, ONE CLIENT
-----------------------
There is no `scripts/` helper any more, and no service-model file to install.
`boto3.client("bedrock-agentcore-control")` carries both the CapacityProvider
APIs and the agent-runtime APIs — they are the same service (API version
2023-06-05). See `require_capacity_provider_support` for the version floor, and
Sample 1's README → "Same client".

ZIP ONLY
--------
This sample deploys a zip artifact and nothing else — no ECR, no CodeBuild, no
Dockerfile. The container path is the same in every respect except the artifact;
Sample 1 shows both side by side if you want to see it.

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
# Names are matched against [a-zA-Z][a-zA-Z0-9_]{0,47} — no hyphens.
NAME = f"basic_mcp_{STAMP}"

# ── Compute shape ──────────────────────────────────────────────────────────
# Graviton (ARM64) by default: cheaper, and the instance type we have verified.
# Switch both values together to run x86.
OPERATING_SYSTEM = os.environ.get("CP_OS", "LINUX_ARM64")
INSTANCE_TYPE = os.environ.get("CP_INSTANCE_TYPE", "m6g.large")

_ARCH = {
    "LINUX_ARM64": {"pip_platform": "aarch64-manylinux2014"},
    "LINUX_X86_64": {"pip_platform": "x86_64-manylinux2014"},
}[OPERATING_SYSTEM]

PYTHON_RUNTIME = "PYTHON_3_12"
PYTHON_VERSION = "3.12"

# Lifecycle. maxLifetime's ceiling is 1209600s (14 days), and the service
# requires maxLifetime >= idleInstanceTimeout.
IDLE_INSTANCE_TIMEOUT = int(os.environ.get("IDLE_INSTANCE_TIMEOUT", "900"))
IDLE_SESSION_TIMEOUT = int(os.environ.get("IDLE_SESSION_TIMEOUT", "900"))
MAX_LIFETIME = int(os.environ.get("MAX_LIFETIME", "86400"))

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
ROLE_NAME = "agentcore-cp-samples-runtime-role"


# ══════════════════════════════════════════════════════════════════════════
# 1. IAM
# ══════════════════════════════════════════════════════════════════════════
def ensure_roles() -> tuple[str, str]:
    """
    Resolve the two roles this path needs. They are not the same thing.

    The runtime execution role is what your server's code runs as; it is created
    here if absent. The operator role is what the AgentCore service assumes to
    create EC2 instances and EBS volumes IN YOUR ACCOUNT — the part with no
    serverless equivalent. Nothing is created for that one: it defaults to the
    role you are already running as. See `ensure_operator_role`.

    Note there is no bedrock:InvokeModel in the execution policy: this MCP server
    calls no model. The client (or the agent using this server as a tool source)
    does that. Contrast Sample 1, whose agent needs it.
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
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:DescribeLogStreams",
                ],
                "Resource": f"arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/bedrock-agentcore/*",
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
        # A different policy name from Sample 1's `runtime-access`, on purpose:
        # the role is shared across the samples, and this policy grants strictly
        # less (no Bedrock). Reusing Sample 1's name would overwrite it and take
        # its model access away; a separate name leaves both attached. So if you
        # have also run Sample 1, this server's role carries its Bedrock grant
        # too — harmless, since the MCP server never calls a model, but it means
        # the *role* is not minimal even though this policy is.
        PolicyName="mcp-access",
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
    yourself, which is what you should do outside a sample — see Sample 1's
    README → "The two roles".
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
    Vendor deps for the INSTANCE architecture, not this laptop's.

    The critical flag is --python-platform: your laptop is probably not the same
    architecture as the instance. Code artifacts do NOT get a pip install on the
    instance — whatever is in the zip is what you get, so the wheels have to
    match the instance.
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

    This declares the fleet your server will run on: which instance types are
    allowed, which VPC/subnet they join, and when idle instances shut down.
    Instances are launched into YOUR account, on demand, when a session starts.
    There are none running right after this call returns.

    Required members are exactly three: `name`, `permissionsConfiguration` and
    `computeConfiguration`. `description`, `tags` and `clientToken` are optional.

    Terminal state is READY — not ACTIVE. The full status enum is CREATING,
    CREATE_FAILED, UPDATING, UPDATE_FAILED, READY, DELETING, DELETE_FAILED. There
    is no waiter for CapacityProviders, so this polls.

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
        description="Basic MCP sample — MCP server on your own EC2 fleet",
        permissionsConfiguration={"capacityProviderOperatorRoleArn": operator_role_arn},
        computeConfiguration={
            "ec2Configuration": {
                "launchTemplateSource": {
                    "launchParameters": {
                        "operatingSystem": OPERATING_SYSTEM,
                        "instanceRequirements": {"allowedInstanceTypes": [INSTANCE_TYPE]},
                    }
                },
                "vpcConfiguration": {"subnets": [subnet], "securityGroups": [sg]},
                "lifecycleConfiguration": {
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
                f"CapacityProvider {status}: {got.get('statusReason') or got.get('statusCode') or 'no reason given'}"
            )
        time.sleep(5)
    sys.exit("CapacityProvider did not become READY in 5 minutes")


# ══════════════════════════════════════════════════════════════════════════
# 5. The agent runtime
# ══════════════════════════════════════════════════════════════════════════
def create_runtime(agentcore, cp_arn: str, role_arn: str) -> dict:
    """
    Create the MCP runtime bound to the CapacityProvider.

    Two members carry the whole sample:

        protocolConfiguration={"serverProtocol": "MCP"}          # the contract
        capacityProviderConfiguration={"capacityProviderArn": …}  # your fleet

    `networkConfiguration` and `capacityProviderConfiguration` are mutually
    exclusive — the network is declared once, on the CapacityProvider.
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
        # ── The line that makes this an MCP server rather than an HTTP agent ──
        protocolConfiguration={"serverProtocol": "MCP"},
        # ── The line that moves it onto your own EC2 fleet ──
        capacityProviderConfiguration={"capacityProviderArn": cp_arn},
        lifecycleConfiguration={
            "idleRuntimeSessionTimeout": IDLE_SESSION_TIMEOUT,
            "maxLifetime": MAX_LIFETIME,
        },
        # The server reports this back through its `whoami` tool, so you can see
        # from the client side which machine shape answered.
        environmentVariables={"FLEET_INSTANCE_TYPE": INSTANCE_TYPE},
        description="MCP server on a CapacityProvider",
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

    step("3. CapacityProvider (your own EC2 fleet)")
    # Before the fleet exists, so the instances it later launches are visible
    # from their first moment rather than retroactively.
    enable_managed_resource_visibility(REGION)
    cp_info = create_capacity_provider(agentcore, operator_arn)

    step("4. Agent runtime (serverProtocol=MCP)")
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
        "serverProtocol": "MCP",
        "idleInstanceTimeout": IDLE_INSTANCE_TIMEOUT,
        "idleRuntimeSessionTimeout": IDLE_SESSION_TIMEOUT,
        "maxLifetime": MAX_LIFETIME,
        "runtimes": {"mcp": runtime},
    }
    CONFIG_FILE.write_text(json.dumps(config, indent=2))

    step("Deployed")
    log(f"  CapacityProvider : {cp_info['id']}")
    log(f"  Runtime          : {runtime['id']}")
    log("  Protocol         : MCP (port 8000, POST /mcp)")
    log(f"  Fleet            : {INSTANCE_TYPE} ({OPERATING_SYSTEM})")
    log(f"\n  Config written to {CONFIG_FILE.name}")
    log("\n  Next:  python invoke.py      # initialize, tools/list, tools/call")
    log("         python cleanup.py     # delete everything")
    log("\n  No EC2 instances are running yet — the first invoke starts one.")


if __name__ == "__main__":
    main()
