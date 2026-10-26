# AgentCore Optimization Tutorial — HR Assistant

## Overview

This tutorial demonstrates the complete **Amazon Bedrock AgentCore Optimization** workflow: measure your agent's baseline performance, generate AI-driven recommendations, package them into Configuration Bundles, and validate improvements with live A/B testing.

The demo agent is an **HR Assistant** that handles PTO requests, policy lookups, benefits questions, and pay stub retrieval for Acme Corp employees.

### What You Will Learn

| Stage | Concepts Covered |
|-------|-----------------|
| **Baseline Evaluation** | Batch evaluations on agent sessions |
| **Recommendations** | System prompt optimization, tool description optimization from production traces |
| **Configuration Bundles** | Versioned config containers, runtime config hooks, baggage-based injection |
| **A/B Test: Config-Bundle Routing** | Prompt-level A/B testing without redeployment, online evaluation, statistical analysis |
| **A/B Test: Target-Based Routing** | Code-level A/B testing, phased rollout (90/10 canary), multi-runtime comparison |

---

## Architecture

```
                        ┌─────────────────────────────────────────────────────────────┐
                        │                  AgentCore Optimization Loop                 │
                        │                                                               │
                        │  1. Invoke Agent ──────────► CloudWatch Logs (OTel spans)   │
                        │                                         │                     │
                        │  2. Batch Evaluate ◄────────────────────┘                   │
                        │     GoalSuccessRate / Helpfulness / Correctness              │
                        │                │                                              │
                        │  3. Recommend ─┘  ──► Improved System Prompt                │
                        │                        Improved Tool Descriptions            │
                        │                                │                              │
                        │  4. Bundle ───────────────────►│  Configuration Bundle (C)   │
                        │                                 │  Configuration Bundle (T1)  │
                        │                                 │                             │
                        │  5a. A/B Test ─────────────────┘                             │
                        │      Config-Bundle Routing: same runtime, different prompts  │
                        │                                                               │
                        │  5b. A/B Test (target-based)                                 │
                        │      Target Routing: different runtimes (v1 vs v2)           │
                        └─────────────────────────────────────────────────────────────┘

Config-Bundle A/B Architecture:

  User ──► [Gateway] ──50%──► [Config Bundle C  → HR Runtime v1] ──► CloudWatch
                  │                                                         │
                  └──50%──► [Config Bundle T1 → HR Runtime v1] ──► CloudWatch
                                                                            │
                                                              [Online Eval] ┘ ──► A/B Results

Target-Based A/B Architecture (Phased Rollout):

  User ──► [Gateway] ──90%──► [Target HRAgentV1 → HR Runtime v1 (stable)] ──► CloudWatch
                  │                                                                    │
                  └──10%──► [Target HRAgentV2 → HR Runtime v2 (canary)]  ──► CloudWatch
                                                                                       │
                                                                 [Online Eval v1+v2] ──┘ ──► A/B Results
```

### Key Components

| Component | Service | Purpose |
|-----------|---------|---------|
| AgentCore Runtime | `bedrock-agentcore-control` | Hosts the HR Assistant container |
| Configuration Bundle | `bedrock-agentcore-control` | Versioned system prompt storage |
| Batch Evaluation | `bedrock-agentcore` (DP) | Off-line scoring of historical sessions |
| Recommendation | `bedrock-agentcore` (DP) | AI-generated prompt/tool improvements |
| Gateway + Targets | `bedrock-agentcore-control` | Traffic routing for A/B tests |
| Online Eval Config | `bedrock-agentcore-control` | Continuous automatic session scoring |
| A/B Test | `bedrock-agentcore` (DP) | Traffic split + statistical comparison |

---

## Getting Started

### Prerequisites

- AWS account with Bedrock AgentCore access enabled
- AWS CLI configured: `aws configure` (or set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`)
- IAM caller permissions (see [optimization prerequisites](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/optimization-prereqs.html)):
  - `bedrock-agentcore:GetConfigurationBundle*`, `ListConfigurationBundleVersions`, `CreateConfigurationBundle`, `UpdateConfigurationBundle`, `DeleteConfigurationBundle` (ConfigurationBundles)
  - `bedrock-agentcore:StartRecommendation`, `GetRecommendation` (Recommendations)
  - `bedrock-agentcore:StartABTest`, `StopABTest`, `GetABTest`, `DeleteABTest`, `ListABTests` (ABTesting)
  - `logs:GetLogEvents`, `FilterLogEvents`, `StartQuery`, `GetQueryResults` on `runtimes/*` log groups (CloudWatchLogs)
  - `iam:CreateRole`, `AttachRolePolicy`, `PassRole` to create the execution role for the A/B test
- Python 3.10 or later

### Option 1: Jupyter Notebook

Run the full tutorial interactively:

```bash
# Install Jupyter if needed
pip install jupyter

# Install dependencies (also done in the notebook's first cell)
pip install "bedrock-agentcore>=1.7.0" "boto3>=1.43.0" requests

# Launch the notebook
jupyter notebook optimization_tutorial.ipynb
```

Then run all cells from top to bottom. The notebook streams deployment and evaluation output as it runs.

### Option 2: AgentCore CLI

The same workflow can be driven entirely from the command line. Install the CLI:

```bash
npm install -g @aws/agentcore@0.30.0
agentcore --version   # should print 0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
```

See the [CLI Examples](#agentcore-cli-examples) section below for the full command sequence.

---

## AgentCore CLI Examples

The following commands reproduce the notebook workflow from the command line.

### Step 1: Deploy the HR Assistant

```bash
# Scaffold a new AgentCore project
agentcore create --name HRAssistant --framework Strands --model-provider Bedrock --defaults

# Copy the HR assistant implementation
cp hr_assistant_agent.py app/HRAssistant/main.py

# Test locally before deploying
agentcore dev

# Deploy to AWS (builds container, pushes to ECR, creates AgentCore Runtime)
agentcore deploy
# Note the Runtime ID and ARN from the output.
```

### Step 2: Run Baseline Evaluation

```bash
# Invoke the agent to generate traffic
agentcore invoke \
  --runtime HRAssistant \
  --prompt "Employee ID: EMP-001. What is my PTO balance?" \
  --session-id $(python3 -c "import uuid; print(uuid.uuid4())")

# Run batch evaluation across all sessions
agentcore run batch-evaluation \
  --runtime HRAssistant \
  --evaluator Builtin.GoalSuccessRate Builtin.Helpfulness Builtin.Correctness
```

### Step 3: Get Recommendations

```bash
# System prompt recommendation (optimize for GoalSuccessRate)
# Use --inline with the current prompt, or --prompt-file ./system-prompt.txt
agentcore run recommendation \
  --runtime HRAssistant \
  --type system-prompt \
  --evaluator Builtin.GoalSuccessRate \
  --inline "You are an HR assistant for Acme Corp. Help employees with PTO, policies, benefits, and pay stubs."

# Tool description recommendation
agentcore run recommendation \
  --runtime HRAssistant \
  --type tool-description \
  --tools "get_pto_balance:Get the PTO balance for an employee" \
  --tools "get_policy:Look up an HR policy by name"
```

### Step 4: Create Configuration Bundles

```bash
# Create control bundle (original prompt) using {{runtime:<name>}} placeholder
agentcore add config-bundle \
  --name HRControl \
  --components '{"{{runtime:HRAssistant}}": {"configuration": {"systemPrompt": "'"$(cat original_prompt.txt)"'"}}}'
agentcore deploy

# Create treatment bundle (recommended prompt)
agentcore add config-bundle \
  --name HRTreatment \
  --components '{"{{runtime:HRAssistant}}": {"configuration": {"systemPrompt": "'"$(cat recommended_prompt.txt)"'"}}}'
agentcore deploy

# View version IDs (needed for the A/B test below)
agentcore cb versions --bundle HRControl --json
agentcore cb versions --bundle HRTreatment --json
```

### Step 5a: A/B Test — Config-Bundle Routing

```bash
# Create gateway
agentcore add gateway --name HRGateway --authorizer-type AWS_IAM

# Create gateway target
agentcore add gateway-target \
  --gateway HRGateway \
  --name HRAgentV1 \
  --type mcp-server \
  --runtime HRAssistant

# Create online evaluation config
agentcore add online-eval \
  --name HROnlineEval \
  --runtime HRAssistant \
  --evaluator Builtin.GoalSuccessRate Builtin.Helpfulness \
  --sampling-rate 100 \
  --enable-on-create
agentcore deploy

# Create A/B test with config-bundle routing (50/50 split)
# Replace <control-version-id> and <treatment-version-id> with IDs from: agentcore cb versions --bundle HRControl --json
agentcore add ab-test \
  --name HRBundleABTest \
  --runtime HRAssistant \
  --control-bundle HRControl \
  --control-version <control-version-id> \
  --treatment-bundle HRTreatment \
  --treatment-version <treatment-version-id> \
  --control-weight 50 \
  --treatment-weight 50 \
  --online-eval HROnlineEval \
  --enable
agentcore deploy

# Monitor results
agentcore ab-test HRBundleABTest
```

### Step 5b: A/B Test — Target-Based Routing (Phased Rollout)

```bash
# Deploy v2 of the agent (with new code changes)
agentcore create --name HRAssistantV2 --framework Strands --model-provider Bedrock --defaults
cp hr_assistant_agent.py app/HRAssistantV2/main.py
# (Apply v2 code changes to main.py)
cd HRAssistantV2 && agentcore deploy

# Add v2 gateway target
agentcore add gateway-target \
  --gateway HRGateway \
  --name HRAgentV2 \
  --type mcp-server \
  --runtime HRAssistantV2

# Create online eval config for v2
agentcore add online-eval \
  --name HROnlineEvalV2 \
  --runtime HRAssistantV2 \
  --evaluator Builtin.GoalSuccessRate Builtin.Helpfulness \
  --sampling-rate 100 \
  --enable-on-create
agentcore deploy

# Register named endpoints for each runtime version (required for target-based mode)
agentcore add runtime-endpoint --runtime HRAssistant   --name v1
agentcore add runtime-endpoint --runtime HRAssistantV2 --name v2
agentcore deploy

# Create A/B test with target-based routing (90/10 canary)
agentcore add ab-test \
  --name HRTargetABTest \
  --mode target-based \
  --control-endpoint v1 \
  --treatment-endpoint v2 \
  --control-weight 90 \
  --treatment-weight 10 \
  --control-online-eval HROnlineEval \
  --treatment-online-eval HROnlineEvalV2 \
  --enable
agentcore deploy

# Monitor canary results
agentcore ab-test HRTargetABTest

# If v2 wins, stop the test
agentcore stop ab-test HRTargetABTest
```

### Step 6: Cleanup

```bash
agentcore stop ab-test HRBundleABTest
agentcore stop ab-test HRTargetABTest
agentcore remove ab-test --name HRBundleABTest
agentcore remove ab-test --name HRTargetABTest
agentcore remove online-eval --name HROnlineEval
agentcore remove online-eval --name HROnlineEvalV2
agentcore remove config-bundle --name HRControl
agentcore remove config-bundle --name HRTreatment
agentcore remove gateway --name HRGateway
agentcore remove agent --name HRAssistant
agentcore remove agent --name HRAssistantV2
agentcore deploy -y
```

---

## File Reference

| File | Description |
|------|-------------|
| `hr_assistant_agent.py` | HR Assistant Strands agent with Configuration Bundle hook. Handles PTO, policies, benefits, and pay stubs. |
| `deploy_agent.py` | Standalone deploy script: creates IAM role, packages dependencies, uploads to S3, and creates an AgentCore Runtime. Supports `--version v1` and `--version v2`. |
| `optimization_tutorial.ipynb` | End-to-end tutorial notebook covering all optimization features. |

---

## Key Concepts

### Config-Bundle vs. Target-Based A/B Testing

| | Config-Bundle Routing | Target-Based Routing |
|---|---|---|
| **What changes** | System prompt, config (no code change) | Agent binary, tools, model |
| **Redeployment needed** | No — config applied at request time | Yes — new runtime required |
| **Best for** | Prompt tuning, config experiments | Code releases, version upgrades |
| **Traffic split** | Typically 50/50 | Typically 90/10 canary |
| **Rollback** | Instant — update bundle version | Runtime still running; shift weights back |

### Phased Rollout Workflow (Target-Based)

```
10% canary  →  validate no regressions (errors, latency, quality drop)
      ↓
50% ramp    →  gather statistical significance
      ↓
100% promote →  complete cutover; decommission old runtime
```

### Configuration Bundle Hook

The HR agent reads its system prompt from the bundle on every model call:

```python
from bedrock_agentcore.runtime import BedrockAgentCoreContext
from strands.hooks.events import BeforeModelCallEvent

def _config_bundle_hook(event: BeforeModelCallEvent) -> None:
    bundle = BedrockAgentCoreContext.get_config_bundle()
    if bundle:
        event.agent.system_prompt = bundle.get("system_prompt", DEFAULT_SYSTEM_PROMPT)

agent.hooks.add_callback(BeforeModelCallEvent, _config_bundle_hook)
```

This pattern allows live prompt updates and A/B testing without redeployment.

---

## Next Steps

- **Add custom evaluators**: Implement Lambda-based code evaluators for deterministic HR policy compliance checks (see tutorial `06-workshops/07-AgentCore-evaluations/06-programmatic_evaluators`)
- **Automate the loop**: Run batch evaluations in CI/CD to catch regressions before deployment (see tutorial `06-workshops/07-AgentCore-evaluations/05-groundtruth-based-evalautions`)
- **Use recommendations iteratively**: Re-run recommendations after each traffic batch to compound improvements
- **Multi-metric optimization**: Run separate recommendation jobs targeting different evaluators, then pick the prompt that best balances between the metrics you care about
- **Increase canary exposure**: When target-based test shows improvement, use `update_ab_test` to increase treatment weight gradually (10% → 25% → 50% → 100%)
- **Explore online evaluation**: Keep online eval configs enabled in production for continuous quality monitoring with zero explicit API calls per session
