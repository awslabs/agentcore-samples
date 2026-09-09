"""Simulated claims history check."""

from strands import tool
from tools import signals


def _normalize_claim_type(text: str) -> str:
    """Map free-text incident wording to a canonical claim-type category.

    The agent describes incidents in varied language ("burst pipe", "water came
    through the ceiling"); we normalize so repeat-claim detection is reliable.
    """
    t = (text or "").lower()
    # Water damage family (covered internal water — flood handled separately by coverage)
    if any(k in t for k in ["water", "pipe", "burst", "leak", "plumb"]):
        return "Water damage"
    if any(k in t for k in ["collision", "rear-end", "rear end", "fender", "crash", "accident", "vehicle", "auto"]):
        return "Auto collision"
    if any(k in t for k in ["theft", "stolen", "burglar", "break-in", "break in", "robbery"]):
        return "Theft"
    if "fire" in t or "smoke" in t:
        return "Fire"
    if any(k in t for k in ["wind", "storm", "hail", "roof"]):
        return "Wind damage"
    return text or "Unknown"


# Past claims keyed by actor_id  (single source of truth, also used by fraud_check)
_CLAIMS_HISTORY = {
    "PH-1001": [
        {
            "claim_id": "CLM-2024-0101",
            "date": "2024-04-10",
            "type": "Water damage",
            "description": "Burst pipe in basement, flooring damaged",
            "amount": "$12,500",
            "outcome": "Approved",
            "policy": "HO-2024-1001",
        },
    ],
    "PH-1042": [
        {
            "claim_id": "CLM-2024-0205",
            "date": "2024-08-22",
            "type": "Theft",
            "description": "Laptop and jewelry stolen during break-in",
            "amount": "$4,200",
            "outcome": "Approved",
            "policy": "HO-2024-1042",
        },
    ],
    "PH-1087": [
        {
            "claim_id": "CLM-2024-0310",
            "date": "2024-05-15",
            "type": "Auto collision",
            "description": "Rear-ended at intersection, bumper damage",
            "amount": "$3,800",
            "outcome": "Approved",
            "policy": "AU-2024-1087",
        },
        {
            "claim_id": "CLM-2024-0311",
            "date": "2024-09-02",
            "type": "Water damage",
            "description": "Kitchen pipe leak, cabinet and floor damage",
            "amount": "$8,900",
            "outcome": "Escalated — delayed reporting",
            "policy": "HO-2024-1087",
        },
    ],
    "PH-2001": [],
    "PH-2050": [
        {
            "claim_id": "CLM-2025-0501",
            "date": "2025-11-10",
            "type": "Auto collision",
            "description": "Fender bender in parking lot",
            "amount": "$2,200",
            "outcome": "Approved",
            "policy": "AU-2024-2050",
        },
    ],
    "PH-3001": [],
    "PH-3050": [
        {
            "claim_id": "CLM-2025-0301",
            "date": "2025-03-15",
            "type": "Water damage",
            "description": "Burst pipe in basement, flooring and drywall damaged",
            "amount": "$4,200",
            "outcome": "Approved",
            "policy": "HO-2024-3050",
        },
    ],
}


def evaluate_claims_history(actor_id: str) -> dict:
    """Return a structured claims-history result for a policyholder (pure)."""
    history = _CLAIMS_HISTORY.get(actor_id, [])
    return {
        "actor_id": actor_id,
        "prior_count": len(history),
        "claims": [dict(c) for c in history],
    }


def format_claims_history(result: dict) -> str:
    """Render a structured claims-history result as agent-readable text."""
    actor_id = result["actor_id"]
    claims = result["claims"]
    if not claims:
        return f"No prior claims found for policyholder {actor_id}."

    lines = [f"Claims history for {actor_id} ({len(claims)} prior claim(s)):"]
    for claim in claims:
        lines.append(
            f"  - {claim['claim_id']} | {claim['date']} | {claim['type']} | {claim['amount']} | {claim['outcome']}"
        )
        lines.append(f"    {claim['description']}")
    return "\n".join(lines)


def make_check_claims_history_tool(session_id: str | None = None):
    """Build the check_claims_history tool, recording signals for a session."""

    @tool
    def check_claims_history(actor_id: str) -> str:
        """Check the claims history for a policyholder.

        Args:
            actor_id: The policyholder ID (e.g. PH-1001).

        Returns:
            Summary of past claims including dates, types, amounts, and outcomes.
        """
        result = evaluate_claims_history(actor_id)
        signals.record(session_id, "claims_history", result)
        formatted = format_claims_history(result)
        signals.write_subtool_trace(
            session_id, "check_claims_history", actor_id, f"{result.get('prior_count', 0)} prior claim(s)"
        )
        return formatted

    return check_claims_history


def count_prior_claims_of_type(actor_id: str, claim_type: str) -> int:
    """Count a policyholder's prior claims matching a (normalized) claim type.

    Used by fraud_check for repeat-claim detection. Normalizes both the query
    and the stored claim types so varied wording still matches.
    """
    target = _normalize_claim_type(claim_type)
    history = _CLAIMS_HISTORY.get(actor_id, [])
    return sum(1 for c in history if _normalize_claim_type(c.get("type", "")) == target)
