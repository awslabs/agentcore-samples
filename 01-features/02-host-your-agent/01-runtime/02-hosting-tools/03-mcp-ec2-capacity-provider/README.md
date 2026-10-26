# MCP server on a CapacityProvider

An MCP server on Amazon Bedrock AgentCore Runtime, running on **your own EC2
instances** instead of AgentCore's serverless compute.

Same CapacityProvider setup as [Sample 1](../01-basic-http-zip-and-container/README.md).
One field on the runtime differs:

```python
protocolConfiguration={"serverProtocol": "MCP"}   # instead of "HTTP"
```

That field changes the contract between the runtime and your artifact. It is the
whole sample.

## HTTP agent vs MCP server

The `serverProtocol` enum is `MCP | HTTP | A2A | AGUI`, and each value pins a
different port and surface:

| | HTTP | MCP |
|---|---|---|
| Port | 8080 | **8000** |
| Endpoint | `POST /invocations` | `POST /mcp` |
| Health check | `GET /ping` | JSON-RPC `ping` to `POST /mcp` — no `/ping` route |
| Framework | `BedrockAgentCoreApp` from `bedrock-agentcore` | `FastMCP` from `mcp` |
| Request body | your own JSON, e.g. `{"prompt": "..."}` | JSON-RPC 2.0 envelope |
| Response | your own JSON | JSON-RPC, as `text/event-stream` |

The ports are fixed by the AgentCore Runtime service contract, not by preference.

Note what is *not* in the MCP artifact: `bedrock-agentcore`. An MCP server does
not use `BedrockAgentCoreApp` at all, so `requirements.txt` only needs `mcp`.
There is also no `bedrock:InvokeModel` in the execution role — this server calls
no model. It exposes tools; the caller supplies the intelligence.

## The four things the server must get right

```python
mcp = FastMCP(host="0.0.0.0", port=8000, stateless_http=True)
...
mcp.run(transport="streamable-http")
```
 
1. **`port=8000`** — the service contract. Wrong port, unreachable server.
2. **`host="0.0.0.0"`** — FastMCP defaults to `127.0.0.1`, which is unreachable
   from outside the instance.
3. **`stateless_http=True`** — two requests in one session are not guaranteed to
   reach the same server process, so the transport must not hold session state.
   FastMCP defaults to **stateful**, which fails here.
4. **`transport="streamable-http"`** — not `stdio` (no process to pipe to) and not
   the deprecated `sse`.

`streamable_http_path` already defaults to `/mcp`, so it needs no override.

### Why `stateless_http=True` is not optional

A stateful `FastMCP` demands the session id it handed out on a previous request:

```
stateless_http=True     200 OK, tool result returned, no mcp-session-id header
stateless_http=False    400 Bad Request
                        {"error":{"code":-32600,"message":"Bad Request: Missing session ID"}}
```

As soon as a second instance or process is in play, that id is unknown and every
call fails. And a second instance *is* in play — see below.

In stateless mode the session is constructed already-initialized, so each request
stands alone. `initialize` still works and is still worth sending — it is how you
read `serverInfo` and negotiate `protocolVersion` — but it is not a precondition
for tool calls.

### The load balancer is real: one session, two instances

Calling `whoami` repeatedly on **one unchanging `runtimeSessionId`**, across two
runs:

| run | call | `process_id` | hostname |
|---|---|---|---|
| A | 1 | `11b73fbd` | `ip-172-31-46-95` |
| A | 2 | `e4ba189f` | `ip-172-31-41-123` |
| B | 1 | `8f741275` | `ip-172-31-45-68` |
| B | 2 | `fad0925b` | `ip-172-31-44-242` |
| B | 3 | `cbf6b64c` | `ip-172-31-41-175` |
| B | 4 | `bada16e0` | `ip-172-31-45-220` |
| B | 5 | `44939c3f` | `ip-172-31-44-96` |

**Seven calls, seven distinct processes on seven distinct hosts, one session id —
no repeats at all.** Run A's calls were two seconds apart; run B's minutes apart.

So **a `runtimeSessionId` routes a request; it does not pin one.** That is exactly
the condition a stateful `FastMCP` cannot survive, which makes
`stateless_http=True` a correctness requirement rather than a tuning preference.
Do not build on in-memory or on-disk instance state as a source of truth.

### The health check is a JSON-RPC `ping`

There is no `GET /ping` route on an MCP runtime. The server's log shows what the
service actually sends:

```
StreamableHTTP session manager started
Uvicorn running on http://0.0.0.0:8000
Processing request of type PingRequest
INFO:  100.88.0.1:40798 - "POST /mcp HTTP/1.1" 200 OK
```

`FastMCP` answers it for you — you do not implement a ping handler as you would
for an HTTP agent (contrast Sample 1, where `@app.ping` is the whole point).

## Version warning: mcp 2.0 renamed FastMCP

`requirements.txt` pins `mcp>=1.10.0,<2.0.0` deliberately. In **mcp 2.0.0**,
`FastMCP` was renamed `MCPServer` and moved from `mcp.server.fastmcp` to
`mcp.server.mcpserver`, with **no compatibility alias**. So
`from mcp.server.fastmcp import FastMCP` raises `ImportError` on 2.x. If you drop
the pin, the import and constructor must be updated together.

## Files

| File | What it does |
|---|---|
| [agent/agent.py](agent/agent.py) | The MCP server: `add_numbers`, `greet`, `whoami`. |
| [agent/requirements.txt](agent/requirements.txt) | Just `mcp` — pinned below 2.0. |
| [deploy.py](deploy.py) | IAM → zip→S3 → CapacityProvider → runtime with `serverProtocol=MCP`. |
| [invoke.py](invoke.py) | JSON-RPC client: `initialize`, `tools/list`, `tools/call`. |
| [cleanup.py](cleanup.py) | Deletes everything, including the EC2 fleet. |

Zip only, no helper module and no service model to install. Two clients:
`bedrock-agentcore-control` for the control plane and `bedrock-agentcore` for the
data plane. No `endpoint_url` anywhere — endpoints resolve from the region.

## Calling it

`InvokeAgentRuntime` models the MCP headers explicitly — `accept`, `mcpSessionId`
(→ `Mcp-Session-Id`) and `mcpProtocolVersion` (→ `Mcp-Protocol-Version`):

```python
data.invoke_agent_runtime(
    agentRuntimeArn=arn,
    runtimeSessionId=session_id,
    payload=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).encode(),
    contentType="application/json",
    accept="application/json, text/event-stream",   # transport requires both
    mcpProtocolVersion="2024-11-05",
)
```

`mcpSessionId` is for stateful servers. This one is stateless, so it is omitted.

The response is an SSE frame, not bare JSON, so it needs unwrapping:

```
event: message
data: {"jsonrpc":"2.0","id":1,"result":{...}}
```

`invoke.py` handles that in `parse_body()`, accepting a plain JSON body too in
case the server negotiated `json_response` mode.

### The session is reused across runs

`invoke.py` caches its `runtimeSessionId` in `.mcp_session.json` and reuses it on
the next run, so only the first run pays the cold start and everything after it is
genuinely warm. Two notes:

* **Sending no session id is not the way to do this.** The service then mints one
  per request, so every call is a fresh session — the opposite of warm.
* The cache expires after `idleRuntimeSessionTimeout`, because past that the
  session is gone and the id can no longer be warm. `--new-session` starts cold on
  purpose.

A reused id keeps you in one *session*, not on one *instance* — see [the load
balancer](#the-load-balancer-is-real-one-session-two-instances).

### Errors come back inside the JSON-RPC envelope

Over MCP a service-side failure cannot be a bare HTTP error, so it arrives as a
200 carrying an error envelope:

```json
{"jsonrpc":"2.0","error":{"code":-32603,
 "message":"An internal error occurred while processing the request."},"id":1}

{"jsonrpc":"2.0","error":{"code":-32010,
 "message":"An error occurred when starting the runtime. Please check your
            CloudWatch logs for more information."},"id":1}
```

**A 200 with an error envelope is still a failure.** Check the envelope, not the
HTTP status — an early version of `invoke.py` printed `serverInfo: None` and
`0 tools` and exited 0, because it only looked at the call.

Neither code is in MCP's own error range; both are the service, and both are
retryable. `-32010` says "check your CloudWatch logs", and doing so showed the
server logging `Application startup complete` and clean 200s throughout — so it
reports the service failing to place a session on an instance, not a defect in
your server.

Latency is a hint, not a rule: a failing `-32010` took 522 s while successful
calls took 84 s, 936 s and 981 s. So treat any error envelope as retryable
regardless of how long it took, which is what `invoke.py` does — 6 attempts with
backoff capped at 120 s. It also reports **per-attempt** latency rather than the
elapsed total, since a total buries the signal under its own backoff sleeps.

## Verified live

```
✓ CapacityProvider READY: basic_mcp_1785927473-xVrGzYC1fd
✓ Runtime READY:          basic_mcp_1785927473-33GwOdBDjc
  protocolConfiguration = {"serverProtocol": "MCP"}
```

```
── 1. initialize
  serverInfo      : {'name': 'FastMCP', 'version': '1.29.0'}
  protocolVersion : 2024-11-05

── 2. tools/list
  3 tools
  - add_numbers: Add two numbers together.
  - greet: Greet someone by name.
  - whoami: Report the machine this MCP server is running on.

── 3. tools/call add_numbers
  40 + 2 = 42
```

`serverInfo` is the real `FastMCP` running on the instance and the tool
descriptions are the docstrings from [agent/agent.py](agent/agent.py) — proof the
response came from the deployed artifact and not from the service frontend.

And `whoami`, which is the point of running MCP on a CapacityProvider at all:

```json
{
  "process_id": "11b73fbd",
  "hostname": "ip-172-31-46-95.<region>.compute.internal",
  "machine": "aarch64",
  "python": "3.12.13",
  "cpu_count": 2,
  "memory_mb": 7735,
  "protocol": "MCP",
  "instance_type": "m6g.large"
}
```

`m6g.large`, 2 vCPU, 7735 MB, `aarch64` — the instance type this sample asked
for, in the account, reporting for itself.

## Prerequisites

Same as [Sample 1](../01-basic-http-zip-and-container/README.md): credentials for
a **role** that can create EC2 instances, IAM roles and S3 buckets, `AWS_REGION`
set (there is no default), a boto3 with the CapacityProvider APIs, and `uv` on
PATH. No Bedrock model access needed here — this server calls no model.

The caller's own role is reused as the CapacityProvider operator role, so it must
be assumable by `bedrock-agentcore.amazonaws.com`; `deploy.py` adds that trust
statement if it is missing.

Two things `deploy.py` does that are worth reading about before running it in a
shared account:

* It sets the account's [EC2 managed resource
  visibility](../01-basic-http-zip-and-container/README.md#your-instances-are-hidden-from-describe-instances-by-default)
  to `visible`. That is an **account-wide** change affecting every IAM principal,
  and `cleanup.py` does not revert it. `CP_MANAGED_VISIBILITY=skip` opts out.
* It shares the S3 bucket and the runtime IAM role with the other samples — see
  [below](#shared-with-the-other-samples).

Configuration, all optional except the region:

| Variable | Default | Notes |
|---|---|---|
| `AWS_REGION` | *none — required* | The region. No fallback default. |
| `CP_OS` | `LINUX_ARM64` | `LINUX_ARM64` or `LINUX_X86_64`. Drives the wheel platform. |
| `CP_INSTANCE_TYPE` | `m6g.large` | Must match `CP_OS`. |
| `CP_SUBNET_ID` / `CP_SECURITY_GROUP_ID` | default VPC's first subnet + default SG | Set both together to place the fleet in a VPC of your choosing. |
| `CP_OPERATOR_ROLE_ARN` | the caller's own role | Override only if the calling role cannot be the operator role. |
| `CP_MANAGED_VISIBILITY` | *unset* | Set to `skip` to leave managed resource visibility untouched. |
| `IDLE_INSTANCE_TIMEOUT` / `IDLE_SESSION_TIMEOUT` | `900` | Seconds. Instance reaping and session reaping. |
| `MAX_LIFETIME` | `86400` | Seconds, ceiling 1209600 (14 days). Must be ≥ `IDLE_INSTANCE_TIMEOUT`. |

## Shared with the other samples

The S3 bucket (`agentcore-cp-samples-<account>-<region>`) and the runtime IAM role
(`agentcore-cp-samples-runtime-role`) carry the same names in every sample, so
`cleanup.py` here removes them from under the others too. Harmless — each
`deploy.py` recreates them if missing — but a sibling sample's cleanup will then
report `NoSuchBucket` / `NoSuchEntity`, which means "already gone", not "failed".

This sample attaches its inline policy as `mcp-access` rather than Sample 1's
`runtime-access`, so the two do not overwrite each other: this policy grants
strictly less (no Bedrock), and reusing Sample 1's name would take its model
access away. The flip side is that if you have also run Sample 1, the shared role
still carries Sample 1's Bedrock grant. So *this policy* is minimal; the *role* is
not.

## Quick start

```bash
python deploy.py       # ~3-4 min
python invoke.py       # initialize, tools/list, tools/call — first run is cold
python invoke.py       # again, reusing the session — warm throughout
python invoke.py --tool greet --args '{"name": "Ada"}'
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
