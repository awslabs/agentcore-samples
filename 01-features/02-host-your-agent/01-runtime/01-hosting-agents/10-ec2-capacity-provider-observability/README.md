# A Strands agent on an AWS-managed EC2 instance, behind a Gateway, with end-to-end OTEL

An agent running on **EC2 that AWS manages inside your own account** — no public
IP, no NAT — calling a Bedrock model, fronted by an **AgentCore Gateway**, with the
client's trace crossing both hops and landing in OpenTelemetry.

```
client → AgentCore Gateway → AgentRuntime → EC2 (CapacityProvider) → Bedrock
            (SigV4, AWS_IAM)                     ↓
                                   OTEL: metrics · logs · spans · GenAI Observability
```

Everything below was **measured** in a real account, in three regions (`us-east-2`,
`us-west-2` and `ap-southeast-2`). The traps documented here were hit in practice,
and two of them only surfaced when switching regions.

![One trace, from the HTTP entrypoint down to the tool call, with the agent
reporting the machine it is running on](images/otel-trace-tree-whoami.png)

That is a single trace in the CloudWatch GenAI Observability screen. The span tree
on the left is the agent's own execution — `POST /invocations` → `invoke_agent` →
`execute_event_loop_cycle` → `chat` → `execute_tool whoami` — and the panel on the
right is the answer that came back: **an `m6g.large` with an `aarch64` kernel, 2
vCPUs and 7.7 GB, in `us-east-2a`**. Token counts, latency per span and cost per
trace come along for free, because the spans carry the OpenTelemetry `gen_ai.*`
attributes that screen is built on.

## What makes this different from serverless compute

| | microVMs (serverless) | Instances (this sample) |
|---|---|---|
| Session | up to 8 h | up to **14 days** |
| Storage | ephemeral | **persistent EBS** |
| Agents per session | 1 | **several** (shared filesystem) |
| GPU | no | yes (`g4dn`, `g5`, `g6`, `g6e`, `gr6`, `g6f`, `gr6f`, `g7e`, `inf2` families) |
| Where it runs | AWS's account | **your** account (Savings Plans, ODCR apply) |
| Cold start | ~30 s | **~45-60 s** |

## Files

| File | What it does |
|---|---|
| [`deploy.py`](deploy.py) | Deploys everything: IAM → private networking → image → CapacityProvider → Runtime → Gateway → Transaction Search. Idempotent. |
| [`invoke.py`](invoke.py) | Invokes through the Gateway or directly; checks whether the `trace_id` survived; measures cold vs warm. |
| [`cleanup.py`](cleanup.py) | Removes everything, in the order the service requires. |
| [`agent/agent.py`](agent/agent.py) | The agent: `BedrockAgentCoreApp` + `strands.Agent` with three tools. |
| [`agent/requirements.txt`](agent/requirements.txt) | `bedrock-agentcore`, `strands-agents`, `aws-opentelemetry-distro`. |

## Prerequisites

- **boto3/botocore ≥ 1.43.66** — the release that shipped the CapacityProvider APIs.
  A plain `pip install -U boto3` is enough. Check with:
  ```python
  import boto3
  c = boto3.client("bedrock-agentcore-control")
  print([o for o in c.meta.service_model.operation_names if "Capacity" in o])
  # should list 6 operations
  ```
- **No local Docker needed** — the image is built on CodeBuild, natively for the
  instance architecture. `deploy.py` creates the CodeBuild project and its bucket.
- AWS credentials allowed to create IAM roles, VPC endpoints, ECR, S3, CodeBuild and
  AgentCore resources.
- Model access enabled in Bedrock, in the region you pick.

## Quick start

```bash
export AWS_REGION=us-west-2
python deploy.py                  # ~4-6 min (most of it is VPC endpoints)
python invoke.py                  # 1st call ~45-60s — that is the cold start
python cleanup.py                 # when you are done
```

### Flags

| Script | Flag | What it does |
|---|---|---|
| `deploy.py` | `--skip-build` | Reuses the image already in ECR |
| | `--no-gateway` | CP + runtime only |
| | `--no-observability` | Does not touch Transaction Search (account-level config) |
| | `--sampling N` | X-Ray sampling percentage (default 20) |
| `invoke.py` | `--direct` | Skips the Gateway |
| | `--both` | Tests both paths and compares them |
| | `--repeat N` | N calls on the same session — measures cold vs warm |
| | `--attempts N` | Attempts per call on transient errors (default 5; `1` disables the retry) |
| | `--prompt "…"` | Your own question |
| `cleanup.py` | `--yes` | No confirmation prompt |
| | `--keep-network` | Preserves subnet/SG/VPC endpoints |
| | `--keep-iam` | Preserves the roles |

### Environment variables

| Variable | Default | Note |
|---|---|---|
| `AWS_REGION` | **none — required** | Falls back to your AWS profile's region. No silent default: a sample that launches EC2 where you did not ask is worse than one that refuses to run. The CP is GA in 9 regions: BOM, CMH, DUB, FRA, IAD, NRT, PDX, SIN, SYD |
| `CP_INSTANCE_TYPE` | `m6g.large` | Change it together with `CP_OS` |
| `CP_OS` | `LINUX_ARM64` | or `LINUX_X86_64` |
| `BEDROCK_MODEL_ID` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` | A `global.` inference profile resolves in every region this sample runs in. A geographic prefix (`us.`, `eu.`, `apac.`, `au.`) only resolves inside its own geography, so it fails with `The provided model identifier is invalid` as soon as you deploy elsewhere |
| `CP_IDLE_TIMEOUT` | `900` | The instance self-terminates after this long idle |
| `CP_MAX_LIFETIME` | `3600` | Ceiling: `1209600` (14 days) |
| `CP_SUBNET_CIDR` | `172.31.200.0/24` | Must fit inside the default VPC |

## The agent

Same shape as the [official AWS sample](https://github.com/awslabs/agentcore-samples/tree/main/01-features/02-host-your-agent/01-runtime/01-hosting-agents/08-http-ec2-capacity-provider):
`BedrockAgentCoreApp` provides the HTTP contract (`POST /invocations` and
`GET /ping` on `0.0.0.0:8080`), so there is no hand-written HTTP server. Three
tools:

| Tool | What it proves |
|---|---|
| `whoami` | The machine: kernel, arch, CPUs, memory — and that there is **no public IP** |
| `run_command` | It is an ordinary Linux box (`df -h /mnt/data`, `nproc`, …) |
| `persistent_state` | The EBS counter survived the previous invocation |

### The entrypoint signature is load-bearing

```python
@app.entrypoint
def invoke(payload, context):          # ← the 2nd parameter MUST be named `context`
```

`BedrockAgentCoreApp._takes_context()` checks `params[1] == "context"` literally.
Name it anything else and the SDK calls your function with the payload only — you
lose `request_headers`, and with it the client's `traceparent`. The official
sample uses `invoke(payload)` and has no trace propagation.

Not every header gets through: `is_forwardable_header()` drops anything starting
with `x-amzn-` (except the `...-Custom-` prefix). `traceparent` is fine because it
does not match those rules.

## How it works

### The line that takes you off serverless

```python
capacityProviderConfiguration={"capacityProviderArn": cp_arn}
```

One optional field on `CreateAgentRuntime`. With it, the agent runs on EC2 in your
account; without it, on a managed microVM. **You cannot switch later** — compute
type is immutable.

### Provisioning is lazy

The `CapacityProvider` reaches `READY` in **~0.3 s** and the `AgentRuntime` in
**~5 s** — and at that moment **no EC2 instance exists**. The instance is born on
the first invoke:

```
create_capacity_provider()  → READY   (0 instances)
create_agent_runtime()      → READY   (0 instances)
invoke_agent_runtime()      → ← the EC2 is born HERE (~45s)
```

Measured: `describe_instances` filtered by the CP's tag returned zero before the
first invoke. What the first invoke creates, besides the instance: an **ASG**
(`agentcore-managed-instances-<cp-id>`), a **launch template**, two **EBS
volumes** per host, and the **Reverse X-ENI**.

### Measured cold start

| | Cold (new session) | Warm (same instance) |
|---|---|---|
| This sample (`m6g.large`) | **57-76 s** | **2.3-2.8 s** |
| Official AWS sample (`m6g.large`) | 49 s / 288 s / 488 s | 1.9 – 7.8 s |

A bigger instance does **not** help: the time is launch + boot + artifact
seeding, not CPU.

**Do not benchmark a CapacityProvider with a single invoke** — you would be
measuring EC2 provisioning, not your agent.

### `runtimeSessionId` routes, but does not pin the instance

This is counter-intuitive and worth knowing before you design your agent. The
official docs are explicit:

> "A `runtimeSessionId` routes a request; it does **not pin** one to an instance.
> Treat in-memory state as an optimisation, never as a source of truth."

Confirmed here: **9 invokes with the same sessionId → 9 distinct EC2 instances**,
all cold (~46 s each). The EBS counter came back as `1` every time, because each
host had its own volume. The warm path does happen when the instance *is* reused —
it is opportunistic, not guaranteed.

Practical consequence: if your agent needs reliable state between calls, it has to
live on EBS (or outside), never in memory.

### Transient errors vs. a real cold start

`InvokeAgentRuntime` can return a transient `InternalServerException`. Latency
tells the cases apart:

```
seconds → nothing was provisioned; safe to retry
minutes → an instance really is coming up; do not interrupt
```

And **do not use botocore retries**: `InvokeAgentRuntime` is not idempotent — a
botocore-level retry during a slow cold start would open a SECOND session on a
SECOND instance. The scripts here use `retries={"max_attempts": 0}` and
`read_timeout=900`, and do the retry one layer up instead:

```python
TRANSIENT = ("InternalServerException", "RuntimeClientError")
...
retryable = code and any(t in code for t in TRANSIENT)
if retryable and attempt < attempts:
    time.sleep(15)          # same session id — stay on the same instance
```

That loop only fires on an **error**, never on a slow call, so a genuine cold
start is never interrupted. A non-transient error (`AccessDeniedException`,
`ValidationException`) gives up immediately instead of burning five attempts.
`--attempts 1` disables it.

Through the Gateway the exception name is not in the response body — it comes in
the `x-amzn-ErrorType` header, which is what the code reads to decide whether an
HTTP 424 is retryable.

### ⚠️ A transient `424` on the first invocation — open issue

```
HTTP 424 after 74.0s
{"message":"An error occurred when starting the runtime.
            Please check your CloudWatch logs for more information."}
```

Seen in **2 of ~10** deploys in `us-west-2`, never in `ap-southeast-2`. Retrying
resolves it — which is why `invoke.py` retries `RuntimeClientError` automatically
(a 424 through the Gateway carries that name in `x-amzn-ErrorType`). The retry is
a workaround, not an explanation.

Everything we checked was fine: EC2 `running`, clean boot with the EBS volume
mounted, the right image in ECR (OCI manifest identical to the working region's),
runtime `READY` with the correct configuration, all 7 VPC endpoints `available`,
and the role holding the ECR pull permissions. **The runtime's log group was
completely empty** — the container dies before it logs anything.

**Root cause: unknown.** We are not inventing one. Note this is *not* the
documented transient `InternalServerException`: that one is described as coming
back "in 1–3 seconds, before any instance is launched", whereas this takes 74 s
and the instance **was** provisioned.

### The instances are hidden from `describe-instances`

Since [EC2 Managed Resource Visibility](https://aws.amazon.com/about-aws/whats-new/2026/04/ec2-managed-resource-visibility/)
(Apr 2026), instances an AWS service provisions **on your behalf** do not appear
in listings by default. To see them:

```python
ec2.describe_instances(
    Filters=[{"Name": "tag-key",
              "Values": ["bedrock-agentcore:capacity-provider-id"]}],
    IncludeManagedResources=True)     # ← without this, it returns empty
```

The same flag applies to `describe_volumes` and `describe_launch_templates`.
Without it the 138 GiB of attached EBS reads as **0 GiB** and the launch template
does not exist. The official sample takes the other route and flips the
account-wide setting instead (`modify_managed_resource_visibility`), so the
instances show up in the console too — worth knowing, because **hidden instances
are still running and still billing.**

## Private networking: no public IP, no NAT

The requirement here was zero exposure. The EC2 instance comes up in a subnet with
`MapPublicIpOnLaunch=False` and in a security group with **0 ingress rules** —
inbound traffic arrives over the **Reverse X-ENI** the service attaches
(`DeviceIndex=1`, description "ENI for agent connection"), not over the primary
ENI.

Proof, collected from inside the container:

```
IMDS meta-data/public-ipv4  →  HTTP 404 Not Found
```

**That X-ENI is not yours.** It reports your account as `OwnerId`, but its VPC,
subnet and security group **do not exist** in your account — `DescribeVpcs` on
them returns `InvalidVpcID.NotFound`. They live in a service-owned VPC. That is
the concrete reason the agent's SG can have zero ingress rules.

### ⚠️ One deployment per VPC

An interface VPC endpoint accepts **one subnet per availability zone**, and its
security group only admits the SG you point at it. So two deployments of this sample
in the same VPC cannot share the endpoints: the second one gets a subnet the endpoints
do not serve, and its instances cannot reach ECR. The failure surfaces at invoke time
as `The agent artifact could not be downloaded` — a message that points nowhere near
networking. `deploy.py` now detects this and refuses up front.

Deploy into its own VPC, or its own region.

### ⚠️ Every destination needs its own VPC endpoint — and a missing one fails silently

With no public IP and no NAT, the default VPC's `0.0.0.0/0 → IGW` route is
**useless**. `deploy.py` creates seven endpoints:

| Endpoint | Without it |
|---|---|
| `s3` (gateway) | image layers do not download |
| `ecr.api`, `ecr.dkr` | `RuntimeClientError` on start, **empty logs** |
| `logs` | no logs reach CloudWatch |
| `bedrock-agentcore` | the agent cannot talk to the data plane |
| **`xray`** | **traces vanish with no error at all** |
| **`bedrock-runtime`** | the agent cannot call the model |

The last two cost us time. With the `xray` endpoint missing, OTEL's
`BatchSpanProcessor` swallows the timeout in the background — zero spans, zero
error messages. If your traces are missing and the logs are clean, check the VPC
endpoints first.

## Observability

### Why this sample does not pass an instance profile

`instanceProfileArn` is optional on the capacity provider — only `operatingSystem`
and `instanceRequirements` are required — so this sample omits it and lets the
service attach its own default instance profile. That is a deliberate choice, and
it avoids a sharp edge:

If you *do* pass your own instance profile, its role name must start with
`AmazonBedrockAgentCoreCapacityProviderDefaultInstanceRole` — the operator's managed
policy restricts `iam:PassRole` to that prefix. A different name fails in ~2 s with a
message that points at the wrong thing:

```
CREATE_FAILED / VALIDATION_ERROR
"Auto Scaling group creation failed. Access denied:
 You are not authorized to use launch template: lt-0bc7..."
```

The launch template exists and is tagged correctly; `simulate_principal_policy` shows
`ec2:RunInstances` **allowed** but `iam:PassRole` **implicitDeny**. Worse, because that
name is fixed and **IAM is global**, such a role is shared by every CP in the account
across all regions — so deleting it in one region can break a CP in another. Omitting
the profile sidesteps all of this.

### `xray:PutSpans` is a separate permission

On top of the classic `xray:PutTraceSegments`, ADOT needs **`xray:PutSpans`**.

### ADOT needs explicit AWS endpoints

The platform injects `OTEL_EXPORTER_OTLP_LOGS_HEADERS` but **not**
`OTEL_EXPORTER_OTLP_ENDPOINT` — and the SDK's default is `localhost:4318`, which
does not exist on this path (there is no sidecar collector). The result is
`connection refused` in a loop.

ADOT recognises native AWS endpoints by regex and, in that case, exports over
SigV4 directly. `deploy.py` sets both as runtime `environmentVariables`, built
from the region it is deploying to:

```python
"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"https://xray.{REGION}.amazonaws.com/v1/traces",
"OTEL_EXPORTER_OTLP_LOGS_ENDPOINT":   f"https://logs.{REGION}.amazonaws.com/v1/logs",
```

⚠️ **Do not bake the region into the image.** We did, and it worked — until we
deployed to a second region, where the agent exported to the first region's X-Ray
and, with no VPC endpoint for it, failed in complete silence. The reason is specific:
ADOT signs SigV4 with the region it **parses out of the endpoint**, not with
`AWS_REGION` — so a region baked into the image wins at runtime. Setting the endpoints
from `deploy.py` gives the region a single source (`deploy.py` already knows it). The
image's `CMD` runs `opentelemetry-instrument python agent.py` and pins nothing
region-specific.

### The GenAI Observability screen is built on `gen_ai.*`

An agent that never calls a model leaves the token/cost widgets at zero and the
"Session & traces" tab breaks with "An unexpected error occurred".

With Strands, you get these **for free** — no manual Bedrock instrumentation. The
spans that showed up in CloudWatch:

```
 invoke_agent Strands Agents   ← root span, with accumulated tokens
 execute_event_loop_cycle
 chat <model-id>
 execute_tool whoami
 execute_tool run_command
 POST /invocations             ← the SDK's ASGI span
```

The root span carries `gen_ai.usage.input_tokens`, `output_tokens`,
`total_tokens`, `prompt_tokens`, `completion_tokens`,
`cache_read_input_tokens`, `gen_ai.agent.tools` and more. Each tool gets its own
span with `gen_ai.tool.status`.

The `Agent(trace_attributes={...})` argument stamps `agentcore.compute_type` on
every span, which makes filtering in X-Ray easy.

![Spans grouped by capability, with traces, tokens, cost and latency per span
name](images/otel-spans-by-capability.png)

Those same span names come back aggregated, one row each, with the model span
resolved to its capability type (`AWS::Bedrock::Model`) and priced. Note how cheap
the tool spans are next to the model span — `execute_tool whoami` averages 9 ms
against 1.25 s for `chat`. That contrast is the argument for tracing tools
separately instead of timing the invocation as a whole.

### Do not instrument the IMDS

The `public-ipv4` check returns **404** — which is the network requirement
*succeeding*. But automatic `urllib` instrumentation marks every 404 as
`STATUS_CODE_ERROR`, and that inflated the error rate to 13.8% with failures that
never happened (25 of 29 "errors"). The agent suppresses instrumentation for
those calls:

```python
from opentelemetry.context import attach, detach, set_value
ctx = attach(set_value("suppress_instrumentation", True))
try:
    ...   # IMDS calls
finally:
    detach(ctx)
```

### The metric dimensions are not the obvious ones

There is no `ResourceId` dimension. It is `Resource` with the **full ARN**, plus
`Operation`, plus `Name` in the form `<agent-name>::DEFAULT`:

```python
# runtime
Dimensions=[{"Name": "Resource",    "Value": runtime_arn},
            {"Name": "Operation",   "Value": "InvokeAgentRuntime"},
            {"Name": "ComputeType", "Value": "Instances"},
            {"Name": "Name",        "Value": "cpsample_agent::DEFAULT"}]
# gateway
Dimensions=[{"Name": "Resource",  "Value": gateway_arn},
            {"Name": "Operation", "Value": "InvokeGateway"},
            {"Name": "Protocol",  "Value": "HTTP"}]
```

Query by `ResourceId` and you get **zero datapoints**, which looks exactly like
"there are no metrics".

### Transaction Search is account-level config

For spans to reach the log group that feeds the GenAI Observability screen,
Transaction Search must be on with sampling > 0. `deploy.py` enables it at 20%.

It also needs a **CloudWatch Logs resource policy** granting
`logs:PutLogEvents` to `xray.amazonaws.com`. `deploy.py` checks **coverage**, not
existence: with ADOT ≥ 0.18 spans go to the **agent's** log group, so a policy
covering only `aws/spans` is not enough — and that is exactly what a pre-existing
policy in the account did, silently losing every span.

⚠️ This affects **every** service in the account in that region and **bills per
ingested span**. Use `--no-observability` to leave it alone. At 20% sampling,
expect to see ~1 in 5 invokes in X-Ray — for a demo, generate load instead of a
single invoke.

## End-to-end trace

### The API has native trace parameters

No header hacking required — `InvokeAgentRuntime` accepts:

| boto3 parameter | HTTP header |
|---|---|
| `traceParent` | `traceparent` (W3C) |
| `traceState` | `tracestate` |
| `baggage` | `baggage` |
| `traceId` | `X-Amzn-Trace-Id` |

And the runtime has to let them through to the agent:

```python
requestHeaderConfiguration={"requestHeaderAllowlist": ["traceparent","tracestate","baggage"]}
```

⚠️ `X-Amzn-Trace-Id` is **restricted**: including it returns
`ValidationException: Header 'X-Amzn-Trace-Id' is restricted and cannot be configured`.
The platform manages it — and does convert the incoming `traceparent` into
`X-Amzn-Trace-Id`, bridging W3C ↔ X-Ray.

Validated: **every invocation matched**, with the `trace_id` of the span created
inside the EC2 instance **identical** to the `traceparent` the client sent.

![Timeline view of one trace, with the prompt and the tool-use payload beside
it](images/otel-trace-timeline.png)

Flip the same trace to the timeline and the shape of an agent turn becomes obvious:
one `POST /invocations` spanning the whole thing, two `execute_event_loop_cycle`
passes because the model needed a second turn to read the tool result, and
`execute_tool run_command` as the thin sliver between them. The right-hand panel is
the payload the span captured — the system prompt, and the `toolUse` block the model
emitted. This is the view that tells you *why* a turn was slow, not just that it was.

## The Gateway in front

### Two traps

**1. `protocolType` must be OMITTED.** The docs are literal: *"AgentCore Runtime
targets can be added to gateways that don't have a protocol type set. They cannot
be added to MCP protocol type gateways."* With `protocolType="MCP"`, creating the
target fails after 40 s with `"MCP server did not respond to the initialize
request within 40 seconds"` — a message that suggests a timeout or cold start,
when the real cause is the gateway's type. Omit the field and the target is
`READY` in **6 s**.

**2. The Gateway does not forward the `Accept` header.** It is on the restricted
list. If the runtime is on `serverProtocol="MCP"`, it requires
`application/json, text/event-stream` and returns **406 `-32011`** on every call
through the Gateway. This sample uses `serverProtocol="HTTP"`, which does not
have that problem.

### How to invoke it

```
POST https://{gatewayId}.gateway.bedrock-agentcore.{region}.amazonaws.com/{targetName}/invocations
```

With SigV4 (service `bedrock-agentcore`) and an
`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` of **≥33 characters**.

### What you gain and what you lose

You gain: a single entry point, centralised auth, unified observability,
path-based routing, and the option to force all traffic through the Gateway (via a
resource-based policy on the runtime).

You lose: **tool aggregation**. `tools/list` comes back **empty** — by design:
*"No capability synchronization or semantic tool search is available for AgentCore
Runtime targets. Clients must know exact tool names."* If what you want from the
Gateway is aggregating MCP tools from several sources, the answer is an MCP
gateway with MCP targets, not this.

Note: agent errors come back as **424** through the Gateway (instead of 500 on the
direct invoke).

## Step by step

### Step 1 — CapacityProvider

```python
agentcore.create_capacity_provider(
    name="cpsample_cp",
    permissionsConfiguration={"capacityProviderOperatorRoleArn": operator_arn},
    computeConfiguration={"ec2Configuration": {
        "launchTemplateSource": {"launchParameters": {
            "operatingSystem": "LINUX_ARM64",
            "instanceRequirements": {"allowedInstanceTypes": ["m6g.large"]},
            "monitoring": "BASIC"}},        # no instanceProfileArn — see below
        "vpcConfiguration": {"subnets": [subnet], "securityGroups": [sg]},
        "volumes": [{"ebsConfiguration": {
            "name": "data_volume", "sizeGiB": 30, "volumeType": "gp3",
            "iops": 3000, "throughput": 125, "encrypted": True}}],
        "lifecycleConfiguration": {"idleInstanceTimeout": 900, "maxLifetime": 3600}}})
```

`instanceProfileArn` is omitted on purpose — the service attaches its own default
instance profile (see *Why this sample does not pass an instance profile*). Only
`description` is editable afterwards; to change instance type, VPC or volumes, create
a new CP (blue/green).

![The capacity provider this sample creates, as the console renders
it](images/capacity-provider-details.png)

Every field above maps to one argument in that call, which makes this page a decent
way to check your own deployment. Two worth pointing at: **Instance profile is `-`**,
which is the omission described below rendered as a dash, and the lifecycle pair —
**idle instance timeout 15 minutes, instance max lifetime 1 hour** — which is what
stops a forgotten sample from billing all weekend.

### Step 2 — AgentRuntime

```python
agentcore.create_agent_runtime(
    agentRuntimeName="cpsample_agent",
    agentRuntimeArtifact={"containerConfiguration": {"containerUri": img}},
    roleArn=runtime_role,
    protocolConfiguration={"serverProtocol": "HTTP"},
    capacityProviderConfiguration={"capacityProviderArn": cp_arn},
    filesystemConfigurations=[{"capacityProviderVolume": {
        "volumeName": "data_volume", "mountPath": "/mnt/data"}}],
    requestHeaderConfiguration={"requestHeaderAllowlist": ["traceparent","tracestate","baggage"]})
```

⚠️ Do **not** send `networkConfiguration` together with
`capacityProviderConfiguration` — the networking comes from the CP.
`FilesystemConfiguration` is a **union**: one member per entry.

⚠️ The image's architecture must match the CP's `operatingSystem`. An x86 CP with
an arm image **fails silently**.

### Step 3 — Gateway

```python
gw = agentcore.create_gateway(
    name="cpsample-gw", roleArn=gateway_role,
    authorizerType="AWS_IAM")            # protocolType OMITTED on purpose

agentcore.create_gateway_target(
    gatewayIdentifier=gw["gatewayId"], name="runtime-cp",
    targetConfiguration={"http": {"agentcoreRuntime": {
        "arn": runtime_arn, "qualifier": "DEFAULT"}}},
    credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    metadataConfiguration={"allowedRequestHeaders": ["traceparent","tracestate","baggage"]})
```

The Gateway resolves the runtime's endpoint internally — do not build the URL
yourself.

⚠️ `list_gateways` does **not** return `gatewayUrl`; only `get_gateway` does. Miss
that on an idempotent path and the state saves `gateway_url=None`, after which the
client silently falls back to the direct route and never touches the Gateway. That
bug was in this sample.

### Step 4 — clean up (the order matters)

```
sessions → runtime → CapacityProvider → VPC endpoints → ECR → subnet/SG → IAM
```

The CP refuses deletion while any runtime version references it. Deleting the CP
stops and deletes every session and volume. Deleting the EC2 instances by hand
does not help: the service's ASG relaunches them.

Two asynchronous steps that will bite you if you do not wait:
deleting a **gateway target** leaves it in `DELETING`, so `delete_gateway` fails
with "has targets associated with it"; and deleting an **interface VPC endpoint**
holds its ENI for ~2 min, which blocks the SG and the subnet with
`DependencyViolation`.

## The three roles

| Role | Assumed by | For what |
|---|---|---|
| **Operator** (infrastructure) | `bedrock-agentcore.amazonaws.com` | AgentCore provisions EC2/ASG/EBS/ENI in your account |
| **Runtime execution** | `bedrock-agentcore.amazonaws.com` | What *your code* is allowed to do (Bedrock, logs, X-Ray) |
| **Gateway** | `bedrock-agentcore.amazonaws.com` | Invoking the runtime |

There is no instance profile role — the capacity provider omits `instanceProfileArn`
and the service supplies its own (see *Why this sample does not pass an instance
profile*).

The operator role's managed policy is
`BedrockAgentCoreRuntimeInstancesOperatorRolePolicy` (the real name, which differs
from some docs); the runtime and gateway roles use inline policies.

## ⚠️ Cost

- **EC2 + EBS**: per provisioned instance — two volumes each (a 30 GiB encrypted
  `data_volume` and a 16 GiB root). They self-terminate after
  `idleInstanceTimeout` (10-18 min in practice), but **a new session means a new
  instance** — 20 invokes can create 20 instances.
- **6 interface VPC endpoints** (+1 gateway endpoint, free): they bill **per hour,
  per AZ**, with no traffic at all. This is the cost that keeps running after the
  instances die.
- **Transaction Search**: per ingested span.
- **Bedrock**: per token. Note the agentic loop with tool calling uses
  considerably more tokens than a single model call — measured 2,110 and 4,362
  total tokens for one question each.

`python cleanup.py` removes everything, endpoints included. What it does **not**
undo is listed in its own output.

![Model cost broken down per trace](images/otel-cost-per-trace.png)

The model half of that bill is visible per trace, not just per month: the run behind
these screenshots cost **$0.041 in tokens, about $0.002 per trace**. Worth knowing
which number to be scared of — the tokens are pennies, the idle VPC endpoints are not.

## References

- [Instances compute type — how it works](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-instances-how-it-works.html)
- [HTTP protocol contract](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-http-protocol-contract.html)
- [AgentCore Runtime targets on the Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-http-runtime.html)
- [Configure observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
- [Strands Agents — traces](https://strandsagents.com/docs/user-guide/observability-evaluation/traces)
- [Official AWS sample](https://github.com/awslabs/agentcore-samples/tree/main/01-features/02-host-your-agent/01-runtime/01-hosting-agents/08-http-ec2-capacity-provider)
