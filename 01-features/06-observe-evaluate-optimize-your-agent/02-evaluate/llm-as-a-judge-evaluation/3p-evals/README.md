# Third-party evaluators

AgentCore Evaluations can run metrics from the open-source **DeepEval** and **AutoEval** libraries. This sample builds on the [parent LLM-as-a-judge sample](../README.md) and reuses the same HR Assistant agent.

> **Evaluator quality:** AgentCore built-in evaluators are tested and benchmarked. DeepEval and AutoEval are open-source, and AWS makes no claims about their quality.

## Two ways to use them

| Approach | `evaluatorType` | Who runs the model | You configure |
|---|---|---|---|
| **Managed** | `ThirdParty` | AWS (service capacity) | Just the ID |
| **Custom-derived** | `CustomDerived` | You (your account + credentials) | The Bedrock model + inference config |

- **Managed** — reference an evaluator by ID (e.g. `ThirdParty.DeepEval.TaskCompletion`). There's no model field and no version to pick; the service runs the library version it has validated. A managed ID works wherever a `Builtin.*` ID does: on-demand, online, or batch.
- **Custom-derived** — reuse a base evaluator's prompt and scoring (a built-in or a managed 3p evaluator) but run it on a model you choose. The base owns the prompt and scale; you supply the model. LLM-based evaluators only.

## Evaluator identity

Every evaluator is one `(evaluatorType, provider)` pair:

| Evaluator | `evaluatorType` | `provider` |
|---|---|---|
| Managed built-in | `Builtin` | `AWS` |
| Managed third-party | `ThirdParty` | `DeepEval` or `AutoEval` |
| Derived from a built-in | `CustomDerived` | `AWS` |
| Derived from a third-party | `CustomDerived` | `DeepEval` or `AutoEval` |

Managed third-party IDs follow `ThirdParty.<Provider>.<Metric>` (for example `ThirdParty.DeepEval.TaskCompletion` or `ThirdParty.AutoEval.Security`), mirroring the `Builtin.<Metric>` format.

## Available metrics (initial set)

**DeepEval**

| Metric | What it checks |
|---|---|
| `Bias` | Gender, political, racial, or geographical bias |
| `Toxicity` | Attacks, mockery, hate, or threats |
| `PIILeakage` | Whether the response exposes personal information |
| `Summarization` | Whether a summary is faithful and comprehensive |
| `TaskCompletion` | Whether the agent accomplished the user's goal |
| `ConversationCompleteness` | Whether all requests across the conversation were addressed |
| `KnowledgeRetention` | Whether the agent remembered information shared earlier |
| `TurnRelevancy` | Whether each reply stays relevant to prior turns |
| `GoalAccuracy` | Whether goals were achieved across a multi-turn conversation |
| `ToolUse` | Whether the agent picked the right tool with correct arguments |

**AutoEval**

| Metric | What it checks |
|---|---|
| `Security` | Whether the response is malicious |
| `Humor` | Whether the response is funny |
| `Possible` | Whether the agent attempted a solution or declared the task impossible |

Step 1 of the script lists what's active in your account with `ListEvaluators`.

---

## Prerequisites

**1. Enable CloudWatch Transaction Search** (once per account/region). On-demand evaluation reads *span documents* from the `aws/spans` log group, which only exists once Transaction Search is on. Without it, `Evaluate` fails with `no span documents … ensure that transaction search is enabled`. Enable it in the CloudWatch console (Application Signals → Transaction Search), or via API:

```bash
# X-Ray needs a Logs resource policy to write span documents into aws/spans
aws logs put-resource-policy \
    --policy-name TransactionSearchAccess \
    --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"xray.amazonaws.com"},"Action":"logs:PutLogEvents","Resource":["arn:aws:logs:<region>:<account>:log-group:aws/spans:*","arn:aws:logs:<region>:<account>:log-group:/aws/application-signals/data:*"],"Condition":{"StringEquals":{"aws:SourceAccount":"<account>"},"ArnLike":{"aws:SourceArn":"arn:aws:xray:<region>:<account>:*"}}}]}'

aws xray update-trace-segment-destination --destination CloudWatchLogs
aws xray update-indexing-rule --name Default --rule '{"Probabilistic":{"DesiredSamplingPercentage":100.0}}'
```

Enabling takes a few minutes to go `ACTIVE`, and span indexing lags emission — right after enabling, allow extra time before evaluating a fresh session (this sample waits 180s).

**2. Deploy the shared HR Assistant agent** (runs once for all `02-evaluate/` samples):

```bash
cd ../../utils
python deploy.py
```

This writes `utils/agent_config.json`, which `evaluate.py` reads automatically.

## Run the evaluation

```bash
pip install -r requirements.txt
python evaluate.py
```

Optional flags:

```bash
python evaluate.py --region us-west-2
python evaluate.py --config /path/to/agent_config.json
```

## What the script does

### Step 1 — Discover third-party evaluators

Calls `ListEvaluators`, filters to `evaluatorType == "ThirdParty"`, and prints them grouped by provider. It also captures each evaluator's **level** (`TRACE` / `SESSION`) — needed later because the SDK cannot resolve the level of AWS-managed global evaluators via `GetEvaluator`.

### Step 2 — Create a custom evaluator derived from a 3p base

Creates a `CustomDerived` evaluator from `ThirdParty.DeepEval.TaskCompletion` that runs on a Bedrock model you choose:

```python
_cp.create_evaluator(
    evaluatorName="MyTaskCompletion_<suffix>",
    level="TRACE",  # must match the base evaluator's level
    evaluatorConfig={
        "derived": {
            "baseEvaluatorId": "ThirdParty.DeepEval.TaskCompletion",
            "modelConfig": {
                "bedrockEvaluatorModelConfig": {
                    "modelId": "us.amazon.nova-lite-v1:0",
                    "inferenceConfig": {"temperature": 0.0, "topP": 1.0, "maxTokens": 2048},
                }
            },
        }
    },
)
```

Don't set `instructions` or `ratingScale`: the base evaluator owns both, and `provider` comes from the base (`DeepEval` here). Note that although the docs describe `level` as derived from the base, the API validates it as required, so pass the base evaluator's level (the script reuses the level discovered in step 1).

> **Inference ownership:** a derived evaluator's model runs in your account with your credentials (the caller's for on-demand, the execution role for online), rather than on service capacity as a managed evaluator does. Since you pick the model, evaluation quality reflects that choice.

### Step 3 — Invoke the HR Assistant

Sends three turns to the deployed HR Assistant to generate a CloudWatch session, then waits for span ingestion.

### Step 4 — On-demand evaluation (`EvaluationClient`)

Managed 3p, derived, and built-in evaluators run in one call. All metrics chosen here are **reference-free**, so no ground truth is needed:

| Evaluator | Type | Level |
|---|---|---|
| `ThirdParty.DeepEval.TaskCompletion` | managed 3p | discovered |
| `ThirdParty.DeepEval.Toxicity` | managed 3p | discovered |
| `ThirdParty.AutoEval.Security` | managed 3p | discovered |
| `MyTaskCompletion` (derived) | `CustomDerived` | inherited from base |
| `Builtin.Helpfulness` | built-in | `TRACE` |

Results are saved to `results/on_demand_results.json`.

### Step 5 — Online evaluation with reference-free 3p metrics

Creates an online config using `ThirdParty.DeepEval.Toxicity` and `ThirdParty.AutoEval.Security` against live traffic.

> **Why these go online but the parent sample's custom evaluators can't:** the parent sample's evaluators use reference-input placeholders (`{expected_response}`, `{assertions}`) that need ground truth, which live traffic doesn't have, so they stay on-demand only. Many managed 3p metrics (Toxicity, Bias, PIILeakage, `AutoEval.Security`) need no reference input, so they can go on an online config.

Configuration details are saved to `results/online_eval_config.json`.

## Results files

| File | Contents |
|---|---|
| `results/discovered_evaluators.json` | Third-party evaluators returned by `ListEvaluators` |
| `results/on_demand_results.json` | Managed + derived + built-in scores for the session |
| `results/online_eval_config.json` | Online config ID, ARN, and evaluators |

## Managing the online evaluation config

```bash
# Disable
aws bedrock-agentcore-control update-online-evaluation-config \
    --online-evaluation-config-id <config-id-from-results/online_eval_config.json> \
    --enable-config false

# Delete when no longer needed
aws bedrock-agentcore-control delete-online-evaluation-config \
    --online-evaluation-config-id <config-id>
```

---

## Additional resources

- [Third-party evaluators — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/third-party-evaluators.html)
- [Parent sample: LLM-as-a-judge evaluation](../README.md)
- [Amazon Bedrock AgentCore Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/)
