# LLM-as-a-Judge evaluation

Evaluate your Amazon Bedrock AgentCore agent using LLM-based scoring. This sample shows how to author custom domain-specific evaluators in natural language and run them alongside built-in evaluators in two modes: on-demand spot-checks and continuous online monitoring.

## What You'll Learn

| Concept | Description |
|---|---|
| **Custom LLM-as-a-judge evaluators** | Define quality criteria in natural language; the service substitutes ground-truth placeholders at evaluation time |
| **TRACE-level evaluation** | Score each individual agent response against an expected answer |
| **SESSION-level evaluation** | Assess the whole conversation — tools called, assertions satisfied, goal reached |
| **On-demand evaluation** | Evaluate a specific recorded session using `EvaluationClient` |
| **Online evaluation** | Create a config that continuously scores live traffic without per-session API calls |

## When to Use Each Evaluator Type

### Built-in Evaluators

Built-in evaluators are pre-configured LLM-as-a-judge evaluators with carefully crafted prompt templates, selected evaluator models, and standardized scoring criteria.

**When to use:**
- You need to implement quality evaluations quickly
- You want standardized assessment metrics across teams or projects
- Your evaluation needs align with common quality dimensions
- You prioritize consistency and reliability over customization

**All 13 built-in evaluators:**

| Evaluator | Level | Ground Truth | Description |
|---|---|---|---|
| `Builtin.Correctness` | TRACE | `expected_response` | Evaluates whether the information is factually accurate |
| `Builtin.Faithfulness` | TRACE | None | Evaluates whether information is supported by provided context/sources |
| `Builtin.Helpfulness` | TRACE | None | Evaluates how useful and valuable the agent's response is |
| `Builtin.ResponseRelevance` | TRACE | None | Evaluates whether the response addresses the user's query |
| `Builtin.Conciseness` | TRACE | None | Evaluates whether the response is appropriately brief |
| `Builtin.Coherence` | TRACE | None | Evaluates whether the response is logically structured |
| `Builtin.InstructionFollowing` | TRACE | None | Measures how well the agent follows system instructions |
| `Builtin.Refusal` | TRACE | None | Detects when agent evades questions or refuses to answer |
| `Builtin.GoalSuccessRate` | SESSION | `assertions` | Evaluates whether the conversation meets user goals |
| `Builtin.ToolSelectionAccuracy` | SESSION | None | Evaluates whether the agent selected the appropriate tool |
| `Builtin.ToolParameterAccuracy` | SESSION | None | Evaluates how accurately parameters are extracted |
| `Builtin.Harmfulness` | TRACE | None | Evaluates whether the response contains harmful content |
| `Builtin.Stereotyping` | TRACE | None | Detects generalizations about individuals or groups |

> **Note:** Built-in evaluator configurations cannot be modified to maintain evaluation consistency across all users. You can create your own custom evaluator using a built-in one as a base.

### Custom LLM-as-a-Judge Evaluators

Custom evaluators provide maximum flexibility — you define every aspect of your evaluation process while leveraging LLMs as underlying judges.

**Customization options:**
- **Evaluator model**: Choose the LLM that best fits your evaluation needs
- **evaluation prompts**: Craft evaluation instructions specific to your use case
- **Scoring schema**: Design scoring systems that align with your organization's metrics
- **Ground-truth placeholders**: Reference expected responses, trajectories, and assertions

**When to use custom evaluators:**
- You're evaluating domain-specific agents (healthcare, finance, legal)
- You have unique quality standards or compliance requirements
- You need specialized scoring systems aligned with organizational KPIs
- Built-in evaluators don't capture your specific evaluation dimensions

### evaluation Modes

| Mode | Description | Best for |
|---|---|---|
| **On-demand** | Evaluate specific recorded sessions synchronously | Debugging, CI/CD, regression testing |
| **Online** | Continuously evaluate live traffic automatically | Production monitoring, trend tracking |

> **Why only built-in evaluators for online?** Custom LLM-as-a-judge evaluators that use reference input placeholders (`{expected_response}`, `{assertions}`) require ground truth, which is not available for live production traffic. Online evaluation uses only evaluators that can score without ground truth.

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

# Run all evaluation steps
python evaluate.py
```

Optional flags:

```bash
python evaluate.py --region us-west-2
python evaluate.py --config /path/to/custom/agent_config.json
```

## What the Script Does

### Step 1 — Create Custom LLM-as-a-Judge Evaluators

Two domain-specific evaluators are created via the AgentCore control plane:

**`HRResponseQuality` (TRACE level)**
Scores each individual agent response for accuracy, completeness, and professional tone. Uses the `{assistant_turn}` and `{expected_response}` placeholders.

```
Rating scale:
  0.0 — poor:       inaccurate, incomplete, or unprofessional
  0.5 — acceptable: mostly correct but missing detail
  1.0 — excellent:  accurate, complete, and well-written
```

**`HRSessionCompleteness` (SESSION level)**
Checks whether the agent called all expected tools and satisfied all assertions across the entire conversation. Uses `{actual_tool_trajectory}`, `{expected_tool_trajectory}`, and `{assertions}`.

```
Rating scale:
  0.0 — incomplete: required tools not called or request unresolved
  0.5 — partial:    some tools missing or assertions unmet
  1.0 — complete:   all expected tools called, all assertions satisfied
```

### Step 2 — Invoke the HR Assistant

Three turns are sent to the HR Assistant agent to generate a CloudWatch session:

1. PTO balance lookup for EMP-001
2. PTO request submission (July 14–18, 2026)
3. Remote work policy inquiry

Each turn has a corresponding `expected_response` and the session has `expected_trajectory` and `assertions` for ground-truth evaluation.

### Step 3 — On-Demand evaluation (`EvaluationClient`)

Six evaluators run against the recorded session:

| Evaluator | Level | Ground Truth Used |
|---|---|---|
| `Builtin.GoalSuccessRate` | SESSION | assertions |
| `Builtin.Correctness` | TRACE | expected_response |
| `Builtin.Helpfulness` | TRACE | none |
| `HRResponseQuality` (custom) | TRACE | expected_response |
| `HRSessionCompleteness` (custom) | SESSION | expected_trajectory + assertions |

> **Note:** SPAN-level evaluators (`ToolParameterAccuracy`, `ToolSelectionAccuracy`) require direct span IDs and are not supported by `EvaluationClient.run()`. They are available via the low-level `evaluate()` API.

Results are saved to `results/on_demand_results.json`.

### Step 4 — Online evaluation Configuration

A persistent evaluation config is created and immediately enabled. It watches the agent's CloudWatch log group and automatically scores every new session.

```
Sampling rate : 100%  (lower this for high-traffic production agents)
Evaluators    : GoalSuccessRate, Correctness, Helpfulness (built-in only)
Results       : /aws/bedrock-agentcore/evaluations/results/<config-id>
```

> **Why only built-in evaluators for online?** Custom LLM-as-a-judge evaluators that use reference input placeholders (`{expected_response}`, `{assertions}`) require ground truth, which is not available for live production traffic. Online evaluation uses only evaluators that can score without ground truth.

Configuration details are saved to `results/online_eval_config.json`.

## Ground-Truth Placeholders

| Placeholder | Level | Source |
|---|---|---|
| `{assistant_turn}` | TRACE | agent's actual response text |
| `{expected_response}` | TRACE | `ReferenceInputs.turns[i].expectedResponse` |
| `{context}` | TRACE | conversation history before this turn |
| `{actual_tool_trajectory}` | SESSION | tools the agent actually called |
| `{expected_tool_trajectory}` | SESSION | `ReferenceInputs.expectedTrajectory` |
| `{assertions}` | SESSION | `ReferenceInputs.assertions` |
| `{available_tools}` | SESSION | tools declared in the agent definition |

## Expected Output

```
[1/4] Creating custom LLM-as-a-judge evaluators ...
  Creating HRResponseQuality (TRACE) ...
    evaluatorId: HRResponseQuality_<suffix>-XXXXXXXXXX
  Creating HRSessionCompleteness (SESSION) ...
    evaluatorId: HRSessionCompleteness_<suffix>-XXXXXXXXXX

[2/4] Invoking HR Assistant to generate a session ...
  Turn 1: What is the PTO balance for employee EMP-001?
         -> Employee EMP-001 has 10 remaining PTO days ...
  Turn 2: Please submit a PTO request for EMP-001 ...
         -> PTO request submitted ...
  Turn 3: What is the company remote work policy?
         -> The company allows up to 3 days of remote work ...

[3/4] Running on-demand evaluation (EvaluationClient) ...
  Evaluator                                     Value    Label
  -----------------------------------------------------------------------
  Builtin.GoalSuccessRate                       1.0      success
  Builtin.Correctness                           0.9      correct
  Builtin.ToolParameterAccuracy                 1.0      correct
  Builtin.ToolSelectionAccuracy                 1.0      correct
  HRResponseQuality                             1.0      excellent
  HRSessionCompleteness                         1.0      complete

[4/4] Creating online evaluation configuration ...
  Online evaluation config created:
    ID  : hr_llm_judge_eval_<suffix>-XXXXXXXXXX
```

## Managing the Online evaluation Config

After the script runs, the online config continues scoring new sessions until you disable it:

```bash
# Disable the online evaluation config
aws bedrock-agentcore-control update-online-evaluation-config \
    --online-evaluation-config-id <config-id-from-results/online_eval_config.json> \
    --enable-config false

# Delete when no longer needed
aws bedrock-agentcore-control delete-online-evaluation-config \
    --online-evaluation-config-id <config-id>
```

## Results Files

| File | Contents |
|---|---|
| `results/on_demand_results.json` | Per-turn and per-session scores from EvaluationClient |
| `results/online_eval_config.json` | Config ID, ARN, custom evaluator IDs, triggered session ID |

---

## Additional Resources

- [Ground-truth evaluations — custom evaluators](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/ground-truth-evaluations.html#gt-custom-evaluators)
- [Amazon Bedrock AgentCore Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/)
- [Strands Agents SDK](https://strandsagents.com/)
- [Build reliable AI agents with Amazon Bedrock AgentCore evaluations](https://aws.amazon.com/blogs/machine-learning/build-reliable-ai-agents-with-amazon-bedrock-agentcore-evaluations/)

## Next Steps

- Run DeepEval / AutoEval metrics as managed or custom-derived evaluators — see [`3p-evals/`](3p-evals/)
- Add trajectory evaluators (`Builtin.TrajectoryExactOrderMatch`, `TrajectoryInOrderMatch`, `TrajectoryAnyOrderMatch`) using `expected_trajectory` in `ReferenceInputs`
- Explore [`ground-truth-based-evaluation/`](../ground-truth-based-evaluation/) for `EvaluationClient`, `DatasetRunner`, and `BatchRunner` patterns
- Explore [`custom-code-based-evaluation/`](../custom-code-based-evaluation/) for deterministic Lambda evaluators
