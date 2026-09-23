"""In process MCP server for Code Interpreter."""

from .client import CodeInterpreterClient
from claude_agent_sdk import tool, create_sdk_mcp_server
from typing import Any
import json
import logging

logger = logging.getLogger(__name__)

# Initialize the client
client = CodeInterpreterClient()


@tool(
    "execute_code",
    "Execute code using Code Interpreter.",
    {"code": str, "language": str, "code_int_session_id": str},
)
async def execute_code(args: dict[str, Any]) -> dict[str, Any]:
    result = client.execute_code(
        args.get("code"),
        args.get("language", "python"),
        args.get("code_int_session_id", ""),
    )
    response_text = result.model_dump_json(indent=2)

    return {"content": [{"type": "text", "text": response_text}]}


@tool(
    "execute_command",
    "Execute command using Code Interpreter. ",
    {"command": str, "code_int_session_id": str},
)
async def execute_command(args: dict[str, Any]) -> dict[str, Any]:
    result = client.execute_command(args.get("command"), args.get("code_int_session_id", ""))
    response_text = result.model_dump_json(indent=2)

    return {"content": [{"type": "text", "text": response_text}]}


@tool(
    "write_files",
    "Execute command using Code Interpreter. ",
    {"files_to_create": list, "code_int_session_id": str},
)
async def write_files(args: dict[str, Any]) -> dict[str, Any]:
    files_to_create = args["files_to_create"]
    if isinstance(files_to_create, str):
        files_to_create = json.loads(files_to_create)

    result = client.write_files(files_to_create, args.get("code_int_session_id", ""))
    response_text = result.model_dump_json(indent=2)

    return {"content": [{"type": "text", "text": response_text}]}


@tool(
    "read_files",
    "Execute command using Code Interpreter. ",
    {"paths": list, "code_int_session_id": str},
)
async def read_files(args: dict[str, Any]) -> dict[str, Any]:
    paths = args["paths"]
    if isinstance(paths, str):
        paths = json.loads(paths)
    result = client.read_files(paths, args.get("code_int_session_id", ""))
    response_text = result.model_dump_json(indent=2)

    return {"content": [{"type": "text", "text": response_text}]}


code_int_mcp_server = create_sdk_mcp_server(
    name="codeinterpretertools",
    version="1.0.0",
    tools=[execute_code, execute_command, write_files, read_files],
)
