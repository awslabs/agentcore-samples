# Host Your Agent

Deploy AI agents and MCP tool servers on Amazon Bedrock AgentCore — a secure, serverless hosting environment with session isolation, multi-protocol support (HTTP, MCP, A2A, AG-UI), and extended runtime for async agents.

## Top-level layout

| Folder | What's inside |
|:-------|:--------------|
| [`01-runtime/`](./01-runtime/) | Deploy and host agents (HTTP, A2A, AG-UI) and MCP tool servers; advanced capabilities: streaming, sessions, async, multi-agent, VPC, middleware, and more |

## How this section is organized

1. **Runtime** — how you deploy and host your agent or tool server as a scalable service on AgentCore Runtime microVMs.

> **Built-in tools (Code Interpreter, Browser Tool)** are covered in [`03-connect-your-agent-to-anything/`](../03-connect-your-agent-to-anything/).

Inside `01-runtime/`, the sub-tree is organized by concern:

- `01-hosting-agents/` — agent deployment across protocols (HTTP, A2A, AG-UI) and frameworks (Strands, LangGraph, CrewAI, Java, TypeScript)
- `02-hosting-tools/` — MCP server deployment (basics + full feature set)
- `03-advanced/` — streaming, sessions, async, execute-commands, multi-agent, VPC, middleware, and MCP auth
- `04-coding-agents/` — Claude Code agents on AgentCore with persistent S3 or EFS filesystems

## Deployment model

All runtime samples deploy using **direct code deployment**: your Python files are zipped with pre-compiled `aarch64-manylinux2014` wheels (AgentCore runs on Graviton) and uploaded to S3. Each sample's `deploy.py` automates this using `uv` to fetch the correct wheels. Alternatively you can deploy Docker containers via ECR.

```
deploy.py  →  zip + S3 upload  →  create_agent_runtime()  →  status: READY
                                →  create_agent_runtime_endpoint()  →  status: READY
invoke.py  →  invoke_agent_runtime()
cleanup.py →  delete endpoint + runtime + S3 + IAM
```

## Supported protocols

| Protocol | `serverProtocol` value | Agent listens on | Best for |
|:---------|:----------------------|:-----------------|:---------|
| HTTP | `HTTP` | `POST /invocations` port 8080 | Standard request/response agents |
| MCP | `MCP` | `POST /mcp` port 8000 (JSON-RPC) | Tool servers for LLM tool use |
| A2A | `A2A` | `POST /` port 8080 + agent card | Agent-to-agent orchestration |
| AG-UI | `AGUI` | `POST /invocations` + `GET /ws` port 8080 | Real-time streaming UIs |

## Finding things

- **Start with HTTP agents** → `01-runtime/01-hosting-agents/01-http-protocol/01-strands-bedrock/` — every deploy parameter explained
- **By protocol** → `01-hosting-agents/01-http-protocol/`, `02-a2a-protocol/`, `03-ag-ui-protocol/`
- **By framework** → sub-folders named `with-strands/`, `with-langgraph/`, `with-crewai/`, `05-java-agents/`, `07-typescript-agents/`
- **MCP tool servers** → `01-runtime/02-hosting-tools/`
- **Advanced capabilities** (streaming / sessions / async / VPC / middleware) → `01-runtime/03-advanced/`
- **Coding agents** (Claude Code + S3/EFS) → `01-runtime/04-coding-agents/`
- **Code execution sandbox** → [`../03-connect-your-agent-to-anything/01-code-interpreter/`](../03-connect-your-agent-to-anything/01-code-interpreter/)
- **Browser automation** → [`../03-connect-your-agent-to-anything/02-browser/`](../03-connect-your-agent-to-anything/02-browser/)

## AgentCore CLI

The AgentCore CLI (`@aws/agentcore`) is the fastest way to scaffold and deploy an agent to AgentCore Runtime. Install it:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
```

### Create a new project and deploy

```bash
# Scaffold from scratch (interactive)
agentcore create

# Or add a BYO agent to an existing project
agentcore add agent \
  --name MyAgent \
  --type byo \
  --code-location app/MyAgent \
  --entrypoint main.py \
  --language Python \
  --protocol HTTP

# Deploy all resources to AWS
agentcore deploy

# Check deployment status
agentcore status
```

### Invoke the deployed agent

```bash
agentcore invoke --prompt "Hello, what can you help me with?"
```

### Deploy an MCP tool server

```bash
agentcore add agent \
  --name MyMCPServer \
  --type byo \
  --code-location app/MyMCPServer \
  --entrypoint server.py \
  --language Python \
  --protocol MCP

agentcore deploy
```

### Deploy with A2A or AG-UI protocol

```bash
agentcore add agent \
  --name MyA2AAgent \
  --type byo \
  --code-location app/MyA2AAgent \
  --entrypoint agent.py \
  --language Python \
  --protocol A2A

agentcore deploy
```

### Stream logs from a running agent

```bash
agentcore logs --follow
```

> **Note:** The samples in this section use `deploy.py` / `invoke.py` / `cleanup.py` scripts directly
> (Mode A), which gives full control over IAM, S3, and runtime parameters. The CLI is an alternative
> that manages this infrastructure automatically.

## Resources

- [AgentCore Runtime — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)
- [boto3 Control Plane Reference (`bedrock-agentcore-control`)](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control.html)
- [boto3 Data Plane Reference (`bedrock-agentcore`)](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore.html)

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — required for building arm64 deployment packages
- AWS account with Amazon Bedrock AgentCore access
- AWS CLI configured with credentials

## Running the Python Scripts

Each runtime sample follows the same three-script pattern:

```bash
pip install boto3 bedrock-agentcore

# Deploy an agent to AgentCore Runtime
python 01-runtime/01-hosting-agents/01-http-protocol/01-strands-bedrock/deploy.py

# Invoke it
python 01-runtime/01-hosting-agents/01-http-protocol/01-strands-bedrock/invoke.py

# Clean up
python 01-runtime/01-hosting-agents/01-http-protocol/01-strands-bedrock/cleanup.py
```

For built-in tools (Code Interpreter and Browser), see [](../03-connect-your-agent-to-anything/).
