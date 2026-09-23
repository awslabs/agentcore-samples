"""
Local Supervisor Agent for Lab 05
Runs Strands agent locally from notebook with parameterized gateway URL and access token
"""

from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client
import logging

# Configure logging
logging.getLogger("strands").setLevel(logging.INFO)
logging.basicConfig(format="%(levelname)s | %(name)s | %(message)s", handlers=[logging.StreamHandler()])


def create_mcp_client(gateway_url, access_token):
    """
    Create MCP client with OAuth authentication

    Args:
        gateway_url: Gateway MCP endpoint URL
        access_token: OAuth access token from Cognito

    Returns:
        MCPClient: Configured MCP client
    """
    return MCPClient(lambda: streamablehttp_client(gateway_url, headers={"Authorization": f"Bearer {access_token}"}))


def get_all_tools(mcp_client):
    """
    Retrieve all tools from Gateway with pagination support

    Args:
        mcp_client: MCPClient instance

    Returns:
        list: All available MCP tools
    """
    tools = []
    pagination_token = None

    while True:
        result = mcp_client.list_tools_sync(pagination_token=pagination_token)
        tools.extend(result)

        if result.pagination_token is None:
            break
        pagination_token = result.pagination_token

    return tools


def create_supervisor_agent(model_id, tools, region="us-west-2"):
    """
    Create Strands supervisor agent

    Args:
        model_id: Bedrock model identifier or inference profile ARN
        tools: List of MCP tools
        region: AWS region

    Returns:
        Agent: Configured Strands agent
    """
    system_prompt = """
# Supervisor Agent System Prompt

You are an expert SRE Supervisor Agent that orchestrates three specialized sub-agents to provide complete infrastructure troubleshooting solutions.

## Sub-Agent Tools

### 1. Diagnostic Agent 
- Analyzes AWS infrastructure to identify root causes
- Provides detailed diagnostic information
- Identifies performance bottlenecks and configuration issues

### 2. Infrastructure Agent 
- Executes infrastructure fixes and remediation scripts
- Implements corrective actions with approval workflows
- Uses AgentCore Code Interpreter for secure execution

### 3. Prevention Agent 
- Researches AWS best practices and preventive measures
- Provides proactive recommendations
- Uses AgentCore Browser for real-time documentation

## Orchestration Workflow

For each user request:
1. **Diagnose**: Use diagnostic tools to identify issues
2. **Remediate**: Execute approved remediation steps
3. **Prevent**: Provide preventive recommendations

## Response Structure

Always provide:
- **Summary**: Brief overview of the issue
- **Diagnostic Results**: What was found
- **Remediation Actions**: What was fixed (if applicable)
- **Prevention Recommendations**: How to avoid future issues

## Tool Usage Guidelines

- Use diagnostic tools to analyze and identify problems
- Use remediation tools for fixes (requires approval)
- Use prevention tools for best practices and research
- Coordinate across agents for comprehensive solutions

## CRITICAL - Ensure when calling Infrastructure / Remediation Agent always use only_execute

## Safety Rules

- Always validate environment before making changes
- Require explicit approval for remediation actions
- Provide clear explanations of all actions taken
- Include risk assessments for remediation steps

Note: After every tool call, provide a short summary of what you did with that tool call.
"""

    model = BedrockModel(
        model_id=model_id,
        streaming=True,
    )

    return Agent(model=model, tools=tools, system_prompt=system_prompt)


def run_supervisor_agent(
    gateway_url,
    access_token,
    prompt,
    model_id="us.anthropic.claude-haiku-4-5-20251001-v1:0",
):
    """
    Run supervisor agent with parameterized configuration

    Args:
        gateway_url: Gateway MCP endpoint URL
        access_token: OAuth access token from Cognito
        prompt: User prompt/query for the agent
        model_id: Bedrock model identifier (default: Claude Haiku 4.5)

    Returns:
        str: Agent response text
    """
    try:
        mcp_client = create_mcp_client(gateway_url, access_token)

        with mcp_client:
            tools = get_all_tools(mcp_client)
            print(f":white_check_mark: Retrieved {len(tools)} tools from gateway")

            agent = create_supervisor_agent(model_id, tools)
            print(f":white_check_mark: Created supervisor agent with model: {model_id}")
            print(f":robot_face: Processing: {prompt}\n")

            response = agent(prompt)

            # Extract text from response
            content = response.message.get("content", [])
            if isinstance(content, list) and len(content) > 0:
                text = content[0].get("text", str(response))
            else:
                text = str(content)

            return text
    except Exception as e:
        print(f":x: Supervisor Agent Failed: {e}")
        raise
