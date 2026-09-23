# Getting Started with AgentCore harness

| Information         | Details                                                    |
|:--------------------|:-----------------------------------------------------------|
| Tutorial type       | Getting Started                                            |
| Agent type          | General-purpose assistant                                  |
| Agentic Framework   | None (direct boto3)                                        |
| LLM model           | Anthropic Claude Haiku 4.5 and Claude Sonnet 4.6           |
| Tutorial components | AgentCore harness — Create, Invoke, ExecuteCommand         |
| Example complexity  | Beginner                                                   |

## Overview

This tutorial walks through the complete harness workflow: creating an IAM execution role,
creating a harness agent, invoking it with two different Claude models in the same session,
and using ExecuteCommand to run shell commands directly on the agent's isolated microVM.

## What is AgentCore harness?

Harness helps developers ship agents faster by letting them:

- **Skip framework setup** — no orchestration code, no deployment pipeline
- **Define everything in one API call** — model, system prompt, tools
- **Access a full Linux microVM** — the agent can write files, run code, install packages
- **Switch models mid-session** — different model per invocation, same session state

## Architecture

```
Your code
    │
    ▼
[Control Plane] create_harness / update_harness / delete_harness
    │
    ▼
[harness resource] — READY status
    │
    ▼
[Data Plane] invoke_harness(harnessArn, runtimeSessionId, messages, model)
    │
    ▼
[Firecracker microVM] — isolated per session
    ├── Agent loop (model + tools)
    ├── file_operations tool (read/write files)
    ├── shell tool (run commands)
    └── invoke_agent_runtime_command (ExecuteCommand — bypasses agent loop)
```

## Sample Prompts

**Prompt**: "What are three fun things to do in Seattle on a rainy day? Save your answer to a Markdown file."
**Expected Behavior**: Agent calls the `file_operations` tool to write a Markdown file, then responds with a summary.

**Prompt**: "Create a Python script that prints the Fibonacci sequence up to n=20 and run it."
**Expected Behavior**: Agent writes the script using `file_operations`, then executes it with the `shell` tool and shows the output.

**Prompt**: "What files are in the current directory?"
**Expected Behavior**: Agent uses the `shell` tool to run `ls -la` and reports the contents.

**Prompt**: "What is the Python version available on this machine?"
**Expected Behavior**: Agent runs `python3 --version` via the `shell` tool and reports the version.

## Key Concepts

**Session ID**: A UUID that identifies an isolated Firecracker microVM. Same session ID = same VM (state persists). New session ID = fresh VM.

**ExecuteCommand**: `invoke_agent_runtime_command` bypasses the agent loop and runs imperative shell commands directly on the microVM. Use this to inspect files, check environment state, or run deterministic commands.

**Model switching**: Each `invoke_harness` call specifies a model. You can use different models in the same session — the VM state (files, installed packages) persists between calls.

## Troubleshooting

### Issue: `CREATING` status never transitions to `READY`
**Solution**: Give it a few minutes. A new harness has been observed taking around
150 seconds to reach `READY` on the public network, and longer in VPC network mode
or when a custom container image has to be pulled — so a `CREATING` status well
past the one-minute mark is normal, not a failure. The polling loop in the script
waits up to 10 minutes and prints the status on every poll.

If it genuinely does fail, the harness moves to `CREATE_FAILED` rather than
staying in `CREATING`, and the script raises straight away with the service's
`failureReason` — that message is what tells you the cause (most often a missing
permission on the execution role).

### Issue: `NoSuchEntityException` when creating IAM role
**Solution**: Your AWS credentials may lack IAM permissions. The `create_harness_role()` helper requires `iam:CreateRole`, `iam:PutRolePolicy`, and `iam:UpdateAssumeRolePolicy`. Check your credentials' IAM policy.

### Issue: Tool calls show but no text output
**Solution**: The agent is working but producing output in tool calls, not text. This is normal for task-oriented prompts. The agent's final response will contain text.

## AgentCore CLI

The harness is also accessible via the AgentCore CLI. Install it:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
```

Create and deploy a harness project:

```bash
# Interactive wizard (select "Harness" as project type when prompted)
agentcore create

# Or non-interactive
agentcore create --name myresearchagent --model-provider bedrock
agentcore deploy
```

Invoke the harness:

```bash
agentcore invoke --harness myresearchagent \
  --session-id "$(uuidgen)" \
  "What are three fun things to do in Seattle on a rainy day?"
```

Reuse the same `--session-id` to continue the conversation in the same environment. Run `agentcore dev` to test locally before deploying. Check project status with `agentcore status`.

## Clean Up

```python
# Delete the harness (stops billing for the resource)
control.delete_harness(harnessId=harness_id)

# Delete the IAM role (optional — shared across examples)
from utils.iam import delete_harness_role
delete_harness_role()
```

## Running the Python Scripts

```bash
pip install -r ../requirements.txt
```

```bash
python getting_started.py
```
