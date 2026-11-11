# [Amazon Bedrock AgentCore Web Answer](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-connector-web-answer-tool.html) with a LangChain agent

This example connects LangChain to an
[Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
with `langchain-mcp-adapters`. The agent discovers the managed `WebAnswer` tool
and uses it for direct factual questions requiring current web information.

![LangChain agent Web Answer architecture](images/langchain-web-search-architecture.png)

## Why include a LangChain example?

The Web Answer connector and Gateway MCP endpoint are framework-agnostic. The
Strands and LangChain samples differ only in how each framework consumes that
same MCP interface:

| Concern | Strands sample | LangChain sample |
|:--|:--|:--|
| MCP integration | Uses Strands `MCPClient` directly as a context manager. | Uses `MultiServerMCPClient` from `langchain-mcp-adapters`. |
| Tool conversion | Passes discovered MCP tools directly to `strands.Agent`. | Converts discovered MCP tools into LangChain tool objects. |
| Agent construction | Uses `strands.Agent` with `BedrockModel`. | Uses LangChain `create_agent` with `ChatBedrockConverse`. |
| Execution model | Runs the agent synchronously. | Uses asynchronous `get_tools` and `ainvoke` calls. |

Use the Strands sample for applications built with the Strands Agents SDK. Use
this sample when an existing LangChain application needs the same managed
`WebAnswer` tool without changing frameworks.

## Prerequisites

- Complete the Gateway setup below.
- Enable access to the configured Bedrock model.
- Install the shared dependencies.

```bash
pip install -r ../requirements.txt
```

## Run

From this directory:

```bash
python setup_gateway.py --gateway-name langchain-web-answer-gw
source .env.web-answer
python web_answer_langchain.py
```

Ask another question:

```bash
python web_answer_langchain.py \
  --question "What is the current long-term support release of Node.js?"
```

Override the model if needed:

```bash
export BEDROCK_MODEL_ID="global.anthropic.claude-sonnet-4-6"
```

## How it works

1. The script obtains a Cognito OAuth token.
2. `MultiServerMCPClient` connects to the Gateway over Streamable HTTP.
3. The adapter converts discovered MCP tools into LangChain tools.
4. `create_agent` builds an agent using `ChatBedrockConverse`.
5. The model invokes Web Answer when it needs a current fact.
6. Web Answer returns a grounded answer with citations for the model to present.

## Files

| File | Description |
|:--|:--|
| `setup_gateway.py` | Delegates to the shared setup utility. |
| `web_answer_langchain.py` | Runs the LangChain agent. |
| `cleanup.py` | Deletes provisioned AWS resources. |
| `../utils/gateway_auth.py` | Handles Gateway OAuth authentication. |

## Cleanup

```bash
python cleanup.py \
  --gateway-id "$GATEWAY_ID" \
  --user-pool-id "$USER_POOL_ID" \
  --role-name "$ROLE_NAME"
```
