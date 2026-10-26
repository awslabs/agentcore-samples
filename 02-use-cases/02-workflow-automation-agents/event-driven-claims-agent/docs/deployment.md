# Deployment Guide

## Quick Deploy (One Command)

```bash
./deploy.sh us-west-2
```

This runs all steps below automatically:
1. Configures deployment target (auto-detects account ID, generates `aws-targets.json`)
2. Checks/creates Cognito User Pool for Gateway auth (interactive — auto-creates if needed)
3. Registers OAuth credential with AgentCore Identity (`agentcore add credential`)
4. Installs CDK dependencies (`npm install`)
5. Installs agent Python dependencies (`uv sync`)
6. Validates `agentcore.json`
7. Bootstraps CDK (first-time only)
8. Deploys via `agentcore deploy --target dev --yes`
9. Seeds DynamoDB with test data (4 policies)

When complete, you'll see:

```
✅ Done! Claims Agent deployed to us-west-2

📋 Test with:
   python3 scripts/test_invoke.py --region us-west-2

🛡️  Test Cedar policy (should block $100k+ claims):
   python3 scripts/test_invoke.py --region us-west-2 --prompt 'File a claim for POL-12345. Car totaled. $150000 damage.'

🔭 Enable full observability (optional — adds Gateway/Memory trace + log delivery):
   python3 scripts/enable_observability.py --region us-west-2 --stack-name AgentCore-ClaimsAgent-dev

🧹 Teardown:
   ./scripts/destroy.sh us-west-2
```

If the deploy fails at any step, see [Troubleshooting](#troubleshooting) below.

---

## Manual Step-by-Step Deployment

### 1. Clone and Navigate

```bash
git clone <repository-url>
cd event-driven-claims-agent
```

### 2. Configure AWS Targets

Create `agentcore/aws-targets.json` with your account and region:

```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=us-west-2

cat > agentcore/aws-targets.json <<EOF
[
  {
    "name": "dev",
    "account": "$ACCOUNT_ID",
    "region": "$REGION"
  }
]
EOF
```

### 3. Install CDK Dependencies

```bash
cd agentcore/cdk
npm install
cd ../..
```

### 4. Install Agent Dependencies

```bash
cd app/claimsagent
uv venv
uv sync
cd ../..
```

If `uv` is not available:
```bash
cd app/claimsagent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ../..
```

### 5. Validate Configuration

```bash
agentcore validate
```

This checks `agentcore/agentcore.json` for syntax and schema errors.

### 6. Bootstrap CDK (First-Time Only)

```bash
cdk bootstrap aws://<account-id>/<region>
```

Example:
```bash
cdk bootstrap aws://123456789012/us-west-2
```

### 7. Deploy

**Option A: Using AgentCore CLI (Recommended)**

```bash
agentcore deploy --target dev --yes
```

This deploys both the AgentCore resources (Runtime, Gateway, Memory, PolicyEngine, OnlineEval) and the supplementary infrastructure (DynamoDB, Lambda, SNS, S3, Cognito, EventBridge) via CDK.

**Option B: Using deploy.sh**

```bash
./deploy.sh us-west-2
```

Sets environment variables, validates, bootstraps, deploys, and seeds data.

### 8. Seed DynamoDB with Test Data

```bash
python3 scripts/seed_dynamodb.py --region us-west-2
```

Creates four test policies:
- `POL-12345` — John Smith, auto, $50,000 coverage
- `POL-67890` — Jane Doe, home, $250,000 coverage
- `POL-11111` — Bob Johnson, auto, $75,000 coverage
- `POL-99999` — Alice Williams, auto, $100,000 coverage (expired)

### 9. Verify Deployment

**Test with a simple claim:**

```bash
python3 scripts/test_invoke.py --region us-west-2
```

Expected output (abbreviated):

```
🔑 Authenticating...
✅ Connected to claimsagent
📝 I need to file a claim. My policy is POL-12345. Fender bender yesterday, $2000 damage.

━━━ Agent Response ━━━

## Phase 1: Claims Processing
[Agent calls lookup_policy, verifies POL-12345 is active with $50k auto coverage]
DECISION: ACCEPT
AMOUNT: 2000
POLICY: POL-12345
...

---
## Phase 2: Validation & Routing
CONFIDENCE: 92
ROUTING: AUTO_APPROVE
...

---
## Phase 3: Execution
**Auto-approved** (confidence: 92/100)
✅ Claim created: CLM-XXXXXXXX
📧 Approval notification sent to claimant@example.com

✅ Processing complete.

━━━━━━━━━━━━━━━━━━━━━
```

> **Note:** Phase 3 is deterministic — it does not use an LLM call. Tool calls are made directly via MCPClient based on the routing decision from Phase 2. The exact wording of Phase 1/2 varies between runs (LLM output is non-deterministic), but the structure (3 phases, DECISION, CONFIDENCE, ROUTING) is consistent.

**Test Cedar policy enforcement (should block — $150k exceeds the $100k threshold):**

```bash
python3 scripts/test_invoke.py --region us-west-2 --prompt 'File a claim for POL-12345. Car totaled. $150000 damage.'
```

Expected: The agent will try to call `create_claim` but receive an authorization error from the Cedar policy engine. It will then route to human review instead of creating the claim directly.

**Test with a custom prompt:**

```bash
python3 scripts/test_invoke.py --region us-west-2 --prompt 'File a claim for POL-12345. Storm damage. $5000.'
```

**Run full E2E test suite:**

```bash
python3 scripts/test_e2e.py --region us-west-2
```

This runs 5 test scenarios:
1. Auto-approved claim (high confidence) — expects `create_claim` called successfully
2. Cedar-blocked claim (>=$100k) — expects authorization denied on `create_claim`
3. Human review claim (low confidence) — expects `request_human_review` called
4. Rejected claim (policy not found) — expects `send_notification` with rejection
5. Email-format claim (S3 + EventBridge path) — expects full event-driven pipeline

Run a single test:

```bash
python3 scripts/test_e2e.py --region us-west-2 --test 2
```

**Validate authentication patterns:**

```bash
python3 scripts/test_auth.py --region us-west-2
```

This exercises the full auth model:
1. SigV4 → Runtime (the correct inbound path) succeeds
2. JWT → Runtime is rejected (Runtime uses AWS_IAM inbound, not CUSTOM_JWT)
3. Runtime → Gateway Cognito M2M auth works (verified via a tool call)
4. Unauthenticated request is rejected
5. Invalid/expired JWT is rejected
6. Wrong-scope token is rejected at the Cognito token endpoint

### 10. Teardown

**Unified teardown (recommended):**

```bash
./scripts/destroy.sh us-west-2
```

This runs a two-step process:
1. Removes observability deliveries (CloudWatch logs + traces) if enabled
2. Calls `cleanup_agentcore.py` which handles stack deletion (with `DELETE_FAILED` auto-recovery), orphaned AgentCore resource cleanup, Cognito teardown, and local state cleanup

**Manual teardown:**

The AgentCore CLI does not have a `destroy` command. Use CDK directly:

```bash
cd agentcore/cdk && npx cdk destroy --all --force
```

Then clean up orphaned resources and Cognito:

```bash
python3 scripts/cleanup_agentcore.py --region us-west-2 --project-dir .
```

**Note:** S3 buckets and DynamoDB tables are configured with `removalPolicy: DESTROY` and `autoDeleteObjects: true` for development. They will be deleted on stack teardown.

---

## Local Development

Run the agent locally while tools, auth, and data stay in the cloud.

### What runs where

| Component | Local | Cloud |
|-----------|-------|-------|
| Agent logic (main.py) | ✅ Local process on :8080 | — |
| MCP Gateway + Cedar | — | ✅ Deployed stack |
| Lambda tools | — | ✅ Deployed stack |
| DynamoDB tables | — | ✅ Deployed stack |
| Cognito auth | — | ✅ Deployed stack |

**Key insight:** You must deploy the stack first. Local dev runs only your agent code — tool calls still go to the cloud Gateway.

### Setup

1. Deploy the full stack (if not already done):
   ```bash
   ./deploy.sh us-west-2
   ```

2. Get connection values from the deployed stack:
   ```bash
   aws cloudformation describe-stacks \
     --stack-name AgentCore-ClaimsAgent-dev \
     --query 'Stacks[0].Outputs' \
     --region us-west-2 \
     --output table
   ```

3. Create your `.env` file:
   ```bash
   cp .env.example .env
   ```
   Fill in values from the deployed stack. Note that for the new Identity-based auth, you only need:
   ```
   AGENTCORE_GATEWAY_URL=https://xxx.gateway.bedrock-agentcore.us-west-2.amazonaws.com/mcp
   COGNITO_DISCOVERY_URL=https://cognito-idp.us-west-2.amazonaws.com/<pool-id>/.well-known/openid-configuration
   AGENTCORE_GATEWAY_CLIENT_ID=<client-id>
   AGENTCORE_GATEWAY_CLIENT_SECRET=<client-secret>
   AGENTCORE_GATEWAY_OAUTH_SCOPES=agentcore/invoke
   AGENTCORE_GATEWAY_CREDENTIAL_PROVIDER=cognito-gateway-m2m
   ```

   Optional tuning (safe defaults are provided):
   ```
   # Confidence threshold for auto-approval (0-100)
   AUTO_APPROVE_THRESHOLD=80
   # Memory retrieval tuning
   MEMORY_RETRIEVAL_TOP_K=5
   MEMORY_RETRIEVAL_RELEVANCE=0.5
   ```

   The local `agentcore dev` server will use these to register a local workload identity automatically.

4. Start the local dev server:
   ```bash
   agentcore dev --logs
   ```
   Or run the agent directly:
   ```bash
   cd app/claimsagent && source .venv/bin/activate && python main.py
   ```
   The agent serves on `http://localhost:8080`.

5. Test against local:
   ```bash
   python3 scripts/test_local.py
   ```
   Or use curl directly:
   ```bash
   curl -X POST http://localhost:8080/invocations \
     -H "Content-Type: application/json" \
     -d '{"prompt": "File a claim for POL-12345. $3000 windshield damage."}'
   ```

### Iterating on prompts

The fastest way to tune agent behavior without redeploying:

1. Edit `PROCESSOR_PROMPT` or `VALIDATOR_PROMPT` in `app/claimsagent/main.py`
2. The dev server detects the change and reloads automatically
3. Re-run your test command
4. Observe the behavior change in the response
5. Repeat until satisfied
6. When ready, deploy: `agentcore deploy --target dev --yes`

No container rebuild needed during local dev — only when deploying to the cloud.

### When to redeploy vs. iterate locally

| Change | Local iteration | Requires redeploy |
|--------|----------------|-------------------|
| Agent prompts | ✅ | — |
| Routing logic (main.py) | ✅ | — |
| New Python dependency | — | ✅ (Dockerfile rebuild) |
| New Lambda tool | — | ✅ (CDK creates Lambda) |
| Cedar policy change | — | ✅ (agentcore.json) |
| DynamoDB schema change | — | ✅ (CDK infra) |

---

## Troubleshooting

### agentcore validate — schema errors after CLI upgrade

If `agentcore validate` reports errors like:
- `memories[0].type: expected "AgentCoreMemory"`
- `credentials[0].type: invalid "type" value`

This means the CLI version has been upgraded and expects new schema fields. Common fixes:

1. **Memories** — add `"type": "AgentCoreMemory"` to each memory object in `agentcore.json`
2. **Credentials** — remove the `credentials` array entirely (credentials are managed via `agentcore add credential` CLI command, not the JSON file)

After fixing:
```bash
agentcore validate   # Should print "Valid"
```

### Deploy fails: "No agents or gateways defined"

CLI v0.3.0+ expects `agents` (not `runtimes`) as the top-level key. However, `agentcore validate` still accepts `runtimes`. If you encounter this discrepancy, rename `runtimes` → `agents` and add `"type": "AgentCoreRuntime"` to each entry. Also add `"runtimeVersion": "PYTHON_3_12"` if not present.

### Stack in REVIEW_IN_PROGRESS state

If a previous deploy failed during CloudFormation changeset review, the stack may be stuck:

```bash
aws cloudformation delete-stack --stack-name AgentCore-ClaimsAgent-dev --region us-west-2
aws cloudformation wait stack-delete-complete --stack-name AgentCore-ClaimsAgent-dev --region us-west-2
./deploy.sh us-west-2
```

### CDK Bootstrap Failed

If `cdk bootstrap` fails with "Stack already exists":

```bash
cdk bootstrap aws://<account>/<region> --force
```

### Container Build Failed

Check Docker/Finch is running:

```bash
docker info
# or
finch version
```

Set the container runtime explicitly:

```bash
export CDK_DOCKER=finch
./deploy.sh us-west-2
```

### Bedrock Model Access Denied

Enable Claude Sonnet in Bedrock console:
1. Go to **Bedrock console** → **Model access**
2. Click **Manage model access**
3. Enable **Claude Sonnet (Anthropic)**
4. Click **Save changes**

### Lambda Function Not Found

If tools fail with "Function not found", verify IAM permissions on Lambda ARNs in the Gateway configuration. Re-deploy:

```bash
agentcore deploy --target dev --yes
```

### Agent reports a Gateway tool is unavailable (e.g. "lookup_policy is not available")

**Symptom:** The agent responds that it can't access a Gateway tool (`lookup_policy`, `create_claim`, etc.) and rejects/flags the claim for manual review. Runtime logs show, on every invocation:

```
Failed to build MCP client (Identity auth): GetResourceOauth2Token:
Failed to fetch discovery document from:
https://cognito-idp.<region>.amazonaws.com/<pool-id>/.well-known/openid-configuration
```

**Cause:** The `cognito-gateway-m2m` credential provider in AgentCore Identity has a discovery URL pointing at a Cognito pool that no longer exists or is in a different region than the deployment. This commonly happens after redeploying to a **new region**: `setup_cognito.sh` creates a fresh pool and updates `.env`, but `agentcore add credential` is **idempotent** — since the provider already exists, the `add` is a no-op and the stale discovery URL is never updated. The Runtime can't fetch a Gateway token, so `get_mcp_client()` returns `None` and no Gateway tools load (the co-located `submit_decision` tool still works, so the agent runs but can't verify policies).

**Diagnose:**
```bash
# Check the provider's current discovery URL vs. the deployed region
aws bedrock-agentcore-control get-oauth2-credential-provider \
  --name cognito-gateway-m2m --region <region> \
  --query "oauth2ProviderConfigOutput.customOauth2ProviderConfig.oauthDiscovery.discoveryUrl" \
  --output text
# Compare against COGNITO_DISCOVERY_URL in .env
grep COGNITO_DISCOVERY_URL .env
```

**Fix:** Reconcile the credential provider with the current `.env` values (secret is sourced in-shell, never printed):
```bash
./scripts/fix_credential_region.sh <region>
```

Then invoke with a **fresh session** — the Runtime caches the MCP client as a module-level singleton, so a warm session keeps the old failure; a new cold session picks up the corrected token.

### `agentcore deploy` fails: Gateway `DiscoveryUrl: failed validation` / PLACEHOLDER in synth

**Symptom:** Running `agentcore deploy` directly (not via `deploy.sh`) fails with:
```
Properties validation failed ... #/AuthorizerConfiguration/CustomJWTAuthorizer/DiscoveryUrl:
failed validation constraint for keyword [pattern]
```
and the synthesized template shows `DiscoveryUrl: PLACEHOLDER_DISCOVERY_URL`.

**Cause:** The CDK stack reads `COGNITO_DISCOVERY_URL` and `AGENTCORE_GATEWAY_CLIENT_ID` from the environment at synth time. `deploy.sh` sources `.env` first; a bare `agentcore deploy` does not, so the placeholders never get patched.

**Fix:** Export the env before deploying (or just use `./deploy.sh <region>`):
```bash
set -a && source .env && set +a
export AWS_REGION=<region> CDK_DEFAULT_REGION=<region>
agentcore deploy --target dev --yes
```

### Event-driven (email/S3) claim never appears in DynamoDB

**Symptom:** You upload an email to `s3://claims-inbox-.../claims-inbox/` but no claim shows up.

**Cause / expected behavior:** The Trigger Lambda is **fire-and-forget** — it invokes the Runtime and returns in a few seconds without waiting for the full dual-agent pipeline (~60–90s). Results are written to DynamoDB by the agent's tool calls *after* the Lambda returns.

**Diagnose:**
```bash
# 1. Confirm the Trigger Lambda fired
aws logs tail /aws/lambda/ClaimsAgent-Trigger --region <region> --since 5m

# 2. Wait ~90s, then check the Claims table
aws dynamodb scan --table-name ClaimsAgent-dev-Claims --region <region> \
  --filter-expression "policy_number = :p" \
  --expression-attribute-values '{":p":{"S":"POL-67890"}}'
```
If the Lambda log shows `Runtime accepted claim ... (HTTP 200)` but nothing lands in DynamoDB, check the Runtime logs for the "tool unavailable" issue above (a failed Gateway connection means `create_claim` never runs).

### SES Email Not Sending

In SES sandbox mode, verify sender and recipient emails:

```bash
aws ses verify-email-identity --email-address your@email.com --region us-west-2
```

Check the verification link in the email, then redeploy with a verified sender (the notification Lambda reads `SENDER_EMAIL`, set from the `SENDER_EMAIL` shell variable at deploy time):

```bash
export SENDER_EMAIL=your@email.com
agentcore deploy --target dev --yes
```

To exit sandbox mode, request production access in the SES console.

---

## Multi-Region Deployment

Deploy to a different region:

```bash
./deploy.sh us-east-1
```

Or manually:

```bash
export AWS_REGION=us-east-1
export CDK_DEFAULT_REGION=us-east-1
agentcore deploy --target dev --yes
```

**Note:** Ensure the Bedrock model (`global.anthropic.claude-sonnet-4-6`) is available in your target region. The global inference profile automatically routes to the nearest available region.

**⚠️ Credential provider region caveat:** The `cognito-gateway-m2m` credential provider is created once and `agentcore add credential` is idempotent. When you deploy to a **different region** than a previous run, the provider keeps its old discovery URL and the agent will fail to load Gateway tools (see [Agent reports a Gateway tool is unavailable](#agent-reports-a-gateway-tool-is-unavailable-eg-lookup_policy-is-not-available)). After a cross-region redeploy, run:

```bash
./scripts/fix_credential_region.sh <new-region>
```
