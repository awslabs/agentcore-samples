# Tutorial: Make It Your Own

This guide walks you through modifying the Claims Agent sample step by step. Each section builds on the previous one, taking you from "it's deployed and running" to "I've adapted it for my own domain."

> **Prerequisite:** Complete the [Quick Start](../README.md#quick-start) first. You should have a working deployment before modifying it.

---

## Understand the Processing Flow

Before changing anything, trace how a single claim moves through the system. Run this command and watch the output:

```bash
python3 scripts/test_invoke.py --region us-west-2 \
  --prompt 'I need to file a claim under policy POL-12345. Storm damage to my vehicle. $7,500 in repairs.'
```

What happens internally:

1. **Your script** gets a Cognito JWT token via `client_credentials` flow
2. **Your script** POSTs to the AgentCore Runtime endpoint with `Authorization: Bearer <token>`
3. **Phase 1 (Claims Processor)** calls `lookup_policy` via the MCP Gateway → Lambda looks up POL-12345 in DynamoDB → returns policy details
4. **Phase 1** evaluates: policy active? amount within limits? category covered? → outputs `DECISION: ACCEPT`
5. **Phase 2 (Validation Agent)** receives the processor's decision + original claim → assigns `CONFIDENCE: 92`, `ROUTING: AUTO_APPROVE`
6. **Phase 3 (Execution)** calls `create_claim` (DynamoDB write) + `send_notification` (SES email)

The Gateway enforces Cedar policies before each tool call. If `create_claim` receives `estimated_amount >= 100000`, the policy engine denies it.

---

## Experiment 1: Change the Confidence Threshold

The simplest modification — change when claims are auto-approved vs. sent to human review. The threshold is a runtime configuration variable, so **no code change is needed**.

**Current behavior:** Claims with confidence ≥ 80 are auto-approved.

**Goal:** Make the agent more conservative — require confidence ≥ 90 for auto-approval.

### Steps

1. Set the new threshold in your `.env` file or as an environment variable:
   ```bash
   export AUTO_APPROVE_THRESHOLD=90
   ```

2. Also update `VALIDATOR_PROMPT` in `app/claimsagent/main.py` to match (find "If CONFIDENCE >= 80"):
   ```python
   - If CONFIDENCE >= 90: set ROUTING to AUTO_APPROVE
   - If CONFIDENCE < 90: set ROUTING to HUMAN_REVIEW
   ```
   > **Why both?** The env var controls the hard threshold in code (`routing.py`). The prompt guides the LLM's own scoring logic. They should stay in sync for consistent behavior.

3. Redeploy:
   ```bash
   agentcore deploy --target dev --yes
   ```

4. Test — with the stricter threshold, borderline claims route to human review:
   ```bash
   python3 scripts/test_invoke.py --region us-west-2 \
     --prompt 'Policy POL-12345. Minor scratch on bumper. $500 repair.'
   ```

**What you learned:** The routing threshold is externalized to `AUTO_APPROVE_THRESHOLD` in `config.py` (read from environment). This means you can tune it per-deployment without modifying code. For production, you might set `AUTO_APPROVE_THRESHOLD=95` to be extra cautious.

**How it works under the hood:** `config.py` → `routing.py` reads `AUTO_APPROVE_THRESHOLD` → `resolve_routing()` enforces the threshold when the validator assigns routing.

---

## Experiment 2: Add a Cedar Policy

Cedar policies enforce rules at the Gateway level — the agent cannot bypass them even if it wants to. Let's add a policy that blocks claims on inactive policies.

### Steps

1. Use the AgentCore CLI to add the policy:

   ```bash
   agentcore add policy \
     --name BlockInactivePolicy \
     --engine ClaimsPolicyEngine \
     --description "Forbid creating claims against policies with inactive status" \
     --statement 'forbid(principal, action, resource is AgentCore::Gateway) when { context has "toolName" && context.toolName == "create-claim" && context has "input" && context.input has "policy_status" && context.input.policy_status == "inactive" };' \
     --validation-mode IGNORE_ALL_FINDINGS
   ```

   This updates `agentcore/agentcore.json` automatically — no hand-editing needed.

   > **Tip:** You can also generate policies from natural language using `--generate`:
   > ```bash
   > agentcore add policy \
   >   --name BlockInactivePolicy \
   >   --engine ClaimsPolicyEngine \
   >   --generate "Block the create-claim tool when the policy_status input is inactive"
   > ```

2. Verify it was added:
   ```bash
   agentcore validate
   ```

3. Deploy:
   ```bash
   agentcore deploy --target dev --yes
   ```

> **Note:** This policy only works if the agent passes `policy_status` as an input to `create_claim`. You'd need to update the tool schema in `lambdas/schemas/create_claim.json` to accept this field and update the Lambda handler to include it. This demonstrates how Cedar policies, tool schemas, and Lambda logic need to stay in sync.

**What you learned:** The `agentcore add policy` command is the standard way to add Cedar policies — it validates the engine name, writes the correct JSON structure, and avoids manual editing mistakes. Cedar policies are enforced at the Gateway before the Lambda runs — the agent gets an authorization error if a policy denies the call. Use `IGNORE_ALL_FINDINGS` validation mode for policies that reference runtime context values.

---

## Experiment 3: Observe Policy Blocking

The existing `BlockExcessiveClaims` policy blocks claims ≥$100k. Let's see it in action and trace what happens:

```bash
python3 scripts/test_invoke.py --region us-west-2 \
  --prompt 'File a claim for POL-67890. Major house fire. $150,000 damage.'
```

Watch the output — you'll see:
1. Phase 1 evaluates the claim and decides ACCEPT (policy has $250k coverage, fire is covered)
2. Phase 2 assigns a confidence score
3. Phase 3 tries to call `create_claim` with `estimated_amount: 150000`
4. The Gateway **denies** the call — Cedar policy returns an authorization error
5. The agent adapts and routes to human review instead

This shows Cedar acting as a guardrail that the agent cannot override, even though its logic says the claim should be approved.

---

## Experiment 4: Add a New Tool

Let's add a `check_fraud_score` tool that the agent can call to assess claim risk.

### Step 1: Create the Lambda handler

```bash
mkdir -p lambdas/fraud_check
```

Create `lambdas/fraud_check/handler.py`:

```python
"""Fraud risk scoring tool — returns a risk score for a claim."""
import json
import random


def handler(event, context):
    """Score fraud risk based on claim characteristics."""
    policy_number = event.get("policy_number", "unknown")
    amount = event.get("amount", 0)
    category = event.get("category", "unknown")

    # Simple scoring logic (replace with ML model in production)
    score = 10  # base risk
    if amount > 25000:
        score += 30
    if amount > 50000:
        score += 20
    if category in ("theft", "total_loss"):
        score += 15

    # Add some randomness for demo purposes
    score += random.randint(0, 10)
    score = min(score, 100)

    risk_level = "LOW" if score < 30 else "MEDIUM" if score < 60 else "HIGH"

    return json.dumps({
        "policy_number": policy_number,
        "fraud_risk_score": score,
        "risk_level": risk_level,
        "recommendation": "proceed" if score < 60 else "flag_for_review",
    })
```

### Step 2: Create the tool schema

Create `lambdas/schemas/fraud_check.json`:

```json
[{
  "name": "check_fraud_score",
  "description": "Assess fraud risk for a claim. Returns a risk score (0-100) and recommendation.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "policy_number": { "type": "string", "description": "Policy number for the claim" },
      "amount": { "type": "integer", "description": "Claimed dollar amount" },
      "category": { "type": "string", "description": "Claim category (auto_collision, theft, natural_disaster, etc.)" }
    },
    "required": ["policy_number", "amount", "category"]
  }
}]
```

### Step 3: Register the Lambda in CDK

Open `agentcore/cdk/lib/infra-construct.ts` and add the Lambda following the same pattern as the existing tools (search for `PolicyLookupFn` to see the pattern):

```typescript
const fraudCheckFn = new lambda.Function(this, 'FraudCheckFn', {
  functionName: 'ClaimsAgent-FraudCheck',
  runtime: lambda.Runtime.PYTHON_3_12,
  handler: 'handler.handler',
  code: lambda.Code.fromAsset(path.join(__dirname, '../../../lambdas/fraud_check')),
  architecture: lambda.Architecture.ARM_64,
  timeout: cdk.Duration.seconds(10),
});
```

### Step 4: Register the tool with the Gateway

Use the AgentCore CLI to add the gateway target:

```bash
agentcore add gateway-target \
  --name check-fraud-score \
  --gateway ClaimsGateway \
  --type lambda-function-arn \
  --lambda-arn PLACEHOLDER_FRAUD_CHECK \
  --tool-schema-file lambdas/schemas/fraud_check.json \
  --description "Assess fraud risk for a claim. Returns a risk score (0-100) and recommendation."
```

This adds the target to `agentcore/agentcore.json` → `agentCoreGateways[0].targets[]` automatically.

> **Note:** The `PLACEHOLDER_FRAUD_CHECK` ARN will be patched with the real Lambda ARN by CDK at deploy time (same pattern as all other tools).

### Step 5: Wire the Lambda ARN in CDK

In `agentcore/cdk/lib/cdk-stack.ts`, add the ARN patching (follow the existing pattern for other tools).

### Step 6: Update the agent prompt (optional)

If you want the Claims Processor to use this tool, add it to `PROCESSOR_PROMPT` in `main.py`:

```python
2. Call check_fraud_score to assess risk level
```

### Step 7: Validate and deploy

```bash
agentcore validate
agentcore deploy --target dev --yes
```

**What you learned:** Adding a tool requires four coordinated changes: Lambda handler, JSON schema, CDK infrastructure, and Gateway target registration. The CLI commands (`agentcore add gateway-target`) handle the `agentcore.json` changes — you just provide the target name, gateway, and schema file. CDK handles creating the Lambda and wiring its ARN. The agent discovers the tool automatically via semantic search (because `enableSemanticSearch: true` is configured on the Gateway).

---

## Adapt to a Different Domain

The insurance domain is just one example. Here's how to adapt this sample to a different use case — say, **loan application processing**.

### What to change

| Component | Insurance (current) | Your domain (example: loans) |
|-----------|--------------------|-----------------------------|
| `PROCESSOR_PROMPT` | Evaluates claim validity | Evaluates loan eligibility |
| `VALIDATOR_PROMPT` | Reviews claim decision | Reviews loan approval decision |
| DynamoDB tables | Policies, Claims, Reviews | Applicants, Loans, Approvals |
| Tool: `lookup_policy` | Gets insurance policy | Gets credit profile |
| Tool: `create_claim` | Creates claim record | Creates loan application |
| Tool: `human_review` | Escalates to claims adjuster | Escalates to loan officer |
| Tool: `notification` | Emails claimant | Emails applicant |
| Cedar policy | Blocks claims ≥$100k | Blocks loans ≥$500k or poor credit |
| `AUTO_APPROVE_THRESHOLD` | 80 | 95 (loans need higher confidence) |

### Configuration-first customization

Before touching any code, review what's configurable via environment variables alone. These changes require only a redeploy, not a code edit:

| Variable | Effect |
|----------|--------|
| `AGENT_MODEL_ID` | Switch the primary reasoning model |
| `AUTO_APPROVE_THRESHOLD` | Tune how conservative the auto-approval is |
| `MEMORY_RETRIEVAL_TOP_K` | Retrieve more/fewer prior interactions |
| `MEMORY_RETRIEVAL_RELEVANCE` | Raise/lower the relevance bar for memory recall |
| `LAMBDA_TIMEOUT_SECONDS` | Give tools more time for complex operations |
| `S3_INBOX_PREFIX` | Change which S3 prefix triggers processing |
| `SNS_TOPIC_NAME` | Use a different notification topic |
| `DESTROY_ON_DELETE` | Set `false` for production (preserves data on stack delete) |
| `SENDER_EMAIL` | Change the notification sender address |

### Steps

1. **Tune configuration first** — set `AUTO_APPROVE_THRESHOLD`, model IDs, and memory tuning in `.env` for your domain's needs.

2. **Update agent prompts** in `app/claimsagent/main.py` — rewrite `PROCESSOR_PROMPT` and `VALIDATOR_PROMPT` for your domain. Keep the same output format (DECISION/CONFIDENCE/ROUTING) so the routing logic still works.

3. **Update DynamoDB tables** in `agentcore/cdk/lib/infra-construct.ts` — change table names, partition keys, and sort keys for your data model.

4. **Rewrite Lambda handlers** in `lambdas/` — each tool becomes domain-specific. Keep the same interface pattern (receive JSON params, return `json.dumps({...})`).

5. **Update tool schemas** in `lambdas/schemas/` — change property names, descriptions, and required fields.

6. **Update Cedar policies** — use `agentcore add policy` to add domain-specific authorization rules:
   ```bash
   agentcore add policy \
     --name BlockHighRiskLoans \
     --engine ClaimsPolicyEngine \
     --statement 'forbid(principal, action, resource is AgentCore::Gateway) when { context has "toolName" && context.toolName == "create-loan" && context has "input" && context.input has "amount" && context.input.amount >= 500000 };' \
     --validation-mode IGNORE_ALL_FINDINGS
   ```

7. **Update seed data** in `scripts/seed_dynamodb.py` — populate with test records for your domain.

8. **Update tests** in `scripts/test_e2e.py` — write scenarios that exercise your domain's routing paths.

**What you learned:** The architecture is domain-agnostic. The dual-agent pattern, event-driven trigger, Gateway + Cedar enforcement, cost routing, and confidence-based routing all transfer directly. Start with configuration (env vars), then modify prompts, then tools and schemas. Only the domain-specific logic needs rewriting.

---

## Local Development Workflow

You don't need to redeploy for every code change. Here's how to iterate locally:

### What runs locally vs. in the cloud

| Component | Local dev | Deployed |
|-----------|-----------|----------|
| Agent runtime (main.py) | Local process on :8080 | Fargate container |
| MCP Gateway | **Cloud** (needs deployed stack) | Cloud |
| Lambda tools | **Cloud** (needs deployed stack) | Cloud |
| DynamoDB tables | **Cloud** (needs deployed stack) | Cloud |
| Cognito auth | **Cloud** (needs deployed stack) | Cloud |
| Cedar policies | **Cloud** (enforced at Gateway) | Cloud |

**Key insight:** Local dev still calls cloud resources for tools and auth. You're running only the agent logic locally — everything else is the deployed stack.

### Setup

1. Deploy the stack first (so Gateway, Lambda, Cognito, and DynamoDB exist):
   ```bash
   ./deploy.sh us-west-2
   ```

2. Get connection details from the deployed stack:
   ```bash
   aws cloudformation describe-stacks \
     --stack-name AgentCore-ClaimsAgent-dev \
     --query 'Stacks[0].Outputs' \
     --region us-west-2 \
     --output table
   ```

3. Create `.env` from the example:
   ```bash
   cp .env.example .env
   # Fill in AGENTCORE_GATEWAY_URL, CLIENT_ID, CLIENT_SECRET, TOKEN_ENDPOINT
   # Optionally tune: AUTO_APPROVE_THRESHOLD, FAST_MODEL_ID, MEMORY_RETRIEVAL_TOP_K
   ```

4. Run locally:
   ```bash
   agentcore dev --no-browser
   ```

5. Test against local instance:
   ```bash
   python3 scripts/test_local.py
   # or
   curl -X POST http://localhost:3000/invocations \
     -H "Content-Type: application/json" \
     -d '{"prompt": "File a claim for POL-12345. $3000 windshield damage."}'
   ```

### Iterating on prompts

The fastest feedback loop for prompt engineering:

1. Edit `PROCESSOR_PROMPT` or `VALIDATOR_PROMPT` in `main.py`
2. The local dev server hot-reloads (watches for file changes)
3. Re-run your test curl command
4. Observe the agent's behavior change
5. Repeat until satisfied
6. Deploy: `agentcore deploy --target dev --yes`

No container rebuild needed during local dev — only when you deploy.

---

## Next Steps

- Read [docs/ARCHITECTURE.md](ARCHITECTURE.md) for the full component breakdown and data flow diagrams
- Browse the [decision records](decisions/README.md) to understand why each architectural choice was made
- Check [docs/CONFIGURATION.md](CONFIGURATION.md) for every configurable parameter — start here before modifying code
- Look at `app/claimsagent/main.py` — the entire agent logic is in one file (~200 lines of orchestration code)
- Review `app/claimsagent/config.py` — the single source of truth for all environment-driven configuration
