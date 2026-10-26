"""Local CloudWatch tools for the monitoring agent.

Read-only CloudWatch Logs and Metrics operations exposed as Strands tools,
backed directly by boto3 inside the runtime (no external gateway). Region comes
from AWS_REGION (set by the AgentCore Runtime environment).
"""

import os
from typing import Optional

import boto3
from strands import tool

_REGION = os.getenv("AWS_REGION", "us-west-2")
_logs = boto3.client("logs", region_name=_REGION)
_cw = boto3.client("cloudwatch", region_name=_REGION)


@tool
def describe_log_groups(name_prefix: Optional[str] = None, limit: int = 20) -> list:
    """List CloudWatch log groups, optionally filtered by name prefix.

    Args:
        name_prefix: Only return log groups whose name starts with this prefix.
        limit: Maximum number of log groups to return (1-50).
    """
    kwargs = {"limit": max(1, min(limit, 50))}
    if name_prefix:
        kwargs["logGroupNamePrefix"] = name_prefix
    resp = _logs.describe_log_groups(**kwargs)
    return [
        {
            "logGroupName": g["logGroupName"],
            "storedBytes": g.get("storedBytes"),
            "retentionInDays": g.get("retentionInDays"),
        }
        for g in resp.get("logGroups", [])
    ]


@tool
def describe_log_streams(log_group_name: str, limit: int = 10) -> list:
    """List the most recent log streams in a log group.

    Args:
        log_group_name: The CloudWatch log group name.
        limit: Maximum number of streams to return (1-50).
    """
    resp = _logs.describe_log_streams(
        logGroupName=log_group_name,
        orderBy="LastEventTime",
        descending=True,
        limit=max(1, min(limit, 50)),
    )
    return [
        {
            "logStreamName": s["logStreamName"],
            "lastEventTimestamp": s.get("lastEventTimestamp"),
        }
        for s in resp.get("logStreams", [])
    ]


@tool
def filter_log_events(
    log_group_name: str,
    filter_pattern: str = "",
    limit: int = 20,
) -> list:
    """Search log events across a log group with an optional filter pattern.

    Args:
        log_group_name: The CloudWatch log group name.
        filter_pattern: CloudWatch Logs filter pattern (empty matches all).
        limit: Maximum number of events to return (1-100).
    """
    resp = _logs.filter_log_events(
        logGroupName=log_group_name,
        filterPattern=filter_pattern,
        limit=max(1, min(limit, 100)),
    )
    return [
        {
            "timestamp": e.get("timestamp"),
            "logStreamName": e.get("logStreamName"),
            "message": e.get("message"),
        }
        for e in resp.get("events", [])
    ]


@tool
def get_log_events(log_group_name: str, log_stream_name: str, limit: int = 20) -> list:
    """Read the most recent log events from a specific log stream.

    Args:
        log_group_name: The CloudWatch log group name.
        log_stream_name: The log stream within that group.
        limit: Maximum number of events to return (1-100).
    """
    resp = _logs.get_log_events(
        logGroupName=log_group_name,
        logStreamName=log_stream_name,
        limit=max(1, min(limit, 100)),
        startFromHead=False,
    )
    return [
        {"timestamp": e.get("timestamp"), "message": e.get("message")}
        for e in resp.get("events", [])
    ]


@tool
def list_metrics(
    namespace: Optional[str] = None, metric_name: Optional[str] = None
) -> list:
    """List available CloudWatch metrics, optionally filtered.

    Args:
        namespace: Metric namespace (for example, AWS/Lambda).
        metric_name: Specific metric name to filter on.
    """
    kwargs = {}
    if namespace:
        kwargs["Namespace"] = namespace
    if metric_name:
        kwargs["MetricName"] = metric_name
    resp = _cw.list_metrics(**kwargs)
    return [
        {
            "Namespace": m.get("Namespace"),
            "MetricName": m.get("MetricName"),
            "Dimensions": m.get("Dimensions"),
        }
        for m in resp.get("Metrics", [])[:50]
    ]


@tool
def get_metric_statistics(
    namespace: str,
    metric_name: str,
    period_seconds: int = 300,
    hours_back: int = 3,
    stat: str = "Average",
) -> dict:
    """Get statistics for a CloudWatch metric over a recent time window.

    Args:
        namespace: Metric namespace (for example, AWS/EC2).
        metric_name: The metric name (for example, CPUUtilization).
        period_seconds: Aggregation period in seconds.
        hours_back: How many hours of history to query.
        stat: One of Average, Sum, Minimum, Maximum, SampleCount.
    """
    from datetime import datetime, timedelta, timezone

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=max(1, hours_back))
    resp = _cw.get_metric_statistics(
        Namespace=namespace,
        MetricName=metric_name,
        StartTime=start,
        EndTime=end,
        Period=max(60, period_seconds),
        Statistics=[stat],
    )
    points = sorted(resp.get("Datapoints", []), key=lambda d: d["Timestamp"])
    return {
        "label": resp.get("Label"),
        "datapoints": [
            {
                "timestamp": p["Timestamp"].isoformat(),
                stat: p.get(stat),
                "unit": p.get("Unit"),
            }
            for p in points
        ],
    }


@tool
def list_dashboards(name_prefix: Optional[str] = None) -> list:
    """List CloudWatch dashboards, optionally filtered by name prefix.

    Args:
        name_prefix: Only return dashboards whose name starts with this prefix.
    """
    kwargs = {}
    if name_prefix:
        kwargs["DashboardNamePrefix"] = name_prefix
    resp = _cw.list_dashboards(**kwargs)
    return [
        {
            "DashboardName": d.get("DashboardName"),
            "LastModified": str(d.get("LastModified")),
        }
        for d in resp.get("DashboardEntries", [])
    ]


ALL_TOOLS = [
    describe_log_groups,
    describe_log_streams,
    filter_log_events,
    get_log_events,
    list_metrics,
    get_metric_statistics,
    list_dashboards,
]
