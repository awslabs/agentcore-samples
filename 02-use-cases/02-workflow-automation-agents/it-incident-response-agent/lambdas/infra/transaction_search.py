"""Custom resource: enable CloudWatch Transaction Search (X-Ray → CloudWatch Logs).

STEP: OBSERVABILITY — Enables CloudWatch Transaction Search at the account level so
that OpenTelemetry spans emitted by the AgentCore Runtime are ingested as structured
logs into the `aws/spans` log group. This is the data source the online evaluation
pipeline reads from, and it powers the X-Ray trace / service-map views in the
CloudWatch console.

There is no native CloudFormation resource type for Transaction Search, so this custom
resource calls the X-Ray control-plane APIs:
  - update_trace_segment_destination(Destination='CloudWatchLogs')
  - update_indexing_rule(...)  to set the span indexing sampling percentage

This is an ACCOUNT-LEVEL, REGION-LEVEL setting. It is idempotent: re-applying the same
destination is a no-op. On Delete we intentionally DO NOT disable Transaction Search,
because other stacks/agents in the same account may depend on it — tearing it down
would break their observability. The custom resource simply reports success on Delete.

This Lambda is invoked by the CDK Provider framework (cr.Provider) which guarantees
that a CloudFormation response is always sent, even on unhandled exceptions.
"""

import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Physical ID is stable across Create/Update so CFN treats changes as in-place updates.
PHYSICAL_ID = "transaction-search-config"


def handler(event, context):
    """CDK Provider onEvent handler for enabling Transaction Search."""
    request_type = event["RequestType"]
    props = event.get("ResourceProperties", {})
    # Indexing percentage: how much of span data is indexed for search (1-100).
    # Default 100 for dev/demo so every interaction is searchable + evaluable.
    indexing_percentage = int(props.get("IndexingPercentage", 100))

    logger.info("transaction_search %s (indexing=%s%%)", request_type, indexing_percentage)

    # On Delete: do NOT disable Transaction Search (shared account-level setting).
    if request_type == "Delete":
        logger.info("Delete requested — leaving Transaction Search enabled (shared account setting).")
        return {"PhysicalResourceId": PHYSICAL_ID}

    xray = boto3.client("xray")

    # ─── 1. Route trace segments to CloudWatch Logs (creates aws/spans) ───
    try:
        current = xray.get_trace_segment_destination()
        dest = current.get("Destination")
        status = current.get("Status")
        logger.info("Current trace segment destination: %s (status=%s)", dest, status)
    except Exception as exc:
        logger.warning("get_trace_segment_destination failed (continuing): %s", exc)
        dest = None

    if dest != "CloudWatchLogs":
        xray.update_trace_segment_destination(Destination="CloudWatchLogs")
        logger.info("Set trace segment destination -> CloudWatchLogs")
    else:
        logger.info("Trace segment destination already CloudWatchLogs (no-op)")

    # ─── 2. Set span indexing sampling percentage ─────────────────────────
    # The indexing rule controls what fraction of spans are indexed for
    # Transaction Search queries. 100% ensures online eval sees every session.
    try:
        xray.update_indexing_rule(
            Name="Default",
            Rule={"Probabilistic": {"DesiredSamplingPercentage": float(indexing_percentage)}},
        )
        logger.info("Set indexing rule sampling -> %s%%", indexing_percentage)
    except Exception as exc:
        # Non-fatal: destination routing is the critical part; indexing rule
        # may not be updatable in all account states.
        logger.warning("update_indexing_rule failed (non-fatal): %s", exc)

    return {
        "PhysicalResourceId": PHYSICAL_ID,
        "Data": {
            "Destination": "CloudWatchLogs",
            "IndexingPercentage": str(indexing_percentage),
        },
    }
