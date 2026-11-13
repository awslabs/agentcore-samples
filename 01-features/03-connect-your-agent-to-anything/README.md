# Connect Your Agent to Anything

Give your agents access to sandboxed code execution, headless browser automation,
current web information, organizational knowledge, and grounded answers through
[Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html).

## Top-level layout

| Folder | What's inside |
|:-------|:--------------|
| [`01-code-interpreter/`](./01-code-interpreter/) | Sandboxed Python execution environment: run code, execute shell commands, upload and read files, and use the AWS CLI in an isolated per-session sandbox. |
| [`02-browser/`](./02-browser/) | Fully managed headless Chromium browser: drive it with Nova Act, Browser-Use, Strands, or raw Playwright through the Chrome DevTools Protocol. |
| [`03-web-search/`](./03-web-search/) | Current web search exposed as an MCP tool: return ranked results for an agent to analyze and synthesize. |
| [`04-fmkb-managed-kb/`](./04-fmkb-managed-kb/) | Amazon Bedrock Managed Knowledge Base exposed through an AgentCore Gateway: retrieve from organizational data with raw MCP or a Strands agent on AgentCore Runtime. |
| [`05-web-answer/`](./05-web-answer/) | Amazon Bedrock AgentCore Web Answer exposed through an AgentCore Gateway: return a finished, grounded answer with citations to current web sources. |

## How these tools work

The samples use two integration patterns:

- **Managed tool sessions:** Code Interpreter and Browser provision isolated
  sessions on demand. An agent calls APIs inside a session and stops the session
  when its work is complete.
- **AgentCore Gateway connectors:** Web Search, Managed Knowledge Bases, and Web
  Answer attach managed connector targets to a Gateway. Any MCP-compatible
  client can discover their tools with `tools/list` and invoke them with
  `tools/call`.

### Code Interpreter

- **What it is:** A Python 3.12 sandbox with a writable filesystem, shell, and AWS CLI.
- **Use it for:** Agents that need to write and run code, perform data analysis, install packages, or make authenticated AWS API calls.
- **Entry point:** `from bedrock_agentcore.tools.code_interpreter_client import code_session`

```python
from bedrock_agentcore.tools.code_interpreter_client import code_session

with code_session("us-west-2") as client:
    result = client.invoke(
        "executeCode",
        {
            "code": "print(2 + 2)",
            "language": "python",
            "clearContext": False,
        },
    )
```

### Browser Tool

- **What it is:** A managed headless Chromium instance accessed through the Chrome DevTools Protocol (CDP).
- **Use it for:** Agents that need to navigate websites, fill forms, extract structured data, or test web applications.
- **Entry point:** `from bedrock_agentcore.tools.browser_client import browser_session`

```python
from bedrock_agentcore.tools.browser_client import browser_session

with browser_session("us-west-2") as client:
    ws_url, headers = client.generate_ws_headers()
    # Pass ws_url and headers to Nova Act, Browser-Use, Playwright, or Strands.
```

### Web Search Tool

- **What it is:** A managed web-search connector exposed through an AgentCore Gateway over MCP.
- **Use it for:** Agents whose model should compare and reason over ranked titles, URLs, snippets, and publication dates.
- **Entry point:** Create a Gateway target with `connectorId: "web-search"`, then connect any MCP client.

```python
from mcp.client.streamable_http import streamablehttp_client
from strands.tools.mcp.mcp_client import MCPClient

transport = lambda: streamablehttp_client(
    gateway_url,
    headers={"Authorization": f"Bearer {token}"},
)
mcp_client = MCPClient(transport)

with mcp_client:
    tools = mcp_client.list_tools_sync()  # Discovers WebSearch
    result = mcp_client.call_tool_sync(
        "demo",
        "WebSearch",
        {"query": "latest AI news"},
    )
```

### Amazon Bedrock Managed Knowledge Base

- **What it is:** A managed knowledge base attached to an AgentCore Gateway as an MCP connector.
- **Use it for:** Agents that need grounded retrieval from organizational documents already ingested into an Amazon Bedrock Managed Knowledge Base.
- **Entry point:** Create a Gateway target with `connectorId: "bedrock-knowledge-bases"` and configure the knowledge base ID.

The sample first validates the Gateway and knowledge-base integration with a raw,
SigV4-signed MCP call. It also includes a Strands agent deployed to AgentCore
Runtime that invokes the same Gateway tool.

### Amazon Bedrock AgentCore Web Answer

- **What it is:** A managed connector that searches current web sources, reads relevant pages, and returns one synthesized answer with citations.
- **Use it for:** Direct factual questions where the agent needs a finished, traceable answer instead of ranked search results.
- **Entry point:** Create a Gateway target with `connectorId: "web-answer"`; MCP clients discover the `WebAnswer` tool.

Web Search and Web Answer are complementary. Choose Web Search when the agent's
model should control source comparison and synthesis. Choose Web Answer when the
agent should receive a completed answer and citations from a single tool call.

## Quick Start

```bash
# Code Interpreter
pip install -r 01-code-interpreter/requirements.txt
python 01-code-interpreter/01-file-operations/file_operations.py

# Browser Tool
pip install -r 02-browser/requirements.txt
playwright install chromium
python 02-browser/01-nova-act/getting_started.py \
  --nova-act-key "$NOVA_ACT_API_KEY" \
  --prompt "Search Amazon for MacBooks"

# Web Search Tool
pip install -r 03-web-search/requirements.txt
python 03-web-search/01-raw-mcp/setup_gateway.py
source 03-web-search/01-raw-mcp/.env.web-search
python 03-web-search/02-strands-agent/web_search_strands.py

# Amazon Bedrock Managed Knowledge Base
pip install -r 04-fmkb-managed-kb/requirements.txt
python 04-fmkb-managed-kb/01-raw-mcp/setup_gateway.py \
  --kb-id "<YOUR_KB_ID>" \
  --name-prefix fmkb-sample
source 04-fmkb-managed-kb/.env.fmkb-gateway
python 04-fmkb-managed-kb/01-raw-mcp/raw_mcp_call.py \
  "What does the knowledge base say about X?"

# Amazon Bedrock AgentCore Web Answer
pip install -r 05-web-answer/requirements.txt
python 05-web-answer/01-raw-mcp/setup_gateway.py
source 05-web-answer/01-raw-mcp/.env.web-answer
python 05-web-answer/01-raw-mcp/raw_mcp_call.py \
  --question "What is the current long-term support release of Node.js?"
```

Each sample README includes its complete prerequisites, supported Regions,
framework-specific examples, and cleanup instructions.

## Resources

- [Code Interpreter — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-overview.html)
- [Browser Tool — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool-overview.html)
- [Web Search Tool — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-connector-web-search-tool.html)
- [Amazon Bedrock Managed Knowledge Bases connector — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-connector-managed-kb.html)
- [Amazon Bedrock AgentCore Web Answer — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-connector-web-answer-tool.html)
- [AgentCore Gateway — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [Boto3 Data Plane Reference (`bedrock-agentcore`)](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore.html)
