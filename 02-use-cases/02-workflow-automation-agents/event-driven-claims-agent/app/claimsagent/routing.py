"""Pure routing logic for the dual-agent claims pipeline.

Routing matrix:
    decision == REJECT                  → REJECT       (notify only)
    routing  == AUTO_APPROVE            → AUTO_APPROVE (create + notify)
    routing  == HUMAN_REVIEW            → HUMAN_REVIEW (create + escalate + notify)

Both agents MUST call their respective structured-output tools (submit_decision,
submit_validation). If they fail to do so, routing defaults to HUMAN_REVIEW —
the safe fallback that ensures no claim is auto-approved without proper validation.
"""

import logging

from config import AUTO_APPROVE_THRESHOLD

log = logging.getLogger(__name__)

# Routing outcomes
REJECT = "REJECT"
AUTO_APPROVE = "AUTO_APPROVE"
HUMAN_REVIEW = "HUMAN_REVIEW"


def resolve_decision(structured_decision: dict | None, processor_response: str) -> str:
    """Return the processor's ACCEPT/REJECT decision.

    Requires the structured-output tool result. If the agent failed to call
    submit_decision, defaults to REJECT (safe: no unintended approvals).
    """
    if structured_decision:
        return structured_decision["decision"].upper()

    log.error(
        "Claims Processor did not call submit_decision tool. Defaulting to REJECT. Agent response: %.200s",
        processor_response,
    )
    return REJECT


def resolve_routing(structured_validation: dict | None, validator_response: str) -> tuple[int, str]:
    """Return ``(confidence, routing)`` from the validator's assessment.

    Requires the structured-output tool result. If the agent failed to call
    submit_validation, defaults to (0, HUMAN_REVIEW) — ensuring human oversight.
    """
    if structured_validation:
        confidence = structured_validation["confidence"]
        routing = structured_validation["routing"].upper()
        # Enforce threshold consistency even if the validator's routing disagrees
        if confidence >= AUTO_APPROVE_THRESHOLD and routing == HUMAN_REVIEW:
            log.info(
                "Validator said HUMAN_REVIEW but confidence %d >= threshold %d", confidence, AUTO_APPROVE_THRESHOLD
            )
        elif confidence < AUTO_APPROVE_THRESHOLD and routing == AUTO_APPROVE:
            log.warning(
                "Validator said AUTO_APPROVE but confidence %d < threshold %d; overriding to HUMAN_REVIEW",
                confidence,
                AUTO_APPROVE_THRESHOLD,
            )
            routing = HUMAN_REVIEW
        return confidence, routing

    log.error(
        "Validation Agent did not call submit_validation tool. Defaulting to HUMAN_REVIEW. Agent response: %.200s",
        validator_response,
    )
    return 0, HUMAN_REVIEW


def decide_action(decision: str, routing: str) -> str:
    """Combine decision + routing into the final action.

    A REJECT decision always wins (the claim is denied regardless of validator
    routing). Otherwise the validator's routing determines auto-approve vs human
    review.
    """
    if decision == REJECT:
        return REJECT
    if routing == AUTO_APPROVE:
        return AUTO_APPROVE
    return HUMAN_REVIEW
