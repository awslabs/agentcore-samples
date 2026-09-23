# 01 — Travel Guide Agent

A full end-to-end Harness agent that doubles as a **tour of every Harness feature** — built around a travel-guide persona that recommends destinations, renders itineraries, and remembers users across conversations.

This is the canonical "read this first" use case — it touches every major capability in one notebook.

## What's in this folder

| File | Type | What it does |
|---|---|---|
| [`01_travel_guide_agent.ipynb`](01_travel_guide_agent.ipynb) | Notebook | Full guided walkthrough — Parts 0-8, each demonstrating a different Harness feature. |

## What you'll build

A travel-guide agent that:

- Generates **self-contained HTML itineraries** (with inline CSS/JS) for any destination
- **Renders them inline** in the notebook via `ExecuteCommand` + iframe
- Emits **automatic traces** to CloudWatch (browsable in X-Ray console)
- **Remembers users** across sessions via AgentCore Memory
- Uses a **headless browser** to pull live weather data
- Combines **Exa MCP search + Code Interpreter** to produce a data-driven tourism report with a matplotlib chart
- Powers a **local chat web app** (FastAPI + SSE streaming + vanilla JS front-end)
- Leverages **Agent Skills** (Anthropic's `xlsx` skill) to generate a real Excel budget spreadsheet

## Notebook walkthrough

| Part | Feature | What happens |
|---|---|---|
| **0** | Setup | Create IAM execution role, configure boto3 clients, load beta service models |
| **1** | Create Harness | Control plane: `create_harness` → poll until `READY` |
| **2** | Invoke + HTML render | Data plane: `invoke_harness` → agent writes HTML → pull back via `ExecuteCommand` → render inline in notebook |
| **3** | Observability | Check Transaction Search is enabled → open CloudWatch X-Ray console to see the full agent trace |
| **4** | Memory | Create Memory instance → attach to Harness → multi-turn conversation where the agent remembers name + preferences across invocations |
| **5** | Browser Tool | `tools=[{"type": "agentcore_browser"}]` → agent navigates a weather site → produces live weather HTML |
| **6** | Exa + Code Interpreter | Multi-tool invocation: Exa for tourism stats → Code Interpreter for matplotlib chart → pull chart back as PNG |
| **7** | Local Chat UI | `%%writefile` to save `server.py` (FastAPI + SSE) and `index.html` → copy service models → run locally |
| **8** | Agent Skills | Install `xlsx` skill via `npx skills add` → invoke with `skills=[...]` → download generated `.xlsx` |

## Prerequisites

- AWS account allowlisted for AgentCore Harness (private beta) in `us-west-2`
- `uv` installed
- `HarnessExecutionRole` will be created automatically via `helper/iam.py`

For **Part 3 (Observability)**: enable [CloudWatch Transaction Search](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Transaction-Search-getting-started.html) once per account.

For **Part 7 (Chat UI)**: `uv` handles the server dependencies via inline script metadata.

## How to run

```bash
cd 02-use-cases/01-travel-agent
jupyter notebook 01_travel_guide_agent.ipynb
# or open in VSCode
```

Run cells top-to-bottom. Each Part is self-contained — you can skip Parts after creating the Harness (Part 1).

### Running the chat UI from Part 7

After Part 7 saves the files, a `travel_chat/` folder appears in `02-use-cases/` (gitignored — it's a generated artifact). To run it:

```bash
cd ../travel_chat
HARNESS_ARN=<from-notebook> REGION=us-west-2 DATA_ENDPOINT=<from-notebook> uv run server.py
# open http://localhost:8000
```

## Cleanup

**Parts 9+10** delete the Harness, Memory instance, and IAM role. Always run them — idle Harnesses and Memory instances accrue charges.

## What to try next

- **Swap the model** — change `bedrockModelConfig.modelId` to `us.anthropic.claude-sonnet-4-6-20251101-v1:0` or `us.anthropic.claude-opus-4-5-20251101-v1:0` and compare quality
- **Swap providers** — use OpenAI or Gemini instead of Bedrock (requires `openAiModelConfig` + API key in Secrets Manager)
- **Add your own tool** — build a custom MCP server and register it as a `remote_mcp` tool in the invoke call
