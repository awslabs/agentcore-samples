# Basic HTTP agent on a CapacityProvider — zip and container

A Strands agent on Amazon Bedrock AgentCore Runtime, running on **your own EC2
instances** instead of AgentCore's serverless compute.

The same `agent/agent.py` is deployed twice, from the same CapacityProvider: once
as a **zip** artifact and once as a **container image**. That is the point of the
sample — artifact type and compute type are independent choices, and the agent
code does not change.

## What a CapacityProvider is

By default, AgentCore Runtime runs your agent on serverless compute that AWS
manages. A **CapacityProvider** runs it on EC2 instances **in your own account**,
launched and reaped for you.

Choose it when you need something serverless does not give you: a specific
instance type, GPUs, sessions that live for days, a persistent local filesystem,
or a fleet inside your own VPC.

## Files

| File | What it does |
|---|---|
| [agent/agent.py](agent/agent.py) | The agent. One file, used by **both** deployments. |
| [agent/requirements.txt](agent/requirements.txt) | `bedrock-agentcore` + `strands-agents`. |
| [deploy.py](deploy.py) | IAM → zip→S3 → image→ECR → CapacityProvider → 2 runtimes. |
| [invoke.py](invoke.py) | Invokes both, prints the machine each landed on, times cold vs warm. |
| [cleanup.py](cleanup.py) | Deletes everything, including the EC2 fleet. |

Three scripts, no helper module and no service model to install. Two clients:
`bedrock-agentcore-control` for the control plane and `bedrock-agentcore` for the
data plane. No `endpoint_url` anywhere — endpoints resolve from the region.

## One client for the control plane

The CapacityProvider APIs and the agent-runtime APIs live in the **same service**,
so one client does both:

```python
agentcore = boto3.client("bedrock-agentcore-control", region_name=REGION)

agentcore.create_capacity_provider(...)     # the fleet
agentcore.get_capacity_provider(...)
agentcore.list_capacity_providers(...)
agentcore.update_capacity_provider(...)     # description only
agentcore.delete_capacity_provider(...)
agentcore.list_agent_runtime_versions_by_capacity_provider(...)

agentcore.create_agent_runtime(...)         # the agent, with capacityProviderConfiguration
agentcore.get_agent_runtime(...)
agentcore.delete_agent_runtime(...)
```

`deploy.py` checks the operations exist up front, so an old boto3 fails with a
clear message instead of an `AttributeError` halfway through.

## How it works

### The one line that moves you off serverless

A CapacityProvider runtime is an ordinary `CreateAgentRuntime` plus one member:

```python
agentcore.create_agent_runtime(
    agentRuntimeName=name,
    roleArn=role_arn,
    agentRuntimeArtifact=artifact,
    protocolConfiguration={"serverProtocol": "HTTP"},
    capacityProviderConfiguration={"capacityProviderArn": cp_arn},  # ← this
    lifecycleConfiguration={"idleRuntimeSessionTimeout": 900, "maxLifetime": 86400},
    environmentVariables={"ARTIFACT_KIND": kind, "MODEL_ID": MODEL_ID},
)
```

`capacityProviderConfiguration` and `networkConfiguration` are **mutually
exclusive** — passing both is rejected, and omitting both gives
`NetworkConfiguration is required`. The network belongs to the CapacityProvider:
you declare the VPC, subnets and security groups once when you create it, and
every runtime bound to it inherits them.

### The two artifacts

Both must be built for the **instance** architecture, which defaults to ARM64
(`m6g.large`) here.

**Zip.** There is no `pip install` on the instance — whatever is in the zip is
what your agent gets, so dependencies are vendored for the target platform, not
for your laptop:

```bash
uv pip install --python-platform aarch64-manylinux2014 --python-version 3.12 \
    --target build/ --only-binary :all: -r requirements.txt
```

`--python-platform` is what makes this work from a Mac. Native wheels
(`cryptography`, `cffi`, `pydantic-core`) would otherwise be built for macOS and
fail to import on the instance.

**Container.** Built by CodeBuild on a native ARM64 runner, so no
cross-compilation and no emulation. `deploy.py` uploads the build context to S3
and CodeBuild pushes the image to ECR.

### Cold start

Measured on `m6g.large`. Every cold figure is a new runtime session, which means
a new EC2 instance:

| Cold (new session) | Warm (same session) |
|---|---|
| 49 s, 288 s, 488 s | 1.9 s – 7.8 s |

The first invoke pays for an EC2 launch, boot and artifact seeding. After that it
is single-digit seconds. **Do not benchmark a CapacityProvider with one invoke** —
you would be measuring instance provisioning, not your agent.

### Which instance served you

Invoking both runtimes launched two instances:

```
i-06b459e6092694cb6  m6g.large  172.31.34.226  session basichttp-86ac…   (zip)
i-0508bf13032119efa  m6g.large  172.31.41.100  session basichttpctr-18…  (container)
```

Both tagged `bedrock-agentcore:capacity-provider-id=<cp-id>`, both in the
autoscaling group `bedrock-agentcore-runtime-instances-<cp-id>`, and both agents
self-reported the same machine — `Linux 6.1.176-223.369.amzn2023.aarch64`,
`aarch64`, 2 CPUs, 7735 MiB — differing only in `artifact_kind`. That is the
sample's thesis, confirmed from inside the agent and from the EC2 API.

A `runtimeSessionId` routes a request; it does **not** pin one to an instance.
Sample 2 measured seven calls on a single session id arriving on seven different
hosts. Treat in-memory state as an optimisation, never as a source of truth.

Instances terminate automatically once every agent on them has been idle for
`idleInstanceTimeout` (900 s here); we observed all three self-terminating 10–18
minutes after their last invoke.

### Transient errors on invoke

`InvokeAgentRuntime` can return `InternalServerException` transiently. Those
failures come back in **1–3 seconds**, before any instance is launched, so
latency tells them apart from a real cold start:

```
seconds  → nothing was placed; retry
minutes  → an instance is really booting; never interrupt it
```

`invoke.py` retries on the **same** session id, so a retry does not open a second
session on a second instance. Its data-plane client sets `read_timeout=900` and
disables botocore retries for the same reason — `InvokeAgentRuntime` is not
idempotent.

### Your instances are hidden from `describe-instances` by default

Since [EC2 Managed Resource
Visibility](https://aws.amazon.com/about-aws/whats-new/2026/04/ec2-managed-resource-visibility/)
shipped in April 2026, EC2 instances an AWS service provisions **on your behalf**
are hidden by default — from the console and from `DescribeInstances`. A
CapacityProvider's instances are exactly that, along with their EBS volumes,
snapshots and network interfaces.

It is a sensible default that is wrong *here*, for one reason:

> **Hidden instances are still running, and still billing.**

The question you most need to answer on this path is "did anything survive
cleanup?" — and with the default in force, a fleet that is still up prints as an
empty table. That reads exactly like success.

**So `deploy.py` turns visibility on, and never turns it back off:**

```python
if ec2.get_managed_resource_visibility()["Visibility"]["DefaultVisibility"] != "visible":
    ec2.modify_managed_resource_visibility(DefaultVisibility="visible")
```

`cleanup.py` deliberately leaves it alone — reverting it at the end of a run would
re-hide the instances at the moment you want to check for leftovers.

Two things to know first:

* **The setting is account-wide.** It applies to every IAM principal, and also
  un-hides any EKS, ECS, Lambda or WorkSpaces managed instances the account has.
  `deploy.py` announces it in its output rather than doing it quietly.
* **Visibility is cosmetic.** Hidden or visible, the instances launch, run, serve
  traffic and bill identically.

To leave the account as it is, and pass the flag per call instead:

```bash
CP_MANAGED_VISIBILITY=skip python deploy.py

aws ec2 describe-instances --include-managed-resources --region "$AWS_REGION" \
  --filters "Name=tag:bedrock-agentcore:capacity-provider-id,Values=<cp-id>" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output table
```

To put the account back:

```bash
aws ec2 modify-managed-resource-visibility --region "$AWS_REGION" \
  --default-visibility hidden
```

If `ec2:ModifyManagedResourceVisibility` is not granted to you, `deploy.py` warns
and carries on — `--include-managed-resources` covers you either way.

### Lifecycle limits

`maxLifetime`'s ceiling is **1209600 s (14 days)**, enforced server-side, and it
must be ≥ the idle timeout. Both idle timeouts are min 60 / max 1209600.

## Prerequisites

1. **Credentials for a role** with permission to create EC2 instances, IAM roles,
   S3 buckets, ECR repos and CodeBuild projects. A *role*, not an IAM user: that
   role doubles as the CapacityProvider operator role by default, and a user
   cannot be assumed by a service — see [The two roles](#the-two-roles).

   `deploy.py` also calls `ec2:GetManagedResourceVisibility` and
   `ec2:ModifyManagedResourceVisibility`. Neither is required, but the second is
   an **account-wide** change — read [the section
   above](#your-instances-are-hidden-from-describe-instances-by-default) before
   running this in a shared account.
2. **A region.** Nothing is hardcoded. `deploy.py` reads `AWS_REGION`, then
   `AWS_DEFAULT_REGION`, then your profile's region, and exits if none is set
   rather than launching EC2 instances somewhere you did not choose.

   ```bash
   export AWS_REGION=<your-region>
   ```
3. **A boto3 with the CapacityProvider APIs.** Verified on **boto3/botocore
   1.43.65**; older releases have neither the CP operations nor
   `capacityProviderConfiguration`.

   ```bash
   uv pip install --upgrade boto3 botocore
   ```
4. **`uv`** on your PATH, for cross-platform dependency vendoring.
5. **Model access** to `MODEL_ID` in your region. The default is a `global.`
   inference profile, which is not tied to one geography.

## Quick start

```bash
python deploy.py       # ~6-9 min, most of it the container build
python invoke.py       # first invoke is slow — see Cold start above
python cleanup.py      # deletes the runtimes, the CP, and the EC2 fleet
```

Deploy one artifact at a time with `--only zip` or `--only container`.

### Environment variables

All optional except the region.

| Variable | Default | What it does |
|---|---|---|
| `AWS_REGION` | *none — required* | The region. No fallback default. |
| `MODEL_ID` | `global.anthropic.claude-sonnet-4-5-20250929-v1:0` | Passed to the agent as a runtime env var, so switching models needs no rebuild. |
| `CP_OS` | `LINUX_ARM64` | `LINUX_ARM64` or `LINUX_X86_64`. Drives the wheel platform, Docker platform and CodeBuild image. |
| `CP_INSTANCE_TYPE` | `m6g.large` | Must match `CP_OS`. |
| `CP_SUBNET_ID` / `CP_SECURITY_GROUP_ID` | default VPC's first subnet + default SG | Set both together to place the fleet in a VPC of your choosing. |
| `CP_OPERATOR_ROLE_ARN` | the role you are running as | A scoped-down operator role — see [The two roles](#the-two-roles). |
| `CP_MANAGED_VISIBILITY` | *unset* | Set to `skip` to leave managed resource visibility untouched. |

## Step by step

What the scripts do, in order, with the exact API calls — so you can do it by hand
or port it into your own tooling.

### Step 1 — create the CapacityProvider

The fleet: which instance types are allowed, which VPC and subnet the instances
join, and when idle instances shut down.

```python
resp = agentcore.create_capacity_provider(
    name="basic_http_1234567890",
    description="Basic HTTP sample",
    permissionsConfiguration={
        # The role AgentCore assumes to create EC2 instances and EBS volumes IN
        # YOUR ACCOUNT. Separate from the runtime's execution role, and it must
        # trust bedrock-agentcore.amazonaws.com. deploy.py defaults this to the
        # role you are already running as.
        "capacityProviderOperatorRoleArn": operator_role_arn,
    },
    computeConfiguration={
        "ec2Configuration": {
            "launchTemplateSource": {
                "launchParameters": {
                    "operatingSystem": "LINUX_ARM64",          # or LINUX_X86_64
                    "instanceRequirements": {
                        "allowedInstanceTypes": ["m6g.large"],
                    },
                }
            },
            "vpcConfiguration": {
                "subnets": [subnet_id],
                "securityGroups": [security_group_id],
            },
            "lifecycleConfiguration": {
                "idleInstanceTimeout": 900,     # 15 min idle → instance reclaimed
                "maxLifetime": 86400,           # ceiling is 1209600 (14 days)
            },
        }
    },
)
cp_id, cp_arn = resp["capacityProviderId"], resp["capacityProviderArn"]
```

Required members are exactly three: `name`, `permissionsConfiguration`,
`computeConfiguration`.

Then wait. **The terminal state is `READY`, not `ACTIVE`** — the enum is
`CREATING, CREATE_FAILED, UPDATING, UPDATE_FAILED, READY, DELETING,
DELETE_FAILED` — and there is no waiter, so poll:

```python
while True:
    got = agentcore.get_capacity_provider(capacityProviderId=cp_id)
    if got["status"] == "READY":
        break
    if "FAILED" in got["status"]:
        # statusReason and statusCode are on Get, not on Create.
        raise SystemExit(f"{got['status']}: {got.get('statusReason')}")
    time.sleep(5)
```

Takes well under a minute, and **launches no instances**. The fleet is a
declaration; instances appear on the first invoke.

A CapacityProvider is immutable except for its description, so changing the
machine shape means creating a new one. A running fleet cannot shift under you.

### Step 2 — create the agent

An ordinary `CreateAgentRuntime` plus `capacityProviderConfiguration`, and
**without** `networkConfiguration`:

```python
resp = agentcore.create_agent_runtime(
    agentRuntimeName="basic_http_1234567890_zip",
    roleArn=execution_role_arn,
    agentRuntimeArtifact={
        "codeConfiguration": {                       # zip
            "code": {"s3": {"bucket": BUCKET, "prefix": ZIP_KEY}},
            "runtime": "PYTHON_3_12",
            "entryPoint": ["agent.py"],
        }
        # or, for a container:
        # "containerConfiguration": {"containerUri": IMAGE_URI}
    },
    protocolConfiguration={"serverProtocol": "HTTP"},
    capacityProviderConfiguration={"capacityProviderArn": cp_arn},   # ← the fleet
    lifecycleConfiguration={"idleRuntimeSessionTimeout": 900, "maxLifetime": 86400},
    environmentVariables={"MODEL_ID": MODEL_ID},
)
```

Poll `get_agent_runtime` until `READY` the same way. The artifact must already
exist — the zip in S3 or the image in ECR, built for the **instance**
architecture.

`deploy.py` does this twice against the same CapacityProvider, once per artifact
type, and writes everything to `cp_config.json` for `invoke.py` and `cleanup.py`.

Then invoke, on the data plane:

```python
data = boto3.client("bedrock-agentcore", region_name=REGION,
                    config=Config(read_timeout=900,          # not optional
                                  retries={"max_attempts": 1}))
data.invoke_agent_runtime(
    agentRuntimeArn=runtime_arn,
    runtimeSessionId=session_id,        # must be ≥33 chars
    payload=json.dumps({"prompt": "hello"}),
)
```

Both non-default client settings matter — see [Transient errors on
invoke](#transient-errors-on-invoke).

### Step 3 — delete the agent, then the CapacityProvider

Order matters: runtimes reference the CapacityProvider, so runtimes go first.

```python
agentcore.delete_agent_runtime(agentRuntimeId=runtime_id)
```

`DeleteAgentRuntime` returns **before** its versions detach, so an immediate
`DeleteCapacityProvider` fails with `ValidationException: ... still has attached
agent runtime versions` even though `GetAgentRuntime` already reports the runtime
gone. Ask the API directly rather than matching on that error string:

```python
while agentcore.list_agent_runtime_versions_by_capacity_provider(
        capacityProviderId=cp_id).get("agentRuntimes"):
    time.sleep(15)

agentcore.delete_capacity_provider(capacityProviderId=cp_id)
```

Deleting the CapacityProvider terminates the instances it manages. Then poll until
it is gone, and be careful about what "gone" means:

```python
while True:
    try:
        status = agentcore.get_capacity_provider(capacityProviderId=cp_id)["status"]
    except agentcore.exceptions.ResourceNotFoundException:
        break                      # ONLY this means deleted
    if status == "DELETE_FAILED":
        agentcore.delete_capacity_provider(capacityProviderId=cp_id)   # re-issue
    time.sleep(15)
```

Two traps in that loop: `DELETE_FAILED` is not terminal (re-issuing the delete has
succeeded on the third attempt, ~9 minutes in), and a throttle is not a success —
treating any exception as "deleted" prints success over a fleet that is still
billing.

`cleanup.py` then removes the S3 bucket, the ECR repo, the CodeBuild project and
the IAM roles, and prints a `describe-instances` command — with
`--include-managed-resources`, which is [not
optional](#your-instances-are-hidden-from-describe-instances-by-default).

Two things it intentionally does **not** undo: the managed resource visibility
setting, and the `bedrock-agentcore.amazonaws.com` trust statement it may have
added to your own role. Both are account-level state it only amended.

## The two roles

The CapacityProvider path uses **two** roles, both assumed by the same service
principal.

| | Execution role | Operator role |
|---|---|---|
| Passed to | `create_agent_runtime(roleArn=…)` | `create_capacity_provider(permissionsConfiguration=…)` |
| Who runs as it | your agent's code | the AgentCore service, on your behalf |
| Needs | Bedrock, CloudWatch Logs, ECR read | EC2, EBS, network interfaces, Auto Scaling |
| In this sample | created for you, `agentcore-cp-samples-runtime-role` | **your own role** — nothing created |

Both must trust `bedrock-agentcore.amazonaws.com`. Get that wrong and the API
rejects the role with a message that reads like the role is missing rather than
mis-trusted:

```
ValidationException: Role validation failed for the operator role. Please verify
that the role exists and its trust policy allows assumption by this service
```

```python
{"Effect": "Allow",
 "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
 "Action": "sts:AssumeRole"}
```

`deploy.py` sets no `aws:SourceAccount` condition, to keep the sample minimal. Add
one in a real deployment — it is the standard confused-deputy guard. A
least-privilege custom **execution** role does work on this path; you do not need
an admin role for the runtime.

### The operator role defaults to your own

`deploy.py` does not create an operator role and does not guess a name. It asks
STS which role your credentials already are:

```python
arn = boto3.client("sts").get_caller_identity()["Arn"]
# arn:aws:sts::<acct>:assumed-role/<role-name>/<session-name>
name = arn.split(":")[-1].split("/")[1]
operator_arn = iam.get_role(RoleName=name)["Role"]["Arn"]
```

If you can create EC2 instances yourself, that role already has the permissions
the service needs. Two reasons it reads the name from STS instead of building the
ARN: **role ARNs are case-sensitive** (an earlier version defaulted to
`role/Admin` and failed in an account whose role is `admin`), and **roles can have
paths**, which `get_role` returns and the assumed-role ARN does not show.

If your role does not already trust `bedrock-agentcore.amazonaws.com`,
`deploy.py` **appends** a statement — appends, never replaces — logs that it did,
and `cleanup.py` does not undo it, since the role is yours.

To use a role you have scoped down yourself, which is what you should do outside a
sample:

```bash
export CP_OPERATOR_ROLE_ARN=arn:aws:iam::<acct>:role/<role>
```

That skips the STS lookup and the trust check. Such a role needs EC2
`RunInstances`/`CreateTags`/`TerminateInstances`, launch template and network
interface management, EBS volume create/attach, the `autoscaling:*` actions for
the `bedrock-agentcore-runtime-instances-<cp-id>` group, and `iam:PassRole` for
the instance profile.

## Cost warning

This sample launches **real EC2 instances in your account** and you pay for them
while they run. Run `python cleanup.py` when you are done, and confirm:

```bash
aws ec2 describe-instances --include-managed-resources --region "$AWS_REGION" \
  --filters "Name=tag:bedrock-agentcore:capacity-provider-id,Values=<cp-id>" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output table
```

`--include-managed-resources` is what makes an empty result trustworthy — see
[above](#your-instances-are-hidden-from-describe-instances-by-default).

No production data, no production workloads, and not in an account where a service
bug creating or deleting EC2 instances could affect real workloads.
