# Custom Code-Based evaluation

Evaluate your Amazon Bedrock AgentCore agent using **deterministic Lambda-backed evaluators**. Code-based evaluators run your own Python logic — regex checks, business rule validation, statistical tests — and return a score without any LLM inference. Results are fully reproducible across runs.

## What You'll Learn

| Concept | Description |
|---|---|
| **Code-based evaluators** | Lambda functions that receive agent spans and return scores using deterministic logic |
| **TRACE-level code evaluator** | `HRResponseLength` — validates response length is within acceptable bounds |
| **SESSION-level code evaluator** | `HRFactChecker` — pattern-matches HR facts (PTO balances, pay figures, policy details) against known ground truth |
| **Mixed evaluator sets** | Combine code-based evaluators with built-in LLM evaluators in the same run |
| **On-demand evaluation** | Spot-check a specific session with `EvaluationClient` |
| **Dataset runner** | Automate agent invocation + evaluation across multiple scenarios |
| **Online evaluation** | Create a config that continuously scores live traffic with code-based evaluators |

## Setup with AgentCore CLI

The fastest way to bootstrap and deploy the agent is with the [AgentCore CLI](https://github.com/aws/agentcore-cli) (`0.30.0`).

### Install the CLI

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
agentcore --version   # should print 0.30.0
```

### Create and deploy the agent

```bash
# Scaffold a new AgentCore project
agentcore create --name HRAssistant --framework Strands --model-provider Bedrock --defaults

# Copy the HR assistant implementation
cp ../utils/hr_assistant_agent.py app/HRAssistant/main.py

# Test locally
agentcore dev

# Deploy to AWS (builds container, pushes to ECR, creates AgentCore runtime)
agentcore deploy
```

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

```bash
# Mix code-based (--evaluator-arn) with builtin (--evaluator) in one command
agentcore run eval \
  --runtime-arn <agent-runtime-arn> \
  --evaluator-arn <hr-response-length-evaluator-arn> \
  --evaluator-arn <hr-fact-checker-evaluator-arn> \
  --evaluator Builtin.Correctness \
  --evaluator Builtin.Helpfulness \
  --session-id <session-id> \
  --region <aws-region>
```

### Add online evaluation via CLI

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

---

## Key Concepts

### Code-Based vs Built-in Evaluators

| | Built-in (LLM-as-judge) | Code-based (Lambda) |
|---|---|---|
| **Judge** | LLM with a fixed evaluation prompt | Your custom Lambda function |
| **Output** | Probabilistic score with explanation | Deterministic score |
| **Cost** | LLM inference per evaluation | Lambda invocation |
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
│  evaluate.py                                                                 │
│                                                                              │
│  1. Deploy Lambda functions (hr-response-length, hr-fact-checker)            │
│  2. Register evaluators via bedrock-agentcore-control                        │
│  3a. On-demand: EvaluationClient.run(session_id, evaluator_ids)             │
│  3b. Dataset: OnDemandEvaluationDatasetRunner.run(dataset, agent_invoker)   │
│  3c. Online: create_online_evaluation_config (auto-evaluates all sessions)  │
└────────────────┬────────────────────────────────────────────────────────────┘
                 │
     ┌───────────▼────────────┐        ┌──────────────────────────────┐
     │  AgentCore runtime      │        │  AgentCore evaluations DP   │
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

**evaluation flow:**
1. Agent is invoked; OTel spans are written to CloudWatch
2. `EvaluationClient` or `OnDemandEvaluationDatasetRunner` collects spans from CloudWatch
3. The service calls each evaluator — builtin evaluators run LLM inference; code-based evaluators invoke your Lambda with the span payload
4. For **online evaluation**, AgentCore continuously watches the log group and automatically evaluates new sessions without any explicit trigger
5. All results are aggregated and returned (on-demand) or written to the online evaluation results log group

---

## Prerequisites

Deploy the shared HR Assistant agent (runs once for all `evaluate/` subfolders):

```bash
cd ../utils
python deploy.py
```

This writes `utils/agent_config.json` which `evaluate.py` reads automatically.

## Run the evaluation

```bash
# Install dependencies
pip install -r requirements.txt

# Run all evaluation steps (takes ~15–20 min due to Lambda packaging)
python evaluate.py
```

Optional flags:

```bash
python evaluate.py --region us-west-2
python evaluate.py --config /path/to/custom/agent_config.json
```

## What the Script Does

### Step 1 — Lambda Execution Role

Creates (or reuses) an IAM role `AgentCoreLambdaEvaluatorRole` with `AWSLambdaBasicExecutionRole` permissions.

### Step 2 — Package and Deploy Lambda Evaluators

Two Lambda functions are packaged with the `bedrock-agentcore` SDK and deployed to AWS Lambda. The source files live in `lambdas/`:

**`lambdas/hr_response_length/lambda_function.py`** — TRACE level

```python
@custom_code_based_evaluator()
def lambda_handler(evaluator_input: EvaluatorInput, _context) -> EvaluatorOutput:
    # Extracts the agent's response text from invoke_agent spans
    # Returns PASS if 50 <= len(response) <= 600, FAIL otherwise
```

**`lambdas/hr_fact_checker/lambda_function.py`** — SESSION level

```python
@custom_code_based_evaluator()
def lambda_handler(evaluator_input: EvaluatorInput, _context) -> EvaluatorOutput:
    # Checks PTO balances, pay stub figures, and policy facts against
    # the known mock data store using exact regex pattern matching
    # Returns PASS / PARTIAL / FAIL / SKIP based on fraction of checks passed
```

The `@custom_code_based_evaluator()` decorator handles the Lambda handler protocol. The evaluator receives `EvaluatorInput` with `session_spans` (the agent's CloudWatch OTel spans) and returns `EvaluatorOutput` with `value`, `label`, and `explanation`.

### Step 3 — Register Evaluators

Each Lambda is registered as an AgentCore evaluator via `create_evaluator` with a `codeBased.lambdaConfig`. The resulting evaluator ID can be used anywhere built-in evaluator IDs are accepted.

```python
resp = cp.create_evaluator(
    evaluatorName="HRResponseLength_<suffix>",
    level="TRACE",
    evaluatorConfig={
        "codeBased": {
            "lambdaConfig": {
                "lambdaArn": lambda_arn,
                "lambdaTimeoutInSeconds": 30,
            }
        }
    },
)
```

### Step 4 — On-Demand evaluation

An HR assistant session is invoked (PTO balance + PTO request + policy lookup), then evaluated with a mix of code-based and built-in evaluators:

| Evaluator | Type | Level | What it checks |
|---|---|---|---|
| `Builtin.Correctness` | Built-in | TRACE | factual accuracy |
| `Builtin.GoalSuccessRate` | Built-in | SESSION | did agent meet user's goal |
| `HRResponseLength` | Code-based | TRACE | response is 50–600 chars |
| `HRFactChecker` | Code-based | SESSION | PTO numbers and policy facts are accurate |

The same mixed set also runs through `OnDemandEvaluationDatasetRunner` with 5 scenarios.

### Step 5 — Online evaluation with Code-Based Evaluators

An online evaluation config is created with the two code-based evaluators. Every new HR assistant session is automatically scored as it completes.

```
Evaluators : HRResponseLength (TRACE) + HRFactChecker (SESSION)
Sampling   : 100%
Results    : /aws/bedrock-agentcore/evaluations/results/<config-id>
```

## Lambda Span Input Structure

Your Lambda receives `evaluator_input.session_spans` — a list of OTel span dicts:

```python
# invoke_agent span (contains the response text):
{
    "name": "invoke_agent",
    "span_events": [{
        "body": {
            "output": {
                "messages": [{"content": {"message": "Agent response text..."}}]
            }
        }
    }]
}

# execute_tool span (contains tool call info):
{
    "name": "execute_tool",
    "attributes": {
        "gen_ai.operation.name": "execute_tool",
        "gen_ai.tool.name": "get_pto_balance"
    }
}
```

For TRACE-level evaluators, `evaluator_input.target_trace_id` identifies which trace to evaluate.

## Expected Output

```
[1/5] Setting up Lambda execution role ...
  Using existing role: arn:aws:iam::...

[2/5] Packaging and deploying Lambda evaluators ...
  Packaging hr-response-length ...
    Bundling bedrock-agentcore SDK ...
    Zip size: 12345 KB
  ARN: arn:aws:lambda:us-east-1:...:function:hr-response-length

[3/5] Registering code-based evaluators ...
  Creating 'HRResponseLength_<suffix>' (level=TRACE) ...
    evaluatorId: HRResponseLength_<suffix>-XXXXXXXXXX
  Creating 'HRFactChecker_<suffix>' (level=SESSION) ...
    evaluatorId: HRFactChecker_<suffix>-XXXXXXXXXX

[4/5] Running on-demand evaluation ...
  Evaluator                                     Value    Label
  -------------------------------------------------------------------------
  Builtin.Correctness                           0.9      correct
  Builtin.GoalSuccessRate                       1.0      success
  HRResponseLength                              1.0      PASS
  HRFactChecker                                 1.0      PASS

  Dataset runner complete: 5 completed, 0 failed.

[5/5] Creating online evaluation config ...
  Online eval config created:
    ID  : hr_code_eval_<suffix>-XXXXXXXXXX
```

## Results Files

| File | Contents |
|---|---|
| `results/code_evaluator_ids.json` | Lambda ARNs and evaluator IDs for both evaluators |
| `results/on_demand_results.json` | Per-turn/session scores from EvaluationClient |
| `results/dataset_runner_results.json` | Per-scenario scores across 5 test scenarios |
| `results/online_eval_config.json` | Online config ID and ARN |

## Managing the Online evaluation Config

```bash
# Disable (must disable before deleting while evaluators are locked)
aws bedrock-agentcore-control update-online-evaluation-config \
    --online-evaluation-config-id <config-id> \
    --enable-config false

# Delete
aws bedrock-agentcore-control delete-online-evaluation-config \
    --online-evaluation-config-id <config-id>
```

---

## Evaluators Built in This Tutorial

### HRResponseLength (TRACE level)

Checks that each agent response is between 50 and 600 characters. Responses shorter than 50 chars are likely incomplete; longer than 600 suggests over-explanation. Thinking blocks (`<thinking>...</thinking>`) are stripped before measurement.

- **Level:** TRACE — evaluated once per agent response
- **Lambda:** `hr-response-length`
- **Returns:** `1.0` (PASS) if within range, `0.0` (FAIL) otherwise
- **Used in:** On-demand evaluation (EvaluationClient + DatasetRunner) and Online evaluation

### HRFactChecker (SESSION level)

Deterministically validates that the HR assistant's responses contain accurate facts drawn from the mock data store. Uses exact pattern matching with no LLM inference.

- **Level:** SESSION — evaluated once per conversation
- **Lambda:** `hr-fact-checker`
- **Facts checked:**
  - PTO balances: EMP-001 (10 remaining), EMP-002 (3 remaining), EMP-042 (13 remaining)
  - Pay stubs: gross/net pay figures for each employee/period
  - PTO request ID format `PTO-2026-NNN`
  - policy facts: 15-day PTO accrual, 2-day advance notice, 401k 4% match, 90% health coverage
- **Returns:** fraction of applicable checks passed (0.0–1.0), labeled `PASS`, `PARTIAL`, `FAIL`, or `SKIP`
- **Used in:** On-demand evaluation (EvaluationClient + DatasetRunner) and Online evaluation

---

## Mixed Evaluator Set

The script runs `OnDemandEvaluationDatasetRunner` with five evaluators simultaneously:

| Evaluator | Type | Level |
|---|---|---|
| `Builtin.Correctness` | Built-in LLM | TRACE |
| `Builtin.Helpfulness` | Built-in LLM | TRACE |
| `Builtin.ResponseRelevance` | Built-in LLM | TRACE |
| `HRResponseLength` | Code-based Lambda | TRACE |
| `HRFactChecker` | Code-based Lambda | SESSION |

Results from all five evaluators are collected per scenario, letting you compare qualitative LLM scores with deterministic code scores side-by-side.

---

## Online evaluation with Code-Based Evaluators

Step 5 demonstrates **online evaluation** — a continuous evaluation mode where AgentCore automatically evaluates every live agent session without explicit API calls per session.

### How it works

1. Register code-based evaluators (same as for on-demand)
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
| Code-based evaluators | Supported | Supported |
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

## Script Walkthrough

| Step | Description |
|---|---|
| 1 | Lambda execution role — create (or reuse) `AgentCoreLambdaEvaluatorRole` |
| 2 | Package and deploy Lambda functions (`hr-response-length`, `hr-fact-checker`) with bedrock-agentcore SDK bundled |
| 3 | Register evaluators via `bedrock-agentcore-control` boto3 service |
| 4 | On-demand evaluation — invoke HR assistant, run `EvaluationClient` (code-based + built-in), then `OnDemandEvaluationDatasetRunner` with 5 scenarios |
| 5 | Online evaluation — create `online_evaluation_config` with code-based evaluators; auto-triggered on all new sessions |

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

Code-based evaluators are supported for **both on-demand** (`EvaluationClient`, `OnDemandEvaluationDatasetRunner`) and **online** (`create_online_evaluation_config`) evaluation.

---

## Next Steps

- Extend `HRFactChecker` with additional business rules as your agent and data model evolve
- Combine code-based evaluators with `EvaluationClient` to validate specific production sessions
- Add code-based evaluators to your CI/CD pipeline for zero-cost regression testing on every deployment
- Use online evaluation with a lower sampling rate (e.g. 10%) to cost-effectively monitor high-traffic agents
- Explore [`ground-truth-based-evaluation/`](../ground-truth-based-evaluation/) for `EvaluationClient` and ground-truth-based evaluations with built-in evaluators
