# Integrate SpecStream with Amazon Bedrock AgentCore Gateway

## Introduction

This sample connects [SpecStream](https://specstream.ai) to Amazon Bedrock AgentCore Gateway in two ways:

1. **OpenAPI target (`openapi`, default)**: Registers SpecStream's Search API (`GET /v1/web-search`) and Extract API (`POST /v1/extract`) on an `AWS_IAM` AgentCore Gateway using an inline OpenAPI 3.0.1 schema. The gateway stores your `SPECSTREAM_API_KEY` in an AgentCore Identity `API_KEY` credential provider and injects `Authorization: Bearer <key>` on outbound requests, so clients only authenticate to the gateway with AWS SigV4.
2. **Remote MCP passthrough (`mcp-passthrough`)**: Fronts SpecStream's Streamable HTTP MCP server (`https://api.specstream.ai/mcp`) through an AgentCore Gateway HTTP passthrough target (`protocolType="MCP"`) with `JWT_PASSTHROUGH`, forwarding the caller's `Authorization` header unchanged.

See the [SpecStream agent reference](https://specstream.ai/agents.md) for endpoint fields, parameters, and pricing ($1 per 1,000 searches with `speed=fast`, $5 per 1,000 default searches, and $0.80 per 1,000 extracted pages; new accounts start with $10 credit).

## Architecture Diagram

### OpenAPI mode (stored API key)

```text
Strands Agent (SigV4)
  |
  v
AgentCore Gateway (AWS_IAM inbound, MCP protocol)
  |-- reads SPECSTREAM_API_KEY from AgentCore Identity Token Vault
  |-- injects Authorization: Bearer <key>
  v
https://api.specstream.ai
  |-- GET /v1/web-search   (webSearch tool)
  |-- POST /v1/extract     (extractPages tool)
```

### MCP passthrough mode

```text
MCP Client (optional Authorization: Bearer <SPECSTREAM_API_KEY>)
  |
  v
AgentCore Gateway (http.passthrough, protocolType=MCP, JWT_PASSTHROUGH)
  |
  v
https://api.specstream.ai/mcp  (search_web, read_pages, and workspace tools)
```

## Prerequisites

- Python 3.10+
- AWS credentials configured (`aws sts get-caller-identity`)
- Bedrock model access enabled for the configured `MODEL_ID` (default: `global.anthropic.claude-haiku-4-5-20251001-v1:0`)
- IAM permissions for `iam`, `bedrock-agentcore:*`, and `bedrock:InvokeModel`
- A SpecStream API key created under **Agents & API** at [specstream.ai/workspace](https://specstream.ai/workspace) (required for `openapi` mode deployment; optional when deploying `mcp-passthrough` or running offline unit tests)

## Usage

### 1. Install dependencies

```bash
cd 03-integrations/gateway/specstream
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
# Edit .env and set SPECSTREAM_API_KEY
```

### 3. Deploy the gateway and target

Preview the generated gateway target payload offline without AWS calls or a key:

```bash
python deploy.py --dry-run
python deploy.py --mode mcp-passthrough --dry-run
```

Deploy the OpenAPI target (requires `SPECSTREAM_API_KEY`):

```bash
python deploy.py
```

Or deploy the remote MCP passthrough target (no key required at deploy time):

```bash
python deploy.py --mode mcp-passthrough
```

### 4. Invoke SpecStream through the gateway

```bash
python invoke.py
```

Or pass a single query:

```bash
python invoke.py --query "What is Amazon Bedrock AgentCore Gateway?"
```

### 5. Run offline unit tests

No AWS account or SpecStream API key is required for the unit test suite:

```bash
python3 -m unittest discover -v
```

## Sample Prompts

When running `invoke.py` in `openapi` mode, the Strands agent executes prompts using `webSearch` and `extractPages`:

- `"Search the web for the latest release notes of Amazon Bedrock AgentCore and cite your sources."`
- `"Extract the main text from https://specstream.ai/agents.md and summarize the Search API tiers."`

## Clean Up

Delete the gateway target, gateway, API key credential provider (if created), IAM role, and `.deploy_state.json`:

```bash
python cleanup.py
```
