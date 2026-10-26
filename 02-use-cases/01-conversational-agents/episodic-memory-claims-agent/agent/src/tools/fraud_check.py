"""Simulated fraud indicator check."""

from datetime import datetime, timezone

from strands import tool
from tools import signals
from tools.claims_history import _normalize_claim_type, count_prior_claims_of_type


def evaluate_fraud_indicators(
    actor_id: str,
    claim_type: str,
    incident_date: str,
    filing_date: str,
    claimed_amount: str,
) -> dict:
    """Compute a structured fraud-risk assessment (pure, no side effects)."""
    flags = []
    risk_score = 0
    delay_days = None

    # Filing date is definitionally "now" — don't trust the model for it.
    # Default to today (UTC) when missing/unparseable.
    today = datetime.now(timezone.utc).date()
    try:
        filed = datetime.strptime(filing_date, "%Y-%m-%d").date()  # noqa: DTZ007 - .date() discards time/tz
    except (ValueError, TypeError):
        filed = today
        filing_date = today.isoformat()

    # Delayed reporting check (incident date comes from the policyholder).
    try:
        incident = datetime.strptime(incident_date, "%Y-%m-%d").date()  # noqa: DTZ007 - .date() discards time/tz
        delay_days = (filed - incident).days
        if delay_days > 5:
            flags.append(f"DELAYED REPORTING: {delay_days} days between incident and filing")
            risk_score += 30
        elif delay_days > 3:
            flags.append(f"MODERATE DELAY: {delay_days} days between incident and filing")
            risk_score += 15
    except (ValueError, TypeError):
        flags.append("Unable to parse incident date for delay check")

    # Repeat claim type check — derived from the shared claims history
    # (single source of truth), with normalized type matching.
    prior_count = count_prior_claims_of_type(actor_id, claim_type)
    if prior_count >= 1:
        normalized = _normalize_claim_type(claim_type)
        flags.append(f"REPEAT CLAIM TYPE: {prior_count} prior {normalized} claim(s) on file")
        risk_score += 25

    # High amount check
    try:
        amount_val = float(claimed_amount.replace("$", "").replace(",", ""))
        if amount_val > 50000:
            flags.append(f"HIGH VALUE CLAIM: {claimed_amount}")
            risk_score += 20
        elif amount_val > 25000:
            flags.append(f"ELEVATED VALUE: {claimed_amount}")
            risk_score += 10
    except ValueError:
        pass

    if risk_score >= 50:
        level = "HIGH"
    elif risk_score >= 25:
        level = "MEDIUM"
    else:
        level = "LOW"

    return {
        "actor_id": actor_id,
        "claim_type": claim_type,
        "incident_date": incident_date,
        "filing_date": filing_date,
        "claimed_amount": claimed_amount,
        "risk_score": risk_score,
        "risk_level": level,
        "delay_days": delay_days,
        "prior_count": prior_count,
        "flags": flags,
    }


def format_fraud_indicators(result: dict) -> str:
    """Render a structured fraud assessment as agent-readable text."""
    lines = [
        f"Fraud Risk Assessment for {result['actor_id']}",
        f"Risk Level: {result['risk_level']} (score: {result['risk_score']}/100)",
        f"Indicators found: {len(result['flags'])}",
    ]
    if result["flags"]:
        for flag in result["flags"]:
            lines.append(f"  ⚠ {flag}")
    else:
        lines.append("  ✓ No fraud indicators detected")
    return "\n".join(lines)


def make_check_fraud_indicators_tool(session_id: str | None = None):
    """Build the check_fraud_indicators tool, recording signals for a session."""

    @tool
    def check_fraud_indicators(
        actor_id: str,
        claim_type: str,
        incident_date: str,
        filing_date: str,
        claimed_amount: str,
    ) -> str:
        """Check for fraud indicators on a new claim.

        Args:
            actor_id: The policyholder ID (e.g. PH-1001).
            claim_type: Type of claim (e.g. Water damage, Auto collision, Theft).
            incident_date: Date the incident occurred (YYYY-MM-DD).
            filing_date: Date the claim was filed (YYYY-MM-DD).
            claimed_amount: Dollar amount being claimed.

        Returns:
            Fraud risk assessment with specific indicators found.
        """
        result = evaluate_fraud_indicators(actor_id, claim_type, incident_date, filing_date, claimed_amount)
        signals.record(session_id, "fraud", result)
        formatted = format_fraud_indicators(result)
        signals.write_subtool_trace(
            session_id,
            "check_fraud_indicators",
            f"{actor_id} | {claim_type} | {incident_date} → {filing_date}",
            f"{result.get('risk_level', '?')} risk (score {result.get('risk_score', '?')}/100) | delay {result.get('delay_days', '?')} day(s) | flags: {result.get('flags', [])}",
        )
        return formatted

    return check_fraud_indicators
