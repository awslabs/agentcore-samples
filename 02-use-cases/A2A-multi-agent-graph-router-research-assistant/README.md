# A2A Multi-Agent Graph Router Research Assistant

| Key | Value |
|-----|-------|
| **Description** | Router-supervisor pattern that dynamically dispatches parallel sub-agents using Strands Agents GraphBuilder with conditional edges and AND-join semantics |
| **Architecture** | Router → conditional dispatch → parallel workers → AND-join → synthesizer |
| **Framework** | [Strands Agents](https://github.com/strands-agents/strands-agents) |
| **Domain** | Research & Report Generation |
| **Deployment modes** | Local demo · Distributed A2A protocol |
| **Language** | Python 3.13+ |

## Architecture

```
                    ┌─────────┐
                    │  Router  │  (classifies request, outputs DISPATCH line)
                    └────┬────┘
          ┌──────────────┼──────────────┐
          │              │              │
   (needs research?) (needs analysis?) (no workers?)
          │              │              │
          ▼              ▼              │
    ┌──────────┐  ┌───────────┐        │
    │ Research  │  │ Analysis  │  ← parallel execution
    └─────┬────┘  └─────┬─────┘        │
          │   AND-join   │              │
          └──────┬───────┘              │
                 │                      │
                 └──────────┬───────────┘
                            │
                            ▼
                    ┌──────────────┐
                    │ Synthesizer  │  (writer agent — final report)
                    └──────────────┘
```

**Key pattern**: The router outputs a structured `DISPATCH:` line. Conditional edge functions parse it to decide which sub-agents to invoke. Independent sub-agents run in parallel. The synthesizer uses an **AND-join guard** — it only fires when ALL dispatched workers have `Status.COMPLETED`.

### Agents and Models

| Agent | Role | Model | Tools |
|-------|------|-------|-------|
| Router | Classifies request, decides dispatch | Claude Haiku 4.5 | None (LLM-only) |
| Research | Gathers information via search | Claude Haiku 4.5 | `web_search`, `extract_key_points` |
| Analysis | Analyzes data, compares sources | Claude Haiku 4.5 | `analyze_data`, `compare_sources` |
| Writer (Synthesizer) | Produces final report | Claude Sonnet 4.6 | `draft_section`, `format_report` |

Tools are functional stubs returning structured placeholder data. Comments in each tool indicate where to plug in real implementations (Tavily, Brave Search, etc.).

## Project Structure

```
├── agents/
│   ├── __init__.py
│   ├── research_agent.py         # Research sub-agent + tools
│   ├── analysis_agent.py         # Analysis sub-agent + tools
│   └── writer_agent.py           # Writer/synthesizer sub-agent + tools
├── graph/
│   ├── __init__.py
│   ├── router_agent.py           # Router agent (classifies & dispatches)
│   ├── conditions.py             # Conditional edge functions + AND-join
│   └── supervisor_graph.py       # GraphBuilder assembly
├── deployment/
│   ├── README.md
│   ├── sigv4_auth.py             # Boto3 transport + SigV4A2AAgent for cross-runtime calls
│   ├── agentcore_*.py            # AgentCore entrypoints (A2A sub-agents + HTTP supervisor)
│   ├── deploy.py                 # Deploy all 4 agents to AgentCore
│   ├── cleanup.py                # Destroy all 4 agents + IAM roles
│   ├── serve_*.py                # Local A2A servers (no AgentCore needed)
│   └── main.py                   # Local supervisor CLI
├── demo/
│   └── run_demo.py               # Local CLI demo (no AgentCore needed)
├── images/
│   └── architecture.excalidraw   # Architecture diagram source
├── requirements.txt
└── pyproject.toml
```

## Prerequisites

- Python 3.13+
- AWS credentials configured for Amazon Bedrock model access (Claude Haiku 4.5, Claude Sonnet 4.6)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Setup

```bash
git clone https://github.com/awslabs/amazon-bedrock-agentcore-samples.git
cd amazon-bedrock-agentcore-samples/02-use-cases/A2A-multi-agent-graph-router-research-assistant

# Install dependencies
uv sync
# or
pip install -r requirements.txt
```

## Quick Start — Local Demo

No AgentCore dependency required. Runs the full graph locally:

```bash
uv run python -m demo.run_demo --prompt "Research the impact of AI on healthcare and analyze the trends"
```

JSON output:

```bash
uv run python -m demo.run_demo --prompt "Research the impact of AI on healthcare" --json
```

### Dispatch Paths

The router dynamically decides which workers to invoke based on the request:

| Request type | Dispatch | Example |
|---|---|---|
| Research + analysis needed | `research,analysis,writer` | "Research AI in healthcare and analyze the trends" |
| Research only | `research,writer` | "What are the latest developments in quantum computing?" |
| Analysis only | `analysis,writer` | "Analyze the provided sales data and identify patterns" |
| Direct synthesis | `writer` | "Write a brief summary of cloud computing" |

## Deployment to AgentCore

Each sub-agent runs on its own [Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/) runtime using the A2A protocol. The supervisor runs on a separate runtime (HTTP) and calls sub-agents via boto3's `invoke_agent_runtime` (which handles IAM SigV4 signing automatically).

There is no CDK or CloudFormation — deployment is handled by the [`bedrock-agentcore-starter-toolkit`](https://pypi.org/project/bedrock-agentcore-starter-toolkit/) Python SDK.

### How deployment works

`deployment/deploy.py` performs these steps in order:

1. **Deploys 3 sub-agents** (research, analysis, writer) as A2A protocol runtimes using the starter toolkit's `Runtime` class (`runtime.configure()` + `runtime.launch()`)
2. **Captures each sub-agent's ARN** from the generated `.bedrock_agentcore.yaml` config and converts it to a runtime URL
3. **Deploys the supervisor** as an HTTP protocol runtime, injecting sub-agent URLs as environment variables (`RESEARCH_AGENT_URL`, `ANALYSIS_AGENT_URL`, `WRITER_AGENT_URL`)
4. **Grants IAM permissions** — attaches an inline policy to the supervisor's execution role allowing `bedrock-agentcore:InvokeAgentRuntime` on all runtimes in the account (via `boto3` IAM client)

Each agent gets its own auto-created IAM execution role (`AmazonBedrockAgentCoreSDKRuntime-<region>-<hash>`).

### Deploy

```bash
uv run python -m deployment.deploy
# Optional: specify region
uv run python -m deployment.deploy --region us-east-1
```

### Invoke

```bash
uv run agentcore invoke -a supervisor_a2a '{"prompt": "Research the impact of AI on healthcare"}'
```

### Destroy

`deployment/cleanup.py` tears down all 4 agent runtimes and their auto-created IAM execution roles. It uses the AWS API directly (no dependency on `.bedrock_agentcore.yaml`), so it works even if local config was lost.

```bash
uv run python -m deployment.cleanup
# Optional: specify region
uv run python -m deployment.cleanup --region us-east-1
```

### Local development (no AgentCore needed)

You can run the full distributed architecture locally without deploying to AgentCore:

```bash
# Terminal 1-3: Start sub-agent A2A servers
uv run python -m deployment.serve_research   # :9001
uv run python -m deployment.serve_analysis   # :9002
uv run python -m deployment.serve_writer     # :9003

# Terminal 4: Run the supervisor (connects to localhost)
uv run python -m deployment.main --prompt "Research the impact of AI on healthcare"
```

See [deployment/README.md](deployment/README.md) for more details.

## How the AND-Join Works

The Strands `GraphBuilder` fires a target node when **any** incoming edge's condition returns `True` (OR semantics). To implement an AND-join:

1. Every edge feeding the synthesizer checks that **all** dispatched workers have `Status.COMPLETED`
2. When research completes first but analysis is still running, `research_to_synthesizer()` returns `False`
3. When analysis then completes, `analysis_to_synthesizer()` returns `True` (both done)
4. The synthesizer fires exactly once

See [`graph/conditions.py`](graph/conditions.py) for the implementation.

## License

This project is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
