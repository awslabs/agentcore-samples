# Synchronizing MCP server capabilities with AgentCore gateway

## Overview

Tool, prompt, and resource definitions on an MCP server change over time. AgentCore gateway has three mechanisms for keeping its catalog in sync with what each MCP server target actually exposes:

1. **Explicit synchronization** — call `SynchronizeGatewayTargets` on demand after the upstream MCP server changes. 2. **Implicit synchronization** — `CreateGatewayTarget` and `UpdateGatewayTarget` always re-read the upstream server's catalog as part of the operation. 3. **Dynamic listing** (`listingMode='DYNAMIC'`) — gateway forwards every list request (`tools/list`, `prompts/list`, `resources/list`, `resources/templates/list`) to the MCP server, so no synchronization is ever required.

> **How (1) and (2) relate to (3).** Explicit and implicit synchronization are both **control-plane operations on `listingMode='DEFAULT'` targets** — i.e. they populate the AgentCore gateway _cache_ that DEFAULT-mode list calls will be answered from. `CreateGatewayTarget` is the very first cache fill (implicit at create time); `UpdateGatewayTarget` refills it as a side effect of every update; `SynchronizeGatewayTargets` is the on-demand refill in between. DYNAMIC-mode targets are not cached during create or update operations.

## Workshop roadmap

| Step  | What you do |
| ----- | --- |
| **1** | Set up the notebook environment (env vars, utilities, logging). |
| **2** | Create the AgentCore gateway: Cognito inbound auth, IAM role, then the gateway itself. |
| **3** | Deploy the initial FastMCP server (just `getOrder` + `updateOrder` for now) to AgentCore runtime. |
| **4** | Wire that MCP Server in as a gateway target (outbound OAuth, target creation, inbound token, `GatewayMCPClient` helper). |
| **5** | Demonstrate **explicit synchronization** — add a tool, redeploy, observe the gateway catalog stays stale until `SynchronizeGatewayTargets` runs. |
| **6** | Demonstrate **implicit synchronization** — add another tool, redeploy, then call `UpdateGatewayTarget` and watch the catalog refresh as a side effect. |
| **7** | Demonstrate **dynamic listing** — create a second target with `listingMode='DYNAMIC'`, and compare cached vs live across list tools operations. |
| **8** | Clean up. |

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on gateway patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../../../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../../../../00-optional-setup/).

Once deployed, capture the outputs into environment variables:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

export MCP_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`MCPClientId`].OutputValue' --output text)

export GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayClientId`].OutputValue' --output text)

export TOKEN_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`TokenEndpoint`].OutputValue' --output text)

export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

export MCP_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $MCP_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)

export GATEWAY_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)
```

### Step 2: Register MCP Server (AgentCore CLI)

The MCP server code is at [`gatewaylabproject/app/labsync/main.py`](../../../../gatewaylabproject/app/labsync/main.py). The sync demos in Steps 5 and 6 work by _adding_ tools to this file and watching when the gateway notices. Start small: just two tools.

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory:

```bash
agentcore add agent \
  --name sync_mcp_server \
  --type byo \
  --language Python \
  --protocol MCP \
  --code-location app/labsync \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $MCP_CLIENT_ID \
  --allowed-scopes api/mcp \
  --client-id $MCP_CLIENT_ID \
  --client-secret $MCP_CLIENT_SECRET
```

### Step 3: Create AgentCore gateway (AgentCore CLI)

> [!IMPORTANT]
> This tutorial uses `searchType=NONE` (no semantic search) because `listingMode='DYNAMIC'` targets (Step 7) are not supported on gateways with semantic search enabled.

```bash
agentcore add gateway \
  --name sync-gateway \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $DISCOVERY_URL \
  --allowed-clients $GATEWAY_CLIENT_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --client-secret $GATEWAY_CLIENT_SECRET \
  --exception-level DEBUG
```

### Step 4: Deploy MCP Server and gateway (AgentCore CLI)

```bash
agentcore deploy --yes
```

Capture the MCP server URL, gateway ID, and gateway URL:

```bash
export MCP_SERVER_URL=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['invocationUrl'] for r in data['resources'] if r['name'] == 'sync_mcp_server'))
")

export GATEWAY_ID=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['identifier'] for r in data['resources'] if r['name'] == 'sync-gateway'))
")

export GATEWAY_URL=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier $GATEWAY_ID \
  --query 'gatewayUrl' --output text)/mcp

echo "MCP Server URL: $MCP_SERVER_URL"
echo "gateway ID:     $GATEWAY_ID"
echo "gateway URL:    $GATEWAY_URL"
```

### Step 5: Create gateway Target (AgentCore CLI)

```bash
agentcore add gateway-target \
  --name sync-mcp-server-target \
  --type mcp-server \
  --endpoint $MCP_SERVER_URL \
  --gateway sync-gateway \
  --outbound-auth oauth \
  --credential-name sync_mcp_server-oauth
```

### Step 6: Deploy gateway Target (AgentCore CLI)

```bash
agentcore deploy --yes
agentcore status
```

Capture the target ID (needed for explicit sync):

```bash
export TARGET_ID=$(aws bedrock-agentcore-control list-gateway-targets \
  --gateway-identifier $GATEWAY_ID \
  --query 'items[?name==`sync-mcp-server-target`].targetId' --output text)
echo "Target ID: $TARGET_ID"
```

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to explore tools interactively.

## Step 5: Explicit synchronization with `SynchronizeGatewayTargets`

### Step 5.1: Background

`SynchronizeGatewayTargets` is a **control-plane operation that refills the catalog cache for `listingMode='DEFAULT'` targets** — gateway opens a session with the MCP server, retrieves and processes its catalog (tools, prompts, resources, resource templates), prefixes tool/prompt names with the target name to prevent collisions, and updates its persistent index.

Because this populates a _cache_, it only matters for DEFAULT-mode targets. DYNAMIC-mode targets never read from the cache, so calling `SynchronizeGatewayTargets` on them is unnecessary.

Below we switch the MCP server entrypoint to [`main1.py`](../../../../gatewaylabproject/app/labsync/main1.py) which adds a new tool (`cancelOrder`), redeploy, observe that the gateway's tool list still doesn't include it (the cache is stale), then call `SynchronizeGatewayTargets` and watch the new tool appear.

![Diagram](../images/mcp-server-target-explicit-sync.png)

### Step 5.2: Update the MCP server (add `cancelOrder`)

The updated server code is at [`gatewaylabproject/app/labsync/main1.py`](../../../../gatewaylabproject/app/labsync/main1.py). Switch the entrypoint and redeploy:

```bash
python3 -c "
import json
with open('agentcore/agentcore.json', 'r') as f:
    data = json.load(f)
for rt in data.get('runtimes', []):
    if rt.get('name') == 'sync_mcp_server':
        rt['entrypoint'] = 'main1.py'
        break
with open('agentcore/agentcore.json', 'w') as f:
    json.dump(data, f, indent=2)
print('Updated entrypoint to main1.py')
"

agentcore deploy --yes
```

### Step 5.3: Verify tools are stale, then sync

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../../../05-community/gateway-mcp-inspector/) to explore tools interactively.

![demo1](./images/demo1.gif)

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory, install Python dependencies (first time only):

```bash
uv sync
```

List tools — the new `cancelOrder` should NOT appear yet (cache is stale):

```bash
uv run python scripts/sync/demo.py list-tools
```

Run explicit sync — calls `SynchronizeGatewayTargets`, waits, then lists tools again:

```bash
uv run python scripts/sync/demo.py explicit-sync
```

![demo2](./images/demo2.gif)

## Step 6: Implicit synchronization with `UpdateGatewayTarget`

### Step 6.1: Background

`CreateGatewayTarget` and `UpdateGatewayTarget` are also **control-plane operations on `listingMode='DEFAULT'` targets**, and they perform the same catalog refill as `SynchronizeGatewayTargets` — just bundled into the same call as the create/update. `CreateGatewayTarget` is the very first cache fill for a new target; `UpdateGatewayTarget` refills it as an automatic side effect of every update. No separate sync call is needed afterwards.

Like explicit sync, this only matters for DEFAULT-mode targets. DYNAMIC targets don't have a cache to fill.

Below we switch the entrypoint to [`main2.py`](../../../../gatewaylabproject/app/labsync/main2.py) which adds `deleteOrder`, redeploy, then update the gateway target's description via `agentcore deploy`. The catalog refresh happens implicitly as a side effect of `UpdateGatewayTarget`.

![Diagram](../images/mcp-server-target-implicit-sync.png)

### Step 6.2: Update the MCP server and target description

The updated server code is at [`gatewaylabproject/app/labsync/main2.py`](../../../../gatewaylabproject/app/labsync/main2.py). Switch the entrypoint to `main2.py` and update the target description in `agentcore/agentcore.json`, then deploy. The target description change triggers `UpdateGatewayTarget`, which implicitly re-syncs the catalog:

```bash
python3 -c "
import json
with open('agentcore/agentcore.json', 'r') as f:
    data = json.load(f)
for rt in data.get('runtimes', []):
    if rt.get('name') == 'sync_mcp_server':
        rt['entrypoint'] = 'main2.py'
        break
for gw in data.get('gateways', []):
    for tgt in gw.get('targets', []):
        if tgt.get('name') == 'sync-mcp-server-target':
            tgt['description'] = 'Sync MCP Server target - with deleteOrder'
            break
with open('agentcore/agentcore.json', 'w') as f:
    json.dump(data, f, indent=2)
print('Updated entrypoint to main2.py and target description')
"

agentcore deploy --yes
```

### Step 6.3: Verify the catalog refreshed

List tools — `deleteOrder` should now appear (the deploy triggered an implicit sync):

```bash
uv run python scripts/sync/demo.py list-tools
```

![demo3](./images/demo3.gif)

## Step 7: Dynamic listing with `listingMode='DYNAMIC'`

### Step 7.1: Background — DEFAULT vs DYNAMIC

By default, AgentCore gateway _caches_ the capabilities (tools, prompts, resources, resource templates) it discovered when the target was created, updated, or last synchronized. With `listingMode='DEFAULT'`, the four MCP list operations are answered from gateway's catalog **without invoking the upstream MCP server**. Fast and resilient, but stale until the next sync.

With `listingMode='DYNAMIC'`, every list request is forwarded to the upstream MCP server, and no synchronization is ever required.

A few things to note:

- DYNAMIC mode is **not interoperable with semantic search** (`x_amz_bedrock_agentcore_search`) or with outbound three-legged OAuth (3LO).
- DYNAMIC mode applies uniformly across all four primitive types — tools, prompts, resources, and resource templates.

### Step 7.2: Extend the MCP server with prompts, resources, and a resource template

To demonstrate DEFAULT vs DYNAMIC for **all four** list operations, the upstream MCP server needs to expose all four primitive types. Switch the entrypoint to [`main3.py`](../../../../gatewaylabproject/app/labsync/main3.py) which adds prompts and resources alongside the existing tools, and adds a fresh tool `archiveOrder` so the cached/live contrast is visible on the tools axis too.

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory:

```bash
python3 -c "
import json
with open('agentcore/agentcore.json', 'r') as f:
    data = json.load(f)
for rt in data.get('runtimes', []):
    if rt.get('name') == 'sync_mcp_server':
        rt['entrypoint'] = 'main3.py'
        break
with open('agentcore/agentcore.json', 'w') as f:
    json.dump(data, f, indent=2)
print('Updated entrypoint to main3.py')
"

agentcore deploy --yes
```

### Step 7.3: Create a DYNAMIC gateway target (AgentCore CLI)

Rather than mutate the existing target (which uses the default `listingMode='DEFAULT'`), create a _separate_ target so both modes coexist on the same gateway and can be compared side-by-side. Both targets point at the same upstream MCP server URL, but they will report different capabilities depending on whether they read from a cache or from the live server.

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory:

```bash
agentcore add gateway-target \
  --name sync-mcp-server-target-dynamic \
  --type mcp-server \
  --endpoint $MCP_SERVER_URL \
  --gateway sync-gateway \
  --outbound-auth oauth \
  --credential-name sync_mcp_server-oauth

agentcore deploy --yes
```

> [!NOTE]
> After creating, update `agentcore/agentcore.json` to set `"listingMode": "DYNAMIC"` on this target's `mcpServer` configuration.

### Step 7.4: Side-by-side list tools

Both targets point at the same MCP server URL. The DEFAULT target's catalog is whatever was last synced. The DYNAMIC target fetches live on every list call.

> **Pagination is per-target.** When multiple targets are attached, `tools/list` returns **one target's tools per page**, with a `nextCursor` for the next target.

```bash
uv run python scripts/sync/demo.py list-all
```

![demo4](./images/demo4.gif)

### Step 7.6: DEFAULT vs DYNAMIC summary

| Aspect                                                                     | DEFAULT                   | DYNAMIC                      |
| -------------------------------------------------------------------------- | ------------------------- | ---------------------------- |
| `tools/list`, `prompts/list`, `resources/list`, `resources/templates/list` | served from gateway cache | forwarded to MCP server live |
| `tools/call`, `prompts/get`, `resources/read`                              | live to MCP server        | live to MCP server           |
| Requires `SynchronizeGatewayTargets` after capability changes              | yes                       | no                           |
| Compatible with semantic search (`x_amz_bedrock_agentcore_search`)         | yes                       | no                           |
| Compatible with outbound 3LO OAuth                                         | yes                       | no                           |

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../../gatewaylabproject/) directory, remove resources in reverse order:

```bash
agentcore remove gateway-target --name sync-mcp-server-target -y
agentcore remove gateway-target --name sync-mcp-server-target-dynamic -y
agentcore remove gateway --name sync-gateway -y
agentcore remove agent --name sync_mcp_server -y
```

> [!NOTE]
> The auto-created credentials are CLI-managed. Remove them by running `agentcore remove all --yes` or manually from `agentcore/agentcore.json`, then run `agentcore deploy --yes`.

```bash
agentcore deploy --yes
```

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [Synchronize gateway Targets](https://docs.aws.amazon.com/bedrock-agentcore-control/latest/APIReference/API_SynchronizeGatewayTargets.html)
