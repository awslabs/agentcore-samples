# AgentCore evaluations

Amazon Bedrock AgentCore evaluations provides automated assessment tools to measure how well your agent or tools perform specific tasks, handle edge cases, and maintain consistency across different inputs and contexts. The service enables data-driven optimization and ensures your agents meet quality standards before and after deployment.

AgentCore evaluations integrates with popular agent frameworks including Strands and LangGraph with OpenTelemetry and OpenInference instrumentation libraries. Under the hood, traces from these agents are converted to a unified format and scored using LLM-as-a-Judge techniques for both built-in and custom evaluators.

## Overview

While AgentCore observability provides operational insights into agent health, AgentCore evaluations focuses on **agent decision quality and performance outcomes**. It provides built-in and custom evaluators with both on-demand and online evaluation capabilities.

![AgentCore Evaluation Architecture](images/evaluation_architecture_simple.svg)

![AgentCore evaluation Interfaces](images/agentcore_interfaces.png)

## Key Features

### Built-in and Custom Evaluators

AgentCore evaluations offers **15 built-in evaluators** for critical dimensions like correctness, helpfulness, safety, tool use, and skill use, plus the ability to create custom evaluators for business-specific requirements.

Test your agents during development and deployment using the on-demand evaluations API, or monitor production agents with the online evaluations API.

### On-demand evaluations

Run synchronous, on-demand evaluations using built-in and custom metrics on individual traces.

The system uses OpenTelemetry (OTEL) traces to perform scoring and returns a response that includes:

- Score value
- Explanation for the score
- Token usage

**When to use on-demand evaluations:**

- Investigating specific customer interactions or reported issues
- Validating fixes for identified problems
- Analyzing historical data for quality improvements
- Testing evaluators before deploying them in production
- Performing deep-dive analysis on edge cases

![On-demand evaluations](images/on_demand_evaluations.png)

### Online evaluations

In production, you need continuous performance monitoring across all interactions without manually evaluating each trace. A statistical sample is often sufficient for generating meaningful performance metrics.

AgentCore evaluations' online capabilities enable automatic sampling and evaluation:

- Define your sample size and trace selection criteria
- Choose your evaluation metrics (built-in or custom)
- AgentCore evaluations handles the rest, generating the performance data you need to monitor your agent at scale

**When to use online evaluations:**

- Monitoring production agent performance continuously
- Catching quality regressions before they impact users
- Identifying patterns in user interactions at scale
- Maintaining consistent agent performance over time
- A/B testing different agent configurations

![Online evaluations](images/online_evaluations.png)

![Online evaluations Dashboard](images/online_evaluations_dashboard.png)

## AgentCore observability Integration

Both evaluation types rely on **AgentCore observability** to capture agent behavior through OpenTelemetry (OTEL) traces.

![observability Traces](images/observability_traces.png)

AgentCore relies on **AWS Distro for OpenTelemetry (ADOT)** to instrument different types of OTEL traces across various agent frameworks:

**For AgentCore runtime-hosted agents:**

- Instrumentation is automatic with minimal configuration
- Simply include `aws-opentelemetry-distro` in your `requirements.txt`
- AgentCore runtime handles OTEL configuration automatically
- Traces appear in CloudWatch GenAI observability Dashboard

## Built-in Evaluators

| Evaluator                       | Level   | Needs Ground Truth  | Description                                                                            |
| :------------------------------ | :------ | :------------------ | :------------------------------------------------------------------------------------- |
| `Builtin.Correctness`           | TRACE   | `expected_response` | Evaluates whether the information in the agent's response is factually accurate        |
| `Builtin.Faithfulness`          | TRACE   | None                | Evaluates whether information in the response is supported by provided context/sources |
| `Builtin.Helpfulness`           | TRACE   | None                | Evaluates from user's perspective how useful and valuable the agent's response is      |
| `Builtin.ResponseRelevance`     | TRACE   | None                | Evaluates whether the response appropriately addresses the user's query                |
| `Builtin.Conciseness`           | TRACE   | None                | Evaluates whether the response is appropriately brief without missing key information  |
| `Builtin.Coherence`             | TRACE   | None                | Evaluates whether the response is logically structured and coherent                    |
| `Builtin.InstructionFollowing`  | TRACE   | None                | Measures how well the agent follows the provided system instructions                   |
| `Builtin.Refusal`               | TRACE   | None                | Detects when agent evades questions or directly refuses to answer                      |
| `Builtin.GoalSuccessRate`       | SESSION | `assertions`        | Evaluates whether the conversation successfully meets the user's goals                 |
| `Builtin.ToolSelectionAccuracy` | SESSION | None                | Evaluates whether the agent selected the appropriate tool for the task                 |
| `Builtin.ToolParameterAccuracy` | SESSION   | None                | Evaluates how accurately the agent extracts parameters from user queries               |
| `Builtin.SkillSelectionAccuracy` | TOOL_CALL | None                | Evaluates whether the agent loaded the appropriate skill for the task                  |
| `Builtin.SkillInstructionFollowing` | TOOL_CALL | None              | Evaluates how completely the agent followed the loaded skill's instructions            |
| `Builtin.Harmfulness`           | TRACE      | None                | Evaluates whether the response contains harmful content                                |
| `Builtin.Stereotyping`          | TRACE   | None                | Detects content that makes generalizations about individuals or groups                 |

**TRACE** evaluators produce one score per conversational turn.

**SESSION** evaluators produce one score per complete conversation.

**TOOL_CALL** skill evaluators produce one score per detected skill invocation.

![Metrics Per Level](images/metrics_per_level.png)

## evaluation Interfaces

Three evaluation interfaces are available depending on your use case:

| Interface                         | Best For                                                        | How it runs                                      |
| :-------------------------------- | :-------------------------------------------------------------- | :----------------------------------------------- |
| `EvaluationClient`                | Ad-hoc debugging, CI spot-checks on known sessions              | Client-side, synchronous per session             |
| `OnDemandEvaluationDatasetRunner` | Regression testing, CI/CD pipelines with a dataset              | Client-side, runs agent + evaluates per scenario |
| `BatchEvaluationRunner`           | Baseline snapshots, large-scale evaluation, pre/post comparison | Service-side, aggregate scores per evaluator     |

## evaluation Samples

| Sample                                                             | What it demonstrates                                                                                                                                                |
| :----------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`ground-truth-based-evaluation/`](ground-truth-based-evaluation/) | EvaluationClient + DatasetRunner + BatchRunner with expected responses, expected tool trajectories, and session assertions                                          |
| [`llm-as-a-judge-evaluation/`](llm-as-a-judge-evaluation/)         | Custom LLM-as-a-judge evaluators (TRACE + SESSION) with ground-truth placeholders alongside built-in evaluators                                                     |
| [`custom-code-based-evaluation/`](custom-code-based-evaluation/)   | Lambda-backed deterministic evaluators (code-based) for exact data validation, mixed with built-in LLM evaluators; on-demand and online modes                       |
| [`skills-evaluation/`](skills-evaluation/)                         | Native Strands Agent Skills evaluated with `Builtin.SkillSelectionAccuracy` and `Builtin.SkillInstructionFollowing`                                               |
| [`supported-frameworks/`](supported-frameworks/)                   | The same HR Assistant re-implemented in other supported frameworks (OpenAI Agents SDK, LlamaIndex), each deployed and evaluated with built-in and custom evaluators |

The `ground-truth-based-evaluation/`, `llm-as-a-judge-evaluation/`, and `custom-code-based-evaluation/` samples share the default HR Assistant agent deployed from `utils/`. The `skills-evaluation/` sample uses the same agent source with an opt-in skills directory and a separate runtime config. The `supported-frameworks/` samples re-implement that agent in each framework and deploy it from their own folders.

## Agent Architecture

![Agent Architecture](images/hr_agent_arch.png)

## AgentCore CLI

The AgentCore CLI supports evaluation workflows. Install it:

```bash
npm install -g @aws/agentcore
```

### On-demand evaluation — single session

```bash
agentcore run eval \
  --runtime HRAssistant \
  --evaluator Builtin.GoalSuccessRate \
  --session-id <session-id>
```

### Batch evaluation — aggregate scores across sessions

```bash
agentcore run batch-evaluation \
  --runtime HRAssistant \
  --evaluator Builtin.GoalSuccessRate Builtin.Helpfulness Builtin.Correctness \
              Builtin.ToolSelectionAccuracy
```

### Create a custom LLM-as-a-judge evaluator

```bash
# TRACE-level evaluator (one score per turn)
agentcore add evaluator \
  --name HRResponseAccuracy \
  --level TRACE \
  --type llm-as-a-judge \
  --instructions "Evaluate whether the HR assistant's response is factually accurate about company policies."

# SESSION-level evaluator (one score per conversation)
agentcore add evaluator \
  --name HRResolutionRate \
  --level SESSION \
  --type llm-as-a-judge \
  --instructions "Score whether the HR assistant fully resolved the employee's request by end of conversation."
```

### Create a code-based evaluator (Lambda-backed)

```bash
agentcore add evaluator \
  --name HRResponseLength \
  --level TRACE \
  --type code-based \
  --lambda-arn arn:aws:lambda:<region>:<account>:function:hr-response-length
```

### Enable online evaluation (automatic sampling in production)

```bash
agentcore add online-eval \
  --name HRProductionEval \
  --runtime HRAssistant \
  --evaluator Builtin.GoalSuccessRate Builtin.Helpfulness \
  --sampling-rate 100 \
  --enable-on-create
```

Then deploy to provision all configured resources:

```bash
agentcore deploy
```

> **Note:** Custom code-based evaluators and advanced ground-truth patterns (expected tool
> trajectories, session assertions) require using the Python SDK directly — see the samples below.

## Prerequisites

- Python 3.10+
- AWS CLI installed and configured with credentials
- Permissions for: `bedrock-agentcore:*`, `bedrock-agentcore-control:*`, `logs:*`, `iam:CreateRole`, `iam:PutRolePolicy`, `iam:PassRole`, `s3:CreateBucket`, `s3:PutObject`, `bedrock:InvokeModel`

## Running the Python Scripts

```bash
# Deploy the shared HR Assistant agent (runs once for all samples)
cd utils
pip install -r requirements.txt
python deploy.py
```

```bash
# Ground truth evaluation (EvaluationClient + DatasetRunner + BatchRunner)
cd ground-truth-based-evaluation
pip install -r requirements.txt
python evaluate.py

# LLM-as-a-judge evaluation (custom LLM evaluators)
cd llm-as-a-judge-evaluation
pip install -r requirements.txt
python evaluate.py

# Code-based evaluation (Lambda evaluators)
cd custom-code-based-evaluation
pip install -r requirements.txt
python evaluate.py

# Skill selection and instruction-following evaluation (from 02-evaluate/)
cd skills-evaluation
pip install -r requirements.txt
python ../utils/deploy.py --skills-dir skills --config-output agent_config.json
python evaluate.py
```
