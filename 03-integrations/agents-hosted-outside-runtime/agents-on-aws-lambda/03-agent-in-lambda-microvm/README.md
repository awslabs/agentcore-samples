# Agent in Lambda MicroVM with AgentCore Observability

This sample shows how to host a **Strands agent inside an AWS Lambda MicroVM** and emit
Gen AI observability traces to **Amazon CloudWatch** via the AWS Distro for OpenTelemetry
(ADOT) Python SDK. All spans, structured logs, and token metrics land in the agent's own
log group under `/aws/bedrock-agentcore/runtimes/<agent-id>` and are visible in the
CloudWatch **GenAI Observability** dashboard.

## What MicroVMs bring to agent observability

Lambda MicroVMs are Firecracker VMs with VM-level isolation, snapshot start and resume, and
a per-VM HTTPS endpoint. Because the Python process is long-lived, the ADOT SDK stays
initialized across invocations and every span carries the same MicroVM identity attributes
(`aws.lambda.microvm.image_name`, `aws.lambda.microvm.image_version`). Snapshot resume also
means the agent is already running the moment a MicroVM starts, so telemetry begins flowing
on the first request without a per-invocation ADOT bootstrap cost.

## Architecture

```
Caller (invoke.py / any HTTPS client with X-aws-proxy-auth token)
  └── Lambda MicroVM  (per-VM HTTPS endpoint on port 8080)
        ├── Python 3.11 process (snapshotted at build time, resumes on launch)
        │     ├── opentelemetry-instrument
        │     │     └── ADOT SDK (aws-opentelemetry-distro >= 0.18.0)
        │     │           └── auto-instruments Strands, boto3, http.server
        │     └── Strands Agent  (AGENT_OBSERVABILITY_ENABLED=true)
        │           └── Amazon Bedrock (Claude Haiku)
        └── OTLP over HTTPS, SigV4-signed with the execution role's credentials
              ▼
         CloudWatch OTLP endpoint
              ▼
         /aws/bedrock-agentcore/runtimes/<agent-id>
              ├── stream "runtime-logs"  ← structured application logs
              └── stream "spans"         ← OTLP spans (5 per invoke)
                    ▼
         CloudWatch GenAI Observability dashboard + X-Ray Trace Search
```

The MicroVM's execution role provides temporary IAM credentials via the standard AWS SDK
credential chain — no static access keys anywhere.

## Files

| File | Description |
|:-----|:------------|
| `app/Dockerfile` | Container image built inside the MicroVM: al2023-minimal ARM64 + Python 3.11 + ADOT |
| `app/agent.py` | Strands agent + `http.server` on port 8080, MicroVM lifecycle hooks (`/ready`, `/validate`) |
| `app/entrypoint.sh` | Launches Python under `opentelemetry-instrument` |
| `app/requirements.txt` | Container-side deps: `aws-opentelemetry-distro`, `strands-agents[otel]`, `boto3` |
| `config.py` | Shared constants + `otel_env()` — the exact env-var contract from the AgentCore docs |
| `deploy.py` | 9-step idempotent deploy (Transaction Search → log group + resource policy → S3 → IAM → package + upload → `create_microvm_image`) |
| `invoke.py` | Launches a MicroVM, mints an auth token, POSTs test prompts, prints verify links |
| `cleanup.py` | Terminates MicroVMs → deletes image → deletes IAM roles → empties + deletes S3 bucket |
| `requirements.txt` | Client-side dep for the scripts above (`boto3 >= 1.43.72`) |

---

## Prerequisites

- Python 3.10+ with **`boto3 >= 1.43.72`** (the version that ships the `lambda-microvms` client)
- AWS CLI configured (`aws sts get-caller-identity`)
- Amazon Bedrock Claude Haiku enabled in your region
- A region where Lambda MicroVMs are available: `us-east-1`, `us-east-2`, `us-west-2`, `eu-west-1`, `ap-northeast-1`

Docker is **not** required on your developer machine — Lambda builds the container image on
its own infrastructure.

---

## Quick Start

```bash
pip install -r requirements.txt

python deploy.py     # ~3-5 min: builds and snapshots the MicroVM image
python invoke.py     # ~1 min:   launches a MicroVM and sends 3 prompts
python cleanup.py    # tears everything down
```

Override defaults with env vars:

```bash
export AWS_REGION=us-west-2
export MICROVM_DEMO_PREFIX=my-microvm-demo
export AGENT_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
```

---

## How observability works

### Why the ADOT SDK (and not the ADOT Collector)

AgentCore Observability supports two transports for agents hosted outside the AgentCore
runtime: the **ADOT SDK** (in-process, per-language) and the **AWS Lambda Layer for
OpenTelemetry** (for Lambda functions). The managed Lambda Layer targets the Lambda
function execution environment and does not apply to MicroVMs, and the standalone ADOT
Collector is not supported for AgentCore's `x-aws-log-group` / `x-aws-log-stream` routing.
This sample uses the ADOT SDK path: `aws-opentelemetry-distro` is installed in the
container image and Python starts under `opentelemetry-instrument`, so Strands, boto3,
urllib3, and `http.server` are auto-instrumented with no code changes.

### Baked-in OTEL env vars

`config.otel_env()` returns the exact environment variables the AgentCore Observability
docs prescribe for agents hosted outside the AgentCore runtime:

```
AGENT_OBSERVABILITY_ENABLED=true
OTEL_PYTHON_DISTRO=aws_distro
OTEL_PYTHON_CONFIGURATOR=aws_configurator
OTEL_RESOURCE_ATTRIBUTES=service.name=<name>,aws.log.group.names=/aws/bedrock-agentcore/runtimes/<id>
OTEL_EXPORTER_OTLP_LOGS_HEADERS=x-aws-log-group=<log-group>,x-aws-log-stream=runtime-logs,x-aws-metric-namespace=bedrock-agentcore
OTEL_EXPORTER_OTLP_TRACES_HEADERS=x-aws-log-group=<log-group>,x-aws-log-stream=spans
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_TRACES_EXPORTER=otlp
```

`deploy.py` passes these to `create_microvm_image` via the `environmentVariables` field, so
they are captured in the MicroVM snapshot and every launched MicroVM inherits them.

### SigV4 with the execution role

The ADOT AWS distro signs each OTLP request with SigV4 using credentials from the standard
AWS SDK credential chain. Inside a MicroVM those credentials come from the execution role
you pass to `run_microvm` — no IAM user keys required.

### Unified log group destination

`OTEL_EXPORTER_OTLP_TRACES_HEADERS` routes spans into the agent's own log group
(`/aws/bedrock-agentcore/runtimes/<agent-id>`) instead of the shared `aws/spans` group.
This requires `aws-opentelemetry-distro >= 0.18.0` and a CloudWatch Logs resource policy
that lets X-Ray write into the log group — `deploy.py` installs it.

---

## Viewing traces in CloudWatch

After running `invoke.py` (allow 1–2 minutes for OTLP to flush and Transaction Search to
ingest):

1. **GenAI Observability**:
   CloudWatch → GenAI Observability → **Bedrock AgentCore** tab.
   - Look for the agent `microvm-agentcore-obs-agent` and the demo session id printed by
     `invoke.py`.
   - Session view, token usage, latency per span.
2. **Agent log group**:
   CloudWatch → Log groups → `/aws/bedrock-agentcore/runtimes/microvm-agentcore-obs`.
   - Log stream `runtime-logs` — structured application logs.
   - Log stream `spans` — OTLP spans per invoke:
     `agent.invoke` → `invoke_agent Strands Agents` → `execute_event_loop_cycle` →
     `chat` → `chat <model-id>`.
   Each span carries `session.id`, `aws.lambda.microvm.image_name`, and
   `aws.lambda.microvm.image_version`.
3. **X-Ray Trace Search**:
   CloudWatch → X-Ray → Trace Search.
   - Filter: `service("microvm-agentcore-obs-agent")`.

---

## Troubleshooting

### `AccessDeniedException` from Bedrock naming the build role

**Symptom:** invocations return `AccessDeniedException` on `bedrock:InvokeModel*`
mentioning the *build* role, even though `run_microvm` was called with the exec role ARN.

**Cause:** if an AWS client (e.g. a `BedrockModel`) is constructed at Python module-import
time, boto3 caches whichever credentials it found during the image build (the build role)
into the MicroVM snapshot. After resume, that cached principal keeps signing every request.

**Fix:** lazy-init AWS clients on the first request rather than at import time. See
`_get_agent()` in `app/agent.py` — the `BedrockModel` and Strands `Agent` are built the
first time `/invoke` is called, so they pick up the exec role's fresh credentials.

### `CreateMicrovmImage` fails with `Environment variable key 'AWS_REGION' is reserved`

**Cause:** `AWS_REGION` (and the `AWS_LAMBDA_MICROVM_*` keys) are reserved — Lambda injects
them into every MicroVM at runtime.

**Fix:** don't include them in `environmentVariables`. Read `AWS_REGION` from
`os.environ` at runtime instead. `config.otel_env()` in this sample already excludes it.

### `Failed to export logs batch code: 400, reason: The specified log stream does not exist`

**Cause:** the CloudWatch OTLP endpoint requires the log stream named in
`OTEL_EXPORTER_OTLP_*_HEADERS` to exist — it does not auto-create.

**Fix:** pre-create both `runtime-logs` and `spans` streams in the agent log group before
the MicroVM starts exporting. `deploy.py` does this in step 2.

### Image build fails with `Ready hook check failed: the application returned a client error (HTTP 4xx) response`

**Cause:** two possible reasons.
1. `hooks.microvmImageHooks.ready` was set to a path string. The field is actually a
   toggle (`"ENABLED"` / `"DISABLED"`); the URL path Lambda hits is fixed by the platform
   (`/aws/lambda-microvms/runtime/v1/ready`).
2. The app's `/ready` handler only answers a specific HTTP method that Lambda didn't use.

**Fix:** set `hooks.microvmImageHooks.ready = "ENABLED"` and make the handler answer any
HTTP method on any path variant of `/ready`. `agent.py` routes `do_GET`, `do_POST`,
`do_HEAD`, and `do_PUT` through a shared `_hook_response()` helper.

### No spans appear in the CloudWatch GenAI Observability dashboard

Check, in order:
1. **Transaction Search enabled?** `aws xray get-trace-segment-destination` should return
   `Destination: CloudWatchLogs, Status: ACTIVE`. `deploy.py` enables it if it isn't.
2. **Logs resource policy present?** Without a policy allowing `xray.amazonaws.com` to
   `logs:PutLogEvents` on the agent's log group, X-Ray silently drops spans destined for
   a custom log group. `deploy.py` installs `microvm-agentcore-obs-xray-span-delivery`.
3. **ADOT version?** `aws-opentelemetry-distro >= 0.18.0` is required for the
   `x-aws-log-group` / `x-aws-log-stream` trace-header routing. Earlier versions ignore
   those headers and deliver spans to the shared `aws/spans` group.

---

## Additional Resources

- [AWS Lambda MicroVMs — user guide](https://docs.aws.amazon.com/lambda/latest/dg/microvms-images.html)
- [Collecting CPU and memory metrics for AWS Lambda MicroVMs (AWS Compute Blog)](https://aws.amazon.com/blogs/compute/collecting-cpu-and-memory-metrics-for-aws-lambda-microvms/)
- [AgentCore Observability — enabling observability for agents hosted outside of AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html#observability-configure-non-agentcore)
- [Monitor on-premises and multi-cloud AI agents with AgentCore Observability (AWS ML Blog)](https://aws.amazon.com/blogs/machine-learning/monitor-on-premises-and-multi-cloud-ai-agents-with-agentcore-observability/)
- [AWS Distro for OpenTelemetry Python](https://aws-otel.github.io/docs/getting-started/python-sdk)
