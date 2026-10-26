"""
HR Assistant Agent: LlamaIndex agent workflow deployed on Bedrock AgentCore Runtime.

Same HR Assistant domain as the shared Strands agent in ../../utils, re-implemented
as a LlamaIndex FunctionAgent workflow so it can be evaluated with AgentCore
Evaluations. The tools, mock data, and system prompt are identical, so ground-truth
and expected responses stay consistent across framework samples.

Built as a LlamaIndex agent workflow (FunctionAgent) so the framework emits a
top-level workflow span with inference and tool child spans — the structure
AgentCore Evaluations reconstructs a session from. Tools are registered as
FunctionTool objects and return text-serializable values, per the AgentCore
best practices for LlamaIndex agents.

Observability is provided by ADOT with the OpenTelemetry LlamaIndex instrumentation
(added to requirements.txt). ADOT discovers it at startup, so no explicit
instrumentation code is needed here. The LLM is a Bedrock model (Nova Lite) via
BedrockConverse, matching the shared Strands agent.

Conversation history is persisted in AgentCore Memory (short-term memory
events) per runtime session, so multi-turn context survives microVM restarts.
deploy.py creates the memory resource and injects AGENTCORE_MEMORY_ID. History
is replayed via FunctionAgent's chat_history as plain USER/ASSISTANT text turns
(tool-call messages are not replayed — see the note above _load_chat_history).

Tools (deterministic / mock data for reproducible evaluations):
  get_pto_balance        - remaining PTO days for an employee
  submit_pto_request     - request time off
  lookup_hr_policy       - company policy documents
  get_benefits_summary   - health, dental, vision, 401k, life insurance details
  get_pay_stub           - pay stub for a given period
"""

import logging
import os
import re

from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from llama_index.core.agent.workflow import FunctionAgent
from llama_index.core.base.llms.types import ChatMessage
from llama_index.core.tools import FunctionTool
from llama_index.llms.bedrock_converse import BedrockConverse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

REGION = os.environ.get("AWS_REGION", "us-west-2")
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.amazon.nova-lite-v1:0")

# AgentCore Memory holds the conversation history across turns (and across
# microVM restarts). The memory resource is created by deploy.py and its id is
# injected as an environment variable on the runtime.
MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "")
ACTOR_ID = "hr-employee"
_memory_client = MemoryClient(region_name=REGION) if MEMORY_ID else None

# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

_PTO_BALANCES = {
    "EMP-001": {"total_days": 15, "used_days": 5, "remaining_days": 10},
    "EMP-002": {"total_days": 15, "used_days": 12, "remaining_days": 3},
    "EMP-042": {"total_days": 20, "used_days": 7, "remaining_days": 13},
}

_HR_POLICIES = {
    "pto": (
        "PTO Policy: Full-time employees accrue 15 days of PTO per year (20 days after 3 years). "
        "PTO requests must be submitted at least 2 business days in advance. "
        "Unused PTO up to 5 days rolls over to the next year. "
        "PTO cannot be taken in advance of accrual."
    ),
    "remote_work": (
        "Remote Work Policy: Employees may work remotely up to 3 days per week with manager approval. "
        "Core collaboration hours are 10am-3pm local time. "
        "A dedicated workspace with reliable internet (25 Mbps+) is required. "
        "Employees must be reachable via Slack and email during core hours."
    ),
    "parental_leave": (
        "Parental Leave Policy: Primary caregivers receive 16 weeks of fully paid parental leave. "
        "Secondary caregivers receive 6 weeks of fully paid parental leave. "
        "Leave may begin up to 2 weeks before the expected birth or adoption date. "
        "Benefits continue unchanged during parental leave."
    ),
    "code_of_conduct": (
        "Code of Conduct: All employees are expected to treat colleagues, customers, and partners "
        "with respect and professionalism. Harassment, discrimination, and retaliation of any kind "
        "are strictly prohibited. Violations should be reported to HR or via the anonymous hotline."
    ),
}

_BENEFITS = {
    "health": (
        "Health Insurance: The company covers 90% of premiums for employee-only coverage and 75% "
        "for family coverage. Plans available: Blue Shield PPO, Kaiser HMO, and HDHP with HSA. "
        "Annual deductible: $500 (PPO), $0 (HMO), $1,500 (HDHP). "
        "Open enrollment is each November for the following calendar year."
    ),
    "dental": (
        "Dental Insurance: 100% coverage for preventive care (cleanings, X-rays). "
        "80% coverage for basic restorative care (fillings, extractions). "
        "50% coverage for major restorative care (crowns, bridges). "
        "Annual maximum benefit: $2,000 per person. Orthodontia lifetime maximum: $1,500."
    ),
    "vision": (
        "Vision Insurance: Annual eye exam covered in full. "
        "Frames or contacts allowance: $200 per year. "
        "Laser vision correction discount: 15% off at participating providers."
    ),
    "401k": (
        "401(k) Plan: The company matches 100% of employee contributions up to 4% of salary. "
        "An additional 50% match on the next 2% (total effective match up to 5%). "
        "Employees are eligible to contribute immediately; company match vests over 3 years. "
        "2026 IRS contribution limit: $23,500 (under 50), $31,000 (age 50+)."
    ),
    "life_insurance": (
        "Life Insurance: Basic life insurance of 2x annual salary provided at no cost. "
        "Employees may purchase supplemental coverage up to 5x salary during open enrollment. "
        "Accidental death and dismemberment (AD&D) coverage equal to basic life benefit is included."
    ),
}

_PAY_STUBS = {
    ("EMP-001", "2025-12"): {
        "gross_pay": 8333.33,
        "federal_tax": 1458.33,
        "state_tax": 416.67,
        "social_security": 516.67,
        "medicare": 120.83,
        "health_premium": 125.00,
        "401k_contribution": 333.33,
        "net_pay": 5362.50,
        "period": "December 2025",
    },
    ("EMP-001", "2026-01"): {
        "gross_pay": 8333.33,
        "federal_tax": 1458.33,
        "state_tax": 416.67,
        "social_security": 516.67,
        "medicare": 120.83,
        "health_premium": 125.00,
        "401k_contribution": 333.33,
        "net_pay": 5362.50,
        "period": "January 2026",
    },
    ("EMP-042", "2026-01"): {
        "gross_pay": 10416.67,
        "federal_tax": 1875.00,
        "state_tax": 520.83,
        "social_security": 645.83,
        "medicare": 151.04,
        "health_premium": 200.00,
        "401k_contribution": 416.67,
        "net_pay": 6607.30,
        "period": "January 2026",
    },
}

_PTO_REQUEST_COUNTER = {"n": 0}


# ---------------------------------------------------------------------------
# Tool functions (registered as LlamaIndex FunctionTool objects below)
# ---------------------------------------------------------------------------


def get_pto_balance(employee_id: str) -> dict:
    """
    Return the current PTO balance for an employee.

    Args:
        employee_id: Employee identifier (e.g. EMP-001)

    Returns:
        Dict with total_days, used_days, and remaining_days.
    """
    balance = _PTO_BALANCES.get(employee_id)
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
    text = _HR_POLICIES.get(key)
    if text:
        return {"topic": topic, "policy_text": text}
    return {
        "topic": topic,
        "error": f"Policy '{topic}' not found. Available: {list(_HR_POLICIES.keys())}",
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
    text = _BENEFITS.get(key)
    if text:
        return {"benefit_type": benefit_type, "summary": text}
    return {
        "benefit_type": benefit_type,
        "error": f"Benefit '{benefit_type}' not found. Available: {list(_BENEFITS.keys())}",
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
    stub = _PAY_STUBS.get((employee_id, period))
    if stub:
        return {"employee_id": employee_id, **stub}
    return {
        "employee_id": employee_id,
        "period": period,
        "error": f"Pay stub not found for {employee_id} period {period}.",
    }


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a helpful HR Assistant for Acme Corp.

You help employees with:
- Checking PTO (paid time off) balances
- Submitting PTO requests
- Looking up HR policies (PTO, remote work, parental leave, code of conduct)
- Understanding employee benefits (health, dental, vision, 401k, life insurance)
- Retrieving pay stub information

Always use the available tools to answer questions accurately. Do not make up
policy details, benefit amounts, or pay information. Look them up.
Be concise, professional, and friendly."""

_TOOLS = [
    FunctionTool.from_defaults(fn=get_pto_balance),
    FunctionTool.from_defaults(fn=submit_pto_request),
    FunctionTool.from_defaults(fn=lookup_hr_policy),
    FunctionTool.from_defaults(fn=get_benefits_summary),
    FunctionTool.from_defaults(fn=get_pay_stub),
]

_AGENT = FunctionAgent(
    tools=_TOOLS,
    llm=BedrockConverse(model=MODEL_ID, region_name=REGION),
    system_prompt=SYSTEM_PROMPT,
    # Non-streaming: one complete inference span per model call is what the
    # evaluation service reads, and it avoids a BedrockConverse streaming parser
    # issue where split tool-call input deltas raise TypeError.
    streaming=False,
)

# Conversation history lives in AgentCore Memory (short-term memory events),
# keyed by the runtime session id. It survives microVM restarts and is shared
# with the AgentCore Memory console/APIs.
#
# Only USER/ASSISTANT text turns are stored and replayed as chat_history.
# Storing tool-call messages and replaying them breaks the Bedrock Converse
# API's toolUse/toolResult pairing validation, so intermediate tool messages
# are intentionally left out of memory.


def _load_chat_history(session_id: str) -> list:
    """Load the conversation as ChatMessage items from AgentCore Memory."""
    if not _memory_client:
        return []
    history = []
    events = _memory_client.list_events(memory_id=MEMORY_ID, actor_id=ACTOR_ID, session_id=session_id)
    for event in sorted(events, key=lambda e: e["eventId"]):
        for item in event.get("payload", []):
            conv = item.get("conversational")
            if conv:
                role = "user" if conv["role"] == "USER" else "assistant"
                history.append(ChatMessage(role=role, content=conv["content"]["text"]))
    return history


def _save_turn(session_id: str, prompt: str, response: str):
    """Persist one user/assistant turn to AgentCore Memory."""
    if not _memory_client:
        return
    _memory_client.create_event(
        memory_id=MEMORY_ID,
        actor_id=ACTOR_ID,
        session_id=session_id,
        messages=[(prompt, "USER"), (response, "ASSISTANT")],
    )


def _flush_telemetry():
    """
    Flush buffered OTel spans and event records before the microVM freezes.

    AgentCore Runtime suspends the microVM between invocations. Without an
    explicit flush, event records buffered in the OTel batch processors (which
    carry the agent's response text for evaluation) can be lost, and evaluators
    then score empty responses.
    """
    try:
        from opentelemetry import trace as _trace
        from opentelemetry._logs import get_logger_provider as _get_lp

        for provider in (_trace.get_tracer_provider(), _get_lp()):
            flush = getattr(provider, "force_flush", None)
            if flush:
                flush()
    except Exception:
        logger.warning("Telemetry flush failed", exc_info=True)


@app.entrypoint
async def invoke(payload, context):
    """Handle an agent invocation from AgentCore Runtime."""
    prompt = payload.get("prompt", "")
    session_id = context.session_id or "default"
    logger.info("Received prompt (session=%s): %s", session_id, prompt[:80])

    try:
        response = await _AGENT.run(prompt, chat_history=_load_chat_history(session_id))
    finally:
        _flush_telemetry()
    text = str(response)
    # Strip inline <thinking>...</thinking> blocks so spans and memory contain
    # only the final answer
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL).strip()
    _save_turn(session_id, prompt, text)
    return text


if __name__ == "__main__":
    app.run()
