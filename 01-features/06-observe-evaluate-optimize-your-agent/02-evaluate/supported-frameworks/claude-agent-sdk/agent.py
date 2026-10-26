"""
HR Assistant Agent — Claude Agent SDK on Bedrock AgentCore Runtime.

The Claude Agent SDK (claude_agent_sdk) is the programmatic SDK for Claude Code.
It uses MCP tools and communicates via the Claude Code protocol. Tools are
registered as MCP server tool handlers.

Instrumentation:
  The openinference-instrumentation-claude-agent-sdk library is auto-discovered
  by ADOT at startup. It instruments the SDK client's receive_response calls,
  producing AGENT and TOOL spans that AgentCore Evaluations reads.

Tools (deterministic mock data for reproducible evaluations):
  get_pto_balance        - remaining PTO days for an employee
  submit_pto_request     - request time off
  lookup_hr_policy       - company policy documents
  get_benefits_summary   - health, dental, vision, 401k, life insurance details
  get_pay_stub           - pay stub for a given period
"""

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    create_sdk_mcp_server,
    tool,
)

# Add shared module to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.mock_data import (
    BENEFITS,
    HR_POLICIES,
    PAY_STUBS,
    PTO_BALANCES,
    SYSTEM_PROMPT,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

_PTO_REQUEST_COUNTER = {"n": 0}

# ---------------------------------------------------------------------------
# MCP Tool definitions (Claude Agent SDK uses MCP protocol for tools)
# ---------------------------------------------------------------------------


@tool(
    name="get_pto_balance",
    description="Return the current PTO balance for an employee.",
    input_schema={
        "type": "object",
        "properties": {
            "employee_id": {"type": "string", "description": "Employee identifier (e.g. EMP-001)"},
        },
        "required": ["employee_id"],
    },
)
async def get_pto_balance(params: Any) -> dict[str, Any]:
    employee_id = params.get("employee_id", "")
    balance = PTO_BALANCES.get(employee_id)
    if balance:
        return {"content": json.dumps({"employee_id": employee_id, **balance})}
    return {"content": json.dumps({"employee_id": employee_id, "error": f"Employee {employee_id} not found."})}


@tool(
    name="submit_pto_request",
    description="Submit a PTO request for an employee.",
    input_schema={
        "type": "object",
        "properties": {
            "employee_id": {"type": "string", "description": "Employee identifier"},
            "start_date": {"type": "string", "description": "First day (YYYY-MM-DD)"},
            "end_date": {"type": "string", "description": "Last day (YYYY-MM-DD)"},
            "reason": {"type": "string", "description": "Reason for request", "default": "Personal time off"},
        },
        "required": ["employee_id", "start_date", "end_date"],
    },
)
async def submit_pto_request(params: Any) -> dict[str, Any]:
    _PTO_REQUEST_COUNTER["n"] += 1
    request_id = f"PTO-2026-{_PTO_REQUEST_COUNTER['n']:03d}"
    return {
        "content": json.dumps(
            {
                "request_id": request_id,
                "employee_id": params.get("employee_id"),
                "start_date": params.get("start_date"),
                "end_date": params.get("end_date"),
                "reason": params.get("reason", "Personal time off"),
                "status": "APPROVED",
                "message": f"PTO request {request_id} approved.",
            }
        )
    }


@tool(
    name="lookup_hr_policy",
    description="Look up a company HR policy by topic (pto, remote_work, parental_leave, code_of_conduct).",
    input_schema={
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Policy topic"},
        },
        "required": ["topic"],
    },
)
async def lookup_hr_policy(params: Any) -> dict[str, Any]:
    key = params.get("topic", "").lower().replace(" ", "_").replace("-", "_")
    text = HR_POLICIES.get(key)
    if text:
        return {"content": json.dumps({"topic": key, "policy_text": text})}
    return {"content": json.dumps({"topic": key, "error": f"Not found. Available: {list(HR_POLICIES.keys())}"})}


@tool(
    name="get_benefits_summary",
    description="Return a summary of an employee benefit (health, dental, vision, 401k, life_insurance).",
    input_schema={
        "type": "object",
        "properties": {
            "benefit_type": {"type": "string", "description": "Benefit type"},
        },
        "required": ["benefit_type"],
    },
)
async def get_benefits_summary(params: Any) -> dict[str, Any]:
    key = params.get("benefit_type", "").lower().replace(" ", "_").replace("-", "_")
    text = BENEFITS.get(key)
    if text:
        return {"content": json.dumps({"benefit_type": key, "summary": text})}
    return {"content": json.dumps({"benefit_type": key, "error": f"Not found. Available: {list(BENEFITS.keys())}"})}


@tool(
    name="get_pay_stub",
    description="Retrieve a pay stub for an employee for a specific period (YYYY-MM).",
    input_schema={
        "type": "object",
        "properties": {
            "employee_id": {"type": "string", "description": "Employee identifier"},
            "period": {"type": "string", "description": "Pay period (YYYY-MM)"},
        },
        "required": ["employee_id", "period"],
    },
)
async def get_pay_stub(params: Any) -> dict[str, Any]:
    employee_id = params.get("employee_id", "")
    period = params.get("period", "")
    stub = PAY_STUBS.get((employee_id, period))
    if stub:
        return {"content": json.dumps({"employee_id": employee_id, **stub})}
    return {"content": json.dumps({"employee_id": employee_id, "period": period, "error": "Not found."})}


# ---------------------------------------------------------------------------
# MCP Server with tools
# ---------------------------------------------------------------------------

hr_tools_server = create_sdk_mcp_server(
    name="hr_tools",
    tools=[get_pto_balance, submit_pto_request, lookup_hr_policy, get_benefits_summary, get_pay_stub],
)

# ---------------------------------------------------------------------------
# AgentCore Runtime entrypoint
# ---------------------------------------------------------------------------

MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")


@app.entrypoint
async def invoke(payload, context):
    """Handle an agent invocation from AgentCore Runtime."""
    prompt = payload.get("prompt", "")
    session_id = context.session_id
    logger.info("Received prompt (session=%s): %s", session_id, prompt[:80])

    options = ClaudeAgentOptions(
        model=MODEL_ID,
        system_prompt=SYSTEM_PROMPT,
        permission_mode="bypassPermissions",
        max_turns=10,
    )

    # Use the SDK client with our MCP tools server
    async with ClaudeSDKClient(options=options) as client:
        client.add_mcp_server(hr_tools_server)
        response_text = ""
        async for message in client.send_message(prompt):
            if isinstance(message, ResultMessage):
                response_text = message.content
                break

    return response_text


if __name__ == "__main__":
    app.run()
