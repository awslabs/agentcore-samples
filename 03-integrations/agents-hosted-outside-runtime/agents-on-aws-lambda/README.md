# Agents on AWS Lambda — AgentCore Observability

This folder contains three complementary patterns for running agents on AWS Lambda with
AgentCore observability.

## Patterns at a glance

| Pattern | Folder | Agent runs in |
|:--------|:-------|:--------------|
| Agent wrapped in Lambda | [`02-agent-in-lambda/`](./02-agent-in-lambda/) | Lambda execution environment |
| Lambda invokes AgentCore runtime | [`01-lambda-invokes-runtime/`](./01-lambda-invokes-runtime/) | AgentCore runtime (container) |
| Agent in Lambda MicroVM | [`03-agent-in-lambda-microvm/`](./03-agent-in-lambda-microvm/) | Lambda MicroVM (Firecracker VM) |

---

### Pattern 1 — Agent wrapped in Lambda

The Strands agent runs **entirely inside Lambda**. ADOT is bundled via pip
(`aws-opentelemetry-distro`) and X-Ray active tracing is enabled in the console.
Gen AI spans flow to CloudWatch Application Signals automatically.

Best for: lightweight agents, event-driven workloads, low-latency response requirements.

→ [02-agent-in-lambda/README.md](./02-agent-in-lambda/README.md)

---

### Pattern 2 — Lambda invokes AgentCore runtime

Lambda acts as an **orchestration layer** that calls an agent hosted on an AgentCore runtime.
Because Lambda's execution environment suppresses outgoing OTel spans by default, the ADOT
Lambda Layer and W3C trace context propagation are required to stitch Lambda and runtime spans
into a single connected trace.

Best for: long-running agents, agents that need persistent state, agents already deployed as
AgentCore runtimes.

→ [01-lambda-invokes-runtime/README.md](./01-lambda-invokes-runtime/README.md)

---

### Pattern 3 — Agent in Lambda MicroVM

The Strands agent runs inside a **Lambda MicroVM** — a Firecracker VM with VM-level
isolation, snapshot start and resume, and a per-VM HTTPS endpoint. The ADOT SDK
(`aws-opentelemetry-distro`) is installed into the container image and OTEL configuration is
baked into the MicroVM snapshot at image build time. OTLP requests are SigV4-signed with
the execution role's temporary credentials and delivered to the agent's own log group under
`/aws/bedrock-agentcore/runtimes/<agent-id>`.

Best for: agents that need a full Linux environment, long-lived process state across
invocations, or a code-execution sandbox.

→ [03-agent-in-lambda-microvm/README.md](./03-agent-in-lambda-microvm/README.md)
