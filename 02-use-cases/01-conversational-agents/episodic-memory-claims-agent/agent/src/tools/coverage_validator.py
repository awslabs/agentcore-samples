"""Simulated coverage validation.

Coverage rules (insurance-correct):
  1. EXCLUSIONS take precedence over covered perils. If an incident matches an
     exclusion it is denied, even if it also resembles a covered peril. This
     prevents "flood" being approved by reclassifying it as generic
     "water damage".
  2. Certain water events are FLOOD (excluded), not covered water damage:
     storm surge, storm-drain/sewer backup, groundwater, rising surface water.
     Covered water damage is sudden/accidental internal water (e.g. burst pipe).
"""

from strands import tool
from tools import signals

# Maps policy type → covered incident types
_COVERAGE_MAP = {
    "Homeowner": {
        "covered": [
            "Water damage",
            "Pipe burst",
            "Burst pipe",
            "Fire",
            "Wind damage",
            "Hail damage",
            "Hailstorm",
            "Theft",
            "Burglary",
            "Vandalism",
            "Lightning",
            "Smoke damage",
            "Wind",
            "Hail",
        ],
        "excluded": [
            "Flood",
            "Earthquake",
            "Mold (pre-existing)",
            "Normal wear and tear",
            "Intentional damage",
            "Nuclear hazard",
        ],
    },
    "Auto": {
        "covered": [
            "Auto collision",
            "Rear-end collision",
            "Side impact",
            "Hit and run",
            "Hit-and-run",
            "Comprehensive",
            "Theft",
            "Vandalism",
            "Weather damage",
            "Animal collision",
            "Collision",
            "Fender bender",
            "Parking",
            "Break-in",
            "Struck",
            "Crash",
        ],
        "excluded": [
            "Racing",
            "Commercial use",
            "DUI-related",
            "Intentional damage",
            "Normal wear and tear",
        ],
    },
}

# Phrases that indicate FLOOD (excluded) even if described as "water damage".
# These are surface/external water events, distinct from sudden internal water.
_FLOOD_INDICATORS = [
    "flood",
    "storm surge",
    "storm drain",
    "sewer backup",
    "sewer back-up",
    "drain backup",
    "drain back-up",
    "backed up",
    "backup from",
    "ground water",
    "groundwater",
    "rising water",
    "surface water",
    "overflow of a body of water",
    "river overflow",
    "water came up from",
    "water rose",
    "rose into",
]


def _matches(term: str, text: str) -> bool:
    """Word-aware containment: the peril/exclusion phrase appears in the text."""
    return term.lower() in text.lower()


def evaluate_coverage(policy_type: str, incident_type: str) -> dict:
    """Determine coverage and return a structured result (pure, no side effects).

    Exclusions take precedence; flood indicators take precedence over generic
    "water damage". Returns ``determination`` in
    {COVERED, EXCLUDED, UNCERTAIN, UNKNOWN_POLICY_TYPE} plus the matched
    peril/exclusion term and a full agent-facing ``message``.
    """
    coverage = _COVERAGE_MAP.get(policy_type)
    if coverage is None:
        return {
            "policy_type": policy_type,
            "incident_type": incident_type,
            "determination": "UNKNOWN_POLICY_TYPE",
            "matched_term": None,
            "message": (f"Unknown policy type: {policy_type}. Valid types: Homeowner, Auto."),
        }

    text = incident_type.lower()

    # --- 1. Flood detection (exclusion) takes precedence over "water damage" ---
    if policy_type == "Homeowner":
        for indicator in _FLOOD_INDICATORS:
            if indicator in text:
                return {
                    "policy_type": policy_type,
                    "incident_type": incident_type,
                    "determination": "EXCLUDED",
                    "matched_term": indicator,
                    "message": (
                        f"❌ EXCLUDED: '{incident_type}' is treated as FLOOD damage, which is "
                        f"excluded under the {policy_type} policy.\n"
                        f"Reason: matched flood indicator '{indicator}'. Flood/surface-water and "
                        f"sewer/drain backup are not covered (separate flood insurance applies). "
                        f"Only sudden, accidental internal water (e.g. burst pipe) is covered."
                    ),
                }

    # --- 2. Explicit exclusions take precedence over covered perils ---
    for exclusion in coverage["excluded"]:
        if _matches(exclusion, text):
            return {
                "policy_type": policy_type,
                "incident_type": incident_type,
                "determination": "EXCLUDED",
                "matched_term": exclusion,
                "message": (
                    f"❌ EXCLUDED: '{incident_type}' is explicitly excluded under {policy_type} policy.\n"
                    f"Matching exclusion: {exclusion}"
                ),
            }

    # --- 3. Covered perils ---
    for peril in coverage["covered"]:
        if _matches(peril, text):
            return {
                "policy_type": policy_type,
                "incident_type": incident_type,
                "determination": "COVERED",
                "matched_term": peril,
                "message": (
                    f"✅ COVERED: '{incident_type}' is covered under {policy_type} policy.\nMatching peril: {peril}"
                ),
            }

    return {
        "policy_type": policy_type,
        "incident_type": incident_type,
        "determination": "UNCERTAIN",
        "matched_term": None,
        "message": (
            f"⚠ UNCERTAIN: '{incident_type}' is not explicitly listed as covered or excluded "
            f"under {policy_type} policy. Manual review recommended."
        ),
    }


def format_coverage(result: dict) -> str:
    """Render a structured coverage result as agent-readable text."""
    return result["message"]


def make_validate_coverage_tool(session_id: str | None = None):
    """Build the validate_coverage tool, recording signals for a session."""

    @tool
    def validate_coverage(policy_type: str, incident_type: str) -> str:
        """Validate whether an incident is covered under a policy type.

        Args:
            policy_type: The type of policy (Homeowner or Auto).
            incident_type: The incident being claimed. Include cause details where
                relevant (e.g. "water damage from storm drain backup" vs
                "water damage from burst pipe") so flood can be distinguished from
                covered water damage.

        Returns:
            Coverage determination with explanation. Exclusions take precedence.
        """
        result = evaluate_coverage(policy_type, incident_type)
        signals.record(session_id, "coverage", result)
        formatted = format_coverage(result)
        signals.write_subtool_trace(
            session_id,
            "validate_coverage",
            f"{policy_type} | {incident_type}",
            f"{result.get('determination', '?')} — {result.get('message', '')[:80]}",
        )
        return formatted

    return validate_coverage
