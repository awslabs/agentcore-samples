"""Gateway tool: query_kb.

Wraps Bedrock Knowledge Base Retrieve so the KB is exposed as a gateway tool.
The agent calls this to look up IT runbook guidance.
"""

import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

KB_ID = os.environ["KB_ID"]
# Region is auto-injected by Lambda runtime; explicit for consistency with other tool Lambdas
_kb = boto3.client("bedrock-agent-runtime", region_name=os.environ.get("AWS_REGION", "us-west-2"))

# Input validation constants
MAX_QUERY_LENGTH = 1000
MIN_TOP_K = 1
MAX_TOP_K = 20


# Gateway Lambda targets return the tool result DIRECTLY to the model — no
# API-Gateway-style {statusCode, body} envelope. Errors are returned as a
# plain {"error": ...} object so the model can read them.
def _ok(body: dict) -> dict:
    return body


def _err(message: str) -> dict:
    return {"error": message}


def lambda_handler(event, context):
    """Search the IT runbook knowledge base."""
    # STEP: REASON — Retrieve runbook guidance for the agent's decision-making
    logger.info("query_kb invoked")

    query = event.get("query")
    top_k = event.get("top_k", 4)

    if not query:
        return _err("query is required")

    # Input validation: enforce type and length constraints
    if not isinstance(query, str):
        return _err("query must be a string")
    if len(query) > MAX_QUERY_LENGTH:
        return _err(f"query exceeds maximum length of {MAX_QUERY_LENGTH} characters")

    try:
        top_k = int(top_k)
    except (TypeError, ValueError):
        return _err("top_k must be an integer")
    if top_k < MIN_TOP_K or top_k > MAX_TOP_K:
        return _err(f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K}")

    try:
        resp = _kb.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": top_k}},
        )
    except Exception as exc:
        logger.exception("KB retrieve failed")
        return _err(f"knowledge base query failed: {exc}")

    results = [
        {
            "score": r.get("score"),
            "content": r.get("content", {}).get("text", ""),
            "source": (r.get("location", {}).get("s3Location") or {}).get("uri"),
        }
        for r in resp.get("retrievalResults", [])
    ]

    return _ok({"query": query, "results": results})
