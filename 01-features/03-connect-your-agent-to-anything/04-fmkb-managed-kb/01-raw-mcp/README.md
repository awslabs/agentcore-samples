# 01-raw-mcp — verify the gateway path without an agent

The smallest possible test of the FMKB-as-MCP-tool plumbing. Run this first when something's broken — it isolates the gateway/IAM layer from the agent layer.

## What it does

1. **`setup_gateway.py`** — creates an IAM role for the gateway, an AgentCore Gateway, and a KB target pointing at the Managed KB you supply with `--kb-id`. Writes the resource ids to `.env.fmkb-gateway` in the repo root.
2. **`raw_mcp_call.py`** — opens an MCP session against the gateway URL using SigV4-signed streamable HTTP, lists tools (one per gateway target), and calls the `<targetName>___Retrieve` tool with the prompt you pass on the CLI. Prints the raw retrieval results.
3. **`cleanup.py`** — deletes the target, the gateway, and the gateway role. The KB itself is *not* touched.

## Use it

```bash
pip install -r ../requirements.txt
python setup_gateway.py --kb-id <YOUR_KB_ID> --region us-west-2
source ../.env.fmkb-gateway

python raw_mcp_call.py "What does the knowledge base say about cat food?"

python cleanup.py
```

Required IAM on your caller principal: ability to create/get/delete IAM roles in the `bedrock-agentcore-*` prefix, plus AgentCore control-plane access. See [Use the AgentCore CLI](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html#runtime-permissions-cli) for the full policy.
