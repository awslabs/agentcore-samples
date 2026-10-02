"""Turn on the observability that makes a request traceable across all four hops.

Explaining the trace is most of this sample's teaching value, so this is not optional
polish:

  * CloudWatch Transaction Search, so Runtime spans are searchable rather than only
    sampled. Strands already emits OTEL spans; without this they are hard to find.
  * Explicit log retention on every log group the sample owns. Unset means "keep
    forever", which quietly accrues cost.
  * A summary of which log group holds what, since the request crosses four of them.

    python deploy/07_enable_observability.py
    python deploy/07_enable_observability.py --retention-days 30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boto3
from _common import clients, env, interceptor_name, load_env, region, resource_lambda_name
from botocore.exceptions import ClientError


def enable_transaction_search(reg: str) -> None:
    """Best-effort: needs xray:UpdateTraceSegmentDestination and may already be on."""
    xray = boto3.client("xray", region_name=reg)
    try:
        xray.update_trace_segment_destination(Destination="CloudWatchLogs")
        print("  ✓ X-Ray trace segments -> CloudWatch Logs (Transaction Search)")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("ConflictException", "InvalidRequestException"):
            print(f"  • Transaction Search already configured ({code})")
        else:
            print(f"  ⚠ could not enable Transaction Search: {code}")
            print("    Enable it in the CloudWatch console under Application Signals -> Transaction Search.")
    try:
        xray.update_indexing_rule(Name="Default", Rule={"Probabilistic": {"DesiredSamplingPercentage": 100.0}})
        print("  ✓ indexing rule: 100% sampling (fine for a sample; lower it in production)")
    except ClientError as exc:
        print(f"  • indexing rule unchanged ({exc.response['Error']['Code']})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--retention-days", type=int, default=int(env("LOG_RETENTION_DAYS", "14")))
    args = ap.parse_args()
    load_env()
    aws = clients()
    reg = region()

    print("[1/3] Transaction Search")
    enable_transaction_search(reg)

    print(f"\n[2/3] Log retention ({args.retention_days} days)")
    runtime = env("AGENT_RUNTIME_NAME", "xaatodoagent")
    groups = [
        f"/aws/lambda/{resource_lambda_name()}",
        f"/aws/lambda/{interceptor_name()}",
    ]
    # The gateway and runtime use vended log groups whose names include generated ids,
    # so discover rather than guess.
    for prefix in ("/aws/vendedlogs/bedrock-agentcore/gateway", "/aws/bedrock-agentcore/runtimes"):
        paginator = aws["logs"].get_paginator("describe_log_groups")
        for page in paginator.paginate(logGroupNamePrefix=prefix):
            for group in page["logGroups"]:
                name = group["logGroupName"]
                if env("GATEWAY_ID", "") in name or runtime.lower() in name.lower():
                    groups.append(name)

    for group in dict.fromkeys(groups):
        try:
            aws["logs"].put_retention_policy(logGroupName=group, retentionInDays=args.retention_days)
            print(f"  ✓ {group}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ResourceNotFoundException":
                print(f"  • {group} (not created yet — it appears on first use)")
            else:
                raise

    print("\n[3/3] Where to look when tracing a request")
    print("  BFF           stdout of `python frontend/app.py`")
    print(f"  Runtime       /aws/bedrock-agentcore/runtimes/<{runtime}...>   OBO exchange, tool loop")
    print(f"  Interceptor   /aws/lambda/{interceptor_name()}   both ID-JAG legs, claims, cache")
    print(f"  Resource API  /aws/lambda/{resource_lambda_name()}   token validation, per-user rows")
    print("\n  Stitch one request together with:  python scripts/show_trace.py")


if __name__ == "__main__":
    main()
