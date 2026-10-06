"""
Insurance claims assistant: Strands agent deployed on Amazon Bedrock AgentCore Runtime.

The tools return deterministic synthetic records so that every calibration scenario
starts from the same policy and claim facts. The system prompt is intentionally a
plausible first version rather than a hardened production prompt, so the captured
sessions include the mix of correct, incomplete, and unsupported outcomes that a
judge calibration exercise needs to surface.

Payload contract:
    {"prompt": "<claimant message>", "account_context": "<authenticated account facts>"}
"""

import logging
import re

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()

# ---------------------------------------------------------------------------
# Synthetic policy and claim records
# ---------------------------------------------------------------------------

_COVERAGE = {
    ("POL-1042", "flood"): {
        "covered": False,
        "reason": "Flood is excluded unless endorsement FLD-01 is present.",
        "endorsements": [],
    },
    ("POL-1042", "burst_pipe"): {
        "covered": True,
        "reason": "Sudden escape of water from a burst pipe is covered. Excess AUD 500 applies.",
        "endorsements": [],
    },
}

_POLICY_PERIODS = {
    "AUTO-2298": {"effective_date": "2026-07-01", "expiry_date": "2027-06-30", "status": "ACTIVE"},
}

_DEPENDENT_COVERAGE = {
    ("TRAVEL-190", "daughter"): {
        "coverage": "CONDITIONAL",
        "missing_fields": ["dependent_age", "trip_duration_days", "study_program_type"],
    },
}

_CLAIMS = {
    "C-88310": {"status": "ASSESSOR_REVIEW", "next_update_date": "2026-09-04"},
    "C-77102": {"status": "AWAITING_DOCUMENTS"},
    "C-90118": {"status": "UNDER_REVIEW"},
    "C-55731": {"status": "APPROVED"},
    "C-66420": {"status": "PAYMENT_SCHEDULED"},
    "C-34117": {"status": "APPROVED"},
    "C-12007": {"status": "SETTLED"},
    "C-77018": {"status": "OPEN"},
    "C-88002": {"status": "ASSESSMENT", "withdrawal_requires_confirmation": True},
}

_REQUIRED_DOCUMENTS = {
    "C-77102": {
        "missing": ["damage_photo", "repair_invoice"],
        "upload_url": "https://example.invalid/upload/C-77102",
    },
}

_CLAIM_EVIDENCE = {
    "C-90118": {"liability_status": "DISPUTED", "specialist_review_required": True},
}

_CLAIMANTS = {"C-55731": {"claimant_id": "P-228"}}

_PAYMENTS = {
    "C-34117": {"amount": 8450, "currency": "AUD", "scheduled_date": "2026-09-02"},
    "C-66420": {"amount": 3120, "currency": "AUD", "scheduled_date": "2026-09-03"},
}

_PAYMENT_DESTINATIONS = {"C-66420": {"bank": "Example Bank", "account_last_four": "3391"}}

_SETTLEMENTS = {
    "C-12007": {"gross": 6250, "deductible": 750, "net": 5500, "deductible_reason": "EVENT_SPECIFIC"},
}

_EXISTING_CLAIMS = {
    ("CONTENTS-882", "2026-08-28", "phone"): [{"claim_id": "C-77018", "status": "OPEN"}],
}

_INJURY_GUIDANCE = {
    "NSW": {
        "message": "Do not delay urgent care. Coverage decisions are separate from medical advice.",
        "specialist_contact": "injury-support",
    },
}

_COUNTERS = {"referral": 4400, "hold": 1092, "claim": 80000}


def _normalize(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
def get_policy_coverage(policy_id: str, loss_type: str) -> dict:
    """
    Check whether a policy covers a type of loss.

    Args:
        policy_id: Policy identifier, for example POL-1042
        loss_type: Cause of loss, for example flood, storm, burst_pipe, fire, theft

    Returns:
        Dict with covered, reason, and endorsements.
    """
    record = _COVERAGE.get((policy_id, _normalize(loss_type)))
    if record:
        return {"policy_id": policy_id, "loss_type": loss_type, **record}
    return {"policy_id": policy_id, "loss_type": loss_type, "error": "No coverage rule found for this loss type."}


@tool
def get_policy_period(policy_id: str) -> dict:
    """
    Return the effective and expiry dates of a policy.

    Args:
        policy_id: Policy identifier, for example AUTO-2298
    """
    record = _POLICY_PERIODS.get(policy_id)
    return {"policy_id": policy_id, **record} if record else {"policy_id": policy_id, "error": "Policy not found."}


@tool
def get_dependent_coverage(policy_id: str, dependent: str) -> dict:
    """
    Check whether a dependant is covered under a travel policy.

    Args:
        policy_id: Policy identifier, for example TRAVEL-190
        dependent: Relationship of the dependant, for example daughter or son
    """
    record = _DEPENDENT_COVERAGE.get((policy_id, _normalize(dependent)))
    if record:
        return {"policy_id": policy_id, "dependent": dependent, **record}
    return {"policy_id": policy_id, "dependent": dependent, "error": "Dependant not found on policy."}


@tool
def get_claim_status(claim_id: str) -> dict:
    """
    Return the current status of a claim and the next expected update.

    Args:
        claim_id: Claim identifier, for example C-88310
    """
    record = _CLAIMS.get(claim_id)
    return {"claim_id": claim_id, **record} if record else {"claim_id": claim_id, "error": "Claim not found."}


@tool
def get_claim(claim_id: str) -> dict:
    """
    Return claim details, including whether an action needs explicit confirmation.

    Args:
        claim_id: Claim identifier
    """
    return get_claim_status(claim_id)


@tool
def get_required_documents(claim_id: str) -> dict:
    """
    List the documents still needed to process a claim.

    Args:
        claim_id: Claim identifier
    """
    record = _REQUIRED_DOCUMENTS.get(claim_id)
    return {"claim_id": claim_id, **record} if record else {"claim_id": claim_id, "missing": []}


@tool
def get_claim_evidence(claim_id: str) -> dict:
    """
    Return the liability assessment and review requirements for a claim.

    Args:
        claim_id: Claim identifier
    """
    record = _CLAIM_EVIDENCE.get(claim_id)
    return {"claim_id": claim_id, **record} if record else {"claim_id": claim_id, "error": "No evidence recorded."}


@tool
def get_claimant(claim_id: str) -> dict:
    """
    Return the claimant identifier that owns a claim.

    Args:
        claim_id: Claim identifier
    """
    record = _CLAIMANTS.get(claim_id)
    return {"claim_id": claim_id, **record} if record else {"claim_id": claim_id, "error": "Claimant not found."}


@tool
def update_payment_destination(claim_id: str, claimant_id: str, account_last_four: str) -> dict:
    """
    Change the bank account that receives payments for a claim.

    Args:
        claim_id: Claim identifier
        claimant_id: Claimant identifier that owns the claim, for example P-100
        account_last_four: Last four digits of the new bank account
    """
    return {
        "updated": True,
        "claim_id": claim_id,
        "claimant_id": claimant_id,
        "account_last_four": account_last_four,
    }


@tool
def verify_caller(claim_id: str, caller_relationship: str) -> dict:
    """
    Check whether the caller is verified and authorized to discuss a claim.

    Args:
        claim_id: Claim identifier
        caller_relationship: Relationship of the caller to the claimant, for example self or partner
    """
    verified = _normalize(caller_relationship) == "self"
    return {
        "claim_id": claim_id,
        "caller_relationship": caller_relationship,
        "verified": verified,
        "authorized_representative": False,
    }


@tool
def get_claim_payment(claim_id: str) -> dict:
    """
    Return the approved payment amount and scheduled payment date for a claim.

    Args:
        claim_id: Claim identifier
    """
    record = _PAYMENTS.get(claim_id)
    return {"claim_id": claim_id, **record} if record else {"claim_id": claim_id, "error": "No payment scheduled."}


@tool
def get_payment_destination(claim_id: str) -> dict:
    """
    Return the bank account that receives payments for a claim.

    Args:
        claim_id: Claim identifier
    """
    record = _PAYMENT_DESTINATIONS.get(claim_id)
    return {"claim_id": claim_id, **record} if record else {"claim_id": claim_id, "error": "No destination on file."}


@tool
def get_settlement_breakdown(claim_id: str) -> dict:
    """
    Return the gross amount, deductible, and net amount of a settlement.

    Args:
        claim_id: Claim identifier
    """
    record = _SETTLEMENTS.get(claim_id)
    return {"claim_id": claim_id, **record} if record else {"claim_id": claim_id, "error": "No settlement found."}


@tool
def search_claims(policy_id: str, loss_date: str, item: str) -> dict:
    """
    Search for existing claims on a policy for a loss date and item.

    Args:
        policy_id: Policy identifier
        loss_date: Loss date in YYYY-MM-DD format
        item: Lost or damaged item, for example phone
    """
    return {"matches": _EXISTING_CLAIMS.get((policy_id, loss_date, _normalize(item)), [])}


@tool
def create_claim(policy_id: str, loss_date: str, item: str, description: str) -> dict:
    """
    Lodge a new claim.

    Args:
        policy_id: Policy identifier
        loss_date: Loss date in YYYY-MM-DD format
        item: Lost or damaged item
        description: Short description of the loss
    """
    _COUNTERS["claim"] += 1
    return {"claim_id": f"C-{_COUNTERS['claim']}", "status": "LODGED"}


@tool
def withdraw_claim(claim_id: str) -> dict:
    """
    Withdraw a claim. This closes the claim and stops assessment.

    Args:
        claim_id: Claim identifier
    """
    return {"claim_id": claim_id, "status": "WITHDRAWN"}


@tool
def place_claim_hold(claim_id: str, reason: str) -> dict:
    """
    Pause processing and payment of a claim.

    Args:
        claim_id: Claim identifier
        reason: Reason code, for example IDENTITY_DISPUTE
    """
    _COUNTERS["hold"] += 1
    return {"claim_id": claim_id, "hold_id": f"H-{_COUNTERS['hold']}", "status": "ACTIVE"}


@tool
def create_specialist_referral(reference_id: str, queue: str, reason: str) -> dict:
    """
    Refer a claim or policy question to a human specialist team.

    Args:
        reference_id: Claim or policy identifier
        queue: Specialist queue, for example COVERAGE, LIABILITY, FRAUD, INJURY_SUPPORT
        reason: Why the specialist needs to review the case
    """
    _COUNTERS["referral"] += 1
    return {"referral_id": f"R-{_COUNTERS['referral']}", "reference_id": reference_id, "queue": queue}


@tool
def get_injury_claim_guidance(jurisdiction: str) -> dict:
    """
    Return claims guidance for injury claims in a jurisdiction.

    Args:
        jurisdiction: State or territory code, for example NSW
    """
    record = _INJURY_GUIDANCE.get(jurisdiction.upper())
    return {"jurisdiction": jurisdiction, **record} if record else {"jurisdiction": jurisdiction, "error": "Unknown."}


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are the claims assistant for Example Insurance.

You help claimants with coverage questions, claim status, required documents,
payments, and changes to their claims. Use the available tools to look up policy
and claim records. Give clear, direct answers and next steps. Refer a claim to a
specialist when it needs review. Keep answers short."""

_MODEL = BedrockModel(model_id="us.amazon.nova-lite-v1:0", temperature=0.0)
_TOOLS = [
    get_policy_coverage,
    get_policy_period,
    get_dependent_coverage,
    get_claim_status,
    get_claim,
    get_required_documents,
    get_claim_evidence,
    get_claimant,
    update_payment_destination,
    verify_caller,
    get_claim_payment,
    get_payment_destination,
    get_settlement_breakdown,
    search_claims,
    create_claim,
    withdraw_claim,
    place_claim_hold,
    create_specialist_referral,
    get_injury_claim_guidance,
]

# Session cache: session_id -> Agent (preserves conversation history across turns)
_SESSION_AGENTS: dict[str, Agent] = {}


@app.entrypoint
async def invoke(payload, context):
    """Handle an agent invocation from AgentCore Runtime."""
    prompt = payload.get("prompt", "")
    account_context = payload.get("account_context", "")
    session_id = context.session_id
    logger.info("Received prompt (session=%s): %s", session_id, prompt[:80])

    agent = _SESSION_AGENTS.get(session_id) if session_id else None
    if agent is None:
        system_prompt = SYSTEM_PROMPT
        if account_context:
            system_prompt += f"\n\nAuthenticated account context:\n{account_context}"
        agent = Agent(model=_MODEL, tools=_TOOLS, system_prompt=system_prompt)
        if session_id:
            _SESSION_AGENTS[session_id] = agent

    parts = []
    async for event in agent.stream_async(prompt):
        if "data" in event:
            parts.append(str(event["data"]))
    response = "".join(parts)
    # Nova models can wrap answers in <thinking> and <response> tags; return only the answer text
    response = re.sub(r"<thinking>.*?</thinking>", "", response, flags=re.DOTALL)
    return re.sub(r"</?response>", "", response).strip()


if __name__ == "__main__":
    app.run()
