# Programmatic (Code-Based) Evaluators

## Introduction

This tutorial shows how to build and run **custom code-based evaluators** with Amazon Bedrock AgentCore Evaluations. Instead of relying on an LLM as the judge, code-based evaluators delegate scoring to an AWS Lambda function you write. This gives you deterministic, low-cost, fully customizable evaluation logic that can encode exact business rules, format constraints, or data validation requirements that an LLM might interpret loosely.

The tutorial demonstrates code-based evaluators in **both on-demand and online evaluation** modes, and pairs them with built-in LLM evaluators to show how both types work side-by-side in a mixed evaluation run.

---

## Setup with AgentCore CLI

The fastest way to bootstrap and deploy the agent is with the [AgentCore CLI](https://github.com/aws/agentcore-cli) (`0.30.0`).

### Prerequisites

- **Node.js** 20.x or later
- **uv** 0.4+ (Python package manager)
- **AWS CLI** 2.x with credentials configured
- **Docker** running locally (for agent container build)
- **Git** 2.x

### Install the CLI

```bash
npm install -g @aws/agentcore@0.30.0
agentcore --version   # should print 0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
```

### Configure AWS credentials

```bash
aws configure
aws sts get-caller-identity   # verify credentials
```

Your IAM user/role needs permissions for: AgentCore Runtime, AgentCore Evaluations, Lambda,
CloudWatch Logs, ECR, IAM, and Bedrock.

### Create and deploy the agent

```bash
# Scaffold a new AgentCore project
agentcore create --name HRAssistant --framework Strands --model-provider Bedrock --defaults

# Copy the HR assistant implementation
cp hr_assistant_agent.py app/HRAssistant/main.py

# Test locally
agentcore dev

# Deploy to AWS (builds container, pushes to ECR, creates AgentCore Runtime)
agentcore deploy
```

After `agentcore deploy` completes, note the **Runtime ID** and **ARN** from the output.

### Register a code-based evaluator via CLI

`agentcore add evaluator` registers the evaluator in your project's `agentcore.json`. The evaluator
is created in AWS when you run `agentcore deploy`.

```bash
# Register a TRACE-level code-based evaluator
agentcore add evaluator \
  --name HRResponseLength \
  --level TRACE \
  --type code-based \
  --lambda-arn arn:aws:lambda:<region>:<account-id>:function:hr-response-length \
  --timeout 30

# Register a SESSION-level code-based evaluator
agentcore add evaluator \
  --name HRFactChecker \
  --level SESSION \
  --type code-based \
  --lambda-arn arn:aws:lambda:<region>:<account-id>:function:hr-fact-checker \
  --timeout 60
```

### Run on-demand evaluation via CLI

**Standalone mode** (no project needed) — use `--runtime-arn` and `--evaluator-arn` with the
full ARNs of already-deployed resources. This works from any directory:

```bash
agentcore run eval \
  --runtime-arn <agent-runtime-arn> \
  --evaluator-arn <hr-response-length-evaluator-arn> \
  --evaluator-arn <hr-fact-checker-evaluator-arn> \
  --session-id <session-id> \
  --region <aws-region>
```

Mix code-based (`--evaluator-arn`) with builtin (`--evaluator`) in one command:

```bash
agentcore run eval \
  --runtime-arn <agent-runtime-arn> \
  --evaluator-arn <hr-response-length-evaluator-arn> \
  --evaluator-arn <hr-fact-checker-evaluator-arn> \
  --evaluator Builtin.Correctness \
  --evaluator Builtin.Helpfulness \
  --session-id <session-id> \
  --region <aws-region>
```

**Project mode** (inside a deployed project directory) — use evaluator names from `agentcore.json`.
Requires `agentcore deploy` to have been run first:

```bash
agentcore run eval \
  --runtime HRAssistant \
  --evaluator HRResponseLength \
  --evaluator HRFactChecker \
  --session-id <session-id>
```

### Add online evaluation via CLI

`agentcore add online-eval` adds the config to `agentcore.json`; it is created in AWS on
`agentcore deploy`. Run from inside your project directory:

```bash
# sampling-rate is a percentage (0.01–100)
agentcore add online-eval \
  --name hr_online_eval \
  --runtime HRAssistant \
  --evaluator HRResponseLength \
  --evaluator HRFactChecker \
  --sampling-rate 100 \
  --enable-on-create
```

> You can also use the notebook (Step 10) to create the online eval config programmatically
> using the boto3 SDK, without needing a project directory.

---

## Key Concepts

### Code-Based vs Built-in Evaluators

| | Built-in (LLM-as-judge) | Code-based (Lambda) |
|---|---|---|
| **Judge** | LLM with a fixed evaluation prompt | Your custom Lambda function |
| **Output** | Probabilistic score with explanation | Deterministic score |
| **Cost** | LLM inference per evaluation | Lambda invocation  |
| **Best for** | Nuanced qualitative assessment | Exact data validation, business rules |
| **Customizable** | Limited (fixed prompt templates) | Fully customizable |

### Evaluator Levels

| Level | Invoked | Use when |
|---|---|---|
| **TRACE** | Once per agent response (turn) | Per-response checks, e.g. length, format |
| **SESSION** | Once per conversation session | End-to-end fact accuracy across all turns |

### SDK v1.6 Lambda Contract

The `@custom_code_based_evaluator()` decorator (new in SDK v1.6) converts raw Lambda events into typed `EvaluatorInput` and `EvaluatorOutput` objects, replacing the raw dict-based pattern from earlier versions.

```python
from bedrock_agentcore.evaluation import (
    EvaluatorInput, EvaluatorOutput, custom_code_based_evaluator,
)

@custom_code_based_evaluator()
def lambda_handler(input: EvaluatorInput, context) -> EvaluatorOutput:
    # input.session_spans      — list of OTel spans for the session
    # input.evaluation_level   — "TRACE" or "SESSION"
    # input.target_trace_id    — set by service for TRACE level
    return EvaluatorOutput(value=1.0, label="PASS", explanation="...")
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  Notebook                                                                    │
│                                                                              │
│  1. Deploy Lambda functions (hr-response-length, hr-fact-checker)            │
│  2. Register evaluators via bedrock-agentcore-control                        │
│  3a. On-demand: EvaluationClient.run(session_id, evaluator_ids)             │
│  3b. Dataset: OnDemandEvaluationDatasetRunner.run(dataset, agent_invoker)   │
│  3c. Online: create_online_evaluation_config (auto-evaluates all sessions)  │
└────────────────┬────────────────────────────────────────────────────────────┘
                 │
     ┌───────────▼────────────┐        ┌──────────────────────────────┐
     │  AgentCore Runtime      │        │  AgentCore Evaluations DP   │
     │  HR Assistant agent     │──OTel─▶│  bedrock-agentcore          │
     │  (Strands Agents)       │        │                             │
     └─────────────────────────┘        │   ┌──────────────────────┐  │
                                        │   │  Builtin LLM evals   │  │
     ┌─────────────────────────┐        │   │  Correctness         │  │
     │  CloudWatch Logs        │        │   │  Helpfulness         │  │
     │  /aws/bedrock-agentcore/│        │   │  ResponseRelevance   │  │
     │  runtimes/<agent-id>    │        │   └──────────────────────┘  │
     └─────────────────────────┘        │   ┌──────────────────────┐  │
                                        │   │  Code-based Lambda   │  │
     ┌─────────────────────────┐        │   │  HRResponseLength    │  │
     │  AWS Lambda             │◀───────│   │  HRFactChecker       │  │
     │  hr-response-length     │        │   └──────────────────────┘  │
     │  hr-fact-checker        │        └─────────────────────────────┘
     └─────────────────────────┘
```

**Evaluation flow:**
1. Agent is invoked; OTel spans are written to CloudWatch
2. `EvaluationClient` or `OnDemandEvaluationDatasetRunner` collects spans from CloudWatch
3. The service calls each evaluator — builtin evaluators run LLM inference; code-based evaluators invoke your Lambda with the span payload
4. For **online evaluation**, AgentCore continuously watches the log group and automatically evaluates new sessions without any explicit trigger
5. All results are aggregated and returned (on-demand) or written to the online evaluation results log group

---

## Prerequisites

- **Python 3.10+** with the `agentcore-evals` Jupyter kernel (see parent README)
- **Docker** running locally (for agent container image build)
- **AWS credentials** with permissions for:
  - `bedrock-agentcore:*` — runtime and evaluations
  - `bedrock-agentcore-control:*` — evaluator registration and online eval config management
  - `lambda:CreateFunction`, `lambda:UpdateFunctionCode`, `lambda:AddPermission`, `lambda:GetFunction`
  - `logs:FilterLogEvents`, `logs:DescribeLogGroups` — CloudWatch span collection
  - `ecr:*` — container image for the agent
  - `iam:*` — creating execution roles for the agent and online evaluation
- **IAM role** named `AgentCoreLambdaExecutionRole` with `AWSLambdaBasicExecutionRole` attached
- **bedrock-agentcore >= 1.6.0** installed in the notebook kernel

> **Tip:** If you already ran `groundtruth_evaluations.ipynb`, the agent is already deployed and its info is stored via `%store`. This notebook reloads it automatically and skips re-deployment.

---

## Files

| File | Description |
|---|---|
| `programmatic_evaluators.ipynb` | Main tutorial notebook (standalone, end-to-end) |
| `hr_assistant_agent.py` | HR Assistant Strands agent (same as groundtruth tutorial) |
| `Dockerfile` | Container definition for the agent (used by Step 3 fresh deploy and `agentcore deploy`) |
| `requirements.txt` | Python dependencies (`bedrock-agentcore>=1.6.0`) |
| `lambdas/hr_response_length/lambda_function.py` | Response length evaluator Lambda |
| `lambdas/hr_fact_checker/lambda_function.py` | HR fact-checking evaluator Lambda |

---

## Evaluators Built in This Tutorial

### HRResponseLength (TRACE level)

Checks that each agent response is between 50 and 600 characters. Responses shorter than 50 chars are likely incomplete; longer than 600 suggests over-explanation. Thinking blocks (`<thinking>...</thinking>`) are stripped before measurement.

- **Level:** TRACE — evaluated once per agent response
- **Lambda:** `hr-response-length`
- **Returns:** `1.0` (PASS) if within range, `0.0` (FAIL) otherwise
- **Used in:** On-demand evaluation (Steps 7 & 8) and Online evaluation (Step 10)

### HRFactChecker (SESSION level)

Deterministically validates that the HR assistant's responses contain accurate facts drawn from the mock data store. Uses exact pattern matching with no LLM inference.

- **Level:** SESSION — evaluated once per conversation
- **Lambda:** `hr-fact-checker`
- **Facts checked:**
  - PTO balances: EMP-001 (10 remaining), EMP-002 (3 remaining), EMP-042 (13 remaining)
  - Pay stubs: gross/net pay figures for each employee/period
  - PTO request ID format `PTO-2026-NNN`
  - Policy facts: 15-day PTO accrual, 2-day advance notice, 401k 4% match, 90% health coverage
- **Returns:** fraction of applicable checks passed (0.0–1.0), labeled `PASS`, `PARTIAL`, `FAIL`, or `SKIP`
- **Used in:** On-demand evaluation (Steps 7 & 8) and Online evaluation (Step 10)

---

## Mixed Evaluator Set

The notebook runs `OnDemandEvaluationDatasetRunner` with five evaluators simultaneously:

| Evaluator | Type | Level |
|---|---|---|
| `Builtin.Correctness` | Built-in LLM | TRACE |
| `Builtin.Helpfulness` | Built-in LLM | TRACE |
| `Builtin.ResponseRelevance` | Built-in LLM | TRACE |
| `HRResponseLength` | Code-based Lambda | TRACE |
| `HRFactChecker` | Code-based Lambda | SESSION |

Results from all five evaluators are collected per scenario, letting you compare qualitative LLM scores with deterministic code scores side-by-side.

---

## Online Evaluation with Code-Based Evaluators

Step 10 of the notebook demonstrates **online evaluation** — a continuous evaluation mode where
AgentCore automatically evaluates every live agent session without explicit API calls per session.

### How it works

1. Register code-based evaluators (Steps 4–6, same as for on-demand)
2. Create an online evaluation config via `create_online_evaluation_config`:
   - Point it at the agent's CloudWatch log group
   - Set a sampling rate (0–100%)
   - List the evaluator IDs (code-based and/or builtin)
   - Provide an IAM execution role the service can assume
3. Enable the config — AgentCore starts watching the log group
4. Every new agent session is automatically evaluated
5. Results appear in the online evaluation results CloudWatch log group

### Evaluator locking

When a code-based evaluator is referenced by an **enabled** online evaluation config, AgentCore
**locks** it automatically. You cannot modify or delete a locked evaluator. To update it:

```
disable/delete online eval config
         ↓
update evaluator Lambda or re-register
         ↓
re-create online eval config
```

### On-demand vs. online comparison

| Dimension | On-demand | Online |
|---|---|---|
| Trigger | Explicit per session | Automatic on every invocation |
| Setup | `EvaluationClient.run()` or `OnDemandEvaluationDatasetRunner` | `create_online_evaluation_config` once |
| Code-based evaluators | ✅ Supported | ✅ Supported |
| Evaluator locking | No | Yes — while config is enabled |
| Best for | CI/CD, ad-hoc debugging | Continuous production monitoring |

### AgentCore CLI shortcut

```bash
# sampling-rate is a percentage (0.01–100); 50 = evaluate 50% of sessions
agentcore add online-eval \
  --name my_online_eval \
  --runtime MyAgent \
  --evaluator MyCodeEvaluator \
  --sampling-rate 50 \
  --enable-on-create
```

---

## Sample Prompts

The dataset includes five scenarios that exercise facts the `HRFactChecker` validates:

| Scenario | Prompt | Expected behavior |
|---|---|---|
| `pto-balance-check` | "What is the current PTO balance for employee EMP-001?" | Agent calls `get_pto_balance`, reports 10 remaining days |
| `submit-pto-request` | "Please submit a PTO request for EMP-001 from 2026-04-14 to 2026-04-16 for a family vacation." | Agent calls `submit_pto_request`, returns a `PTO-2026-NNN` ID |
| `pay-stub-lookup` | "Can you pull up the January 2026 pay stub for employee EMP-001?" | Agent calls `get_pay_stub`, reports gross $8,333.33 / net $5,362.50 |
| `pto-policy-lookup` | "What is the company PTO policy?" | Agent calls `lookup_hr_policy`, mentions 15-day accrual and 2-day advance notice |
| `health-benefits` | "Can you tell me about the company health insurance options?" | Agent calls `get_benefits_summary`, mentions 90% premium coverage |

You can extend the dataset with additional scenarios to test more HR topics (remote work policy, parental leave, 401k, etc.).

---

## Notebook Walkthrough

| Step | Description |
|---|---|
| 1 | Install dependencies (`bedrock-agentcore>=1.6.0`) |
| 2 | Configure AWS session, region, and Lambda role ARN |
| 3 | Agent setup — reload from `%store` (groundtruth notebook) or deploy fresh with boto3 |
| 4 | Define Lambda evaluator functions using the `@custom_code_based_evaluator()` decorator |
| 5 | Deploy Lambda functions (bundled with bedrock-agentcore SDK + pydantic) |
| 6 | Register evaluators via `bedrock-agentcore-control` boto3 service |
| 7 | On-demand evaluation with `EvaluationClient` (code-based + builtin evaluators) |
| 8 | Dataset evaluation with `OnDemandEvaluationDatasetRunner` (mixed evaluator set) |
| 9 | Inspect and compare results (per-scenario tables + aggregate score comparison) |
| **10** | **Online evaluation with `create_online_evaluation_config` (code-based evaluators, auto-triggered)** |
| 11 | Cleanup — delete Lambda functions, evaluator records, online eval config, and agent runtime |

---

## Span Structure (Strands / AgentCore OTel)

Lambda functions receive OTel spans from the evaluation service. Key fields:

```
span.name                                  e.g. "invoke_agent", "llm_call"
span.attributes.gen_ai.operation.name      "execute_tool" for tool-call spans
span.attributes.gen_ai.tool.name           tool name (e.g. "get_pto_balance")
span.span_events[*]
  .body.output.messages[*]
  .content.message                         final agent response text
```

`EvaluatorInput.session_spans` provides the full list. At TRACE level, `EvaluatorInput.target_trace_id` identifies which trace to scope the evaluation to.

---

## When to Use Code-Based Evaluators

- **Exact data validation** — check that specific numbers, IDs, or codes appear in responses
- **Format compliance** — validate response length, structure, or formatting constraints
- **Business rule enforcement** — encode domain-specific rules that LLMs might interpret loosely
- **High-volume evaluation** — reduce cost for evaluations that run on every production session
- **Regulatory requirements** — verify that required disclosures or disclaimers are always present
- **Continuous monitoring** — combine with online evaluation for zero-touch production quality gates

Code-based evaluators are supported for **both on-demand** (`EvaluationClient`,
`OnDemandEvaluationDatasetRunner`) and **online** (`create_online_evaluation_config`) evaluation.

---

## Cleanup

To remove created AWS resources:

```python
# 1. Disable online evaluation config first (unlocks evaluators)
cp_client.update_online_evaluation_config(
    onlineEvaluationConfigId=ONLINE_EVAL_CONFIG_ID,
    enableOnCreate=False,
)
cp_client.delete_online_evaluation_config(onlineEvaluationConfigId=ONLINE_EVAL_CONFIG_ID)

# 2. Delete Lambda functions
for fn in ["hr-response-length", "hr-fact-checker"]:
    lambda_client.delete_function(FunctionName=fn)

# 3. Delete evaluator registrations (now unlocked)
for name, eid in CODE_EVAL_IDS.items():
    cp_client.delete_evaluator(evaluatorId=eid)

# 4. Delete agent runtime (only if deployed in this notebook)
if not _agent_loaded:
    agentcore_control.delete_agent_runtime(agentRuntimeId=AGENT_ID)
```

Alternatively, run the cleanup cell (Step 11) in the notebook — it is commented out by default to prevent accidental deletion.

---

## Next Steps

- Extend `HRFactChecker` with additional business rules as your agent and data model evolve
- Combine code-based evaluators with `EvaluationClient` to validate specific production sessions
- Add code-based evaluators to your CI/CD pipeline for zero-cost regression testing on every deployment
- Use online evaluation with a lower sampling rate (e.g. 10%) to cost-effectively monitor high-traffic agents
- Explore the [groundtruth tutorial](../05-groundtruth-based-evalautions/) for `EvaluationClient` and ground-truth-based evaluations with built-in evaluators
