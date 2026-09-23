# A2A agent on a CapacityProvider

An A2A (Agent-to-Agent) agent on Amazon Bedrock AgentCore Runtime, running on
**your own EC2 instances** instead of AgentCore's serverless compute.

Same CapacityProvider setup as [Sample 1](../01-basic-http-zip-and-container/README.md).
One field on the runtime differs:

```python
protocolConfiguration={"serverProtocol": "A2A"}   # instead of "HTTP"
```

That field changes the contract between the runtime and your artifact. It is the
whole sample.

This is the CapacityProvider counterpart to the upstream
[02-a2a-protocol sample](https://github.com/awslabs/agentcore-samples/tree/main/01-features/02-host-your-agent/01-runtime/01-hosting-agents/02-a2a-protocol),
which runs the same idea on serverless compute — see [Differences from
upstream](#differences-from-upstream).

## HTTP vs MCP vs A2A

The `serverProtocol` enum is `MCP | HTTP | A2A | AGUI`, and each value pins a
different port and surface:

| | HTTP (Sample 1) | MCP (Sample 2) | A2A (this one) |
|---|---|---|---|
| Port | 8080 | 8000 | **9000** |
| Endpoint | `POST /invocations` | `POST /mcp` | `POST /` |
| Health check | `GET /ping` | JSON-RPC `ping` | `GET /ping` |
| Framework | `BedrockAgentCoreApp` | `FastMCP` | `a2a-sdk`, via Strands + `serve_a2a` |
| Request body | your own JSON | JSON-RPC 2.0 | JSON-RPC 2.0 |
| Response | your own JSON | JSON-RPC, `text/event-stream` | JSON-RPC **task** object |
| Discovery | none | `tools/list` | `GET /.well-known/agent-card.json` |
| Model access | needs `bedrock:InvokeModel` | none | needs `bedrock:InvokeModel` |

The ports are fixed by the service contract, not by preference. Bind the wrong one
and invocations fail with HTTP 424 (`RuntimeClientError`), because the runtime
proxies to 9000 only.

Unlike MCP, A2A **keeps** `GET /ping`. That matters for which SDK you serve with.

## Why this agent uses two SDKs

Neither A2A integration alone satisfies the whole contract, so the sample composes
them. This is the one genuinely non-obvious thing here.

| | `strands.multiagent.a2a.A2AServer` | `bedrock_agentcore.runtime.a2a.serve_a2a` |
|---|---|---|
| Agent card | derived from the agent — **one skill per `@tool`** | generic: a single skill called `main` |
| `GET /ping` | **absent** (verified: 404) | present |
| AgentCore session headers | ignored | read into `BedrockAgentCoreContext` |

So the card comes from Strands and the serving from the AgentCore SDK:

```python
card = A2AServer(agent=agent, host=HOST, port=PORT).public_agent_card
serve_a2a(StrandsA2AExecutor(agent), card, host=HOST, port=PORT)
```

`A2AServer` is constructed only to read `public_agent_card` off it — it is never
served. `A2AServer.serve()` would still work today, since the runtime tolerates a
missing `/ping` on the A2A contract, but it drops the session header handling and
the documented health route.

Both packages must agree on the `a2a-sdk` major version. `bedrock-agentcore[a2a]`
targets 0.3; the separate `[a2a-v1]` extra is for 1.x, which has a different card
shape (`supported_interfaces` instead of `url`). Resolved here: `a2a-sdk` 0.3.26.

### Two things the old A2A spec did differently

Copy an old example and you will hit both:

* The method is **`message/send`**, not `tasks/send`. `tasks/send` was removed;
  `a2a-sdk` 0.3 rejects it with `-32601 Method not found`.
* A text part is **`{"kind": "text"}`**, not `{"type": "text"}`. The wire format is
  camelCase throughout: `messageId`, `contextId`, `taskId`.

The upstream sample's `invoke.py` uses the old form of both.

## The response is a task, not a message

This is the real difference from Sample 1's request/response. `message/send`
returns a full A2A task:

```json
{"result": {"kind": "task",
            "id": "0999ddb6-...", "contextId": "c3ccad01-...",
            "status": {"state": "completed", "timestamp": "..."},
            "artifacts": [{"name": "agent_response",
                           "parts": [{"kind": "text", "text": "40 plus 2 equals 42."}]}],
            "history": [ ... ]}}
```

The answer is in `artifacts`. `history` holds the whole exchange — including one
message per streamed chunk, so a two-line answer produces ~20 entries.

Because the task has an id, `tasks/get` can fetch it afterwards, and passing
`contextId` on the next `message/send` continues the conversation. `invoke.py`
demonstrates both.

## The agent card is not reachable through InvokeAgentRuntime

A2A discovery is a `GET /.well-known/agent-card.json`. `InvokeAgentRuntime` is
modelled as `POST /runtimes/{agentRuntimeArn}/invocations` only — no GET, no way to
choose a path — so the card the agent serves cannot be fetched that way. There is
no JSON-RPC fallback either: `agent/getAuthenticatedExtendedCard` returns
`-32603 Authenticated card not supported`.

**This is the one place where owning the fleet buys you something concrete.** The
instances are in *your* VPC, so a peer agent in the same VPC can reach port 9000 on
the instance directly and read the card the normal A2A way. On serverless there is
no such address to talk to.

The card's advertised `url` is therefore informational here — it reads
`http://localhost:9000/`, and only `AGENTCORE_RUNTIME_URL` changes it. `deploy.py`
does not set it: the value would have to come from the runtime ARN, which does not
exist until `create_agent_runtime` returns — the same call that takes
`environmentVariables`.

## Streaming is advertised but not usable on this path

The card says `streaming: true` — `A2AServer` hardcodes it — and `message/stream`
genuinely works when you reach the server directly. Through `InvokeAgentRuntime` it
does not: that API buffers the response body, so SSE frames only arrive after the
task has already finished. Use `message/send`.

## Files

| File | What it does |
|---|---|
| [agent/agent.py](agent/agent.py) | The agent. Strands card + `serve_a2a`, `whoami` and `add_numbers` tools. |
| [agent/requirements.txt](agent/requirements.txt) | `strands-agents[a2a]` + `bedrock-agentcore[a2a]`. |
| [deploy.py](deploy.py) | IAM → zip→S3 → CapacityProvider → runtime with `serverProtocol=A2A`. |
| [invoke.py](invoke.py) | `message/send`, `tasks/get`, a follow-up in the same context. |
| [cleanup.py](cleanup.py) | Deletes everything, including the EC2 fleet. |

Zip only, no helper module and no service model to install. Two clients:
`bedrock-agentcore-control` for the control plane and `bedrock-agentcore` for the
data plane. No `endpoint_url` anywhere — endpoints resolve from the region.

## The model

Default `global.anthropic.claude-sonnet-5` — a **global** inference profile rather
than a `us.` one, so the sample is not tied to one geography. Override with
`MODEL_ID`, which `deploy.py` passes through as a runtime environment variable, so
switching models does not mean rebuilding the zip.

The execution role grants `bedrock:InvokeModel` on `Resource: "*"` because a global
inference profile fans out to foundation models in several regions and cannot be
pinned to one region's ARN. Narrow that in a real deployment.

## Verified locally

Run against the real `agent/agent.py` on a laptop, with the same request bodies
`invoke.py` sends (only the boto3 data client faked, pointing at localhost):

```
ping                    {"status":"Healthy"}
agent card              basic_a2a_agent, skills=['whoami','add_numbers']
POST /invocations       404      ← confirms this is not the HTTP contract
message/send  [5.1s]    state=completed, 22 history messages
tasks/get               state=completed
follow-up     [5.1s]    same contextId; "CPU count (12) plus 40 equals 52"
                        ← the agent remembered the earlier turn
tasks/send              -32601 Method not found, raised immediately (no retry)
```

Not verified: a live deployment. Cold-start behaviour, session routing and the
transient `InternalServerException` are carried over from Samples 1 and 2, whose
measurements were taken live.

## Differences from upstream

Structurally the same idea; four substantive differences, all deliberate:

1. **CapacityProvider instead of serverless.** `capacityProviderConfiguration`
   replaces `networkConfiguration` — they are mutually exclusive. VPC, subnet and
   security group are declared once on the CapacityProvider.
2. **A2A comes from SDKs, not hand-rolled.** Upstream builds a `FastAPI` app with
   its own `AGENT_CARD` dict and a handler that string-matches the task envelope.
   This uses the real `a2a-sdk` via Strands and `serve_a2a`, so task lifecycle,
   `tasks/get`, contexts and discovery come from the protocol implementation.
3. **Current protocol vocabulary.** Upstream's `invoke.py` sends `tasks/send` with
   `{"type": "text"}`; both were replaced in the current spec. Its hand-rolled
   server accepts them because it parses the envelope itself — self-consistent, but
   not A2A-compatible.
4. **No endpoint creation.** Upstream calls `create_agent_runtime_endpoint` and
   then never uses the result. A `DEFAULT` endpoint already exists on this path, so
   the call is omitted.

## Prerequisites

Same as [Sample 1](../01-basic-http-zip-and-container/README.md): credentials for a
**role** that can create EC2 instances, IAM roles and S3 buckets, `AWS_REGION` set
(there is no default), a boto3 with the CapacityProvider APIs, and `uv` on PATH.
Plus Bedrock access to the model above — this sample calls a model, unlike Sample 2.

The caller's own role is reused as the CapacityProvider operator role, so it must
be assumable by `bedrock-agentcore.amazonaws.com`; `deploy.py` adds that trust
statement if it is missing.

Two things `deploy.py` does that are worth reading about before running it in a
shared account:

* It sets the account's [EC2 managed resource
  visibility](../01-basic-http-zip-and-container/README.md#your-instances-are-hidden-from-describe-instances-by-default)
  to `visible`. That is an **account-wide** change affecting every IAM principal,
  and `cleanup.py` does not revert it. `CP_MANAGED_VISIBILITY=skip` opts out.
* It shares the S3 bucket (`agentcore-cp-samples-<account>-<region>`) and the
  runtime IAM role (`agentcore-cp-samples-runtime-role`) with the other samples, so
  `cleanup.py` here removes them from under the others too. Harmless — each
  `deploy.py` recreates them — but a sibling sample's cleanup will then report
  `NoSuchBucket` / `NoSuchEntity`, which means "already gone", not "failed". This
  sample uses Sample 1's inline policy name (`runtime-access`) on purpose: both
  grant Bedrock access, so whichever runs last leaves an equivalent policy behind.

Configuration, all optional except the region:

| Variable | Default | Notes |
|---|---|---|
| `AWS_REGION` | *none — required* | The region. No fallback default. |
| `MODEL_ID` | `global.anthropic.claude-sonnet-5` | Passed to the agent as a runtime env var, so switching models needs no rebuild. |
| `CP_OS` | `LINUX_ARM64` | `LINUX_ARM64` or `LINUX_X86_64`. Drives the wheel platform. |
| `CP_INSTANCE_TYPE` | `m6g.large` | Must match `CP_OS`. |
| `CP_SUBNET_ID` / `CP_SECURITY_GROUP_ID` | default VPC's first subnet + default SG | Set both together to place the fleet in a VPC of your choosing — see below. |
| `CP_OPERATOR_ROLE_ARN` | the caller's own role | Override only if the calling role cannot be the operator role. |
| `CP_MANAGED_VISIBILITY` | *unset* | Set to `skip` to leave managed resource visibility untouched. |
| `IDLE_INSTANCE_TIMEOUT` / `IDLE_SESSION_TIMEOUT` | `900` | Seconds. Instance reaping and session reaping. |
| `MAX_LIFETIME` | `86400` | Seconds, ceiling 1209600 (14 days). Must be ≥ `IDLE_INSTANCE_TIMEOUT`. |

The VPC choice is worth a second thought here and not in the other samples. Because
the instances sit in a VPC you own, a peer agent in the same VPC can reach port
9000 directly and fetch the agent card the normal A2A way — which
`InvokeAgentRuntime` [cannot
do](#the-agent-card-is-not-reachable-through-invokeagentruntime). Putting the fleet
where its peers already live is the difference between A2A discovery working and
not existing.

## Quick start

```bash
python deploy.py       # ~3-4 min
python invoke.py       # message/send, tasks/get, follow-up
python invoke.py --prompt "what is 7 + 35?"
python cleanup.py
```

## Cost warning

Real EC2 instances in your account, billed while they run. Run `python cleanup.py`
when done, then confirm:

```bash
aws ec2 describe-instances --include-managed-resources --region "$AWS_REGION" \
  --filters "Name=tag:bedrock-agentcore:capacity-provider-id,Values=<cp-id>" \
  --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output table
```

`--include-managed-resources` is [not
optional](../01-basic-http-zip-and-container/README.md#your-instances-are-hidden-from-describe-instances-by-default):
CapacityProvider instances are EC2 managed resources, and without the flag a
still-running fleet prints as an empty table — which reads exactly like "cleanup
worked". `cleanup.py` prints this command for you, filled in with your own
CapacityProvider id.

No production data, no production workloads, and not in an account where a service
bug creating or deleting EC2 instances could affect real workloads.
