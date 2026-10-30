"""Simulated policy database lookup."""

from strands import tool
from tools import signals

# Hardcoded policy records keyed by policy number
_POLICIES = {
    "HO-2024-1001": {
        "policy_number": "HO-2024-1001",
        "holder": "Bob Thompson",
        "actor_id": "PH-1001",
        "type": "Homeowner",
        "status": "Active",
        "effective_date": "2024-01-15",
        "expiry_date": "2027-01-15",
        "deductible": "$1,000",
        "coverage_limit": "$350,000",
        "covered_perils": [
            "Fire",
            "Wind",
            "Hail",
            "Water damage (non-flood)",
            "Theft",
            "Vandalism",
            "Liability",
        ],
        "exclusions": ["Flood", "Earthquake", "Mold (pre-existing)"],
    },
    "AU-2024-1001": {
        "policy_number": "AU-2024-1001",
        "holder": "Bob Thompson",
        "actor_id": "PH-1001",
        "type": "Auto",
        "status": "Active",
        "effective_date": "2024-03-01",
        "expiry_date": "2027-03-01",
        "deductible": "$500",
        "coverage_limit": "$100,000",
        "covered_perils": [
            "Collision",
            "Comprehensive",
            "Liability",
            "Uninsured motorist",
        ],
        "exclusions": ["Racing", "Commercial use"],
    },
    "HO-2024-1042": {
        "policy_number": "HO-2024-1042",
        "holder": "Alice Martinez",
        "actor_id": "PH-1042",
        "type": "Homeowner",
        "status": "Active",
        "effective_date": "2024-06-01",
        "expiry_date": "2027-06-01",
        "deductible": "$2,500",
        "coverage_limit": "$500,000",
        "covered_perils": [
            "Fire",
            "Wind",
            "Hail",
            "Water damage (non-flood)",
            "Theft",
            "Vandalism",
            "Liability",
        ],
        "exclusions": ["Flood", "Earthquake"],
    },
    "AU-2024-1087": {
        "policy_number": "AU-2024-1087",
        "holder": "Charlie Davis",
        "actor_id": "PH-1087",
        "type": "Auto",
        "status": "Active",
        "effective_date": "2024-02-15",
        "expiry_date": "2027-02-15",
        "deductible": "$750",
        "coverage_limit": "$75,000",
        "covered_perils": [
            "Collision",
            "Comprehensive",
            "Liability",
            "Uninsured motorist",
        ],
        "exclusions": ["Racing", "Commercial use"],
    },
    "HO-2024-1087": {
        "policy_number": "HO-2024-1087",
        "holder": "Charlie Davis",
        "actor_id": "PH-1087",
        "type": "Homeowner",
        "status": "Active",
        "effective_date": "2024-04-01",
        "expiry_date": "2027-04-01",
        "deductible": "$1,500",
        "coverage_limit": "$400,000",
        "covered_perils": [
            "Fire",
            "Wind",
            "Hail",
            "Water damage (non-flood)",
            "Theft",
            "Vandalism",
            "Liability",
        ],
        "exclusions": ["Flood", "Earthquake", "Mold (pre-existing)"],
    },
    "HO-2024-2001": {
        "policy_number": "HO-2024-2001",
        "holder": "David Park",
        "actor_id": "PH-2001",
        "type": "Homeowner",
        "status": "Active",
        "effective_date": "2024-08-01",
        "expiry_date": "2027-08-01",
        "deductible": "$1,000",
        "coverage_limit": "$400,000",
        "covered_perils": [
            "Fire",
            "Wind",
            "Hail",
            "Water damage (non-flood)",
            "Theft",
            "Vandalism",
            "Liability",
        ],
        "exclusions": ["Flood", "Earthquake", "Mold (pre-existing)"],
    },
    "AU-2024-2050": {
        "policy_number": "AU-2024-2050",
        "holder": "Sarah Chen",
        "actor_id": "PH-2050",
        "type": "Auto",
        "status": "Active",
        "effective_date": "2024-05-15",
        "expiry_date": "2027-05-15",
        "deductible": "$500",
        "coverage_limit": "$75,000",
        "covered_perils": [
            "Collision",
            "Comprehensive",
            "Liability",
            "Uninsured motorist",
        ],
        "exclusions": ["Racing", "Commercial use"],
    },
    "AU-2024-3001": {
        "policy_number": "AU-2024-3001",
        "holder": "Marcus Rivera",
        "actor_id": "PH-3001",
        "type": "Auto",
        "status": "Active",
        "effective_date": "2024-09-01",
        "expiry_date": "2027-09-01",
        "deductible": "$500",
        "coverage_limit": "$100,000",
        "covered_perils": [
            "Collision",
            "Comprehensive",
            "Liability",
            "Uninsured motorist",
        ],
        "exclusions": ["Racing", "Commercial use"],
    },
    "HO-2024-3001": {
        "policy_number": "HO-2024-3001",
        "holder": "Marcus Rivera",
        "actor_id": "PH-3001",
        "type": "Homeowner",
        "status": "Active",
        "effective_date": "2024-09-01",
        "expiry_date": "2027-09-01",
        "deductible": "$1,500",
        "coverage_limit": "$375,000",
        "covered_perils": [
            "Fire",
            "Wind",
            "Hail",
            "Water damage (non-flood)",
            "Theft",
            "Vandalism",
            "Liability",
        ],
        "exclusions": ["Flood", "Earthquake", "Mold (pre-existing)"],
    },
    "HO-2024-3050": {
        "policy_number": "HO-2024-3050",
        "holder": "Lisa Nguyen",
        "actor_id": "PH-3050",
        "type": "Homeowner",
        "status": "Active",
        "effective_date": "2024-07-01",
        "expiry_date": "2027-07-01",
        "deductible": "$1,000",
        "coverage_limit": "$450,000",
        "covered_perils": [
            "Fire",
            "Wind",
            "Hail",
            "Water damage (non-flood)",
            "Theft",
            "Vandalism",
            "Liability",
        ],
        "exclusions": ["Flood", "Earthquake", "Mold (pre-existing)"],
    },
}


def evaluate_policy(policy_number: str) -> dict:
    """Look up a policy and return a structured result (pure, no side effects).

    Returns a dict with ``found`` plus the policy fields (or just the queried
    number when not found). Used both for agent-facing formatting and for the
    review-task signals collector.
    """
    policy = _POLICIES.get(policy_number)
    if policy is None:
        return {"found": False, "policy_number": policy_number}
    return {"found": True, **policy}


def format_policy(result: dict) -> str:
    """Render a structured policy result as agent-readable text."""
    if not result.get("found"):
        return f"No policy found for number: {result.get('policy_number')}"

    lines = [
        f"Policy: {result['policy_number']}",
        f"Holder: {result['holder']}",
        f"Type: {result['type']}",
        f"Status: {result['status']}",
        f"Effective: {result['effective_date']} — {result['expiry_date']}",
        f"Deductible: {result['deductible']}",
        f"Coverage Limit: {result['coverage_limit']}",
        f"Covered Perils: {', '.join(result['covered_perils'])}",
        f"Exclusions: {', '.join(result['exclusions'])}",
    ]
    return "\n".join(lines)


def make_lookup_policy_tool(session_id: str | None = None):
    """Build the lookup_policy tool, recording structured signals for a session.

    Mirrors the ``make_memory_tools`` factory pattern: the closure captures
    ``session_id`` so the structured result can be recorded to the per-session
    signals collector without exposing ``session_id`` to the model.
    """

    @tool
    def lookup_policy(policy_number: str) -> str:
        """Look up an insurance policy by its policy number.

        Args:
            policy_number: The policy number to look up (e.g. HO-2024-1001).

        Returns:
            Policy details including coverage, deductible, and exclusions.
        """
        result = evaluate_policy(policy_number)
        signals.record(session_id, "policy", result)
        formatted = format_policy(result)
        signals.write_subtool_trace(
            session_id,
            "lookup_policy",
            policy_number,
            f"{'FOUND' if result.get('found') else 'NOT FOUND'} | {result.get('type', '')} | {result.get('status', '')} | deductible={result.get('deductible', '')}",
        )
        return formatted

    return lookup_policy
