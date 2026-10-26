"""
HR Assistant Agent — Google ADK on Bedrock AgentCore Runtime.

Demonstrates an agent built with the Google Agent Development Kit (ADK),
instrumented for AgentCore Evaluations via openinference-instrumentation-google-adk.

Model routing:
  Uses LiteLLM connector to call Claude on Bedrock — no external API key needed.
  To use Gemini natively instead, set GOOGLE_API_KEY and change the model string
  to "gemini-2.5-flash" (see commented example below).

Tools (deterministic mock data for reproducible evaluations):
  get_pto_balance        - remaining PTO days for an employee
  submit_pto_request     - request time off
  lookup_hr_policy       - company policy documents
  get_benefits_summary   - health, dental, vision, 401k, life insurance details
  get_pay_stub           - pay stub for a given period

Instrumentation:
  The openinference-instrumentation-google-adk library is auto-discovered
  by ADOT at startup — no explicit tracer code needed. Just add it to
  requirements.txt and deploy to AgentCore Runtime.
"""

import json
import logging
import os
import sys
from pathlib import Path

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import FunctionTool

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
# Tools (Google ADK uses plain functions — decorated with FunctionTool below)
# ---------------------------------------------------------------------------


def get_pto_balance(employee_id: str) -> dict:
    """
    Return the current PTO balance for an employee.

    Args:
        employee_id: Employee identifier (e.g. EMP-001)

    Returns:
        Dict with total_days, used_days, and remaining_days.
    """
    balance = PTO_BALANCES.get(employee_id)
    if balance:
        return {"employee_id": employee_id, **balance}
    return {"employee_id": employee_id, "error": f"Employee {employee_id} not found."}


def submit_pto_request(
    employee_id: str,
    start_date: str,
    end_date: str,
    reason: str = "Personal time off",
) -> dict:
    """
    Submit a PTO request for an employee.

    Args:
        employee_id: Employee identifier (e.g. EMP-001)
        start_date:  First day of leave in YYYY-MM-DD format
        end_date:    Last day of leave in YYYY-MM-DD format
        reason:      Optional reason for the request

    Returns:
        Dict with request_id, status, and confirmation message.
    """
    _PTO_REQUEST_COUNTER["n"] += 1
    request_id = f"PTO-2026-{_PTO_REQUEST_COUNTER['n']:03d}"
    return {
        "request_id": request_id,
        "employee_id": employee_id,
        "start_date": start_date,
        "end_date": end_date,
        "reason": reason,
        "status": "APPROVED",
        "message": f"PTO request {request_id} approved for {employee_id} from {start_date} to {end_date}.",
    }


def lookup_hr_policy(topic: str) -> dict:
    """
    Look up a company HR policy document by topic.

    Args:
        topic: Policy topic. Supported values: pto, remote_work, parental_leave, code_of_conduct

    Returns:
        Dict with topic and policy_text.
    """
    key = topic.lower().replace(" ", "_").replace("-", "_")
    text = HR_POLICIES.get(key)
    if text:
        return {"topic": topic, "policy_text": text}
    return {
        "topic": topic,
        "error": f"Policy '{topic}' not found. Available: {list(HR_POLICIES.keys())}",
    }


def get_benefits_summary(benefit_type: str) -> dict:
    """
    Return a summary of a specific employee benefit.

    Args:
        benefit_type: Type of benefit. Supported values: health, dental, vision, 401k, life_insurance

    Returns:
        Dict with benefit_type and summary text.
    """
    key = benefit_type.lower().replace(" ", "_").replace("-", "_")
    text = BENEFITS.get(key)
    if text:
        return {"benefit_type": benefit_type, "summary": text}
    return {
        "benefit_type": benefit_type,
        "error": f"Benefit '{benefit_type}' not found. Available: {list(BENEFITS.keys())}",
    }


def get_pay_stub(employee_id: str, period: str) -> dict:
    """
    Retrieve a pay stub for an employee for a specific pay period.

    Args:
        employee_id: Employee identifier (e.g. EMP-001)
        period:      Pay period in YYYY-MM format (e.g. 2026-01)

    Returns:
        Dict with gross pay, deductions, and net pay.
    """
    stub = PAY_STUBS.get((employee_id, period))
    if stub:
        return {"employee_id": employee_id, **stub}
    return {
        "employee_id": employee_id,
        "period": period,
        "error": f"Pay stub not found for {employee_id} period {period}.",
    }


# ---------------------------------------------------------------------------
# Agent configuration
# ---------------------------------------------------------------------------

# Model via Bedrock (no external API key needed):
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "bedrock/us.anthropic.claude-sonnet-4-5-20250929-v1:0")

# To use Gemini natively (requires GOOGLE_API_KEY env var):
# GEMINI_MODEL_ID = "gemini-2.5-flash"

_TOOLS = [
    FunctionTool(get_pto_balance),
    FunctionTool(submit_pto_request),
    FunctionTool(lookup_hr_policy),
    FunctionTool(get_benefits_summary),
    FunctionTool(get_pay_stub),
]

hr_agent = LlmAgent(
    model=LiteLlm(model=BEDROCK_MODEL_ID),
    name="hr_assistant",
    instruction=SYSTEM_PROMPT,
    tools=_TOOLS,
)


# ---------------------------------------------------------------------------
# AgentCore Runtime entrypoint
# ---------------------------------------------------------------------------


@app.entrypoint
async def invoke(payload, context):
    """Handle an agent invocation from AgentCore Runtime."""
    prompt = payload.get("prompt", "")
    session_id = context.session_id
    logger.info("Received prompt (session=%s): %s", session_id, prompt[:80])

    from google.adk.sessions import InMemorySessionService
    from google.adk.runners import Runner

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="hr_assistant",
        user_id="default_user",
        session_id=session_id or "default",
    )

    runner = Runner(agent=hr_agent, session_service=session_service, app_name="hr_assistant")

    from google.genai.types import Content, Part

    user_content = Content(parts=[Part(text=prompt)], role="user")

    response_text = ""
    async for event in runner.run_async(
        session_id=session.id,
        user_id="default_user",
        new_message=user_content,
    ):
        if hasattr(event, "content") and event.content and event.content.parts:
            for part in event.content.parts:
                if hasattr(part, "text") and part.text:
                    response_text += part.text

    return response_text


if __name__ == "__main__":
    app.run()
