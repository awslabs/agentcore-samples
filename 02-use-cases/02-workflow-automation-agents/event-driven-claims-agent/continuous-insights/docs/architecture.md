# Architecture: Continuous Insights for the Event-Driven Claims Agent

This sample uses Amazon Bedrock AgentCore Evaluations **Continuous Insights** on the Event-Driven Claims Agent, a public sample that you deploy from its own README. Continuous Insights groups the recurring failures of the agent into Agent Insights. The sample adds no infrastructure to the claims agent. It adds a few scripts that turn on Failure Analysis, read the Agent Insights the service produces, hand them to an AI coding assistant that fixes each cause where it lives, and check the result after the changes.

The scripts use the released AWS SDK: `bedrock-agentcore-control` for the Agent Insight Store and the online evaluation configuration, and `bedrock-agentcore` for Agent Insights. Check the current AgentCore documentation before you rely on a detail here.

## Problem

An agent in production fails in ways you do not see one session at a time. One failed session looks like noise. A hundred sessions that fail for the same reason are a defect. Reading traces by hand to find that pattern does not scale, and a log search finds only the failures you already know to look for.

The sample answers one question: can you go from "the agent is failing in production" to "the agent is changed and I can show the effect" without reading traces by hand?

## Design and why

Run the claims agent as it is. Turn on an online evaluation with the built-in `Builtin.FailureAnalysis` evaluator. Let Continuous Insights group the failures into Agent Insights. Export the Insights to files. Give the export to an AI coding assistant, such as Kiro or Claude Code, which fixes each cause where it lives: the wording of a system prompt or tool description, or a missing deterministic check in code. A person reviews the change. Redeploy once, send the same claims again, and compare the Insight counts with the ones you exported before the changes.

The choices that shape the sample:

| Choice | Why |
|---|---|
| **Tooling around an unmodified agent.** `scripts/register_agent.py` reads the deployed runtime and writes a local state file. It never changes the runtime. | The claims agent has its own deploy path. The sample works with the agent you already have and removes cleanly. |
| **A second online evaluation config next to the agent's own.** The claims agent ships its own config for quality metrics. This sample leaves it alone and adds `<name>_insights`. | Continuous Insights needs `Builtin.FailureAnalysis` as the only evaluator in its config. A separate config keeps the two jobs apart. |
| **One export, one fix path.** The coding assistant reads the `insights/<timestamp>/` folder and fixes each cause where it lives. A wording problem in a system prompt or tool description is fixed in that text. A missing check is fixed in code, with a unit test. | Prompts, tool descriptions and code are all files in the claims agent's repository. One reviewed diff covers all of them, and you redeploy once. |
| **The coding assistant does not deploy or call AWS.** [`AGENTS.md`](../AGENTS.md) tells it to read the exported files, edit the prompt, tool descriptions and code, add tests, and write a short note per Insight. It must not read `.env` or state files. A person reviews the diff against the business rules and redeploys. | The fix path has a review step before anything reaches a running agent. The coding assistant needs no credentials. |
| **"Fixed" means the Insight stops growing.** An Insight is a durable record. `scripts/verify_fix.py` checks that `affectedSessionCount` and `lastSeenAt` stop moving and that no new Insight appears. | You can show the effect of a change on the same traffic without waiting for an Insight to disappear. |
| **Span IDs resolved into tool names and exceptions.** Findings point at spans by SPAN_ID. `scripts/export_insights.py` looks each one up in CloudWatch Logs. | A bare ID tells a coding assistant nothing. A tool name, an exception and a code location do. |
| **Scripts, not infrastructure as code.** Each setup step is one API call you can read. | Each script calls the released SDK directly, so you can read what each step changes. For your own agents, move the online evaluation configuration and the role into your IaC. |

## Components and data flow

![Architecture: claimants send claims by email or upload to S3, or call the runtime directly. EventBridge and a trigger Lambda invoke the AgentCore Runtime, which runs a Claims Processor agent, a Validation Agent and a deterministic execution phase, with AgentCore Memory, Identity, Gateway and a Cedar policy engine, Amazon Cognito for authentication, and tool Lambdas that use SES, SNS and DynamoDB. The runtime sends spans to CloudWatch. An online evaluation runs Builtin.FailureAnalysis and publishes Agent Insights to the Agent Insight Store. Scripts on a developer machine create an AgentCore managed dataset of claim scenarios, send the claims from it, and export the Insights. An AI coding assistant reads the export and changes the system prompt, the tool descriptions and the code. A person reviews the diff, redeploys, and compares the next run with the first. Numbered circles match the steps of the walkthrough in the README.](architecture.png)


In the diagram, blue arrows are the observe-and-capture path and orange arrows are the fix-and-verify path. The numbered circles and the steps below use the step numbers of the [README walkthrough](../README.md#walkthrough):

| Phase | README steps | What happens |
|---|---|---|
| Observe | 1 to 4 | Deploy the claims agent, register it, turn on Continuous Insights, and send 100 claims. |
| Capture | 5 | Export the Insights to `insights/<timestamp>/`. This export is the baseline. |
| Fix | 6 | An AI coding assistant fixes each cause where it lives. A person reviews the diff. |
| Verify | 7 and 8 | Redeploy, send the same 100 claims, and compare with the baseline. |
| Housekeeping | 9 and 10 | Archive or restore Insights, and clean up. |

### The claims agent (not changed by this sample)

Claims arrive as files in S3. An EventBridge rule starts a trigger Lambda, which invokes the AgentCore Runtime. The runtime runs a Claims Processor agent and a Validation Agent that reviews the decision and scores its confidence. A deterministic execution phase then approves, escalates or rejects the claim by calling the AgentCore Gateway. The Gateway authenticates the agent with a Cognito machine-to-machine token, applies a Cedar policy, and forwards the call to Lambda tools that read and write DynamoDB and publish to SNS. AgentCore Memory holds context for repeat claimants. The claims agent's own [README](../../README.md) and [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) describe it in full.

### What this sample adds

| Component | What it does |
|---|---|
| `scripts/register_agent.py` | Finds the runtime by name, checks it is `READY`, and writes `agent_state_<name>.json` with the runtime ARN, the runtime log group and the application name. It only reads from your account. |
| `scripts/setup_insights.py` | Enables the Agent Insight Store if needed, confirms the application name in the runtime's logs, creates the evaluation execution role, and creates the online evaluation config. Each step is safe to re-run. |
| `scripts/create_dataset.py` | Creates an AgentCore managed dataset with the seven claim scenarios (two approvals, a claim over the Cedar limit, a differently formatted policy number, a vague claim, an unknown policy, a claim from an email that is not on the policy) and saves its ID in the state file. |
| `scripts/drive.py` | Reads the scenarios from the dataset and sends the claims straight to the runtime. Each claim uses the policy holder's email except the last scenario. Writes one line per session to `sessions_<name>.jsonl`. You run it again with the same settings after the fix. |
| `scripts/status.py` | Prints the Store state, the evaluation config state, how many sessions Failure Analysis has evaluated in the last 24 hours (with and without failures), and the active Insights. |
| `scripts/export_insights.py` | Lists and gets the Insights, resolves span IDs, and writes `insights/<timestamp>/`. |
| `scripts/verify_fix.py` | Compares current Insights with an exported baseline. |
| `scripts/outcomes.py` | Reads the runtime log for a time window and counts how the claims agent routed each claim: approved automatically, sent to human review, or rejected. It only reads from your account. |
| `scripts/cleanup.py` | Deletes the online evaluation config, the evaluation role, and the scenario dataset. |
| Online evaluation config `<name>_insights` | `Builtin.FailureAnalysis` as the only evaluator, 100% sampling, and a session idle timeout (2 minutes in this sample). Reads the runtime log group for the runtime's `service.name`. |
| Evaluation execution role `AgentCoreEvaluationRole-<name>` | The role the evaluation assumes. Only `bedrock-agentcore.amazonaws.com` can assume it, and only for evaluators and online evaluation configurations in your account and Region. It can query CloudWatch Logs to read spans, write results under `/aws/bedrock-agentcore/evaluations/*`, and invoke Bedrock models for the evaluator. |
| Agent Insight Store | One default store per account and Region. It must be `ACTIVE` and `ENABLED`. It holds the Agent Insights. |
| CloudWatch Logs | Holds the runtime's spans in two places: the `aws/spans` log group (written by CloudWatch Transaction Search) and the runtime log group. |

### Step by step

**Observe (steps 1 to 4).**

1. Deploy the claims agent from its own folder with `./deploy.sh us-east-1`.
2. `scripts/register_agent.py` records the runtime in a local state file.
3. `scripts/setup_insights.py` enables the Agent Insight Store, creates the evaluation execution role, and creates the online evaluation config with `Builtin.FailureAnalysis`. It first checks that the runtime log group contains the expected `service.name`, and exits with the names it found if not.
4. `scripts/create_dataset.py` stores the claim scenarios in a managed dataset. `scripts/drive.py --count 100 --concurrency 20` reads them and invokes the runtime with 100 claims. Each claim is one session. The runtime runs under OpenTelemetry instrumentation and emits spans to CloudWatch Logs. The span `service.name` is `<runtime name>.DEFAULT`, and Continuous Insights uses it as the **application name**. When a session has been idle for the configured timeout, the online evaluation reads its spans, assuming the evaluation execution role, and runs `Builtin.FailureAnalysis` on it. Continuous Insights matches each failure to an existing group or holds it as unmatched evidence. When enough failed sessions have accumulated (about 20), it creates Agent Insights in the Store, scoped to the application name. `scripts/status.py` shows the progress.

**Capture (step 5).**

5. `scripts/export_insights.py` calls `ListAgentInsights` and `GetAgentInsight` for the application name. It lists active Insights by default; `--archive-status ARCHIVED` exports archived ones. Each finding names a target such as a `SPAN_ID`. The script runs CloudWatch Logs Insights queries over `aws/spans` and the runtime log group (up to 100 IDs per query, `--lookback-days` back, default 7). It reads the tool name, the exception, and the last stack frame that is the agent's own code. It writes four kinds of file under `insights/<timestamp>/`: `FAILURES.md` (a brief per Insight: description, root causes, suggested fix, evidence, example sessions), `insight-<id>.json` (the full API response), `spans.json` (the resolved spans), and `summary.json` (Insight ID to name, `affectedSessionCount`, `lastSeenAt`). This folder is the baseline for step 8.

**Fix (step 6).**

6. An AI coding assistant, such as Kiro or Claude Code, reads [`AGENTS.md`](../AGENTS.md) and the export. It fixes each cause where it lives. A cause in the text the model reads is fixed in the system prompt or the tool description. A cause that needs a deterministic check is fixed in code. It adds unit tests for the code changes and writes a short note per Insight. A person reviews the diff against the business rules before anything is deployed.

**Verify (steps 7 and 8).**

7. Redeploy the claims agent with its own deploy script, run its smoke test, and send the same 100 claims with `scripts/drive.py`. The online evaluation runs on the new sessions as in step 4.
8. `scripts/verify_fix.py --baseline insights/<timestamp>` first checks that the evaluator has covered the new sessions: it counts the sessions `scripts/drive.py` sent since the baseline and the result records in the config's results log group (`/aws/bedrock-agentcore/evaluations/results/<config-id>`). Then it compares each baseline Insight with the current one by ID and reports how many sessions each Insight gained. `scripts/outcomes.py` counts how the claims agent routed the claims in each run: approved automatically, sent to human review, or rejected.

**Housekeeping (steps 9 and 10).**

9. `UpdateAgentInsight` archives an Insight you are done with and restores it later.
10. `scripts/cleanup.py` removes what this sample created. The claims agent is removed with its own teardown script.

[`patches/claims-agent-insight-fixes.patch`](../patches/claims-agent-insight-fixes.patch) is a reference change set for the claims agent from one run: system prompt changes, tool-description changes, code changes, and unit tests. The README's [Sample results](../README.md#sample-results) describe the run it comes from. Apply it to a copy of the claims agent to compare with what your coding assistant produces. The sample does not apply it for you.

### What an Insight gives you

| Field | Use |
|---|---|
| `agentInsightId` | Stable. An Insight keeps its ID as it grows, so you can compare before and after by ID. |
| `name`, `description`, `rootCauses[]` | What recurs and why. Each root cause has an explanation and a suggested fix. Treat the fix as a hypothesis to confirm in the code. |
| `affectedSessionCount` | Sessions grouped into the Insight so far. |
| `lastSeenAt` | When a session last joined it. |
| Findings (`sampleSessions[].findings[]`) | Evidence from sampled sessions, each with a category and a target (`SPAN_ID`, `SPAN_NAME`, and so on). |
| Archive status | `UpdateAgentInsight` archives an Insight you are done with and restores it later. Lists default to active Insights. |

`scripts/verify_fix.py` reports `quiet` for an Insight whose count and last-seen time have not moved, `STILL FAILING` for one that has, and `NEW` for an Insight that was not in the baseline. It exits 0 when nothing grew, 1 when something did, and 2 when the evaluator has not yet covered the replayed sessions.

## Sample versus production

This is sample code. It shows a pattern; it is not a hardened deployment.

| Sample choice | For production |
|---|---|
| 100% sampling and a 2-minute idle timeout | Sample less at volume. Set the idle timeout to match how long your real sessions stay quiet. |
| Scripts and a local state file | Define the config, role and Store settings in IaC, and keep state in your pipeline. |
| The evaluation role's log-read statement uses `Resource: *` | Scope it to the runtime log group and `aws/spans`. |
| Setup runs with enough permissions to create an IAM role and AgentCore control-plane resources | Use a dedicated role that can create the evaluation config and pass the evaluation role, and nothing else. |
| `scripts/drive.py` sends scripted claims | Replay representative traffic, or wait for real sessions. |
| One person reviews the coding assistant's diff by hand | Review each change against your business rules in your normal code review, run the tests in your pipeline, and test the agent before you deploy. |
| Single Region (us-east-1) | Run the sample in a Region where Continuous Insights is available. |

Account-wide settings stay on after `scripts/cleanup.py`: the Agent Insight Store (disabling it deletes stored Insights for every application in the account) and CloudWatch Transaction Search. The claims agent is removed with its own teardown script.
