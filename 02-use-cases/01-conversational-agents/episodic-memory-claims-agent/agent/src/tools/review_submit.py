"""Agent tool: submit a completed claim for human adjuster review (Phase 4).

Human-mode only. After the investigation has run, the agent calls
`submit_claim_for_human_review(summary)`. The tool reads the structured signals
captured by the investigation tools (from the in-process collector, keyed by
session_id) and POSTs them to the reviews API's IAM-authed create endpoint.

The create endpoint is `POST /reviews` with AWS_IAM auth, so we sign the request
with SigV4 using the local/backend AWS credentials (the agent runtime is trusted
infra; creating a review task is a privileged backend action, not a user action).
"""

import logging
from urllib.parse import urlparse

import boto3
import requests
from aws_requests_auth.aws_auth import AWSRequestsAuth
from strands import tool
from tools import signals

logger = logging.getLogger("claims-demo.review_submit")


def _sigv4_auth(api_url: str, region: str) -> AWSRequestsAuth:
    host = urlparse(api_url).netloc
    creds = boto3.Session().get_credentials().get_frozen_credentials()
    return AWSRequestsAuth(
        aws_access_key=creds.access_key,
        aws_secret_access_key=creds.secret_key,
        aws_token=creds.token,
        aws_host=host,
        aws_region=region,
        aws_service="execute-api",
    )


def make_submit_for_review_tool(
    session_id: str,
    actor_id: str,
    api_url: str,
    region: str = "us-east-1",
    memory_id: str | None = None,
):
    """Build the submit_claim_for_human_review tool bound to this claim/session."""

    @tool
    def submit_claim_for_human_review(summary: str) -> str:
        """File the completed claim for human adjuster review.

        Call this exactly once, after the investigation has run, to hand the
        claim off to a human claims adjuster. Do not mention this to the customer.

        Args:
            summary: A short, factual one- or two-sentence summary of the claim.

        Returns:
            Confirmation that the claim was filed for review, or an error note.
        """
        collected = signals.get(session_id)
        if not collected or ("coverage" not in collected and "fraud" not in collected):
            return (
                "Cannot file for review yet — the investigation has not produced "
                "factual signals for this claim. Run the investigation first."
            )

        body = {
            "session_id": session_id,
            "actor_id": actor_id,
            "signals": collected,
            "description": summary or "",
            "memory_id": memory_id,
        }
        try:
            resp = requests.post(
                f"{api_url.rstrip('/')}/reviews",
                auth=_sigv4_auth(api_url, region),
                json=body,
                timeout=10,
            )
            if resp.status_code in (200, 201):
                signals.clear(session_id)
                logger.info("Filed review task for session %s", session_id)
                return "Claim filed for adjuster review."
            logger.error("Create review failed: %s %s", resp.status_code, resp.text[:200])
            return f"Could not file the claim for review (status {resp.status_code})."
        except requests.exceptions.RequestException as e:
            logger.error("submit_claim_for_human_review failed: %s", e)
            return "Could not file the claim for review at this time."

    return submit_claim_for_human_review
