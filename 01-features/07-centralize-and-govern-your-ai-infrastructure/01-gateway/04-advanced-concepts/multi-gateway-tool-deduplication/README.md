# Multi-Gateway Tool Deduplication

When building advanced multi-agent architectures, a common pattern is to apply principles of **high cohesion and low coupling** by dedicating specific AgentCore Gateways to distinct domains. For instance, an agent might connect to a `DomainSpecificGateway` (handling specialized business logic) and a `SharedUtilityGateway` (handling common tasks like long-term memory or routing). This prevents code duplication and makes your AI infrastructure easier to maintain. For the sake of generality, throughout the rest of this guide and the code examples, we will refer to these simply as **Gateway A** and **Gateway B**.

However, when integrating multiple AgentCore Gateways into a single agent, you will encounter overlapping tool names. By design, AgentCore Gateways automatically inject built-in infrastructure tools (such as the `x_amz_bedrock_agentcore_search` semantic search tool). 

If you connect to two different gateways (Gateway A and Gateway B), your agent will receive two identical copies of this injected tool. The agent runtime will detect the collision of tool identifiers and raise conflicts or fail during initialization.

## The Problem

Fetching tools from multiple gateways using the standard MCP client pattern might look like this:

```python
with _build_mcp_client(GATEWAY_A_URL) as client_a, \
     _build_mcp_client(GATEWAY_B_URL) as client_b:
     
    gateway_a_tools = get_full_tools_list(client_a)
    gateway_b_tools = get_full_tools_list(client_b)

    # Merging the lists directly creates duplicates!
    all_tools_raw = gateway_a_tools + gateway_b_tools 

    # This will CRASH because of duplicate tool names:
    agent = Agent(
        model=model_instance,
        system_prompt=SYSTEM_PROMPT,
        tools=all_tools_raw
    )
```

Passing `all_tools_raw` directly to the `Agent` constructor will cause the framework's tool registry to crash during initialization:

```log
ERROR | Runtime error: Failed to load tool <strands.tools.mcp.mcp_agent_tool.MCPAgentTool object at 0xffffb0b86cd0>: Tool name 'x_amz_bedrock_agentcore_search' already exists. Cannot register tools with exact same name.

Traceback (most recent call last):
  File "/usr/local/lib/python3.11/site-packages/strands/tools/registry.py", line 124, in add_tool
    self.register_tool(tool)
  File "/usr/local/lib/python3.11/site-packages/strands/tools/registry.py", line 253, in register_tool
    raise ValueError(
ValueError: Tool name 'x_amz_bedrock_agentcore_search' already exists. Cannot register tools with exact same name.
```

## The Solution

By dynamically extracting the tool names and keeping a registry of seen tools, we can safely filter out duplicates before initializing the agent. 

The provided script `deduplicate.py` cleanly deduplicates the tools and logs deep diagnostic warnings to let you inspect the internal properties (`vars()`) of the overlapping tools. When a collision is intercepted, you will see a helpful log detailing exactly which tool is being discarded and its underlying MCP client reference:

```log
🔴 DISCARDED TOOL (Duplicate):
 - Attributes: {'_is_dynamic': False, 'mcp_tool': Tool(name='x_amz_bedrock_agentcore_search', title=None, description='A special tool that returns a trimmed down list of tools...', inputSchema={'type': 'object', 'properties': {'query': {'type': 'string'}}, 'required': ['query']} ...), 'mcp_client': <strands.tools.mcp.mcp_client.MCPClient object at 0xffffaf000cd0>...}
```

### Running the Sample

To see the deduplication logic in action with mock tools:

```bash
python deduplicate.py
```

### Applying in Production

Use the `deduplicate_tools(all_tools_raw)` function right after fetching tools from all your MCP clients and before initializing your agent:

```python
from your_mcp_utils import _build_mcp_client, get_full_tools_list
from deduplicate import deduplicate_tools

with _build_mcp_client(GATEWAY_A_URL) as client_a, \
     _build_mcp_client(GATEWAY_B_URL) as client_b:

    # 1. Fetch tools
    gateway_a_tools = get_full_tools_list(client_a)
    gateway_b_tools = get_full_tools_list(client_b)

    # 2. Merge and deduplicate
    all_tools_raw = gateway_a_tools + gateway_b_tools
    unique_tools = deduplicate_tools(all_tools_raw)

    # 3. Initialize the Agent safely
    agent = Agent(
        model=model_instance,
        system_prompt=SYSTEM_PROMPT,
        tools=unique_tools
    )
```
