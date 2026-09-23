# gateway Integration

| Information         | Details                                                        |
|:--------------------|:---------------------------------------------------------------|
| Tutorial type       | Advanced Example                                               |
| Agent type          | Search and retrieval assistant                                 |
| Agentic Framework   | None (direct boto3)                                            |
| LLM model           | Anthropic Claude Haiku 4.5                                     |
| Tutorial components | AgentCore harness + gateway — MCP proxy, tool routing          |
| Example complexity  | Intermediate                                                   |

## Overview

Demonstrates the full AgentCore gateway lifecycle: create a gateway with MCP protocol,
add an MCP target (Exa search), wire it to a harness, and invoke the agent so it
discovers and calls tools via the gateway.

## What is AgentCore gateway?

AgentCore gateway is a managed proxy between your agent and external tool servers (MCP, HTTP).
It gives you one place to handle auth, routing and observability for all tool traffic — without
changing your agent code.

```
[harness] → tools=[{type: "agentcore_gateway", gatewayArn: ...}]
                │
                ▼   inbound auth: authorizerType (NONE here)
            [gateway] ── routing + observability
                │
                ▼   outbound auth: credentialProviderConfigurations (unset here)
            [MCP Target] ── external MCP server
```

## End-to-End Flow

The step numbers match the ones the script prints:

```
Step 0. Create IAM execution role   (reuses utils/iam.py helper)
Step 1. Create gateway              → MCP protocol, no inbound authorizer
Step 2. Add MCP target              → remote MCP server endpoint (default: Exa)
Step 3. Create harness              → plain harness; it holds no gateway reference
Step 4. invoke_harness              → passes the gateway ARN in `tools`, agent calls them
Then    Cleanup                     → delete harness, target, gateway, IAM role
```

The harness and the gateway are not bound to each other. `create_harness` takes no
gateway parameter — the gateway ARN travels per-invoke in the `tools` argument to
`invoke_harness`, so one harness can be pointed at different gateways on different
calls, and deleting either resource has no effect on the other.

A gateway routes to its targets as soon as they are `READY`, so no routing rule is
needed to run this sample. Rules (`CreateGatewayRule`) are a separate, optional
feature for shaping traffic across multiple targets.

## Sample Prompts

**Prompt**: "Search the web for the top 5 things to do in Tokyo in spring 2025."
**Expected Behavior**: Agent calls the Exa MCP tool via the gateway, retrieves search results, formats a numbered list.

**Prompt**: "Find recent news about Amazon Bedrock and summarize the top 3 stories."
**Expected Behavior**: Agent uses the search tool, retrieves articles, provides a summary.

**Prompt**: "What are the best hiking trails near Seattle? Include difficulty ratings."
**Expected Behavior**: Agent performs a web search via gateway, returns structured trail information.

**Prompt**: "Search for 'AWS re:Invent 2024 announcements' and list the top 5."
**Expected Behavior**: Agent calls search tool, returns a numbered list of major announcements.

## Key Concepts

**gateway vs direct MCP**: The `agentcore_gateway` tool type routes through gateway for centralized control. Direct `remote_mcp` connects without a gateway proxy.

**Target routing**: Targets can be MCP servers, Lambda functions, or HTTP endpoints. A single gateway can have multiple targets.

**Two independent authorization sides**: a gateway controls inbound and outbound auth separately, and this sample uses neither, to keep the focus on the plumbing.

| | Parameter | Values | This sample |
|:--|:--|:--|:--|
| **Inbound** — who may call the gateway | `authorizerType` on `create_gateway` | `NONE`, `AWS_IAM`, `CUSTOM_JWT`, `AUTHENTICATE_ONLY` | `NONE` |
| **Outbound** — how the gateway authenticates to the tool server | `credentialProviderConfigurations` on `create_gateway_target` | `GATEWAY_IAM_ROLE`, `OAUTH`, `API_KEY`, `CALLER_IAM_CREDENTIALS`, `JWT_PASSTHROUGH` | not set |

`NONE` means no inbound authorizer — it is not "IAM auth"; `AWS_IAM` is a separate value this sample does not use. For production, use `CUSTOM_JWT` with a Cognito user pool (see `07-oauth/`, which configures both sides).

## Troubleshooting

### Issue: gateway stays in `CREATING` for >2 minutes
**Solution**: gateway provisioning can take up to 2-3 minutes, and the polling loop allows 300s, so just wait. In practice both the gateway and the target reach `READY` within about 5 seconds.

### Issue: Target `FAILED` status
**Solution**: Check the MCP endpoint is reachable. The default Exa endpoint (`https://mcp.exa.ai/mcp`) requires public internet access from the gateway. The poller raises with the service's `statusReasons`, which names the actual cause.

### Issue: `Gateway UPDATE_UNSUCCESSFUL` or `Target SYNCHRONIZE_UNSUCCESSFUL`
**Solution**: These are the failure statuses for updates rather than creates — a gateway reports `UPDATE_UNSUCCESSFUL`, not `FAILED`, and a target adds `SYNCHRONIZE_UNSUCCESSFUL`. Both are treated as terminal so the run stops with `statusReasons` instead of polling until it times out.

### Issue: Target stuck in `CREATE_PENDING_AUTH`
**Solution**: The target is waiting on an outbound authorization that will not complete by itself — normally an OAuth credential provider whose consent flow has not been finished. This sample sets no credential provider, so it should not reach this state; if it does, check the `credentialProviderConfigurations` on the target.

### Issue: the tool call fails with HTTP 429
**Solution**: The default Exa endpoint is anonymous, and its free tier is rate limited and shared, so a busy account can exhaust it. Supply your own Exa API key as an outbound credential on the target — store the key in the token vault as an API-key credential provider, then pass it to `create_gateway_target`:

```python
credentialProviderConfigurations=[{
    "credentialProviderType": "API_KEY",
    "credentialProvider": {"apiKeyCredentialProvider": {
        "providerArn": "<token-vault api-key provider arn>",
        "credentialLocation": "HEADER",
        "credentialParameterName": "x-api-key",
    }},
}]
```

### Issue: `Harness not READY after 600s`
**Solution**: Harness provisioning is measured at ~150s on the public network and ~255s in VPC mode, so the shared poller in `utils/harness.py` allows 600s. Exceeding that usually means the harness is genuinely stuck rather than slow — check `get_harness` for a `CREATE_FAILED` status and its `failureReason`.

### Issue: cleanup logs `Harness still provisioning, retrying delete`
**Solution**: Expected. A harness cannot be deleted while it is still `CREATING`, so cleanup waits for provisioning to finish before deleting. It gives up after the same 600s and warns rather than failing the run.

## AgentCore CLI

Create a harness with gateway integration via the CLI:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
agentcore create --name mygwagent --model-provider bedrock
```

The interactive wizard lets you configure gateway tools under **Advanced Settings → Tools**. After setup:

```bash
agentcore deploy
agentcore invoke --harness mygwagent \
  --session-id "$(uuidgen)" \
  "Search the web for the top 5 things to do in Tokyo in spring."
```

## Clean Up

A target must be deleted before the gateway that owns it. The harness is
independent of both, so its position in the sequence does not matter — the script
deletes it first only so a long `ConflictException` wait cannot delay the rest.

```python
harness_control.delete_harness(harnessId=harness_id)
gw_control.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
time.sleep(10)
gw_control.delete_gateway(gatewayIdentifier=gateway_id)

from utils.iam import delete_harness_role
delete_harness_role()
```

The script handles this automatically unless `--skip-cleanup` is passed. It only
deletes the IAM role when it created the role itself — pass `--role-arn` and your
own role is left alone.

## Running the Python Scripts

```bash
pip install -r ../../requirements.txt
```

```bash
# Default (Exa search, Tokyo travel query)
python gateway_integration.py

# Custom MCP endpoint and prompt
python gateway_integration.py \
    --mcp-endpoint https://your-mcp-server.example.com/mcp \
    --message "Search for recent AI research papers"

# Keep resources for inspection
python gateway_integration.py --skip-cleanup
```
