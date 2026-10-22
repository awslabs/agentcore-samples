"""Observability — query traces from the aws/spans CloudWatch log group."""

import time

import boto3
from resources import REGION

SPANS_LOG_GROUP = "aws/spans"


def get_recent_traces(harness_name: str | None = None, minutes: int = 10) -> list[dict]:
    """Query aws/spans log group for recent traces from this harness.

    `harness_name` scopes the query to this agent. It used to be accepted and
    then ignored, so the endpoint returned whatever else was emitting spans in
    the account — other harnesses, other samples — presented as this agent's
    traces. The harness publishes spans under the service name
    harness_<harnessName>.DEFAULT.
    """
    logs = boto3.client("logs", region_name=REGION)

    end_time = int(time.time())
    start_time = end_time - (minutes * 60)

    # `sort` has to key on something `stats` produced: @timestamp does not
    # survive the aggregation, so sorting by it left the rows in arbitrary order
    # while looking newest-first. latest(@timestamp) is carried through as
    # last_seen for that purpose.
    scope = ""
    if harness_name:
        scope = f"| filter `resource.attributes.service.name` = 'harness_{harness_name}.DEFAULT'\n"

    query = f"""fields traceId, `status.code` as code, `attributes.http.response.status_code` as http_status
| filter ispresent(traceId) and traceId != ''
{scope}| stats count() as spans,
        sum(code = 'ERROR') as errors,
        sum(http_status >= 500) as faults,
        latest(@timestamp) as last_seen
     by traceId
| sort last_seen desc
| limit 20"""

    try:
        resp = logs.start_query(
            logGroupName=SPANS_LOG_GROUP,
            startTime=start_time,
            endTime=end_time,
            queryString=query,
        )
        query_id = resp["queryId"]

        # Initialised up front: the check below reads `result`, so a zero-trip
        # loop (or a first-call failure) would raise NameError instead of just
        # reporting that no traces were found.
        result = {"status": "Unknown"}
        for _ in range(15):
            time.sleep(2)
            result = logs.get_query_results(queryId=query_id)
            if result["status"] in ("Complete", "Failed", "Cancelled"):
                break

        if result["status"] != "Complete":
            return []

        traces = []
        for row in result.get("results", []):
            fields = {f["field"]: f["value"] for f in row}
            trace_id = fields.get("traceId", "")
            spans = fields.get("spans", "0")

            if trace_id:
                # Derived from the spans rather than hardcoded False: the UI
                # renders these as the health of each trace, so a trace that
                # errored was always reported as healthy.
                traces.append(
                    {
                        "trace_id": trace_id,
                        "spans": int(spans),
                        "has_error": int(float(fields.get("errors", "0") or 0)) > 0,
                        "has_fault": int(float(fields.get("faults", "0") or 0)) > 0,
                    }
                )

        return traces

    except Exception as e:  # noqa: BLE001 - surface the failure to the caller as data
        return [{"error": str(e)}]


def get_transaction_search_status() -> dict:
    """Check if Transaction Search is enabled."""
    xray = boto3.client("xray", region_name=REGION)
    try:
        rules = xray.get_indexing_rules()
        sampling = rules["IndexingRules"][0]["Rule"]["Probabilistic"]["DesiredSamplingPercentage"]
        return {"enabled": True, "sampling_percentage": sampling}
    except Exception as e:  # noqa: BLE001 - surface the failure to the caller as data
        return {"enabled": False, "error": str(e)}
