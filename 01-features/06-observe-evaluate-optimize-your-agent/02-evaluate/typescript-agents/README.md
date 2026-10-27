# TypeScript HR Assistant — AgentCore Runtime + Evaluations

A human-resources assistant built with [LangGraph](https://langchain-ai.github.io/langgraphjs/)
(TypeScript) and deployed on Amazon Bedrock AgentCore Runtime. The sample shows that
AgentCore Evaluations works the same way for TypeScript agents as for Python agents:
the evaluators score OpenTelemetry spans in CloudWatch, so the agent can be written in
any language while the evaluation workflow uses the standard Python SDK.

It covers the full evaluation lifecycle:

| Stage | API | What it answers |
|:------|:----|:----------------|
| 1. On-demand | `EvaluationClient` | "How did this one session go?" — spot-check while developing |
| 2. Dataset | `OnDemandEvaluationDatasetRunner` | "Does the agent still pass my ground-truth test set?" — regression testing / CI |
| 3. Batch | `StartBatchEvaluation` | "How are all recorded sessions scoring?" — service-side scoring of the log group |
| 4. Online | `CreateOnlineEvaluationConfig` | "How is production traffic scoring right now?" — continuous monitoring |

Highlights:

- **LangGraph TypeScript agent on AgentCore Runtime** — `createReactAgent` backed by
  Amazon Bedrock Nova Lite via `ChatBedrockConverse` (`@langchain/aws`)
- **Custom CloudWatch Logs span exporter** — the ADOT sidecar is not injected for
  custom Docker container deployments, so the agent ships a `CloudWatchLogsSpanExporter`
  that writes flat-format OTel span documents directly to the runtime log group
- **OpenInference instrumentation** — `@arizeai/openinference-instrumentation-langchain`
  auto-instruments LangGraph tool calls and LLM invocations
- **Built-in and custom evaluators** — `Builtin.GoalSuccessRate`, `Builtin.Correctness`,
  `Builtin.Helpfulness`, plus two custom LLM-as-a-judge evaluators:
  - **HRPolicyAccuracy** — checks each answer against its expected HR facts (needs ground truth)
  - **HRResponseQuality** — rates helpfulness and specificity per turn (reference-free)

---

## Architecture

```
User request
     │
     ▼
AgentCore Runtime  ──────────────────────────────────────────────────┐
│  hr-assistant/src/agent.ts                                          │
│    ├── LangGraph ReAct agent (createReactAgent)                     │
│    │     └── ChatBedrockConverse (Nova Lite)                        │
│    │           ├── get_pto_balance                                  │
│    │           ├── submit_pto_request                               │
│    │           ├── lookup_hr_policy                                 │
│    │           ├── get_benefits_summary                             │
│    │           └── get_pay_stub                                     │
│    └── CloudWatchLogsSpanExporter                                   │
│          └── @arizeai/openinference-instrumentation-langchain       │
└─────────────────────────────────────────────────────────────────────┘
     │                           │
     ▼                           ▼
  Response                CloudWatch Logs
                          (flat OTel span docs)
                               │
                               ▼
                     AgentCore Evaluations
                       ├── 1. On-demand  (EvaluationClient)
                       ├── 2. Dataset    (OnDemandEvaluationDatasetRunner)
                       ├── 3. Batch      (StartBatchEvaluation)
                       └── 4. Online     (online evaluation config)
```

> **Note — Docker containers and ADOT:** The ADOT collector sidecar is only
> auto-injected for `agentcore-cli` / code-zip deployments. Custom Docker
> containers must export spans themselves. This sample implements
> `CloudWatchLogsSpanExporter` in `agent.ts`, which writes span documents in the
> compact flat format expected by `CloudWatchAgentSpanCollector`.
>
> Each span document also carries an ADOT-style `resource.attributes` block
> (`service.name = <runtime-name>.DEFAULT`, `cloud.resource_id`, and so on).
> Batch and online evaluation use `service.name` to discover a runtime's
> sessions — without it they find zero sessions. The log group name, service
> name, and runtime ARN are only known after the runtime is created, so
> `deploy.py` injects `OTEL_LOG_GROUP_NAME`, `OTEL_SERVICE_NAME`, and
> `AGENT_RUNTIME_ARN` with `update_agent_runtime`.

---

## Files

| File / Directory | Description |
|-----------------|-------------|
| `hr-assistant/src/agent.ts` | TypeScript LangGraph agent (deployed to runtime) |
| `hr-assistant/Dockerfile` | Multi-stage Docker build for `linux/arm64` |
| `hr-assistant/package.json` | Node.js dependencies |
| `hr-assistant/tsconfig.json` | TypeScript compiler settings |
| `deploy.py` | Creates IAM role, ECR repo, builds/pushes image, creates runtime, injects span-export env vars; saves `agent_config.json` |
| `evaluate.py` | Creates custom evaluators and runs the four evaluation stages |
| `cleanup.py` | Deletes all AWS resources created by this sample |
| `requirements.txt` | Local Python dependencies for the scripts |

---

## Quick start

### Prerequisites

- Docker (with `buildx` and `linux/arm64` support; Colima or Docker Desktop)
- Python 3.10+
- AWS credentials with permissions for ECR, IAM, Bedrock, CloudWatch Logs, and AgentCore
- Access to `us.amazon.nova-lite-v1:0` (agent) and `us.amazon.nova-pro-v1:0` (custom judges)

### 1. Install local Python dependencies

```bash
pip install -r requirements.txt
```

### 2. Deploy

```bash
python deploy.py [--region us-east-1]
```

This:
1. Creates an IAM execution role for the runtime
2. Creates an ECR repository and pushes the Docker image (`linux/arm64`)
3. Creates the AgentCore Runtime (container deployment) and waits for `READY`
4. Calls `update_agent_runtime` to inject `OTEL_LOG_GROUP_NAME`, `OTEL_SERVICE_NAME`, and
   `AGENT_RUNTIME_ARN` (enables span export with the resource attributes evaluations need)
5. Waits for `READY` again, then writes `agent_config.json`

### 3. Evaluate

```bash
python evaluate.py [--region us-east-1] [--modes on-demand dataset batch online]
```

`evaluate.py` first creates the `HRPolicyAccuracy` and `HRResponseQuality` evaluators,
then runs the selected stages in lifecycle order (all four by default):

| Stage | What happens | Evaluators | Output |
|:------|:-------------|:-----------|:-------|
| `on-demand` | Invokes a 5-turn session (PTO, remote work, health, 401k, PTO request), waits 150s for span ingestion, then scores that session | GoalSuccessRate (with assertions), Correctness, Helpfulness, HRResponseQuality | `results/on_demand_results.json` |
| `dataset` | Runs a 4-scenario ground-truth dataset (one multi-turn); every turn has its own `expected_response` and each scenario has assertions | GoalSuccessRate, Correctness, HRPolicyAccuracy | `results/dataset_runner_results.json` |
| `batch` | Invokes a fresh 3-turn session, then starts a service-side batch job over the runtime log group, scoped to the session IDs from this run (`filterConfig.sessionIds`), and polls until it finishes | GoalSuccessRate, Helpfulness, HRResponseQuality (all reference-free) | `results/batch_eval_results.json` |
| `online` | Creates an IAM role and an online evaluation config that samples 100% of new sessions | GoalSuccessRate, Helpfulness, HRResponseQuality | `results/online_eval_config.json` |

To re-score a session that already exists without invoking the agent again:

```bash
python evaluate.py --modes on-demand --session-id <SESSION_ID>
```

> **Choosing where to put ground truth:** a plain-string `expected_response` in
> `ReferenceInputs` applies to the *last* trace of a session only. Evaluators that
> need an expected answer for every turn (such as `HRPolicyAccuracy`) belong in the
> dataset stage, where each `Turn` carries its own `expected_response`.
> `expected_trajectory` is not used: trajectory reference inputs are rejected for
> LangGraph/OpenInference spans.

### 4. Cleanup

```bash
python cleanup.py [--region us-east-1]
```

Deletes the batch evaluations, online evaluation config and its results log group,
custom evaluators, runtime and its log group, ECR repository, and IAM roles recorded in
`agent_config.json` and `results/cleanup_state.json`. The shared batch results log group
(`/aws/bedrock-agentcore/evaluations/batch-evaluations/results/default`) is kept.

---

## Evaluation results

Scores from a run against the deployed agent (LLM judges vary slightly between runs):

| Stage | Evaluator | Level | Score |
|-------|-----------|-------|-------|
| On-demand | Builtin.GoalSuccessRate | SESSION | 1.0 |
| On-demand | Builtin.Correctness | TRACE | 0.5–1.0 per turn (4/5 turns at 1.0) |
| On-demand | Builtin.Helpfulness | TRACE | 0.83 (5/5 turns) |
| On-demand | HRResponseQuality (custom) | TRACE | 1.0 (5/5 turns) |
| Dataset | Builtin.GoalSuccessRate | SESSION | 1.0 (4/4 scenarios) |
| Dataset | Builtin.Correctness | TRACE | 1.0 (5/5 turns) |
| Dataset | HRPolicyAccuracy (custom) | TRACE | 0.90 average (5 turns) |
| Batch | Builtin.GoalSuccessRate | SESSION | 1.0 (1/1 session) |
| Batch | Builtin.Helpfulness | TRACE | 0.83 |
| Batch | HRResponseQuality (custom) | TRACE | 1.0 |

Batch results are written to the
`/aws/bedrock-agentcore/evaluations/batch-evaluations/results/default` log group, and
online results to `/aws/bedrock-agentcore/evaluations/results/<config-id>`.

---

## AgentCore CLI usage

```bash
# Invoke a deployed runtime
agentcore invoke \
  --agent-runtime-id <AGENT_ID> \
  --payload '{"prompt": "What is the PTO balance for employee EMP-001?"}' \
  --region us-east-1
```
