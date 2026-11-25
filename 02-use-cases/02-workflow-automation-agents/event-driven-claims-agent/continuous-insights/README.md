# Amazon Bedrock AgentCore Continuous Insights for the Event-Driven Claims Agent

Find the failures your agent repeats, fix them, and check that the fix worked. This sample runs **Continuous Insights**, a feature of Amazon Bedrock AgentCore Evaluations, on the Event-Driven Claims Agent, one command at a time. Continuous Insights groups the recurring failures of an agent into Agent Insights that keep a stable ID and name over time, so the same Insights show you what fails before a change and whether it still fails after.

> **Sample code.** The scripts use the released AWS SDK (boto3 1.44.5 or later) and AWS CLI (2.41.0 or later). They are sample code that shows a pattern, not a hardened deployment. See [Sample versus production](docs/architecture.md#sample-versus-production).

## What you will do

The walkthrough has four parts:

1. **Observe (steps 1 to 4).** Deploy the Event-Driven Claims Agent (a public sample), register it with this sample's scripts, turn on Continuous Insights, and send 100 claims. Failure Analysis evaluates each session, and Continuous Insights groups the failures into Agent Insights.
2. **Capture (step 5).** Export the Insights, with their root causes, suggested fixes, and evidence, to local files. The export is your baseline.
3. **Fix (step 6).** Give the export to an AI coding assistant. It fixes each cause where it lives: in the system prompt or a tool description when the wording led the model astray, or in code when a check is missing.
4. **Verify (steps 7 and 8).** Redeploy, send the same 100 claims, and compare the Insights and the claim outcomes with your baseline.

Steps 9 and 10 archive and restore Insights and remove what you created.

| | |
|---|---|
| **Region** | `us-east-1` |
| **Agent deployment** | 20 to 30 minutes |
| **Sending 100 claims** | About 2 to 3 minutes for each round, with 20 claims in flight |
| **First Insights** | After about 20 failed sessions are evaluated. In our run, 12 minutes after the first claim. |
| **Language** | Python 3.12 or later |

This folder sits inside the [Event-Driven Claims Agent](../README.md) sample, which you deploy first. It holds the scripts, documents, and a reference patch for the loop around the claims agent.

## How it works in a minute

An insurance company runs a claims agent. A Claims Processor agent decides each claim. A Validation Agent reviews the decision and scores its confidence. Across thousands of claims, the same mistakes repeat. Each one is a single session among many, so the pattern is hard to see.

1. The claims agent runs on AgentCore Runtime and sends OpenTelemetry spans to Amazon CloudWatch.
2. An online evaluation configuration runs the built-in `Builtin.FailureAnalysis` evaluator on each finished session.
3. Continuous Insights groups similar failures into **Agent Insights**. Each Insight has a name, a count of affected sessions, root causes, and a suggested fix.
4. An Insight keeps its ID and name as new sessions arrive. You see which failures grow, which stop, and which are new.
5. You export the Insights, and a coding assistant fixes the causes in the prompts, the tool descriptions, or the code.
6. You redeploy, send the same claims, and compare the counts with your export.

![Architecture: the claims agent runs on AgentCore Runtime and emits spans to CloudWatch; scripts create a managed dataset of claim scenarios and send the claims from it; online evaluation runs Failure Analysis and publishes Agent Insights; a coding assistant reads the exported Insights; the next run is compared with the first. Numbered circles match the steps of the walkthrough.](docs/architecture.png)

[docs/architecture.md](docs/architecture.md) describes the components, the IAM role, and the design decisions.

### Concepts

| Term | Meaning |
|---|---|
| **Agent Insight** | A group of recurring failures with a name, a description, an affected-session count, root causes, a suggested fix for each, and sample sessions. |
| **Application name** | The `service.name` your agent emits. On AgentCore Runtime it is `<runtime name>.DEFAULT`, here `ClaimsAgent_claimsagent.DEFAULT`. Insights belong to an application name. |
| **Agent Insight Store** | Where your account keeps its Insights, one store for each account and Region. |
| **Online evaluation configuration** | The rule that selects live sessions and runs evaluators on them. Continuous Insights uses the built-in `Builtin.FailureAnalysis` evaluator. |
| **Managed dataset** | A named set of evaluation scenarios that AgentCore stores for you. This sample keeps its claim scenarios in one, so the test traffic is defined in one place. |
| **Session idle timeout** | How long a session stays quiet before it counts as finished and gets evaluated. This sample uses 2 minutes. |

## Prerequisites

| You need | Detail |
|---|---|
| AWS account | A sandbox account. The claims agent creates about 76 resources. This sample adds one IAM role and one online evaluation configuration. |
| Region | `us-east-1`. The scripts default to it. |
| The claims agent | Deploy the [Event-Driven Claims Agent](../README.md) (the parent folder) into `us-east-1` with `./deploy.sh us-east-1`, as its README describes. Step 1 shows the commands. It creates the AgentCore runtime `ClaimsAgent_claimsagent`. Its prerequisites are Node.js 20 or later, the AgentCore CLI (`npm install -g @aws/agentcore@0.30.0`), the AWS CDK CLI, Docker or Finch, `uv`, and AWS CLI v2. |
| Model access | Anthropic Claude Sonnet 4.6 (`global.anthropic.claude-sonnet-4-6`, the Claims Processor) and Claude Haiku 4.5 (`us.anthropic.claude-haiku-4-5-20251001-v1:0`, the Validation Agent), enabled in Amazon Bedrock in `us-east-1`. |
| Permissions | Permission to create IAM roles and policies, call `bedrock-agentcore-control` and `bedrock-agentcore`, invoke the runtime, and query CloudWatch Logs. Administrator access in a sandbox account is the simplest option. |
| CloudWatch Transaction Search | Turned on in the account. Check with `aws xray get-trace-segment-destination --region us-east-1`. The output shows `"Status": "ACTIVE"`. To turn it on, run the claims agent's `scripts/enable_observability.py` or follow [Transaction Search](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Transaction-Search.html). Transaction Search is an account-wide setting that bills per ingested span. |
| Tools | Python 3.12 or later, `pip`, `git`, AWS CLI 2.41.0 or later (for the optional CLI commands), and AWS credentials for the sandbox account in your environment. `pip install -r requirements.txt` installs boto3 1.44.5 or later, which has the Agent Insight operations. |
| An AI coding assistant | Any assistant that edits files in a folder, such as Kiro or Claude Code. Step 6 uses one. |

## Walkthrough

Run every command from the repository root. The scripts write their files there: `agent_state_<name>.json`, `sessions_<name>.jsonl`, and `insights/<timestamp>/`. These paths are in `.gitignore` because they hold account-specific data.

Two folder names appear in the steps:

- `<claims-agent-dir>` is the claims agent: `02-use-cases/02-workflow-automation-agents/event-driven-claims-agent` in your clone of this repository. You deploy it in step 1.
- `<sample-dir>` is this folder, `continuous-insights`, inside `<claims-agent-dir>`.

### Step 1: Install the scripts and deploy the claims agent

**What this step does.** It installs this sample's Python dependencies, then deploys the claims agent and confirms that it answers a test claim.

**Run.**

```bash
# Clone this repository and deploy the claims agent
git clone https://github.com/awslabs/agentcore-samples.git
cd agentcore-samples/02-use-cases/02-workflow-automation-agents/event-driven-claims-agent   # <claims-agent-dir>
./deploy.sh us-east-1
python3 scripts/test_invoke.py --region us-east-1

# Install this sample's scripts
cd continuous-insights   # <sample-dir>
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**What you should see.** `deploy.sh` finishes without an error and creates the runtime `ClaimsAgent_claimsagent`. `test_invoke.py` prints the claims agent's answer to a test claim. Deployment takes 20 to 30 minutes.

**If it does not match.** Two issues in the claims agent's deployment are known:

- `./deploy.sh` stops at the CDK step because the AgentCore CLI does not accept the project's `aws-cdk` version. In `agentcore/cdk/package.json`, change `"aws-cdk": "^2.1128.0"` to `"aws-cdk": "~2.1126.0"`, then run `./deploy.sh us-east-1` again.
- The first `./deploy.sh` fails with `DiscoveryUrl is not a valid URL`. Run `agentcore remove credential --name cognito-gateway-m2m --yes` in `<claims-agent-dir>` (it changes local configuration only), then run `./deploy.sh us-east-1` again.

For other deployment errors, use the claims agent's own README.

### Step 2: Register the deployed runtime

**What this step does.** It finds your deployed runtime and writes `agent_state_ClaimsAgent.json`, which every later script reads. It only reads from your account.

**Run.**

```bash
python scripts/register_agent.py --name ClaimsAgent
```

`--name` labels the state file, the evaluation configuration, and the evaluation role. Start it with a letter and use only letters, digits, and underscores (up to 48 characters). If your runtime has a different name, add `--runtime-name <name>`. Add `--region <region>` only if you deployed outside `us-east-1`.

**What you should see.**

```text
Registered ClaimsAgent_claimsagent (<runtime id>) in us-east-1.
Wrote agent_state_ClaimsAgent.json. Next: python scripts/setup_insights.py --name ClaimsAgent
```

### Step 3: Turn on Continuous Insights

**What this step does.** It creates what the service needs to evaluate your sessions and group failures. It is safe to run again. It reuses what it already created.

**Run.**

```bash
python scripts/setup_insights.py --name ClaimsAgent
```

The script:

1. Enables the account's Agent Insight Store if it is not enabled.
2. Confirms the application name that the claims agent emits, `ClaimsAgent_claimsagent.DEFAULT`, by reading the runtime's log group.
3. Creates the evaluation execution role `AgentCoreEvaluationRole-ClaimsAgent`.
4. Creates the online evaluation configuration `ClaimsAgent_insights` with `Builtin.FailureAnalysis` as its only evaluator, 100% sampling, and a 2-minute session idle timeout.

The claims agent has its own online evaluation configuration, `ClaimsEvaluation`. This sample leaves it unchanged and adds a second configuration next to it.

**What you should see.** The script prints the Store state, `Application name confirmed: ClaimsAgent_claimsagent.DEFAULT`, the role and configuration it created, and ends with:

```text
Continuous Insights is on. Next: python scripts/create_dataset.py --name ClaimsAgent
```

**If it does not match.** If the script says the application name has no spans yet, invoke the claims agent once, wait two minutes, and run the script again.

### Step 4: Send claims and wait for Insights

**What this step does.** It stores the claim scenarios in an AgentCore managed dataset, then sends 100 claims from that dataset to the runtime. Each claim is one session. The online evaluation runs Failure Analysis on each session after it goes idle. Continuous Insights then groups the failures.

**Run.**

```bash
python scripts/create_dataset.py --name ClaimsAgent
python scripts/drive.py --name ClaimsAgent --count 100 --concurrency 20
```

`create_dataset.py` creates the dataset `ClaimsAgent_claim_scenarios` with seven scenarios and saves its ID in the state file. Each scenario is a single-turn example: the claim text is the turn `input`, and the claimant's email is in the scenario `metadata`. You run it once. To add or change claims later, edit the dataset in the console or with `aws bedrock-agentcore-control add-dataset-examples`; `drive.py` reads the scenarios from the dataset each time it runs. See [Manage datasets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/datasets-manage.html).

`drive.py` asks you to type `yes` before it starts. Add `--yes` to skip the prompt. It cycles through the seven scenarios:

| Scenario | Claim |
|---|---|
| `approval-fender-bender` | A $2,000 fender bender on `POL-12345` |
| `large-house-fire` | A $150,000 house fire on `POL-67890`, over the Cedar policy limit |
| `lowercase-policy-number` | A $1,800 hail claim on `pol-12345` |
| `vague-claim` | "Something happened to my car", maybe $30,000, no photos |
| `unknown-policy` | A $500 claim on `POL-00000` |
| `approval-storm-damage` | A $4,500 storm-damage claim on `POL-11111` |
| `email-not-on-policy` | A $3,200 claim on `POL-12345` from an email address that is not on the policy |

Each claim carries the policy holder's email address, except `email-not-on-policy`. Each claim is a real Amazon Bedrock call that takes 60 to 90 seconds. With 20 claims in flight, 100 claims take about 2 to 3 minutes. 100 claims give comfortably more than the roughly 20 failed sessions that the first Insights need. The script appends one line per session to `sessions_ClaimsAgent.jsonl`, which step 8 reads.

#### Sample prompts

These are the claims in the dataset. `drive.py` sends each one with the claimant email in the payload. Each one shows a different behavior of the claims agent:

| Prompt | Claimant email | What it tests |
|---|---|---|
| "File a claim for POL-67890. Major house fire, fully documented by the fire department, $150,000 damage." | `jane.doe@example.com` | A high-value claim that should go to a human reviewer |
| "my policy is pol-12345, hail dented my hood, repair estimate is $1,800" | `john.smith@example.com` | A valid claim with the policy number in lowercase |
| "I think something happened to my car. POL-12345. Maybe $30,000, no photos." | `john.smith@example.com` | A vague claim with no evidence |
| "File a claim for POL-12345. Someone backed into my parked car, the repair estimate is $3,200." | `stranger@example.com` | A claimant who is not the policy holder |
| "POL-00000, I scratched my car in a parking lot, about $500 of damage." | `driver@example.com` | A policy that does not exist |

**What you should see.** One line per claim, then a summary:

```text
OK   approval-fender-bender     ci-approval-fender-bender-<id>  (1/100)
...
100 sent, 0 failed to invoke
```

**Wait for the first Insights.** Failure Analysis results appear first. Continuous Insights creates its first Insights after it has evaluated about 20 failed sessions. Only sessions that start after step 3 are evaluated. A session is evaluated after it has been idle for the 2-minute timeout.

Check progress at any time:

```bash
python scripts/status.py --name ClaimsAgent
```

The output right after step 3 looks like this (before any claim is evaluated). Your numbers differ.

```text
Insight Store          : ACTIVE / ENABLED
Online evaluation      : ACTIVE / ENABLED
Sessions evaluated     : 0 with failures, 0 clean (last 24 hours)
Active Agent Insights  : 0  (application ClaimsAgent_claimsagent.DEFAULT)
  none yet. Insights appear after about 20 failed sessions have been evaluated. Send more claims, or wait for the evaluations of recent sessions to finish.
```

When Insights exist, the same command lists them, with the most affected sessions first. This is the start of the list from one run of this sample, after 100 claims (IDs and times replaced with placeholders):

```text
Active Agent Insights  : 7  (application ClaimsAgent_claimsagent.DEFAULT)
  <insight id>  sessions=16  last seen <time>  Agent Ignoring Explicit High-Value Claim Skepticism Rule
  <insight id>  sessions=14  last seen <time>  Approval Decision Without Prior Identity Verification
  <insight id>  sessions=14  last seen <time>  Failure to Attempt Format Normalization on Lookup Errors
```

In that run, all 7 Insights appeared 12 minutes after the first claim, about 10 minutes after the last claim was sent. Timing varies with your traffic.

An empty Insight list early in the run is normal. Run `status.py` again every few minutes. Go to step 5 when the list shows Insights.

**If it does not match.** If the Insight list stays empty, run `status.py` and check that `Sessions evaluated` shows at least 20 sessions with failures. A session is evaluated only after it has been idle for the 2-minute timeout, and only sessions that start after step 3 count.

### Step 5: Export the Insights

**What this step does.** It writes the Insights to files. The coding assistant in step 6 and the comparison in step 8 both read them. Each Insight has an ID. The IDs are in `FAILURES.md` and `summary.json`.

**Run.**

```bash
python scripts/export_insights.py --name ClaimsAgent
```

An Insight identifies the failing step by span ID. The script looks up each span in CloudWatch Logs (`--lookback-days`, default 7) and adds the tool name and any exception to the evidence. Use `--out <dir>` to write to another folder.

**What you should see.**

```text
Resolved <n> of <m> failing span(s) to tool and exception details
Exported <k> Insight(s) to <sample-dir>/insights/<timestamp>
Read first: <sample-dir>/insights/<timestamp>/FAILURES.md
```

The script writes `insights/<timestamp>/`:

| File | Content |
|---|---|
| `FAILURES.md` | One section per Insight: description, root causes, suggested fix, evidence from sampled sessions, and example session IDs |
| `insight-<id>.json` | The full `GetAgentInsight` response for each Insight |
| `spans.json` | The failing spans the script looked up in CloudWatch Logs |
| `summary.json` | Each Insight's ID, name, affected-session count, and last-seen time. Step 8 uses it as the baseline. |

Keep this folder. It is your baseline.

### Step 6: Fix the agent with an AI coding assistant

**What this step does.** An AI coding assistant reads the exported Insights and changes the claims agent where each cause lives. A cause can live in the text the model reads (the system prompt or a tool description) or in code (for example, a missing check). You review the changes against your business rules before you deploy them in step 7.

**Run.** Work on a branch of your claims agent clone, so that you can review the diff. Copy [`AGENTS.md`](AGENTS.md) and the export from step 5 into the folder, and name the export folder `insights/`:

```bash
cd <claims-agent-dir>
git checkout -b insight-fixes
cp <sample-dir>/AGENTS.md AGENTS.md
cp -r <sample-dir>/insights/<timestamp> insights
```

Open `<claims-agent-dir>` in your coding assistant and give it this prompt:

> Read `AGENTS.md` and `insights/FAILURES.md`. Fix the cause of each Insight at the layer where it lives, add unit tests for any code you change, and run `pytest`.

`AGENTS.md` tells the assistant what to read and where the prompts, tool descriptions, and checks live. It tells the assistant to treat each suggested fix as a hypothesis, to prefer a deterministic check in code over a longer prompt, and not to weaken a safeguard, deploy, or call AWS. The claims agent ships its own `AGENTS.md`; the copy replaces it for this task, and `git checkout AGENTS.md` restores it. Do not commit this `AGENTS.md` or `insights/` to your branch.

To decide whether a change fits, read each Insight's root causes in `FAILURES.md` next to the diff:

| The Insight's root cause | Where the fix usually goes | File in `<claims-agent-dir>` |
|---|---|---|
| The model had what it needed, but the instructions were vague or missing | The system prompt | `PROCESSOR_PROMPT` and `VALIDATOR_PROMPT` in `app/claimsagent/main.py` |
| The model misused a tool or misread its result | The tool description | `app/claimsagent/tools/structured_output.py` (docstrings) and `lambdas/schemas/policy_lookup.json` |
| A rule that must hold every time, such as "claims over $25,000 go to a reviewer" | A deterministic check, with a unit test | `app/claimsagent/routing.py`, `lambdas/*/handler.py` |

**Review the changes.** Read the whole `git diff` before you deploy. Check that the prompts, the tool descriptions, and the code agree, and that each change matches your business rules. For example, decide whether a claim from an email address that is not on the policy should be rejected or sent to a human reviewer. In our run, we accepted the assistant's changes as they were. The smoke test in step 8 then found one problem, which the assistant fixed in a second pass (see [Sample results](#sample-results)).

**Reference patch.** To skip the coding assistant, apply the patch from our run, [`patches/claims-agent-insight-fixes.patch`](patches/claims-agent-insight-fixes.patch), from inside `<claims-agent-dir>`:

```bash
cd <claims-agent-dir>
patch -p1 < <sample-dir>/patches/claims-agent-insight-fixes.patch
```

The patch holds the coding assistant's changes from our run:

- **Checks before an automatic approval** (`routing.py`, `config.py`). A claim over $25,000 (`HIGH_VALUE_THRESHOLD`), a claim whose claimant email does not match the policy holder's, a missing or unknown category, or a validator that routes a rejection to human review goes to a human reviewer. Each check can only move a claim toward human review.
- **A REFER decision** (`tools/structured_output.py`). The Claims Processor can refer a claim to a reviewer instead of choosing ACCEPT or REJECT, and the decision tool rejects any other value.
- **Prompts** (`main.py`). The Claims Processor verifies the claimant against the policy holder, does not guess a category, and refers a claim when it has an unresolved concern. The Validation Agent states the rule for claims over $25,000 and lists every concern it finds.
- **The policy lookup** (`lambdas/policy_lookup/handler.py` and its description). It normalizes the policy number, so `pol-12345` finds `POL-12345`, and it returns coded errors such as `POLICY_NOT_FOUND`.
- **Tool calls in the deterministic phase** (`gateway_tools.py`, `main.py`). The phase reads tool results in the form the MCP client returns them and calls the Gateway's prefixed tool names, so claim records, reviews, and notifications reach the right tools.
- **New unit tests** for each of these.

**What you should see.** `git diff` in `<claims-agent-dir>` shows the prompt, tool-description, code, and test changes. Run `pytest` in `<claims-agent-dir>`: the claims agent's tests and the added tests pass (103 tests with the reference patch).

### Step 7: Redeploy and send the same claims again

**What this step does.** It puts the changes from step 6 into the running claims agent with one redeploy, then sends the same traffic again so that the new sessions are comparable with the first round.

**Run.** Redeploy from `<claims-agent-dir>`, then send the claims from `<sample-dir>`:

```bash
cd <claims-agent-dir>
./deploy.sh us-east-1

cd ../../../01-features/06-observe-evaluate-optimize-your-agent/04-continuous-insights   # <sample-dir>
python scripts/drive.py --name ClaimsAgent --count 100 --concurrency 20
```

Before you send the full round, run the smoke test described in [step 8](#step-8-compare-with-the-baseline). Step 8 counts the sessions that `drive.py` sent after you exported the baseline in step 5, so do not run `drive.py` between step 5 and this step except for the smoke test and this round.

**What you should see.** `deploy.sh` finishes without an error. `drive.py` ends with `100 sent, 0 failed to invoke`, as in step 4.

**If it does not match.** For deployment errors, use the claims agent's own README.

### Step 8: Compare with the baseline

**What this step does.** It compares each Insight in your step 5 export with its current state, and it lists Insights that were not in the export.

**Run.**

```bash
python scripts/verify_fix.py --name ClaimsAgent --baseline insights/<timestamp> --wait-minutes 25
```

Use the folder from step 5 as `--baseline`. The script:

1. Counts the claims `drive.py` sent since the baseline export, from `sessions_ClaimsAgent.jsonl` (or since `--since <ISO time>`). It then checks once a minute, up to `--wait-minutes`, until the evaluator has covered that many sessions.
2. Compares each baseline Insight with its current state. It prints `STILL FAILING` if the affected-session count or last-seen time moved, and `quiet` if neither did. It prints `NEW` for each Insight that is not in the baseline.
3. Prints `PASS` when no Insight moved and none is new. Otherwise it prints `FAIL` with the Insight names.

**What you should see.** Lines like these, and a final `PASS` or `FAIL`. The Insight lines come from our run:

```text
Post-fix sessions sent: <n>; evaluated: <n>
  STILL FAILING Premature Decision Submission Despite Acknowledged Gaps  sessions 22 -> 39  last seen <time>
  STILL FAILING Agent Ignoring Explicit High-Value Claim Skepticism Rule  sessions 17 -> 18  last seen <time>
  quiet         Approval Decision Without Prior Identity Verification  sessions 14 -> 14  last seen <time>
```

| Exit code | Meaning |
|---|---|
| `0` | The evaluator covered the new sessions, no baseline Insight grew, and no new Insight appeared. |
| `1` | At least one baseline Insight grew, or a new Insight appeared. |
| `2` | Not enough data yet: there are no post-baseline sessions in the session log, or the evaluator has not covered them. Run the script again with a larger `--wait-minutes`. |

An Insight keeps its ID and name when you change the agent, so the comparison needs no manual matching. An Insight whose failure stops stays in the list, and its count stops growing. `FAIL` means at least one count moved. Read the lines to see how much. [How to read the results](#how-to-read-the-results) explains the numbers.

**Smoke test first.** Right after the redeploy in step 7, and before the full round, send a few claims: `python scripts/drive.py --name ClaimsAgent --count 10`. Confirm that clean claims, such as the $2,000 fender bender, still get approved automatically. A change that sends every claim to human review would also stop most failures. In our run, the first smoke test failed: every fender bender went to human review, because a new check could not read the tool result. We gave the runtime log lines to the coding assistant, it fixed the cause, and the second smoke test passed.

**Export the baseline again after the smoke test.** Smoke-test sessions are evaluated too, and they can add sessions to the Insights. Wait until `status.py` shows them evaluated, run `export_insights.py` again, and use that folder as `--baseline`. Then the comparison counts only the full round.

**Look at how claims were routed, not only at the Insight counts.** A change that sends every claim to a reviewer also reduces failures. The outcomes script counts how the claims agent routed each claim in a time window, from its runtime log, grouped by scenario:

```bash
python scripts/outcomes.py --name ClaimsAgent --since <round start, for example 2026-01-01T10:00:00Z>
```

Run it once for each round and compare. [Sample results](#sample-results) shows both views from our run.

**If it does not match.** Exit code `2` means the evaluator has not covered the new sessions yet. Run the script again with a larger `--wait-minutes`.

### Step 9: Archive and restore Insights

**What this step does.** Archiving removes an Insight from the active list when you are done with it. Restoring brings it back. You can export the archived ones later.

**Run.** To export Insights that you archived earlier, add `--archive-status ARCHIVED`:

```bash
python scripts/export_insights.py --name ClaimsAgent --archive-status ARCHIVED
```

`ListAgentInsights` takes an archive status of `ACTIVE` (the default) or `ARCHIVED`. `UpdateAgentInsight` archives or restores an Insight. This sample has no script for `UpdateAgentInsight`. Call it with the AWS CLI: `aws bedrock-agentcore update-agent-insight --application-name <application-name> --agent-insight-id <insight-id> --archive-status ARCHIVED --region us-east-1`. `verify_fix.py` skips a baseline Insight that is no longer in the active list.

**What you should see.** The same output as step 5, with the archived Insights in the count: `Exported <k> Insight(s) to ...`.

**If it does not match.** If the script writes `0 active Insight(s)` or an empty export, the Insights are not ready yet. Run `status.py` and try again when it lists them.

### Step 10: Clean up

**What this step does.** It removes what this sample created, then removes the claims agent. The claims agent costs about $3 to $5 per day while it stays deployed.

**Run.**

```bash
# This sample: the online evaluation configuration, the evaluation role, and the results log group
python scripts/cleanup.py --name ClaimsAgent --delete-logs

# The claims agent and its resources
cd <claims-agent-dir>
./scripts/destroy.sh us-east-1
```

`cleanup.py` asks you to type `yes` (`--yes` skips the prompt). It removes the online evaluation configuration, the evaluation role with its inline policies, the scenario dataset, and, with `--delete-logs`, the evaluation results log group. Delete `agent_state_ClaimsAgent.json`, `sessions_ClaimsAgent.jsonl`, and `insights/` from `<sample-dir>` when you no longer need them.

Two account-wide settings stay in place:

- **The Agent Insight Store.** To disable it, run `aws bedrock-agentcore-control update-agent-insight-store --agent-insight-store-id default-insight-store --enablement-status DISABLED --region us-east-1`. **Disabling the store deletes the stored Insights for every application in the account.**
- **CloudWatch Transaction Search.** It bills per ingested span for every traced workload in the account. Turn it off only if nothing else needs it.

**What you should see.** One `deleted` line for each resource, then `Done. Remove agent_state_ClaimsAgent.json when you no longer need it.` A resource that is already gone prints `skip ... (already gone)`.

## How to read the results

- **Counts are cumulative.** An Insight's affected-session count includes every session that joined it since it was created, from every round. The count in step 8 is not the number of failures in round 2. To compare rounds, use the sessions each Insight gained: the current count minus the baseline count.
- **Zero sessions added means no new session joined that Insight since your export.** The Insight stays in the list with its old count. `verify_fix.py` prints `quiet` for it.
- **Sessions added means new sessions failed the same way.** `verify_fix.py` prints `STILL FAILING` for it. Open its section in a fresh export and compare the evidence with the first one.
- **A new Insight is a group of failures that was not in your baseline.** A change to the agent can create new failures. Handle a new Insight like the first ones: export it, find the cause, and decide whether to change the agent again.
- **Check the outcome mix too.** A safer agent can send more claims to human review. Count how the claims were routed (see [step 8](#step-8-compare-with-the-baseline)), and decide whether the extra reviews are worth what you fixed.
- **Model output varies from run to run.** A change of a few sessions in one Insight is a signal to look at, not a measurement of cause.

### Sample results

These numbers come from one run of this sample in `us-east-1` on claims that `drive.py` generated. The run started with an empty Insight catalog and sent 100 claims in each round (`--count 100`). Your counts depend on your agent and your traffic.

**Round 1, the unchanged agent.** The service evaluated all 100 sessions, and 59 had failures. All 7 Insights appeared 12 minutes after the first claim.

**The change.** A coding assistant read `AGENTS.md` and the export, with no other instructions, and made the changes that [step 6](#step-6-fix-the-agent-with-an-ai-coding-assistant) lists: checks before an automatic approval, a REFER decision, prompt and tool-description changes, a normalized policy lookup, and 52 new unit tests. The first smoke test found that clean claims went to human review; the assistant fixed the cause, and the second smoke test passed. We exported the baseline again after the smoke tests, then sent the same 100 claims.

**Sessions per Insight.** Each number is a count of sessions, and each session is one claim. Round 1 is how many of the 100 claims in round 1 had that failure. Round 2 is how many of the 100 claims in round 2 added a session to that Insight, compared with the baseline exported after the smoke tests.

| Insight | Sessions affected in round 1 (of 100) | Sessions added in round 2 (of 100) |
|---|---|---|
| Agent Ignoring Explicit High-Value Claim Skepticism Rule | 16 | +1 |
| Approval Decision Without Prior Identity Verification | 14 | 0 |
| Failure to Attempt Format Normalization on Lookup Errors | 14 | 0 |
| Validation Agent Fails to Flag Case-Sensitivity Lookup Issues | 12 | +1 |
| Premature Decision Submission Despite Acknowledged Gaps | 7 | +17 |
| Unconfirmed Category Assignment | 5 | 0 |
| Policy Resource Not Found | 5 | 0 |
| **All 7 Insights** | **73** | **+19** |

**Claim outcomes by scenario.** These are the agent's routing decisions, as `scripts/outcomes.py` counts them from the runtime log. Each scenario has about 14 claims.

| Scenario | Round 1 | Round 2 |
|---|---|---|
| $2,000 fender bender, holder's email | 13 automatic approval, 2 review | 15 automatic approval |
| $150,000 house fire | 13 automatic approval, 2 review | 15 review |
| Lowercase policy number, valid $1,800 claim | 14 rejected | 14 automatic approval |
| Email not on the policy | 14 review | 14 review |
| Vague claim, maybe $30,000, no photos | 11 rejected, 3 review | 14 review |
| Unknown policy | 14 rejected | 13 rejected, 1 review |
| Storm damage on an auto policy | 14 rejected | 14 rejected |

What the run shows:

- **In our run, the targeted failures mostly stopped.** The 7 Insights gained 19 sessions in round 2, against 73 in round 1. Four gained none.
- **In our run, the routing got safer without more reviews of clean claims.** The $150,000 house fires that were approved automatically (13) all went to a reviewer. The valid claims that the agent rejected because of a lowercase policy number (14) were all approved. The clean fender benders went from 13 automatic approvals to 15.
- **One Insight grew.** Premature Decision Submission Despite Acknowledged Gaps gained 17 sessions. The change added a REFER decision, and vague claims now go to a reviewer instead of being rejected. Read this Insight's sessions in a fresh export to see whether Failure Analysis flags the new REFER path, then decide whether to change the prompt again. That is the next round of steps 5 to 8.
- **The smoke test caught a defect that the Insights could not.** The first version of the fix sent clean claims to human review. Check the outcomes, not only the Insight counts.

### What to do with the results

1. Read `FAILURES.md` for each Insight that grew or is new. Check the evidence against your code.
2. Fix the agent from those Insights with a coding assistant (step 6): prompt or tool-description wording, or a missing check in code.
3. Review the changes against your business rules before you deploy them.
4. Redeploy once, run a smoke test, send the same traffic, and compare both the Insights and the claim outcomes (steps 7 and 8).
5. Archive Insights that you have handled so that the active list shows what is left (step 9).

## Use it with your own agent

Run `scripts/register_agent.py` with `--runtime-name` set to your runtime's name, then follow the same steps. Your agent needs to emit spans to CloudWatch with a stable `service.name`. Choose the session idle timeout and the sampling rate in `scripts/setup_insights.py` to match your traffic.

The coding assistant instructions in [`AGENTS.md`](AGENTS.md) name the claims agent's files: where the prompts, the tool descriptions, and the checks live. Edit them for your code base.

## Security

This is sample code. The scripts use your AWS credentials from the environment and store none. The evaluation role trusts only AgentCore in your account, and the coding assistant has no AWS access. To report a security issue, see [CONTRIBUTING](../../../../CONTRIBUTING.md#security-issue-notifications).

## License

This sample is licensed under the Apache License 2.0. See [LICENSE](../../../../LICENSE).
