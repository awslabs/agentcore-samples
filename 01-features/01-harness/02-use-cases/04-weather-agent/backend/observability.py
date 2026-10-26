"""Observability — query traces from the aws/spans CloudWatch log group."""

import time

import boto3

from resources import REGION

SPANS_LOG_GROUP = "aws/spans"


def get_recent_traces(harness_name: str = None, minutes: int = 10) -> list[dict]:
    """Query aws/spans log group for recent traces from this harness."""
    logs = boto3.client("logs", region_name=REGION)

    end_time = int(time.time())
    start_time = end_time - (minutes * 60)

    query = """fields traceId, @timestamp
| filter ispresent(traceId) and traceId != ''
| stats count() as spans by traceId
| sort @timestamp desc
| limit 20"""

    try:
        resp = logs.start_query(
            logGroupName=SPANS_LOG_GROUP,
            startTime=start_time,
            endTime=end_time,
            queryString=query,
        )
        query_id = resp["queryId"]

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
                traces.append({
                    "trace_id": trace_id,
                    "spans": int(spans),
                    "has_error": False,
                    "has_fault": False,
                })

        return traces

    except Exception as e:
        return [{"error": str(e)}]


def get_transaction_search_status() -> dict:
    """Check if Transaction Search is enabled."""
    xray = boto3.client("xray", region_name=REGION)
    try:
        rules = xray.get_indexing_rules()
        sampling = rules["IndexingRules"][0]["Rule"]["Probabilistic"]["DesiredSamplingPercentage"]
        return {"enabled": True, "sampling_percentage": sampling}
    except Exception as e:
        return {"enabled": False, "error": str(e)}
