# Custom Containers

| Information         | Details                                                          |
|:--------------------|:-----------------------------------------------------------------|
| Tutorial type       | Advanced Example                                                 |
| Agent type          | Coding assistant                                                 |
| Agentic Framework   | None (direct boto3)                                              |
| LLM model           | Anthropic Claude Haiku 4.5                                       |
| Tutorial components | AgentCore harness — Custom Container, ExecuteCommand             |
| Example complexity  | Intermediate                                                     |

## Overview

Attach any custom container image to a harness so the agent runs in your own environment.
Demonstrates Node.js, Go, and Python container presets, plus cross-compilation on Go.

## What is a Custom Container?

By default, harness runs on Amazon Linux 2023 with Python. Custom containers let you bring any Linux ARM64 image:

- Specific language runtimes (Node.js, Go, Rust, Java, Ruby)
- System libraries (Chromium, FFmpeg, ImageMagick)
- Pre-installed dependencies and source code
- A locked-down environment that matches production

## Architecture

```
[harness resource]
    │
    └── update_harness(environmentArtifact.containerConfiguration.containerUri)
                │
                ▼
        [Custom container image]
        e.g. public.ecr.aws/docker/library/node:slim
                │
                ▼
        [Firecracker microVM]
        - Whatever the image ships (e.g. node + npm, or the Go toolchain)
        - Agent can install packages and run servers
        - Same session ID = same VM state persists
```

> **Only what the image ships is available.** Minimal base images omit tools you
> may take for granted: `python:3.12-slim` has neither `curl` nor `wget`, and
> `golang:1.24` has no `file`. Prefer language built-ins (Node's `http`, Python's
> `urllib`) over shelling out, or install the tool first.

## Sample Prompts

**Prompt (node)**: "Write a Node.js HTTP server on port 3000 that returns JSON with the current time. Test it, then kill the server."
**Expected Behavior**: Agent creates `server.js`, starts it in background, makes an HTTP request, shows JSON output, kills server.

**Prompt (go)**: "Write a Go HTTP server, build it, run it, and curl it."
**Expected Behavior**: Agent initializes a Go module, writes `main.go`, runs `go build`, starts binary, curls it, shows response.

**Prompt (node/npm)**: "Install chalk and write a colorful banner script."
**Expected Behavior**: Agent runs `npm install chalk`, writes `colors.js`, executes it showing colored output.

**Prompt (python)**: "Write a Python HTTP server on port 3000 that returns JSON with the current time. Test it, then kill the server."
**Expected Behavior**: Agent creates `/tmp/server.py` using `http.server`, starts it in background, makes a request with `urllib` (the `python:3.12-slim` image has no `curl` or `wget`), shows JSON output, kills server.

**Prompt (go/cross-compile)**: "Cross-compile for linux/amd64 and show the file info."
**Expected Behavior**: Agent sets `GOOS=linux GOARCH=amd64`, runs `go build -o goserver_linux_amd64`, shows the size with `ls -lh` and the architecture with `readelf -h` (the `golang:1.24` image has no `file` binary).

## Key Concepts

**Container update timing**: After `update_harness`, wait for the harness to return to `READY` status before invoking (the update is async).

**Session isolation**: Each `runtimeSessionId` gets a fresh VM. After updating the container, use a new session ID to get a VM with the updated image.

**ARM64 requirement**: Container images must support `linux/arm64`. All `public.ecr.aws/docker/library/` official images include ARM64 variants.

## Troubleshooting

### Issue: Agent can't find `node` command
**Solution**: The container update may not be complete yet. Ensure `get_harness` returns `READY` before invoking. Also use a new session ID after the container update.

### Issue: `npm install` fails with network errors
**Solution**: Outbound internet access from the microVM depends on your account's network configuration. If using VPC, ensure appropriate routes exist.

### Issue: Go compilation fails with "module not found"
**Solution**: Ensure the agent initializes a Go module (`go mod init`) before writing source files. The script prompt explicitly asks for this.

## AgentCore CLI

Custom container configuration is supported via the AgentCore CLI:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
agentcore create --name mycontaineragent --model-provider bedrock
```

The interactive wizard lets you choose **Custom Environment** and specify a container URI. After configuring:

```bash
agentcore deploy
agentcore invoke --harness mycontaineragent \
  --session-id "$(uuidgen)" \
  "Write a Node.js HTTP server and test it."
```

## Clean Up

```python
control.delete_harness(harnessId=harness_id)
from utils.iam import delete_harness_role
delete_harness_role()
```

## Running the Python Scripts

```bash
pip install -r ../../requirements.txt
```

```bash
# Node.js preset (default)
python custom_container.py

# Go preset
python custom_container.py --language go

# Python preset
python custom_container.py --language python

# Any container image
python custom_container.py --container public.ecr.aws/docker/library/rust:slim

# Keep resources for inspection
python custom_container.py --skip-cleanup
```
