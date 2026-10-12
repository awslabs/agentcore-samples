# Agent Orchestration and Context Management

| Information         | Details                                                                       |
|:--------------------|:------------------------------------------------------------------------------|
| Tutorial type       | Use Case                                                                      |
| Agent type          | Research coordinator (multi-agent orchestration)                              |
| Agentic Framework   | None (direct boto3)                                                           |
| LLM model           | Anthropic Claude Sonnet 5                                                     |
| Tutorial components | AgentCore harness: built-in subagent tool, summarization truncation, limits   |
| Example complexity  | Advanced                                                                      |

## Overview

A Research Coordinator that never does the research itself. It plans the work, briefs three
subagents (researcher, synthesizer, reviewer) through the harness's built-in `subagent` tool,
passes each result to the next step, and delivers a sourced report of 1000 words or fewer.

A run like this produces a long conversation: every brief, every tool result and every file
the coordinator reads stays in its history. The harness is configured with a summarization
truncation strategy, so the run keeps going when that history outgrows the model's context
window.

## What is context truncation?

Every model has a context window. An agent loop that calls tools for 15 minutes fills it.
The `truncation` setting on `create_harness` tells the harness what to do when that happens:

| Strategy | What the harness does |
|:--|:--|
| `summarization` | Replaces the oldest part of the conversation with a model-written summary and keeps the most recent messages as they are (used here) |
| `sliding_window` | Drops the oldest messages |
| `none` | No truncation. The conversation grows until it reaches the model's limit |

```python
truncation={
    "strategy": "summarization",
    "config": {"summarization": {"summaryRatio": 0.3, "preserveRecentMessages": 30}},
}
```

`summaryRatio: 0.3` summarizes the oldest 30% of the messages. `preserveRecentMessages: 30`
guarantees the last 30 messages are never summarized, so the coordinator always sees the
current step in full. You can also pass `summarizationSystemPrompt` to control how the summary
is written.

## Architecture

```
[harness resource]
    │  systemPrompt = system_prompt.md   (workflow, 1000-word limit, file rules)
    │  truncation   = summarization      (summaryRatio 0.3, preserveRecentMessages 30)
    │  maxIterations = 100, timeoutSeconds = 3600
    │
    └── invoke_harness("Perform a full research report on ...")
                │
                ▼
        [Research Coordinator]  ── built-in subagent tool ──┐
                │                                           │
                │   Step 1  researcher   → researcher-result.md
                │   Step 2  synthesizer  → synthesizer-result.md
                │   Step 3  reviewer     → reviewer-result.md   (approved / needs revision)
                │   Step 4  one revision cycle, only if needed
                │   Step 5  coordinator  → final-report.md
                │                                           │
                ▼                                           │
        [Firecracker microVM]  ◄── shell, read, write ──────┘
        - All result files live on the session's filesystem
        - Oldest messages are summarized when the context fills up
```

The subagents don't share memory with the coordinator or with each other. The system prompt
makes the coordinator pass everything a subagent needs (topic, scope, length limit, input
content, output file name) in every call.

## Sample Prompts

**Prompt**: "What is the current trending topic in AI?"
**Expected Behavior**: The coordinator sees a simple question and answers directly, with no tool calls. Finishes in about 15 seconds.

**Prompt**: "Perform a full research report on how data shapes innovations in the tech industry. Use specialized subagents to complete this task. In the final output, clearly state which part each subagent was responsible for, followed by their specific findings."
**Expected Behavior**: The coordinator announces the workflow, then calls the researcher, synthesizer and reviewer in order. In the recorded run the reviewer returned "needs revision", so the coordinator ran one revision cycle (researcher, then synthesizer) and delivered a 999-word report with an Open Limitations section. Five subagent calls, 24 tool calls in total, about 15 minutes.

**Prompt**: "Do a deep dive on the trade-offs between open-weight and closed AI models for enterprise use."
**Expected Behavior**: The coordinator runs the full workflow and writes `researcher-result.md`, `synthesizer-result.md`, `reviewer-result.md` and `final-report.md` on the VM.

## Key Concepts

**Built-in subagent tool**: The harness gives the coordinator a `subagent` tool without any `tools` configuration. The roles (researcher, synthesizer, reviewer) are defined entirely in `system_prompt.md`. The script prints each delegation as `[tool: subagent]`.

**Orchestration lives in the system prompt**: The fixed step order, the 1000-word limit, the source and tool-call budgets for the researcher, the 3-turn cap for the synthesizer and the single revision cycle are all instructions, not code. Change the workflow by editing `system_prompt.md`.

**Files as the hand-off between steps**: Each subagent writes `<agent-name>-result.md`, and the coordinator confirms the file exists before moving on. The coordinator then passes the file's content to the next subagent, because subagents can't see each other's context.

**Large tool results are offloaded**: When a tool result is too big to keep in context, the harness moves it out and the coordinator reads it back with `retrieve_offloaded_content`. You'll see that tool in the stream next to `subagent`, `shell`, `read` and `write`.

**Execution limits sized for the job**: `maxIterations=100` and `timeoutSeconds=3600` leave room for the full workflow. The data plane client uses a 3600s `read_timeout` because the stream stays open for the whole run, and retries are turned off because a retried `invoke_harness` starts the research again from the beginning.

**One session per prompt**: Each `invoke` uses a new `runtimeSessionId`, so the research run starts with an empty context.

## Troubleshooting

### Issue: `ConflictException` when you run the script a second time
**Solution**: `HARNESS_NAME` is fixed, so a harness from an earlier run still holds the name. Delete it first (see [Clean Up](#clean-up)) or change `HARNESS_NAME`.

### Issue: `ReadTimeoutError` part-way through the research run
**Solution**: The stream stays open for the whole run, about 15 minutes here. The script raises `read_timeout` to 3600s. If you reuse `invoke()` elsewhere, keep that `Config` on the data plane client.

### Issue: The research run takes too long
**Solution**: Tighten the budgets in `system_prompt.md`: fewer sources and tool calls for the researcher, fewer turns for the synthesizer. Without those caps an earlier version of this workflow ran for more than 20 minutes for a 1000-word report. If a run genuinely needs longer, raise `maxIterations` and `timeoutSeconds` on `create_harness`.

### Issue: The coordinator answers the research prompt directly instead of delegating
**Solution**: The system prompt runs the workflow when the request asks for a full research, a deep dive or a comprehensive analysis. Use one of those phrases, or say "use specialized subagents" as the sample prompt does.

### Issue: The result files end up in different directories
**Solution**: Each subagent resolves a relative file name from its own working directory. In the recorded run `synthesizer-result.md` landed in `/home/` and the other files in `/home/user/`. The coordinator still found all of them. If you need them in one place, give absolute paths in `system_prompt.md`.

## AgentCore CLI

Create and deploy a harness project via the CLI:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
agentcore create --name myresearchcoordinator --model-provider bedrock
agentcore deploy
agentcore invoke --harness myresearchcoordinator \
  --session-id "$(uuidgen)" \
  "Perform a full research report on how data shapes innovations in the tech industry."
```

The truncation strategy and the coordinator system prompt are set programmatically with `create_harness`, as shown in this tutorial.

## Clean Up

The script leaves the harness running so you can inspect it. Delete it when you're done. The harness ID is the part after `harness/` in the ARN the script prints.

```python
import boto3
# Use the same profile and region as the script
session = boto3.Session(profile_name="agent", region_name="us-west-2")
control = session.client("bedrock-agentcore-control")
control.delete_harness(harnessId="<harness-id>")

# Only if you created the role with utils/iam.py (see below)
from utils.iam import delete_harness_role
delete_harness_role()
```

## Running the Python Scripts

```bash
pip install -r ../../requirements.txt
```

These dependencies are only for running the script on your machine. You don't deploy anything to the harness: its runtime already includes everything the agent needs.

The harness needs an IAM execution role with access to Amazon Bedrock and to the harness's managed memory. You can create one with the shared helper, run from `01-features/01-harness/`:

```bash
python -c "from utils.iam import create_harness_role; print(create_harness_role())"
```

At the top of the script, set the printed ARN as `EXECUTION_ROLE_ARN` and set `AWS_PROFILE` to the AWS CLI profile you want to use. The script passes that profile to boto3 directly, so the `AWS_PROFILE` environment variable has no effect. Change `REGION` too if you don't use `us-west-2`. Then run the script:

```bash
python research_coordinator_harness_truncation.py
```

The full run takes about 15 minutes. The research files are written to the agent's VM, and the final report is also printed to your terminal.
