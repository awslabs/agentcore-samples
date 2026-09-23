# Claude Managed Agents — self-hosted sandbox on AgentCore runtime

Runs the [Anthropic Claude Managed Agents (CMA) self-hosted sandbox](https://github.com/anthropics/claude-cookbooks/tree/main/managed_agents/self_hosted_sandboxes) on Amazon Bedrock AgentCore Runtime. Anthropic CMA owns the agent loop and the conversation; AgentCore Runtime is the per-session sandbox compute. Each Anthropic session is mapped to one AgentCore runtime session via session affinity (`runtimeSessionId == ANTHROPIC_SESSION_ID`).

## Architecture

```
                                                  ┌────────────────────────────────┐
                                                  │  Anthropic CMA control plane   │
                                                  │  api.anthropic.com             │
   bootstrap.py (one time) ─────────────────────► │  /v1/beta/environments         │
   ANTHROPIC_API_KEY                              │  /v1/beta/agents               │
                                                  │  generate environment_key      │
                                                  └────────────────┬───────────────┘
                                                                   │
                                                                   │ work poll, session
   ┌─────────────────────────────────────┐                         │ SSE, skill download
   │  Host (laptop / EC2)                │ ◄───────────────────────┘
   │                                     │   long-poll, env_key auth
   │  ant beta:worker poll               │
   │     │                               │
   │     │ work item                     │
   │     ▼                               │
   │  on-work.sh → on-work.py            │
   │     │ boto3.invoke_agent_runtime(   │
   │     │   runtimeSessionId =          │
   │     │     ANTHROPIC_SESSION_ID)     │
   └────────────────┬────────────────────┘
                    │
                    ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  AgentCore Runtime                                           │
   │   ┌─────────────────────────────────────────────────────┐    │
   │   │  microVM  (one per Anthropic session)               │    │
   │   │   server.py @app.entrypoint                         │    │
   │   │      └─ subprocess: ant beta:worker run ────────────┼────┼──► api.anthropic.com
   │   │            attach SSE, run tools in /workspace      │    │    (event stream)
   │   │            exit 60s after end_turn idle             │    │
   │   └─────────────────────────────────────────────────────┘    │
   └──────────────────────────────────────────────────────────────┘
```

```
deploy.py creates:
  IAM execution role
  AgentCore runtime (PUBLIC network mode, container from ECR)
```

## Prerequisites

### Python environment

```bash
uv venv --python 3.13 .venv
source .venv/bin/activate
uv pip install -r requirements-host.txt
```

### Anthropic CMA access

You need an Anthropic API key with the `managed-agents-2026-04-01` beta enabled on your account. The bootstrap script uses the API key once to create a self-hosted environment and an agent; afterwards every component runs on the **environment key** alone (no org API key reaches the host poller or the runtime).

### `ant` CLI

The host poller needs the `ant` CLI on the host (the same build is also installed inside the container image). Install:

```bash
VERSION=1.9.1
OS=$(uname -s | tr '[:upper:]' '[:lower:]')
ARCH=$(uname -m | sed -e 's/x86_64/amd64/' -e 's/aarch64/arm64/')

# linux: tarball
curl -fsSL "https://github.com/anthropics/anthropic-cli/releases/download/v${VERSION}/ant_${VERSION}_${OS}_${ARCH}.tar.gz" \
  | sudo tar -xz -C /usr/local/bin ant

# macOS: zip with `macos_<arch>.zip`
# curl -fsSL "https://github.com/anthropics/anthropic-cli/releases/download/v${VERSION}/ant_${VERSION}_macos_${ARCH}.zip" \
#   -o /tmp/ant.zip && unzip -p /tmp/ant.zip ant | sudo tee /usr/local/bin/ant >/dev/null && sudo chmod +x /usr/local/bin/ant

ant --version
```

## Step-by-step guide

### Step 1 — Bootstrap the Anthropic environment and agent

Create the self-hosted environment and a Claude agent. After the script creates the environment it asks you to open the Console and paste the generated environment key (key generation is Console-only).

```bash
export ANTHROPIC_API_KEY=sk-ant-api03-...
python3 bootstrap.py
```

Outputs `bootstrap.config` with `ANTHROPIC_ENVIRONMENT_ID`, `ANTHROPIC_ENVIRONMENT_KEY`, and `ANTHROPIC_AGENT_ID`. The API key is no longer needed for any of the next steps.

The default model is `claude-haiku-4-5`; override with `ANTHROPIC_AGENT_MODEL=claude-sonnet-4-6` (or any other supported model) before running.

### Step 2 — Infrastructure setup (ECR + image)

Create the ECR repository, build the arm64 image, and push it.

```bash
./setup.sh us-west-2
```

Outputs `envvars.config` (region, agent name, ECR URI) used by `deploy.py`.

### Step 3 — Deploy the runtime

Create the IAM execution role and the AgentCore runtime:

```bash
python3 deploy.py
```

The script waits until the runtime status is `READY` and saves `runtime_config.json` with the runtime ARN.

### Step 4 — Start the host poller

The host poller long-polls Anthropic CMA for work items and forwards each one to AgentCore Runtime via `invoke_agent_runtime`. There is no public webhook to expose; everything runs against your AWS credentials and the environment key.

```bash
source bootstrap.config
export AGENTCORE_RUNTIME_ARN=$(python3 -c 'import json; print(json.load(open("runtime_config.json"))["runtime_arn"])')
export AGENTCORE_REGION=us-west-2
./start.sh
```

Leave it running. Expected log order on the first turn:

```
[start]    polling env=env_... runtime=arn:aws:bedrock-agentcore:us-west-2:...
[poller]   claimed work session=sesn_...
[on-work]  session=sesn_... -> invoke_agent_runtime
... AgentCore microVM logs (CloudWatch):
   [server]   runtimeSessionId=sesn_...-000 anthropicSessionId=sesn_...
   ant beta:worker run JSON: executing tool: bash, dispatched tool ...
[on-work]  session=sesn_... done in N.Ns status=200
```

### Step 5 — Drive a session

Create a session and send a user message (these calls go directly to the Anthropic API; the AWS side serves them via the worker pipeline you started above).

```python
# drive.py
import os, anthropic
c = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

s = c.beta.sessions.create(
    agent=os.environ["ANTHROPIC_AGENT_ID"],
    environment_id=os.environ["ANTHROPIC_ENVIRONMENT_ID"],
)
print("session:", s.id)

c.beta.sessions.events.send(
    session_id=s.id,
    events=[{
        "type": "user.message",
        "content": [{"type": "text", "text": "Echo 'hello' via bash, then list /workspace."}],
    }],
)
```

```bash
ANTHROPIC_API_KEY=sk-ant-api03-... python3 drive.py
```

Both runs are visible in CloudWatch under
`/aws/bedrock-agentcore/runtimes/<runtime-id>-DEFAULT`.

#### Sample prompts

Plug any of these into the `text` field above to exercise the bash / file tools end-to-end. The microVM's `/workspace` is empty at the start of each session, so the first prompt scaffolds whatever the next prompts depend on.

- `Echo "hello from CMA on AgentCore" via bash, then list /workspace.`
- `Create /workspace/fizzbuzz.py that prints the FizzBuzz numbers from 1 to 30, then run it with python3 and show the output.`
- `Initialize a tiny Python project under /workspace/calc: __init__.py, calc.py with add/sub/mul/div, and a pytest in test_calc.py covering happy paths and a divide-by-zero case. Run pytest and confirm it passes.`
- `Read /workspace/fizzbuzz.py, refactor the FizzBuzz function to take an upper bound argument, and re-run it with bound=15. Show me the diff.`

### Step 6 — Inspect the running session via TUI

While the agent is working, you can attach an operator shell to the *same* microVM via [`InvokeAgentRuntimeCommand`](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-execute-command.html) by reusing the Anthropic session id as `runtimeSessionId` (session affinity routes both calls to the same VM). This is the part the existing `docker/`, `modal/`, `cf/`, etc. variants of the upstream self-hosted sandbox cannot offer — there is no operator surface into the per-session container.

**One-shot HTTP streaming** (`tui_exec.py`) — drop into `/workspace` and look at what the agent just produced, without disturbing the agent loop. The script streams stdout / stderr / exit deltas in real time.

```bash
python3 tui_exec.py --runtime-session-id "$SESSION_ID" -- "ls -la /workspace"
python3 tui_exec.py --runtime-session-id "$SESSION_ID" -- "cat /workspace/fizzbuzz.py"
python3 tui_exec.py --runtime-session-id "$SESSION_ID" -- "pytest /workspace -v"
```

`AGENTCORE_RUNTIME_ARN` and `AGENTCORE_REGION` are picked up from the environment, same as `start.sh`. `InvokeAgentRuntimeCommand` runs each call as a single argv (no shell expansion), so for pipelines or redirects wrap in `bash -c`:

```bash
python3 tui_exec.py --runtime-session-id "$SESSION_ID" -- bash -c 'ls /workspace && cat /workspace/result.json'
```

**Interactive PTY over WebSocket** (`tui_shell.py`) — full bash session into the same microVM, with line editing, Ctrl+C, and resize. This uses [`InvokeAgentRuntimeCommandShell`](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-execute-command.html) via the `bedrock-agentcore` SDK's `AgentCoreRuntimeClient.open_shell()`:

```bash
python3 tui_shell.py --runtime-session-id "$SESSION_ID"
```

Useful when you want to grep around `/workspace`, run partial tests, or watch a file change while the agent edits it. `tui_shell.py` prints the `shellId` it opened; pass it back with the same `--runtime-session-id` to reconnect to that PTY (the service replays up to 256 KB of buffered output, and up to 10 concurrent shells per runtime are allowed):

```bash
python3 tui_shell.py --runtime-session-id "$SESSION_ID" --shell-id <shellId>
```

> **Interactive shells require a runtime created or updated on/after 2026-06-05.** `InvokeAgentRuntimeCommandShell` (and `open_shell()`) only works on runtimes at the post-launch platform version; older runtimes error on connect. If you deployed earlier, re-run `deploy.py` or `aws bedrock-agentcore-control update-agent-runtime …` against the same image to roll the runtime to a new version. The one-shot `tui_exec.py` path (`InvokeAgentRuntimeCommand`) is unaffected. The caller needs `bedrock-agentcore:InvokeAgentRuntimeCommandShell` (interactive) and `bedrock-agentcore:InvokeAgentRuntimeCommand` (one-shot) on the runtime ARN.

The TUI clients use the standard AWS SDK / SigV4 — no Anthropic credentials reach them. They run purely in the AWS data plane: AWS auth, AgentCore session affinity, container PTY.

> **Window:** `/workspace` lives inside the microVM and is evicted when the microVM is reclaimed (60s after `session.status_idle` with `stop_reason: end_turn`, sooner if the runtime needs the slot). Operator inspection has to happen while the agent is mid-turn or within that idle window. For persistent `/workspace` across turns, attach an EFS or S3 Files mount (see [`02-claude-code-with-efs`](../02-claude-code-with-efs) / [`01-claude-code-with-s3-files`](../01-claude-code-with-s3-files)).

### Step 7 — Cleanup

Delete the AgentCore runtime, IAM execution role, and ECR images. The ECR repository is kept by default; pass `--delete-repo` to drop it as well.

```bash
python3 cleanup.py
# python3 cleanup.py --delete-repo
```

Archive the Anthropic-side resources separately (these are control-plane resources, not AWS):

```bash
ant beta:agents archive --agent-id "$ANTHROPIC_AGENT_ID"
ant beta:environments archive --environment-id "$ANTHROPIC_ENVIRONMENT_ID"
```

## Notes

- **No org API key reaches the runtime.** The container starts only with the environment key (re-exported as `ANTHROPIC_AUTH_TOKEN` so the CLI's skill-download client authenticates).
- **`/workspace` is ephemeral** for the lifetime of one session's microVM. For persistent storage shared across sessions, attach an EFS or S3 Files mount to the runtime — the contract here is unchanged, `ant` always writes to `/workspace`. See [`02-claude-code-with-efs`](../02-claude-code-with-efs) and [`01-claude-code-with-s3-files`](../01-claude-code-with-s3-files) for the mount setup.
- **Container build, not direct code deploy.** The runtime ships the `ant` CLI binary and shells out to it per turn, which requires Container mode on AgentCore Runtime.
