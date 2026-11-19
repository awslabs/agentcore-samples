"""Utilities for building live Strands tools from AWS Agent Registry records.

Provides helpers to parse MCP/A2A registry record metadata and create
dynamic tool connections for the Executor agent.
"""

import json
import uuid
from strands import tool


def parse_mcp_metadata(record):
    """Extract connection metadata from an MCP registry record.

    Args:
        record: A registry record dict from get_registry_record.

    Returns:
        dict with keys: url (str), tool_names (list[str])
    """
    descriptors = record.get("descriptors", {})
    mcp = descriptors.get("mcp", {})

    # Parse websiteUrl from server descriptor
    url = ""
    try:
        server_info = json.loads(
            mcp.get("server", {}).get("inlineContent", "{}"))
        url = server_info.get("websiteUrl", "")
    except (json.JSONDecodeError, AttributeError):
        pass

    # Parse tool names from tools descriptor
    tool_names = []
    try:
        tools_info = json.loads(
            mcp.get("tools", {}).get("inlineContent", "{}"))
        tool_names = [t["name"] for t in tools_info.get("tools", [])]
    except (json.JSONDecodeError, AttributeError, KeyError):
        pass

    return {"url": url, "tool_names": tool_names}


def parse_a2a_metadata(record):
    """Extract connection metadata from an A2A registry record.

    Args:
        record: A registry record dict from get_registry_record.

    Returns:
        dict with keys: url (str), skills (list[str])
    """
    descriptors = record.get("descriptors", {})
    a2a = descriptors.get("a2a", {})

    url = ""
    skills = []
    try:
        card = json.loads(
            a2a.get("agentCard", {}).get("inlineContent", "{}"))
        url = card.get("url", "")
        skills = [
            s.get("id", s.get("name", ""))
            for s in card.get("skills", [])
        ]
    except (json.JSONDecodeError, AttributeError):
        pass

    return {"url": url, "skills": skills}


def create_a2a_tool(name, description, agent_arn, skills, ac_data_client):
    """Create a Strands @tool function that invokes an A2A agent.

    The returned function sends an A2A message/send JSON-RPC request via
    invoke_agent_runtime (SigV4 auth) and handles both streaming and
    non-streaming responses.

    Args:
        name:           Tool name (used as the function name for the LLM).
        description:    Tool description shown to the LLM.
        agent_arn:      The AgentCore Runtime ARN of the A2A agent.
        skills:         List of skill names the agent supports.
        ac_data_client: A boto3 bedrock-agentcore data plane client.

    Returns:
        A @tool-decorated callable.
    """
    skill_list = ", ".join(skills) if skills else "general tasks"

    def _add_accept(request, **kwargs):
        request.headers.add_header(
            "Accept", "text/event-stream, application/json")

    @tool
    def a2a_invoke(task: str) -> str:
        """Invoke an A2A agent with a task.
        Args:
            task: The task or question to send.
        Returns:
            The agent's response.
        """
        session_id = str(uuid.uuid4())
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "role": "user",
                    "messageId": str(uuid.uuid4()),
                    "parts": [{"kind": "text", "text": task}]
                }
            }
        })

        ac_data_client.meta.events.register_first(
            "before-sign.bedrock-agentcore.InvokeAgentRuntime",
            _add_accept)
        try:
            resp = ac_data_client.invoke_agent_runtime(
                agentRuntimeArn=agent_arn,
                qualifier="DEFAULT",
                runtimeSessionId=session_id,
                contentType="application/json",
                payload=payload)
        finally:
            ac_data_client.meta.events.unregister(
                "before-sign.bedrock-agentcore.InvokeAgentRuntime",
                _add_accept)

        ct = resp.get("contentType", "")
        body = resp["response"]

        # Streaming SSE response
        if "text/event-stream" in ct:
            texts = []
            for line in body.iter_lines(chunk_size=1):
                if line:
                    line = (line.decode("utf-8")
                            if isinstance(line, bytes) else line)
                    if line.startswith("data: "):
                        try:
                            chunk = json.loads(line[6:])
                            texts.append(
                                chunk if isinstance(chunk, str)
                                else json.dumps(chunk))
                        except Exception:
                            texts.append(line[6:])
            return "\n".join(texts) or "(empty streaming response)"

        # Non-streaming: collect EventStream chunks
        chunks = []
        for event in body:
            chunks.append(
                event.decode("utf-8")
                if isinstance(event, bytes) else str(event))
        raw = "".join(chunks)
        try:
            data = json.loads(raw)
            parts = (data.get("result", {})
                     .get("status", {})
                     .get("message", {})
                     .get("parts", []))
            if not parts:
                parts = data.get("result", {}).get("parts", [])
            texts = [p.get("text", "") for p in parts
                     if p.get("kind") == "text"]
            if texts:
                return "\n".join(texts)
            return json.dumps(data, indent=2)
        except Exception:
            return raw

    a2a_invoke.__name__ = name
    a2a_invoke.__doc__ = (
        f"{description}\n"
        f"Available skills: {skill_list}\n"
        f"Args:\n    task: The task to send.\n"
        f"Returns:\n    The agent's response."
    )
    return a2a_invoke
