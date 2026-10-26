# AgentCore Gateway Cedar Policies

This directory contains Cedar policies for the AgentCore Gateway policy engine (Approach 2).

## Architecture

```
Coding Agent → AgentCore Gateway (Cedar Policy Engine) → Sandbox Runtime
```

The Gateway intercepts all MCP tool calls and evaluates them against these Cedar policies before forwarding to the sandbox. This provides:

- **Managed enforcement** — policies evaluated outside the agent's code path
- **Audit logging** — all decisions logged to CloudWatch automatically
- **Default-deny posture** — no action proceeds unless explicitly permitted
- **Deterministic** — same input always produces the same decision

## Deployment

The gateway and policy engine are provisioned via the `cdk/stacks/gateway_policy_stack.py` CDK stack.

### Using AgentCore CLI

```bash
# Add policy engine
agentcore add policy-engine --name cagent-sandbox-policy-engine \
  --attach-to-gateways cagent-sandbox-gateway \
  --attach-mode ENFORCE

# Add policy from file
agentcore add policy --name sandbox-security \
  --engine cagent-sandbox-policy-engine \
  --source gateway-policies/gateway.cedar

# Or generate from natural language
agentcore add policy --name sandbox-security \
  --engine cagent-sandbox-policy-engine \
  --generate "Allow run_command except for curl, wget, ssh, and sudo. Block file writes to /etc, /proc, /sys."
```

## Relationship to local policies

| Layer | File | Enforcement Point |
|-------|------|-------------------|
| Gateway (this) | `gateway-policies/gateway.cedar` | AgentCore managed service — before request reaches sandbox |
| Local (sandbox) | `sandbox/policies/sandbox.cedar` | In-process cedarpy — inside sandbox before execution |

Both layers should be active for defense in depth. The Gateway catches coarse-grained violations; the local engine catches context-dependent issues (e.g., symlink resolution, runtime state).

## Cedar syntax reference

- `permit(principal, action, resource) when { ... }` — allow if conditions met
- `forbid(principal, action, resource) when { ... }` — deny (overrides permits)
- `context.input.<field>` — access tool call arguments
- `like "*pattern*"` — glob-style pattern matching
- Default: DENY (if no permit matches)
