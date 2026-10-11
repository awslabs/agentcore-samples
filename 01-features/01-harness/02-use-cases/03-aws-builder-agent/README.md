# AWS Builder Agent — Harness + AWS Skills

| Information         | Details                                                                  |
|:--------------------|:-------------------------------------------------------------------------|
| Tutorial type       | Use Case                                                                 |
| Agent type          | AWS engineering / coding agent                                           |
| Agentic Framework   | None (direct boto3)                                                      |
| LLM model           | Anthropic Claude Haiku 4.5                                               |
| Tutorial components | harness — **AWS Skills** (`awsSkills`), filesystem + shell tools, multi-turn |
| Example complexity  | Intermediate                                                             |

## Overview

**This is the "how do you build an agent with the harness?" example — and the
answer is harness + AWS Skills.** The harness *is* the agent: you declare the
model, the tools, and the skills in one `create_harness` call, then invoke. No
orchestration code, no framework.

Here we build an **AWS engineering assistant** by loading the
[AWS Agent Toolkit](https://github.com/aws/agent-toolkit-for-aws) skills via the
`awsSkills` parameter. Those skills give the agent curated AWS expertise
(serverless, CDK, CloudFormation, observability), and the harness's built-in
filesystem + shell tools let it actually **scaffold a runnable project**, not
just describe one.

> **Why this matters:** AWS Skills are the fastest way to see the benefit of the
> harness. A small, cheap model + the right skills = an AWS-aware coding agent in
> ~3 API calls. Change the skill paths or the prompt and you have a different
> agent — that is the whole harness model.

## How AWS Skills power this agent

```python
control.create_harness(
    harnessName=name,
    executionRoleArn=role_arn,
    # ── This one parameter is what makes it an AWS expert ──
    skills=[{"awsSkills": {"paths": ["core-skills/aws-serverless", "core-skills/aws-cdk"]}}],
    systemPrompt=[{"text": "You are a senior AWS solutions engineer..."}],
)
```

`awsSkills` selects bundles from the AWS Agent Toolkit. See
[13-aws-skills](../../01-advanced-examples/13-aws-skills) for every selection
mode (all / glob / specific / mixed).

## Architecture

```
aws_builder_agent.py
│
├── Step 0: create_harness_role() → IAM execution role
│
├── Step 1: create_harness(skills=[{"awsSkills": {"paths": [...]}}], systemPrompt=...)
│              └─ poll_harness_status → READY (30-155s measured, with AWS Skills)
│
├── Step 2: invoke_harness → turn 1: DESIGN (reply only, no files)
│              └─ [Tool: skills] — the agent reads the loaded skill bundles
│
├── Step 3: invoke_harness → turn 2, SAME session: SCAFFOLD
│              └─ [Tool: file_operations] × N → /tmp/url-shortener/{lib,lambda}/...
│                 the VM filesystem belongs to the session, so this must reuse it
│
└── Step 4: invoke_agent_runtime_command → list the files, and fail if there are none
               └─ bash globstar walk (the image has no `find`), exit code checked
```

## Prerequisites

- AWS credentials with permission to create a harness, an IAM role, and invoke Bedrock
- Bedrock model access enabled for the model you pass to `--model` (default: Claude Haiku 4.5)
- `pip install -r ../../requirements.txt`
- A region where AgentCore harness is available — `AWS_DEFAULT_REGION`, `AWS_REGION`, or your profile's region

## What it does, end to end

1. **Create** the agent — harness + `awsSkills` + a builder system prompt
2. **Design** (turn 1) — the agent designs a serverless URL shortener (API GW + Lambda + DynamoDB)
3. **Scaffold** (turn 2, same session) — it writes a real CDK project to the VM filesystem
4. **Inspect** — `ExecuteCommand` lists the files the agent created, and the run fails if it wrote none
5. **Clean up**

## Sample Prompts

**Brief (default)**: "Design a minimal serverless URL shortener on AWS: API Gateway + Lambda + DynamoDB..."
**Expected Behavior**: The agent designs the architecture, then scaffolds a TypeScript CDK app with handler files, README, and package.json under `/tmp/url-shortener`.

**Brief (`-m`)**: "Design and scaffold a CDK app for an S3 + Lambda thumbnail pipeline."
**Expected Behavior**: Same design → scaffold flow for a different serverless use case, drawing on the loaded AWS Skills.

## Key Concepts

**The skill is the difference**: Without `awsSkills`, a small model gives generic answers. With it, the agent applies real AWS best practices and current patterns.

**Multi-turn, one VM**: Design and scaffold run in the same `session_id`, so files from the scaffold step persist and can be inspected. The filesystem belongs to the session — a second `session_id` gets a fresh VM where `/tmp/url-shortener` does not exist.

**Agent acts, not just talks**: The harness's default filesystem + shell tools let the agent write runnable code, not placeholders.

**Verify, don't narrate**: The agent's closing summary is not evidence that the files exist. Step 4 asks the VM and stops the run if the project directory is empty — for a demo of a *building* agent, that check is the point.

## Troubleshooting

### Issue: `find: command not found`, or Step 4 lists nothing at all
**Solution**: The harness image (Amazon Linux 2023) has no `find` — nor `tree` or `xargs`. The original inspect step ran `find … 2>/dev/null | head -40`, and three things independently hid the failure: `2>/dev/null` discarded `find: command not found`, the exit code was never read, and the pipe made the exit code `head`'s rather than `find`'s — a pipeline reports only its **last** command's status, so the 127 surfaced as 0 and would have passed for success even if the code had checked it.

The script now walks the tree with bash `globstar` (no external binary, no pipeline), checks `contentStop.exitCode`, and raises if the listing is empty. When you add inspection commands of your own: don't send stderr to `/dev/null`, do read the exit code, and avoid pipelines — or set `set -o pipefail` so the status survives the pipe.

### Issue: The agent says it wrote the files but they are not there
**Solution**: Confirm both turns used the same `session_id`. The VM belongs to the session, so a scaffold in one session is invisible to an inspect in another. If the session is right, re-run with `--skip-cleanup` and look at the VM yourself — `invoke_agent_runtime_command` with `ls -1R /tmp/url-shortener` — because a small model will sometimes summarise work it did not do.

### Issue: The scaffold turn stops mid-way through writing files
**Solution**: Look for the `⚠️  Turn ended early — stopReason: ...` line. `max_iterations_exceeded` and `timeout_exceeded` mean the agent ran out of the budget set by `maxIterations` / `timeoutSeconds` on `invoke_harness` (this sample passes `timeoutSeconds=300`); raise it, or narrow the brief. See [03-execution-limits](../../01-advanced-examples/03-execution-limits) for how those limits interact.

### Issue: Harness stays `CREATING` for minutes
**Solution**: Expected. Creation with AWS Skills was measured between 30s and 155s across seven runs, most of them near 145s, so a couple of minutes of `CREATING` is normal and not a symptom. The shared poller in [utils/harness.py](../../utils/harness.py) allows 600s. A `CREATE_FAILED` is different: `poll_harness_status` raises immediately and includes the service's `failureReason`, which usually names a missing execution-role permission.

## Clean Up

```python
control.delete_harness(harnessId=harness_id)
from utils.iam import delete_harness_role
delete_harness_role()
```

The script deletes the harness on exit (pass `--skip-cleanup` to keep it). The
execution role is only deleted when the script created it — passing `--role-arn`
leaves your own role alone.

Creating a harness also provisions a managed memory named `harness_<name>_*`. You
cannot delete it directly (`delete-memory` rejects it as managed); it cascades
when the harness is deleted, but asynchronously — so it can still be listed for a
while after the script finishes. If one is still there once the harness is fully
gone, check with:

```bash
aws bedrock-agentcore-control list-memories --query "memories[?starts_with(id, 'harness_')]"
```

## Running the Python Scripts

```bash
pip install -r ../../requirements.txt
```

```bash
# Build the default serverless URL-shortener agent
python aws_builder_agent.py

# Give it your own brief
python aws_builder_agent.py \
    -m "Design and scaffold a CDK app for an S3 + Lambda thumbnail pipeline."

# Narrow the AWS Skills the agent loads
python aws_builder_agent.py --skill-paths core-skills/aws-cdk core-skills/aws-serverless

# See all options
python aws_builder_agent.py --help
```

| Flag | Default | What it does |
|:-----|:--------|:-------------|
| `--message`, `-m` | the URL-shortener brief | Replace the design brief; the scaffold step follows automatically |
| `--skill-paths PATH ...` | `core-skills/aws-serverless core-skills/aws-cdk` | Which AWS Skills to load |
| `--model MODEL_ID` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` | Bedrock model the harness invokes |
| `--role-arn ARN` | *(creates one)* | Reuse an existing execution role instead of creating and deleting one |
| `--skip-cleanup` | off | Keep the harness so you can inspect the VM |
| `--raw-events` | off | Print the raw streaming events instead of the rendered reply |
