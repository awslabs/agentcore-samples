"""Observability for AgentCore Memory.

What you learn:
    - Query CloudWatch metrics for memory operations under the
      AWS/Bedrock-AgentCore namespace
    - Read streaming health metrics: StreamPublishingSuccess,
      StreamPublishingFailure, StreamUserError
    - Set up CloudWatch alarms on streaming failures
    - Tail extraction-pipeline logs from your account log group

Memory observability covers two layers:
    1. Data-plane invocations (CreateEvent, RetrieveMemoryRecords, etc.) —
       Invocations / Latency / Errors per memory resource.
    2. Asynchronous ingestion (extraction + consolidation) — Invocations,
       Latency, Errors, NumberOfMemoryRecords per strategy + record streaming
       publish health.

Run:
    python observability.py

SDK note: CloudWatch metrics and Logs are not exposed by MemoryClient —
please use the boto3 `cloudwatch` and `logs` clients directly (shown below).

Prerequisites:
    pip install boto3
    export AWS_REGION=us-east-1   # use any AgentCore-supported region
    export MEMORY_ARN=arn:aws:bedrock-agentcore:us-east-1:111122223333:memory/mem-abc
"""

import os
from datetime import datetime, timedelta, timezone

REGION = os.getenv("AWS_REGION", "us-east-1")


# === boto3 ============================================================
def run_with_boto3() -> None:
    import boto3

    memory_arn = os.environ.get("MEMORY_ARN")
    if not memory_arn:
        print("[boto3] Set MEMORY_ARN to your memory resource ARN.")
        return

    cw = boto3.client("cloudwatch", region_name=REGION)
    logs = boto3.client("logs", region_name=REGION)

    def get_metric_sum(metric_name: str, minutes: int = 60) -> float:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes)
        resp = cw.get_metric_statistics(
            Namespace="AWS/Bedrock-AgentCore",
            MetricName=metric_name,
            Dimensions=[
                {"Name": "Operation", "Value": "MemoryStreamEvent"},
                {"Name": "Resource", "Value": memory_arn},
            ],
            StartTime=start,
            EndTime=end,
            Period=300,
            Statistics=["Sum"],
            Unit="Count",
        )
        return sum(p["Sum"] for p in resp.get("Datapoints", []))

    print(f"[boto3] Streaming metrics for {memory_arn} (last hour):")
    for name in ("StreamPublishingSuccess", "StreamPublishingFailure", "StreamUserError"):
        print(f"  {name:30s} = {get_metric_sum(name)}")

    memory_id = memory_arn.rsplit("/", 1)[-1]
    log_group = f"/aws/bedrock-agentcore/memory/{memory_id}"
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = end_ms - 30 * 60 * 1000
    print(f"\n[boto3] Recent ingestion logs from {log_group}:")
    try:
        events = logs.filter_log_events(logGroupName=log_group, startTime=start_ms, endTime=end_ms)
        for evt in events.get("events", []):
            print(f"  {evt['timestamp']} {evt['message'].strip()}")
    except logs.exceptions.ResourceNotFoundException:
        print("  (log group not found — enable log delivery on the memory)")

    # Optional: alarm on StreamPublishingFailure (uncomment + set SNS_TOPIC_ARN)
    # sns_topic_arn = os.environ["SNS_TOPIC_ARN"]
    # cw.put_metric_alarm(
    #     AlarmName=f"AgentCoreMemory-StreamFailure-{memory_id}",
    #     MetricName="StreamPublishingFailure",
    #     Namespace="AWS/Bedrock-AgentCore",
    #     Dimensions=[
    #         {"Name": "Operation", "Value": "MemoryStreamEvent"},
    #         {"Name": "Resource", "Value": memory_arn},
    #     ],
    #     Statistic="Sum", Period=300, EvaluationPeriods=1,
    #     Threshold=0, ComparisonOperator="GreaterThanThreshold",
    #     TreatMissingData="notBreaching",
    #     AlarmActions=[sns_topic_arn],
    # )


if __name__ == "__main__":
    run_with_boto3()
