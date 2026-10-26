# Gateway-Enforced Access for an AgentCore Runtime

## Overview

![arch](./images/architecture.png)

A runtime with `CUSTOM_JWT` inbound auth accepts any caller holding a valid token for its audience, **including a caller who hits the runtime invocation URL directly and bypasses the gateway**. The gateway's interceptors, OBO exchange, and access controls only run when the request actually flows through the gateway, so a direct call sidesteps all of them.

This tutorial closes that gap with `allowedWorkloadConfiguration` on the runtime's inbound authorizer. It restricts which workloads in the request's identity chain may invoke the runtime. By allowing only the gateway's hosting environment, the runtime accepts requests that flowed through that gateway and rejects everything else, including direct invocations.

It builds directly on the [A2A agent on AgentCore Runtime](../../../01-attach-targets/http/agents/a2a-agents/agentcore-runtime/) lab: deploy that lab first, then apply the hardening here.

### Tutorial Details

| Information          | Details                                                       |
| :------------------- | :------------------------------------------------------------ |
| Tutorial type        | Interactive                                                   |
| AgentCore components | AgentCore Gateway, AgentCore Runtime                          |
| Gateway target type  | HTTP (`agentcoreRuntime`)                                     |
| Inbound Auth         | Microsoft Entra ID (CUSTOM_JWT) with OBO token exchange       |
| Runtime feature      | `allowedWorkloadConfiguration` on `customJWTAuthorizer`       |
| Example complexity   | Intermediate                                                  |
| SDK used             | boto3                                                         |

### How it works

When the gateway invokes the runtime, the request carries an identity chain that includes the gateway's workload identity. `allowedWorkloadConfiguration` tells the runtime authorizer which workloads in that chain are allowed:

- **hostingEnvironments** is a list of hosting environment ARNs whose workloads may invoke the runtime. The only supported hosting environment is AgentCore Gateway, so this is the gateway ARN.
- **workloadIdentities** is an optional list of specific workload identity names to allow.

With `hostingEnvironments=[{arn: <gateway-arn>}]`, a request that flowed through that gateway is accepted. A direct call to the runtime invocation URL has no gateway in its identity chain, so the runtime rejects it even when the bearer token is otherwise valid.

The AgentCore CLI does not expose this field, so the lab applies it with a small boto3 script, [`scripts/a2a-runtime-target/harden_runtime.py`](../../../gatewaylabproject/scripts/a2a-runtime-target/harden_runtime.py). `update_agent_runtime` has several required fields, so the script first calls `get_agent_runtime`, round-trips the existing configuration, and merges in only the new authorizer setting.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- The [A2A agent on AgentCore Runtime](../../../01-attach-targets/http/agents/a2a-agents/agentcore-runtime/) lab deployed end to end (the gateway, the runtime, and the OBO target). This tutorial reuses that lab's `scripts/a2a-runtime-target/.env` and its Entra ID app registrations. It mints its own tokens with that lab's callback server, so no token needs to carry over.

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1: Capture the gateway and runtime identifiers

This lab reuses the A2A lab's Entra ID exports to mint tokens in Steps 2 and 4, so have them set in your shell. Please follow the lab [here](../../../01-attach-targets/http/agents/a2a-agents/agentcore-runtime/README.md) to setup the architecture first.

```bash
export MICROSOFT_TENANT_ID=""             # Directory (tenant) ID
export MICROSOFT_GATEWAY_CLIENT_ID=""     # Gateway app (client) ID
export MICROSOFT_GATEWAY_CLIENT_SECRET="" # Gateway app client secret
export MICROSOFT_RUNTIME_CLIENT_ID=""     # Runtime app (client) ID
```

The A2A lab wrote the gateway id to its script-local `.env`. Read it back, and capture the runtime ARN and id from `agentcore status`:

```bash
export GATEWAY_ID=$(grep GATEWAY_ID scripts/a2a-runtime-target/.env | cut -d= -f2)

export RUNTIME_ARN=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['identifier'] for r in data['resources'] if r['name'] == 'monitoring_a2a_agent'))
")
export RUNTIME_ID=${RUNTIME_ARN##*/}

echo "Gateway ID:  $GATEWAY_ID"
echo "Runtime ARN: $RUNTIME_ARN"
echo "Runtime ID:  $RUNTIME_ID"
```

### Step 2 (optional): Confirm the gap

Before hardening, a direct call to the runtime invocation URL succeeds with a valid bearer token. The runtime validates the **runtime** app audience, so mint a runtime-audience token: sign in against the gateway app (which holds the delegated permission to the runtime's `access_as_user` scope), passing `--scope` so the token is issued for the runtime audience.

```bash
uv run scripts/obo-token-exchange/token_callback_server.py \
  $MICROSOFT_TENANT_ID $MICROSOFT_GATEWAY_CLIENT_ID $MICROSOFT_GATEWAY_CLIENT_SECRET \
  --scope "api://$MICROSOFT_RUNTIME_CLIENT_ID/access_as_user openid profile email"
```

Sign in when the browser opens, then capture the runtime-audience token:

```bash
export RUNTIME_TOKEN="<Token>"
```

Call the runtime invocation URL directly with it:

```bash
export RUNTIME_URL=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['invocationUrl'] for r in data['resources'] if r['name'] == 'monitoring_a2a_agent'))
")
export SESSION_ID=$(python3 -c "import uuid; print((uuid.uuid4().hex + uuid.uuid4().hex)[:40])")

curl -sS -X POST "$RUNTIME_URL" \
  -H "Authorization: Bearer $RUNTIME_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: $SESSION_ID" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "text", "text": "List up to 3 CloudWatch log group names."}],
        "messageId": "m1"
      }
    }
  }'
```

The direct call returns an answer, demonstrating that the runtime accepts any valid runtime-audience token regardless of whether the gateway was involved.

### Step 3: Harden the runtime

Apply `allowedWorkloadConfiguration`, allowing only this gateway's hosting environment. The script resolves the gateway ARN from `GATEWAY_ID`, reads the runtime's current configuration, and merges the new authorizer setting:

```bash
uv run python scripts/a2a-runtime-target/harden_runtime.py \
  --runtime-id "$RUNTIME_ID"
```

The script calls `update_agent_runtime` with the merged authorizer configuration:

```json
{
  "authorizerConfiguration": {
    "customJWTAuthorizer": {
      "discoveryUrl": "https://login.microsoftonline.com/<tenant>/.well-known/openid-configuration",
      "allowedAudience": ["api://<runtime-client-id>"],
      "allowedWorkloadConfiguration": {
        "hostingEnvironments": [
          { "arn": "arn:aws:bedrock-agentcore:<region>:<account>:gateway/<gateway-id>" }
        ]
      }
    }
  }
}
```

- `discoveryUrl` / `allowedAudience` are preserved from the runtime's existing authorizer; the script does not change them.
- `allowedWorkloadConfiguration.hostingEnvironments[].arn` is the gateway ARN. Only requests whose identity chain includes this gateway are accepted.
- To allow specific workload identities instead of (or in addition to) a whole gateway, pass `--workload-identity <name>` (repeatable); it populates the optional `workloadIdentities` list.


### Step 4: Verify enforcement

Re-run the **direct** call from Step 2 with the same `RUNTIME_TOKEN`. It now fails, because the request reaches the runtime without the gateway in its identity chain:

```bash
curl -sS -o /dev/null -w "%{http_code}\n" -X POST "$RUNTIME_URL" \
  -H "Authorization: Bearer $RUNTIME_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":"1","method":"message/send","params":{"message":{"role":"user","parts":[{"kind":"text","text":"ping"}],"messageId":"m1"}}}'
```

Expect a `401` or `403`. The token is still valid for the runtime audience; the rejection comes from the workload restriction (no approved gateway in the identity chain), which is exactly what the hardening enforces.

The call **through the gateway** still works, since that request carries the allowed gateway in its identity chain. This hop authenticates to the gateway, which validates the **gateway** app audience and then OBO-exchanges to the runtime, so it needs a **gateway-audience** token. Mint one by running the callback server **without** `--scope` (the default requests the gateway app's own scope):

```bash
uv run scripts/obo-token-exchange/token_callback_server.py \
  $MICROSOFT_TENANT_ID $MICROSOFT_GATEWAY_CLIENT_ID $MICROSOFT_GATEWAY_CLIENT_SECRET

export BEARER_TOKEN=$(curl -sS http://localhost:9090/token \
  | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])")
```

Then call through the gateway:

```bash
export GATEWAY_URL=$(grep GATEWAY_URL scripts/a2a-runtime-target/.env | cut -d= -f2)

curl -sS -X POST "${GATEWAY_URL}/a2a-runtime-target/invocations" \
  -H "Authorization: Bearer $BEARER_TOKEN" \
  -H "Content-Type: application/json" \
  -H "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id: $SESSION_ID" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "message/send",
    "params": {
      "message": {
        "role": "user",
        "parts": [{"kind": "text", "text": "List up to 3 CloudWatch log group names."}],
        "messageId": "m1"
      }
    }
  }'
```

The gateway path returns the CloudWatch answer, while the direct path is now blocked.

## Cleanup

The hardening lives on the runtime's authorizer, so it is removed when the runtime is removed as part of the [A2A lab cleanup](../../../01-attach-targets/http/agents/a2a-agents/agentcore-runtime/#cleanup). No separate teardown is needed.

To lift the restriction while keeping the runtime, re-run `harden_runtime.py` after editing it to omit `allowedWorkloadConfiguration`, or update the authorizer with `agentcore` or boto3 to remove that field.

## Documentation

- [AgentCore Gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [Configure inbound JWT authorizer](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/inbound-jwt-authorizer.html)
- [HTTP targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-targets-http.html)
- [A2A agent on AgentCore Runtime (prerequisite lab)](../../../01-attach-targets/http/agents/a2a-agents/agentcore-runtime/)
