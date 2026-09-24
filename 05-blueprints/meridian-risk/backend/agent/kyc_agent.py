# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""AgentCore Runtime entrypoint for the KYC onboarding assessment agent.

Streams progress events as the assessment runs so the demo console can show the
multi-agent workflow unfolding rather than a single blocking response.
"""

import json
import logging
import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from orchestrator import KYCOrchestrator

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)

app = BedrockAgentCoreApp()
orchestrator = KYCOrchestrator()

KNOWN_CUSTOMERS = ("CUST001", "CUST002", "CUST003")


def _event(kind: str, **fields) -> str:
    """Format one SSE-friendly JSON line."""
    return json.dumps({"type": kind, **fields})


@app.entrypoint
async def invoke(payload: dict, context=None):
    """Run a KYC assessment and stream progress.

    Expected payload:
        {
          "customer_id": "CUST003",
          "assessment_type": "full" | "credit_only" | "compliance_only",
          "context": "<optional analyst notes>"
        }

    Yields:
        JSON lines: status events, then a final "result" event (or "error").
    """
    customer_id = (payload or {}).get("customer_id", "").strip().upper()
    assessment_type = (payload or {}).get("assessment_type", "full")
    analyst_context = (payload or {}).get("context")

    # AgentCore assigns the runtime session ID; fall back for local runs.
    session_id = getattr(context, "session_id", None) or "local-session"

    if not customer_id:
        yield _event(
            "error",
            message=f"customer_id is required. Known customers: {list(KNOWN_CUSTOMERS)}",
        )
        return

    if assessment_type not in ("full", "credit_only", "compliance_only"):
        yield _event(
            "error",
            message=(
                f"Invalid assessment_type {assessment_type!r}. "
                "Expected full, credit_only, or compliance_only."
            ),
        )
        return

    logger.info("Assessment start: %s (%s)", customer_id, assessment_type)

    yield _event(
        "status",
        stage="recall",
        message=f"Recalling prior assessments for {customer_id} from AgentCore Memory...",
    )
    yield _event(
        "status",
        stage="specialists",
        message=(
            "Running Credit Analyst and Compliance Officer in parallel "
            "against AgentCore Gateway tools..."
        )
        if assessment_type == "full"
        else f"Running {assessment_type.replace('_', ' ')} analysis...",
    )

    try:
        # run_assessment is synchronous and does the heavy lifting; the status
        # events above tell the user what is happening while it runs.
        assessment = orchestrator.run_assessment(
            customer_id=customer_id,
            session_id=session_id,
            assessment_type=assessment_type,
            context=analyst_context,
        )
    except Exception as exc:
        logger.exception("Assessment failed for %s", customer_id)
        yield _event("error", message=f"Assessment failed: {exc}")
        return

    yield _event(
        "status",
        stage="synthesis",
        message="Synthesizing final onboarding recommendation...",
        tools_invoked=assessment.get("tools_invoked", []),
    )
    yield _event("result", assessment=assessment)

    logger.info(
        "Assessment complete: %s -> %s (score %s)",
        customer_id,
        assessment.get("recommendation"),
        assessment.get("overall_risk_score"),
    )


if __name__ == "__main__":
    app.run()
