# Connect Your Agent to Anything

Give your agents access to powerful built-in tool environments — sandboxed code execution, headless browser automation, and real-time web search — managed and scaled by Amazon Bedrock AgentCore.

## Top-level layout

| Folder | What's inside |
|:-------|:--------------|
| [`01-code-interpreter/`](./01-code-interpreter/) | Sandboxed Python execution environment — run code, execute shell commands, upload and read files, use the AWS CLI, all in an isolated per-session sandbox |
| [`02-browser/`](./02-browser/) | Fully managed headless Chromium browser — drive it with Nova Act, Browser-Use, Strands, or raw Playwright via the Chrome DevTools Protocol |
| [`03-web-search/`](./03-web-search/) | Real-time web search as an MCP-compliant tool — ground your agents in current information via AgentCore gateway with zero infrastructure to manage |

## How these tools work

Code Interpreter and Browser follow the same pattern: AgentCore provisions an isolated sandbox session on demand, your agent calls tool APIs within that session, and the session terminates when you stop it. Web Search uses a different pattern — it's exposed as an MCP-compliant connector through AgentCore gateway, so your agent discovers and invokes it via standard MCP protocol calls. All three require no infrastructure to manage.

### Code Interpreter

- **What it is**: A Python 3.12 sandbox with a writable filesystem, shell, and AWS CLI
- **Use it for**: Agents that need to write and run code, perform data analysis, install packages, or make authenticated AWS API calls
- **Entry point**: `from bedrock_agentcore.tools.code_interpreter_client import code_session`

```python
from bedrock_agentcore.tools.code_interpreter_client import code_session

with code_session("us-west-2") as client:
    result = client.invoke("executeCode", {
        "code": "print(2 + 2)",
        "language": "python",
        "clearContext": False,
    })
```

### Browser Tool

- **What it is**: A managed headless Chromium instance accessed over the Chrome DevTools Protocol (CDP)
- **Use it for**: Agents that need to navigate websites, fill forms, extract structured data, or test web apps
- **Entry point**: `from bedrock_agentcore.tools.browser_client import browser_session`

```python
from bedrock_agentcore.tools.browser_client import browser_session

with browser_session("us-west-2") as client:
    ws_url, headers = client.generate_ws_headers()
    # Pass ws_url + headers to Nova Act, Browser-Use, Playwright, or Strands
```

### Web Search Tool

- **What it is**: A fully managed web search connector exposed through AgentCore gateway via MCP
- **Use it for**: Agents that need real-time information — current events, latest releases, fact-checking, competitive intelligence
- **Entry point**: Create a Gateway with `connectorId: "web-search"`, then connect any MCP client

```python
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client

transport = lambda: streamablehttp_client(gateway_url, headers={"Authorization": f"Bearer {token}"})
mcp_client = MCPClient(transport)

with mcp_client:
    tools = mcp_client.list_tools_sync()  # Discovers WebSearch
    result = mcp_client.call_tool_sync("demo", "WebSearch", {"query": "latest AI news"})
```

## Quick Start

```bash
# Code Interpreter
pip install -r 01-code-interpreter/requirements.txt
python 01-code-interpreter/01-file-operations/file_operations.py

# Browser Tool
pip install -r 02-browser/requirements.txt
playwright install chromium
python 02-browser/01-nova-act/getting_started.py \
  --nova-act-key $NOVA_ACT_API_KEY \
  --prompt "Search Amazon for MacBooks"

# Web Search Tool
pip install -r 03-web-search/requirements.txt
python 03-web-search/01-raw-mcp/setup_gateway.py
# Load the credentials written by setup:
source .env.web-search
python 03-web-search/03-strands-agent/web_search_strands.py
```

## Resources

- [Code Interpreter — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-overview.html)
- [Browser Tool — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool-overview.html)
- [AgentCore gateway — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [boto3 Data Plane Reference (`bedrock-agentcore`)](https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore.html)
