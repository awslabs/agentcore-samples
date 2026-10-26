# Configuration Reference

AgentCore resources are declared in `agentcore/agentcore.json`; supplementary AWS infra is in `agentcore/cdk/lib/infra-construct.ts`. The deployed CloudFormation stack is named **`AgentCore-ClaimsAgent-dev`**. None of these are required to change for a basic deploy — defaults work out of the box.

## Deploy-time Parameters

| Parameter | How to set | Default | Notes |
|-----------|------------|---------|-------|
| `SENDER_EMAIL` | `export SENDER_EMAIL=...` before deploy | `noreply@example.com` | SES verified sender for notifications. `infra-construct.ts` reads `process.env.SENDER_EMAIL` at synth. Must be SES-verified or emails are logged as drafts, not sent. |
| Region | `./deploy.sh <region>` | `us-west-2` | Sets `AWS_REGION`/`CDK_DEFAULT_REGION`. |
| Model | `AGENT_MODEL_ID` runtime env | `global.anthropic.claude-sonnet-4-6` | Primary model for Claims Processor (Phase 1) and Executor (Phase 3). |
| Fast model | `FAST_MODEL_ID` runtime env | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Fast/cheap model for the Validation Agent (Phase 2). Classification task — no tool use needed. |
| Auto-approve threshold | `AUTO_APPROVE_THRESHOLD` runtime env | `80` | Confidence score (0-100) at or above which claims are auto-approved. |
| Memory top_k | `MEMORY_RETRIEVAL_TOP_K` runtime env | `5` | Number of prior facts/sessions retrieved per invocation. |
| Memory relevance | `MEMORY_RETRIEVAL_RELEVANCE` runtime env | `0.5` | Minimum relevance score (0.0-1.0) for memory results. |

```bash
# Example: customize for stricter review
export AUTO_APPROVE_THRESHOLD=90
export SENDER_EMAIL=claims@example.com
agentcore deploy --target dev --yes
```

---

## Runtime Environment Variables

Injected into the Container runtime by CDK. All are set automatically on deploy.

| Variable | Source | Example Value |
|----------|--------|---------------|
| `AGENTCORE_GATEWAY_URL` | CDK `gateway.gateway_url` | `https://xxx.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp` |
| `AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER` | CDK hardcoded | `cognito-gateway-m2m` |
| `AGENTCORE_GATEWAY_OAUTH_SCOPES` | CDK hardcoded | `agentcore/invoke` |
| `AGENT_MODEL_ID` | `agentcore.json` envVars | `global.anthropic.claude-sonnet-4-6` |
| `FAST_MODEL_ID` | `agentcore.json` envVars | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| `AUTO_APPROVE_THRESHOLD` | `agentcore.json` envVars | `80` |
| `AGENT_OBSERVABILITY_ENABLED` | `agentcore.json` envVars | `true` |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | `agentcore.json` envVars | `true` |

**Note:** The Runtime does not receive `GATEWAY_CLIENT_ID`, `GATEWAY_CLIENT_SECRET`, or `GATEWAY_TOKEN_ENDPOINT` as env vars. Those secrets live in the AgentCore Identity token vault and are accessed via the `@requires_access_token` decorator using the `AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER` name.

For local development, copy `.env.example` to `.env` and fill in values from your deployed stack:
```bash
cp .env.example .env
# Fill in AGENTCORE_GATEWAY_URL and COGNITO_* values from CloudFormation outputs
```

---

## Lambda Environment Variables

> **Table naming convention:** DynamoDB tables follow the pattern `ClaimsAgent-{target}-{Purpose}` where `{target}` is the deployment target name (e.g., `dev`). If you deploy with `--target staging`, tables will be named `ClaimsAgent-staging-Policies`, etc.

### ClaimsAgent-PolicyLookup
| Variable | Value |
|----------|-------|
| `POLICIES_TABLE` | `ClaimsAgent-dev-Policies` |

### ClaimsAgent-CreateClaim
| Variable | Value |
|----------|-------|
| `CLAIMS_TABLE` | `ClaimsAgent-dev-Claims` |

### ClaimsAgent-HumanReview
| Variable | Value |
|----------|-------|
| `REVIEWS_TABLE` | `ClaimsAgent-dev-Reviews` |
| `REVIEW_SNS_TOPIC_ARN` | SNS topic ARN (from CDK) |

### ClaimsAgent-Notification
| Variable | Value |
|----------|-------|
| `SENDER_EMAIL` | CDK context `sender_email` or `noreply@example.com` |

### ClaimsAgent-ListPending
| Variable | Value |
|----------|-------|
| `CLAIMS_TABLE` | `ClaimsAgent-dev-Claims` |

### ClaimsAgent-ResolveClaim
| Variable | Value |
|----------|-------|
| `CLAIMS_TABLE` | `ClaimsAgent-dev-Claims` |
| `REVIEWS_TABLE` | `ClaimsAgent-dev-Reviews` |

### ClaimsAgent-Trigger
| Variable | Value | Notes |
|----------|-------|-------|
| `AGENTCORE_RUNTIME_ARN` | Runtime ARN (from CDK) | Used to construct the SigV4-signed HTTPS invocation URL |

---

## Cedar Policies

Cedar policies are declared in `agentcore/agentcore.json` under `policyEngines[0].policies`. Each policy is a `{ name, description, statement, validationMode }` object.

---

## MCP Tool Schema Format

Tool schemas in `lambdas/schemas/` define the contract between the MCP Gateway and each Lambda tool. The Gateway uses these schemas for tool discovery (semantic search matches against `description`) and input validation.

**Format:** Each file is a JSON array containing one tool object:

```json
[{
  "name": "tool-name",
  "description": "Human-readable description of what this tool does. The Gateway uses this for semantic search.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "param_name": { "type": "string", "description": "What this parameter is for" }
    },
    "required": ["param_name"]
  }
}]
```

**How it's wired:**
1. Schema file lives at `lambdas/schemas/<tool_name>.json`
2. `agentcore/agentcore.json` references it via `toolSchemaFile: "lambdas/schemas/<tool_name>.json"`
3. CDK loads it via `ToolSchema.from_local_asset(...)` during synthesis
4. The Gateway registers the tool with its name, description, and input schema
5. The agent discovers tools via semantic search (matching the `description` field)

**Important:** The Lambda handler's expected parameters must match the schema's `properties`. If you add a field to the schema, the Lambda must handle it. If you rename a field in the Lambda, update the schema to match.

---

### Policy 1: AllowAllTools

```cedar
permit(principal, action, resource is AgentCore::Gateway);
```

Grants all authenticated principals the ability to call any tool on the claims gateway. Uses `IGNORE_ALL_FINDINGS` validation mode.

### Policy 2: BlockExcessiveClaims

```cedar
forbid(principal, action, resource is AgentCore::Gateway)
when {
    context has "toolName" && context.toolName == "create-claim"
    && context has "input" && context.input has "estimated_amount"
    && context.input.estimated_amount >= 100000
};
```

Blocks `create-claim` tool calls when the estimated amount is ≥$100,000.

### Adding a New Policy

Add an entry to `policyEngines[0].policies` in `agentcore/agentcore.json`:

```json
{
  "name": "MyPolicyName",
  "description": "Description of what this policy does",
  "statement": "forbid(principal, action, resource is AgentCore::Gateway) when { context has \"toolName\" && context.toolName == \"my-tool\" };",
  "validationMode": "IGNORE_ALL_FINDINGS"
}
```

Then `agentcore validate && agentcore deploy --target dev --yes`.

### Policy Engine Mode

The gateway references the policy engine via `policyEngineConfiguration.mode` (set to `ENFORCE` in `agentcore.json`). In `ENFORCE` mode, Cedar denials block the tool call before it runs. Switch `mode` to `MONITOR` to observe and log policy decisions without blocking — handy while authoring new policies.

---

## Cognito Configuration

Cognito is used **exclusively for Runtime → MCP Gateway authentication**. The Runtime uses the `@requires_access_token` decorator to obtain tokens from the AgentCore Identity vault (no secrets in env vars).

Cognito is managed **outside CDK** — created by `scripts/setup_cognito.sh` (runs during `deploy.sh` if needed) and destroyed by `scripts/teardown_cognito.sh` (runs during `scripts/destroy.sh` if script-created).

A `.cognito-state.json` file tracks whether the script created the User Pool, so teardown only deletes what the script created (preserves manually-created pools).

Callers of the Runtime (Trigger Lambda, test scripts) use **AWS_IAM (SigV4)** authentication instead — they do NOT need Cognito tokens.

### User Pool: Script-Managed

| Setting | Value |
|---------|-------|
| Name | `ClaimsAgent-UserPool` (or custom name from script) |
| Created by | `scripts/setup_cognito.sh` (AWS CLI) |
| Destroyed by | `scripts/teardown_cognito.sh` (if script-created) |
| Domain prefix | `claims-agent-{account}` |

### Resource Server

| Setting | Value |
|---------|-------|
| Identifier | `agentcore` |
| Scopes | `agentcore/invoke` |

### App Client: M2M

| Setting | Value |
|---------|-------|
| Name | Auto-generated by script |
| Flow | `client_credentials` (machine-to-machine) |
| Secret | Auto-generated, registered with AgentCore Identity |
| Allowed scopes | `agentcore/invoke` |

### Token Endpoint

```
https://claims-agent-{account}.auth.{region}.amazoncognito.com/oauth2/token
```

### AgentCore Identity Registration

During `deploy.sh`, the Cognito client secret is registered with AgentCore Identity:

```bash
agentcore add credential \
  --name cognito-gateway-m2m \
  --type oauth \
  --discovery-url "$COGNITO_DISCOVERY_URL" \
  --client-id "$AGENTCORE_GATEWAY_CLIENT_ID" \
  --client-secret "$AGENTCORE_GATEWAY_CLIENT_SECRET" \
  --scopes "agentcore/invoke"
```

The Runtime references this credential provider by name (`AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER=cognito-gateway-m2m`) — no secrets in CloudFormation or env vars.

---

## SES Setup

The notification Lambda uses SES to send emails. SES is in sandbox mode by default.

### Sandbox Mode (default)

In sandbox mode, SES can only send to verified email addresses. To verify an address:

```bash
aws ses verify-email-identity --email-address your@email.com --region us-west-2
```

Click the verification link in the email. Then redeploy with the verified sender:

```bash
export SENDER_EMAIL=your@email.com
agentcore deploy --target dev --yes
```

### Production Mode

To send to any address, request SES production access:
1. Go to SES console → Account dashboard → Request production access
2. Fill in the use case form
3. Wait for AWS approval (typically 24-48 hours)

### SES IAM Permissions

The notification Lambda is granted:
```
ses:SendEmail
ses:SendRawEmail
arn:aws:ses:{region}:{account}:identity/*
```

This scopes permissions to identities in the deploying account, not `*`.

---

## Observability Configuration

### Automated Setup

Full observability is enabled automatically by `deploy.sh` via `scripts/enable_observability.py`:

1. **CloudWatch Transaction Search** — enabled for the account/region
2. **TRACES delivery** — creates CloudWatch Log deliveries for Gateway and Memory traces
3. **LOGS delivery** — creates CloudWatch Log deliveries for Gateway and Memory application logs

These are automatically cleaned up by `scripts/destroy.sh` via `scripts/disable_observability.py`.

### Runtime OTEL Configuration

The Runtime's `agentcore.json` includes environment variables:

| Variable | Value | Purpose |
|----------|-------|---------|
| `AGENT_OBSERVABILITY_ENABLED` | `true` | Enables AgentCore observability features |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | `true` | Captures LLM request/response content in traces |
| `AGENT_MODEL_ID` | `global.anthropic.claude-sonnet-4-6` | Model identifier |
| `AUTO_APPROVE_THRESHOLD` | `80` | Confidence threshold for auto-approval |
| `AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER` | `cognito-gateway-m2m` | Identity credential provider name |
| `AGENTCORE_GATEWAY_OAUTH_SCOPES` | `agentcore/invoke` | OAuth scopes for Gateway auth |

The following OTEL variables are **auto-configured** by the Runtime when `instrumentation.enableOtel: true` is set — you do not need to set them manually:
- `_AWS_XRAY_DAEMON_ADDRESS`
- `_AWS_XRAY_TRACING_ENABLED`
- `OTEL_METRICS_EXPORTER`
- `OTEL_TRACES_EXPORTER`
- `OTEL_EXPORTER_OTLP_PROTOCOL`
- `OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED`
- `OTEL_AWS_APPLICATION_SIGNALS_ENABLED`
- `OTEL_PROPAGATORS`

### Viewing Observability Data

- **CloudWatch Logs:** `/aws/bedrock-agentcore/claims-agent` (1-week retention)
- **X-Ray Traces:** CloudWatch ServiceLens → Service Map
- **Transaction Search:** CloudWatch console → Transaction Search
- **Gateway/Memory Deliveries:** CloudWatch console → Logs → Deliveries

---

## Bedrock Model Access

The Runtime needs access to the Bedrock model specified in `app/claimsagent/model/load.py`.

### Default Model

```
global.anthropic.claude-sonnet-4-6
```

This is a cross-region inference profile that automatically routes to the nearest available region.

### Enable Model Access

1. Go to **Bedrock console** → **Model access** → **Manage model access**
2. Enable **Claude Sonnet** (Anthropic)
3. Click **Save changes**

Available in: us-east-1, us-west-2, eu-west-1, ap-northeast-1 (check console for current list).

### IAM Permissions

The Runtime role is granted:
```json
{
  "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
  "Resource": [
    "arn:aws:bedrock:{region}::foundation-model/anthropic.claude-sonnet-4-6",
    "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-4-6",
    "arn:aws:bedrock:*:*:inference-profile/*"
  ]
}
```

### Changing the Model

Preferred: set the `AGENT_MODEL_ID` environment variable on the Runtime (in `agentcore/agentcore.json` → `runtimes[0].envVars`), then redeploy:

```bash
agentcore deploy --target dev --yes
```

`app/claimsagent/main.py` reads `AGENT_MODEL_ID` (via `config.py`), so no code change is needed.

---

## Memory Configuration

Memory is declared in `agentcore/agentcore.json` under `memories`:

```json
{
  "name": "ClaimsAgentMemory",
  "eventExpiryDuration": 90,
  "strategies": [
    { "type": "SEMANTIC", "name": "semantic_strategy", "namespaces": ["claims/{actorId}/facts"] },
    { "type": "SUMMARIZATION", "name": "summary_strategy", "namespaces": ["claims/{actorId}/{sessionId}"] }
  ]
}
```

| Setting | Value | Notes |
|---------|-------|-------|
| Expiration | 90 days | Adjust `eventExpiryDuration` |
| SEMANTIC | enabled | Fact/concept retrieval across sessions |
| SUMMARIZATION | enabled | Session compression for repeat claimants |
| Retrieval top_k | 5 (facts), 3 (sessions) | Configurable via `MEMORY_RETRIEVAL_TOP_K` env var |
| Relevance threshold | 0.5 | Configurable via `MEMORY_RETRIEVAL_RELEVANCE` env var |

### Disabling Memory

Remove the `memories` entry (or unset `MEMORY_ID`). The Runtime degrades gracefully — `app/claimsagent/memory/session.py` returns `None` when `MEMORY_ID` is unset, and `main.py` wraps the session manager in try/except.

---

## Online Evaluation

Configured in `agentcore/agentcore.json` under `onlineEvalConfigs`:

```json
{
  "name": "ClaimsEvaluation",
  "agent": "claimsagent",
  "evaluators": ["Builtin.Helpfulness", "Builtin.Correctness", "Builtin.ToolSelectionAccuracy"],
  "samplingRate": 100,
  "description": "Online evaluation for claims agent (3 built-in metrics)"
}
```

A custom LLM-as-judge evaluator (`ClaimsQualityEvaluator`) is also declared under `evaluators` for on-demand use.

| Setting | Value | Notes |
|---------|-------|-------|
| Sampling | 100% | Every invocation is evaluated. Reduce for cost savings in production. |
| Built-in metrics | 3 | Helpfulness, Correctness, Tool Selection Accuracy |
| Custom evaluator | LLM-as-judge | Defined separately (`ClaimsQualityEvaluator`). Use for on-demand evaluation only — requires reference inputs not available online. |

### Valid Built-in Evaluator IDs

| ID | What it measures |
|----|-----------------|
| `HELPFULNESS` | Whether the response helps the user |
| `CORRECTNESS` | Whether the response is factually accurate |
| `TOOL_SELECTION_ACCURACY` | Whether the right tools were chosen at the right time |
| `GOAL_SUCCESS_RATE` | Whether the agent achieved its stated goal |

**Tip:** Use the exact IDs above — the tool-selection metric is `ToolSelectionAccuracy`.

### Prerequisites

Online Evaluation requires CloudWatch Transaction Search to be enabled in your region:
1. Go to **CloudWatch console** → **Settings** → **Transaction Search**
2. Enable Transaction Search
3. Then deploy the stack

---

## Deploying to a Different Region

```bash
./deploy.sh us-east-1
```

The deploy script sets `CDK_DEFAULT_REGION`, `AWS_DEFAULT_REGION`, and `AWS_REGION` automatically.

**Note:** Ensure the Bedrock model (`global.anthropic.claude-sonnet-4-6`) is available in your target region. The global inference profile handles cross-region routing automatically.
