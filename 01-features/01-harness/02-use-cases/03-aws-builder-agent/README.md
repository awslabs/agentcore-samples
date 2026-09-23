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

## What it does, end to end

1. **Create** the agent — harness + `awsSkills` + a builder system prompt
2. **Design** (turn 1) — the agent designs a serverless URL shortener (API GW + Lambda + DynamoDB)
3. **Scaffold** (turn 2, same session) — it writes a real CDK project to the VM filesystem
4. **Inspect** — `ExecuteCommand` lists the files the agent created
5. **Clean up**

## Sample Prompts

**Brief (default)**: "Design a minimal serverless URL shortener on AWS: API Gateway + Lambda + DynamoDB..."
**Expected Behavior**: The agent designs the architecture, then scaffolds a TypeScript CDK app with handler files, README, and package.json under `/tmp/url-shortener`.

**Brief (`-m`)**: "Design and scaffold a CDK app for an S3 + Lambda thumbnail pipeline."
**Expected Behavior**: Same design → scaffold flow for a different serverless use case, drawing on the loaded AWS Skills.

## Key Concepts

**The skill is the difference**: Without `awsSkills`, a small model gives generic answers. With it, the agent applies real AWS best practices and current patterns.

**Multi-turn, one VM**: Design and scaffold run in the same `session_id`, so files from the scaffold step persist and can be inspected.

**Agent acts, not just talks**: The harness's default filesystem + shell tools let the agent write runnable code, not placeholders.

## Clean Up

```python
control.delete_harness(harnessId=harness_id)
from utils.iam import delete_harness_role
delete_harness_role()
```

The script deletes the harness on exit (pass `--skip-cleanup` to keep it).

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
```
