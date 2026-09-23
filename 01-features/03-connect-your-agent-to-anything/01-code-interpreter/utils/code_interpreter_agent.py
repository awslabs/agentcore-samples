"""
Shared Strands data-analysis agent using AgentCore Code Interpreter.

Used as the common demo agent across all code interpreter sub-demos:
  - 02-code-execution/code_execution.py
  - 03-data-analysis/data_analysis.py

The agent has a single tool — execute_python — which runs Python code
inside an AgentCore Code Interpreter session managed by `code_session`.
The session is opened per tool call so each demo script can run independently
without managing session lifecycle.
"""

import json
import os

from strands import Agent, tool
from strands.models import BedrockModel

from bedrock_agentcore.tools.code_interpreter_client import code_session

# ── Configuration ─────────────────────────────────────────────────────────────

REGION = os.getenv("AWS_DEFAULT_REGION", "us-west-2")
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")

SYSTEM_PROMPT = """You are a data-analysis assistant that validates every answer
through code execution.

PRINCIPLES:
- Use execute_python for all calculations, data analysis, and logic verification
- Always show your work via actual code execution before stating a conclusion
- The sandbox maintains state between executions within a single demo session
- If asked about data in a file, assume it is already available in the sandbox

TOOL:
- execute_python(code, description) — Run Python code and return structured output

RESPONSE FORMAT:
The tool returns a JSON string with:
  isError       : bool — True if execution failed
  content       : [{type, text}] — human-readable output
  structuredContent.stdout   : captured stdout
  structuredContent.stderr   : captured stderr
  structuredContent.exitCode : process exit code
"""


# ── Tool definition ────────────────────────────────────────────────────────────


@tool
def execute_python(code: str, description: str = "") -> str:
    """Execute Python code in the AgentCore Code Interpreter sandbox.

    Args:
        code: Python source code to execute.
        description: Optional one-line description prepended as a comment.

    Returns:
        JSON string with execution results including stdout, stderr, and exit code.
    """
    if description:
        code = f"# {description}\n{code}"

    print(f"\n[Sandbox] Executing:\n{'-' * 40}\n{code}\n{'-' * 40}")

    with code_session(REGION) as client:
        response = client.invoke(
            "executeCode",
            {
                "code": code,
                "language": "python",
                "clearContext": False,
            },
        )

    for event in response["stream"]:
        result = json.dumps(event["result"])
        return result

    return json.dumps({"isError": True, "content": [{"type": "text", "text": "No result"}]})


# ── Factory ────────────────────────────────────────────────────────────────────


def create_agent() -> Agent:
    """Create and return the shared data-analysis agent."""
    model = BedrockModel(model_id=MODEL_ID)
    return Agent(
        model=model,
        tools=[execute_python],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
    )
