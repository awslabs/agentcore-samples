"""
HR Assistant Agent: OpenAI Agents SDK agent deployed on Bedrock AgentCore Runtime.

Same HR Assistant domain as the shared Strands agent in ../../utils, re-implemented
with the OpenAI Agents SDK so it can be evaluated with AgentCore Evaluations. The
tools, mock data, and system prompt are identical, so ground-truth and expected
responses stay consistent across framework samples.

The LLM is OpenAI GPT-5.5 on Amazon Bedrock, reached through the Bedrock mantle
endpoint's OpenAI-compatible Responses API and authenticated with a Bedrock API
key. aws_bedrock_token_generator.provide_token() mints a short-term Bedrock API
key from the runtime's IAM role on every invocation — the secure, recommended
kind, so no key is stored in code or config.

The Responses API (OpenAIResponsesModel) is used rather than Chat Completions:
the OpenTelemetry instrumentation extracts the agent's response text from
Responses API spans (ResponseSpanData), which AgentCore Evaluations needs to
score the agent's answers.

Conversation history is persisted in AgentCore Memory (short-term memory
events) per runtime session, so multi-turn context survives microVM restarts.
deploy.py creates the memory resource and injects AGENTCORE_MEMORY_ID.

Observability is provided by ADOT with the OpenTelemetry OpenAI Agents
instrumentation (added to requirements.txt). ADOT discovers it at startup, so no
explicit instrumentation code is needed here. The instrumentation hooks into the
SDK's tracing pipeline, so SDK tracing must stay enabled (do NOT call
set_tracing_disabled) — the SDK's default platform.openai.com exporter is inert
without an OPENAI_API_KEY and only logs a skip message.

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

from agents import (
    Agent,
    OpenAIResponsesModel,
    Runner,
    function_tool,
)
from aws_bedrock_token_generator import provide_token
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

# ---------------------------------------------------------------------------
# Model configuration (OpenAI GPT-5.5 on Bedrock via the mantle endpoint)
# ---------------------------------------------------------------------------
#
# GPT-5.5 is served on the mantle endpoint's openai/v1 path (a different path
# from the /v1 used by gpt-oss). It is available in us-east-1 / us-east-2, so
# MODEL_REGION may differ from the region the runtime is deployed in. The
# runtime role needs bedrock-mantle:CreateInference and
# bedrock-mantle:CallWithBearerToken (granted by deploy.py).

REGION = os.environ.get("AWS_REGION", "us-west-2")
MODEL_REGION = os.environ.get("BEDROCK_OPENAI_MODEL_REGION", "us-east-1")
BASE_URL = os.environ.get("BEDROCK_OPENAI_BASE_URL", f"https://bedrock-mantle.{MODEL_REGION}.api.aws/openai/v1")
MODEL_ID = os.environ.get("BEDROCK_OPENAI_MODEL_ID", "openai.gpt-5.5")

# AgentCore Memory holds the conversation history across turns (and across
# microVM restarts). The memory resource is created by deploy.py and its id is
# injected as an environment variable on the runtime.
MEMORY_ID = os.environ.get("AGENTCORE_MEMORY_ID", "")
ACTOR_ID = "hr-employee"
_memory_client = MemoryClient(region_name=REGION) if MEMORY_ID else None

# NOTE: Do not call set_tracing_disabled(True) here. The OpenTelemetry
# instrumentation registers a processor on the SDK's tracing pipeline; disabling
# SDK tracing would silence the evaluation spans. The SDK's default
# platform.openai.com exporter skips exporting when OPENAI_API_KEY is unset.

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
# OpenAI Agents SDK tools
# ---------------------------------------------------------------------------


@function_tool
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


@function_tool
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


@function_tool
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


@function_tool
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


@function_tool
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
    get_pto_balance,
    submit_pto_request,
    lookup_hr_policy,
    get_benefits_summary,
    get_pay_stub,
]


def _build_agent() -> Agent:
    """
    Build the HR Assistant agent.

    provide_token() returns a short-term Bedrock API key (a bedrock-api-key-...
    string) minted from the runtime's IAM role credentials — a local SigV4
    presign with no network call. The agent is rebuilt on every invocation
    rather than cached for the microVM's lifetime, so a long-lived runtime never
    keeps using an expired key.
    """
    api_key = provide_token(region=MODEL_REGION)
    client = AsyncOpenAI(base_url=BASE_URL, api_key=api_key)
    model = OpenAIResponsesModel(model=MODEL_ID, openai_client=client)
    return Agent(name="HRAssistant", instructions=SYSTEM_PROMPT, model=model, tools=_TOOLS)


# Conversation history lives in AgentCore Memory (short-term memory events),
# keyed by the runtime session id. It survives microVM restarts and is shared
# with the AgentCore Memory console/APIs.
#
# The SDK's SQLiteSession is not used here for two reasons: it is local to one
# microVM (history is lost when the runtime scales or restarts), and it replays
# full Responses API output items (including model "reasoning" items) as the
# next turn's input, which the Bedrock mantle endpoint rejects with an empty
# output. Plain role/content text history from Memory round-trips reliably.


def _load_history(session_id: str) -> list:
    """Load the conversation as [{"role", "content"}] items from AgentCore Memory."""
    if not _memory_client:
        return []
    history = []
    events = _memory_client.list_events(memory_id=MEMORY_ID, actor_id=ACTOR_ID, session_id=session_id)
    for event in sorted(events, key=lambda e: e["eventId"]):
        for item in event.get("payload", []):
            conv = item.get("conversational")
            if conv:
                role = "user" if conv["role"] == "USER" else "assistant"
                history.append({"role": role, "content": conv["content"]["text"]})
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

    history = _load_history(session_id)
    history.append({"role": "user", "content": prompt})
    try:
        result = await Runner.run(_build_agent(), history)
    finally:
        _flush_telemetry()
    response = str(result.final_output)
    # Some OpenAI models (e.g. gpt-oss) emit inline <reasoning>...</reasoning>
    # blocks; strip them so spans contain only the final answer
    response = re.sub(r"<reasoning>.*?</reasoning>", "", response, flags=re.DOTALL).strip()
    _save_turn(session_id, prompt, response)
    return response


if __name__ == "__main__":
    app.run()
