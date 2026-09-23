# Ground Truth Evaluations with Custom Evaluators

## Introduction

This tutorial demonstrates evaluation of an agentic application using
[**Amazon Bedrock AgentCore Evaluations**](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations.html) with ground-truth reference inputs. It covers
three evaluation interfaces and shows how to create **custom LLM-as-a-judge
evaluators** that use ground-truth placeholders to define scoring criteria for your
application domain.

The tutorial deploys an **HR Assistant agent** for Acme Corp, a
[Strands Agents](https://strandsagents.com/) application that helps employees with PTO
management, HR policy lookups, benefits information, and pay stub retrieval. Its tools
return deterministic mock data, making evaluation results fully reproducible.

### Key concepts covered

| Concept | Description |
|---|---|
| `EvaluationClient` | Evaluate specific existing CloudWatch sessions against ground-truth references |
| `OnDemandEvaluationDatasetRunner` | Define a test dataset, auto-invoke the agent per scenario, and evaluate the results |
| `BatchEvaluationRunner` | Evaluate many sessions in a single service-side job with aggregate scores per evaluator |
| `ReferenceInputs` | Supply `expected_response`, `expected_trajectory`, and `assertions` as ground truth |
| Custom evaluators | Create LLM-as-a-judge evaluators with domain-specific instructions and ground-truth placeholders |

| | OnDemandEvaluationDatasetRunner | BatchEvaluationRunner |
|---|---|---|
| **Where evaluation runs** | Client-side: invoke, wait, collect spans, call Evaluate API | Service-side: invoke, wait, `StartBatchEvaluation`, poll `GetBatchEvaluation` |
| **Results** | Per-scenario, per-evaluator detail immediately in the response object | Aggregate `averageScore` per evaluator; per-session detail in CloudWatch |
| **Best for** | Dev-time iteration, CI/CD pipelines, small datasets, debugging individual scenarios | Baseline measurement, large datasets, pre/post comparison across many sessions |
| **Evaluator support** | All built-ins + custom evaluators; handles session/trace/tool-call levels automatically | All built-ins; service caps at 500 sessions per job |

> **Further reading**
> - [Ground-truth evaluations](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/ground-truth-evaluations.html)
> - [Dataset-based evaluations](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/dataset-evaluations.html)
> - [Batch evaluation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/batch-evaluations.html)

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Tutorial Notebook (groundtruth_evaluations.ipynb)                      │
│                                                                         │
│  Step 1  ──► Install dependencies (bedrock-agentcore, strands-agents)  │
│                                                                         │
│  Step 2  ──► Configure boto3 session and REGION                        │
│                                                                         │
│  Step 3  ──► Deploy HR Assistant via agentcore CLI                     │
│               │  deploy_hr_assistant_agent.py                           │
│               └──► AgentCore Runtime  (HR Assistant Agent)              │
│                         │  invoke_agent_runtime()                       │
│                                                                         │
│  Step 4  ──► Invoke agent to generate sessions                         │
│               │  OTel spans ──► CloudWatch Logs                         │
│                                                                         │
│  Step 5  ──► EvaluationClient.run()                                    │
│               │  CloudWatchAgentSpanCollector reads spans               │
│               └──► Evaluate API  ──► Built-in + Custom Evaluators       │
│                                       └──► Scores & Explanations        │
│                                                                         │
│  Step 6  ──► OnDemandEvaluationDatasetRunner.run()                     │
│               │  Invokes agent per scenario                             │
│               │  Waits for CloudWatch ingestion                         │
│               └──► Evaluate API  ──► Built-in + Custom Evaluators       │
│                                       └──► Per-scenario Results         │
│                                                                         │
│  Step 7  ──► BatchEvaluationRunner.run_dataset_evaluation()            │
│               │  Invokes agent, submits StartBatchEvaluation            │
│               │  Polls GetBatchEvaluation until complete                │
│               └──► Aggregate scores per evaluator                       │
└─────────────────────────────────────────────────────────────────────────┘
```

**Component roles**

| Component | Role |
|---|---|
| AgentCore Runtime | Hosts the HR Assistant agent, emits OTel spans to CloudWatch |
| CloudWatch Logs | Stores session spans; queried by span collectors and batch evaluation |
| `bedrock-agentcore-control` | Control plane: creates custom evaluators and agent runtimes |
| Evaluate API (`bedrock-agentcore`) | Data plane: scores sessions against evaluator definitions |
| `agentcore` CLI | Builds the container image via CodeBuild and deploys the runtime |

---

## Prerequisites

- **Python 3.10+** with the packages in `requirements.txt`
- **AWS credentials** configured (e.g. via `aws configure` or environment variables) with
  permissions for:
  - `bedrock-agentcore:*`: invoke agent runtime and call Evaluate API
  - `bedrock-agentcore-control:CreateAgentRuntime`, `UpdateAgentRuntime`,
    `GetAgentRuntime`, `CreateEvaluator`: deploy agent and register evaluators
  - `logs:FilterLogEvents`, `logs:DescribeLogGroups`, `logs:StartQuery`,
    `logs:GetQueryResults`: read CloudWatch spans
  - `ecr:GetAuthorizationToken`, `ecr:BatchCheckLayerAvailability`,
    `ecr:InitiateLayerUpload`, `ecr:PutImage`: push container image
  - `codebuild:StartBuild`, `codebuild:BatchGetBuilds`: image build via CodeBuild
  - `iam:CreateRole`, `iam:AttachRolePolicy`, `iam:PassRole`: auto-create execution roles
  - `s3:PutObject`, `s3:GetObject`: CodeBuild source upload

Install dependencies:

```bash
pip install -r requirements.txt
```

---

## Usage

### Run the notebook

Open and run [`groundtruth_evaluations.ipynb`](groundtruth_evaluations.ipynb) top-to-bottom.
Each cell is idempotent. Re-running the notebook updates the existing agent runtime and
creates fresh custom evaluators with a unique suffix to avoid naming conflicts.

```bash
jupyter notebook groundtruth_evaluations.ipynb
```

Or execute non-interactively:

```bash
jupyter nbconvert --to notebook --execute --inplace groundtruth_evaluations.ipynb
```

### Notebook walkthrough

| Step | What happens |
|---|---|
| **1: Install** | Installs `bedrock-agentcore`, `strands-agents`, and other dependencies |
| **2: Configure** | Creates a boto3 session and sets `REGION` |
| **3: Deploy agent** | Runs `deploy_hr_assistant_agent.py` via `%run -i`; uses `agentcore` CLI to build, push, and create the runtime |
| **4: Invoke agent** | Runs 5 sessions (single- and multi-turn), waits 60s for CloudWatch ingestion |
| **Custom evaluators** | Creates `HRResponseSimilarity` (TRACE) and `HRAssertionChecker` (SESSION) custom evaluators |
| **5: EvaluationClient** | Evaluates each session by session ID using built-in and custom evaluators |
| **6: OnDemandEvaluationDatasetRunner** | Defines a 5-scenario dataset, invokes the agent per scenario, waits 180s, evaluates all scenarios |
| **7: BatchEvaluationRunner** | Runs the same dataset through `BatchEvaluationRunner` for aggregate scores per evaluator |
| **Cleanup** | (Commented out) Deletes the agent runtime |

### Using `EvaluationClient` directly

```python
from bedrock_agentcore.evaluation import EvaluationClient, ReferenceInputs
from datetime import timedelta

ec = EvaluationClient(region_name="us-east-1")

results = ec.run(
    evaluator_ids=["Builtin.Correctness", "Builtin.GoalSuccessRate", MY_CUSTOM_EVAL_ID],
    session_id="<session-id>",
    agent_id="<agent-id>",
    look_back_time=timedelta(hours=2),
    reference_inputs=ReferenceInputs(
        expected_response="Employee EMP-001 has 10 remaining PTO days.",
        assertions=["Agent called get_pto_balance", "Agent reported 10 remaining days"],
        expected_trajectory=["get_pto_balance"],
    ),
)
```

### Using `OnDemandEvaluationDatasetRunner` directly

```python
from bedrock_agentcore.evaluation import (
    Dataset, PredefinedScenario, Turn,
    EvaluationRunConfig, EvaluatorConfig,
    OnDemandEvaluationDatasetRunner,
    CloudWatchAgentSpanCollector,
)

dataset = Dataset(scenarios=[
    PredefinedScenario(
        scenario_id="pto-check",
        turns=[Turn(
            input="What is the PTO balance for EMP-001?",
            expected_response="EMP-001 has 10 remaining PTO days.",
        )],
        expected_trajectory=["get_pto_balance"],
        assertions=["Agent reported 10 remaining PTO days"],
    ),
])

runner = OnDemandEvaluationDatasetRunner(region="us-east-1")
result = runner.run(
    config=EvaluationRunConfig(
        evaluator_config=EvaluatorConfig(evaluator_ids=["Builtin.Correctness"]),
        evaluation_delay_seconds=180,
    ),
    dataset=dataset,
    agent_invoker=my_invoker_fn,
    span_collector=CloudWatchAgentSpanCollector(log_group_name=CW_LOG_GROUP, region="us-east-1"),
)
```

### Using `BatchEvaluationRunner` directly

```python
from bedrock_agentcore.evaluation import (
    BatchEvaluationRunner,
    BatchEvaluationRunConfig,
    BatchEvaluatorConfig,
    CloudWatchDataSourceConfig,
)

config = BatchEvaluationRunConfig(
    batch_evaluation_name="my_batch_eval",
    evaluator_config=BatchEvaluatorConfig(
        evaluator_ids=["Builtin.Correctness", "Builtin.GoalSuccessRate"],
    ),
    data_source=CloudWatchDataSourceConfig(
        service_names=[SERVICE_NAME],
        log_group_names=[LOG_GROUP],
        ingestion_delay_seconds=180,
    ),
    polling_timeout_seconds=1800,
    polling_interval_seconds=30,
)

runner = BatchEvaluationRunner(region="us-east-1")
result = runner.run_dataset_evaluation(
    config=config,
    dataset=dataset,
    agent_invoker=my_invoker_fn,
)

print(f"Status: {result.status}")
for summary in result.evaluation_results.evaluator_summaries:
    print(f"  {summary.evaluator_id}: avg={summary.statistics.average_score}")
```

---

## Simulated Multi-Turn Evaluation

The evaluation techniques above use predefined scenarios with scripted user inputs.
Another option of constructing the dataset with lesser manual effort is simulating
the user and constructing a simulated dataset. User simulation uses an LLM-backed
actor to play the role of an end user interacting with your agent. You define the
actor's profile and goal, and the actor drives a multi-turn conversation with your
agent until the goal is met or the turn limit is reached. To learn more and test
this, see the companion notebook:

**[`Strands-AgentCore-ShoppingConcierge.ipynb`](../03-advanced/02-simulating-agent-interactions/Strands-AgentCore-ShoppingConcierge.ipynb)**

That notebook deploys a Shopping Concierge agent, runs five simulated customer
scenarios (headphones purchase, order tracking, returns, multi-item cart, budget
fitness), and scores all sessions in a single `StartBatchEvaluation` call using
`boto3` and the Bedrock Converse API for the actor.

---

## Files

| File | Description |
|---|---|
| `groundtruth_evaluations.ipynb` | Main tutorial notebook |
| `hr_assistant_agent.py` | HR Assistant agent source (Strands agent with 5 tools) |
| `deploy_hr_assistant_agent.py` | Deploy script using `agentcore` CLI |
| `requirements.txt` | Python dependencies |
| `.gitignore` | Ignores generated `.bedrock_agentcore.yaml` |

---

## Custom Evaluators with Ground Truth

Custom evaluators let you define evaluation criteria in natural language. The service
substitutes **ground-truth placeholders** from `ReferenceInputs` before scoring.

### Placeholder reference

| Level | Placeholder | Populated from |
|---|---|---|
| TRACE | `{assistant_turn}` | Agent's actual response for that turn |
| TRACE | `{expected_response}` | `ReferenceInputs.expected_response` |
| TRACE | `{context}` | Conversation context preceding the turn |
| SESSION | `{actual_tool_trajectory}` | Tools the agent called during the session |
| SESSION | `{expected_tool_trajectory}` | `ReferenceInputs.expected_trajectory` |
| SESSION | `{assertions}` | `ReferenceInputs.assertions` |
| SESSION | `{available_tools}` | Tools available to the agent |

### Custom evaluators in this tutorial

| Evaluator | Level | Placeholders used | Where used |
|---|---|---|---|
| `HRResponseSimilarity` | TRACE | `{assistant_turn}`, `{expected_response}` | EvaluationClient (Step 5), DatasetRunner (Step 6) |
| `HRAssertionChecker` | SESSION | `{actual_tool_trajectory}`, `{expected_tool_trajectory}`, `{assertions}` | EvaluationClient (Step 5, multi-turn), DatasetRunner (Step 6) |

---

## Built-in Evaluators

| Evaluator | Level | Ground truth required |
|---|---|---|
| `Builtin.Correctness` | TRACE | `expected_response` |
| `Builtin.Helpfulness` | TRACE | None |
| `Builtin.ResponseRelevance` | TRACE | None |
| `Builtin.GoalSuccessRate` | SESSION | `assertions` |
| `Builtin.TrajectoryExactOrderMatch` | SESSION | `expected_trajectory` |
| `Builtin.TrajectoryInOrderMatch` | SESSION | `expected_trajectory` |
| `Builtin.TrajectoryAnyOrderMatch` | SESSION | `expected_trajectory` |

**Evaluation levels:**
- **TRACE**: one result per conversational turn (agent response)
- **SESSION**: one result per complete conversation

---

## Clean Up

Uncomment and run the cleanup cell in the notebook, or use the AWS CLI:

```bash
# Delete the agent runtime
aws bedrock-agentcore-control delete-agent-runtime \
    --agent-runtime-id <AGENT_ID> \
    --region <REGION>
```

---

## Additional Resources

- [Ground-truth evaluations](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/ground-truth-evaluations.html)
- [Dataset-based evaluations](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/dataset-evaluations.html)
- [Batch evaluation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/batch-evaluations.html)
- [User simulation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/user-simulation.html)
- [Amazon Bedrock AgentCore Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/)
- [Strands Agents SDK](https://strandsagents.com/)
