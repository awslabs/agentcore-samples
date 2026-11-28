"""Bounded Transaction Search readiness check, retaining shared settings on Delete.

Default is verify-only. Mutation requires both explicit approval and the exact
account/region owner string; the CDK grants write actions only in that mode.
The account owner must provision the Logs delivery policy separately.
"""

import time

import boto3
from botocore.config import Config

PHYSICAL_ID = "transaction-search-config"
SDK_CONFIG = Config(connect_timeout=3, read_timeout=5, retries={"total_max_attempts": 1})
MAX_POLLS = 12
POLL_SECONDS = 5


def _observe(xray):
    destination = xray.get_trace_segment_destination()
    token = None
    for _ in range(10):
        page = xray.get_indexing_rules(**({"NextToken": token} if token else {}))
        for rule in page.get("IndexingRules", []):
            if rule["Name"] == "Default":
                value = rule["Rule"]["Probabilistic"]
                return destination, value["DesiredSamplingPercentage"], value.get("ActualSamplingPercentage")
        token = page.get("NextToken")
        if not token:
            break
    raise RuntimeError("Default indexing rule missing or pagination limit exceeded")


def handler(event, context):
    if event["RequestType"] == "Delete":
        return {"PhysicalResourceId": PHYSICAL_ID}  # Never reset shared settings.
    if event["RequestType"] not in ("Create", "Update"):
        raise ValueError("Unsupported lifecycle request")
    props = event["ResourceProperties"]
    account, region = props["Account"], props["Region"]
    # Lambda ARN and CFN StackId must both match the configured account/region.
    for arn in (context.invoked_function_arn, event["StackId"]):
        parts = arn.split(":")
        if len(parts) < 6 or parts[3:5] != [region, account]:
            raise ValueError("Transaction Search account/region mismatch")
    percentage = float(props["IndexingPercentage"])
    if not 1 <= percentage <= 100:
        raise ValueError("IndexingPercentage must be 1..100")
    mutate = props.get("AllowMutation") == "true"
    if mutate and props.get("AccountOwnerApproval") != f"{account}/{region}":
        raise ValueError("Explicit account-owner approval is required")
    xray = boto3.client("xray", region_name=region, config=SDK_CONFIG)
    destination, desired, actual = _observe(xray)
    if mutate:
        if destination.get("Destination") != "CloudWatchLogs":
            xray.update_trace_segment_destination(Destination="CloudWatchLogs")
        if desired != percentage:
            # Required write failures propagate; never report requested as observed.
            xray.update_indexing_rule(Name="Default", Rule={"Probabilistic": {"DesiredSamplingPercentage": percentage}})
    elif destination.get("Destination") != "CloudWatchLogs" or desired != percentage:
        raise RuntimeError("Account owner must configure Transaction Search; verify-only mode made no changes")
    for attempt in range(MAX_POLLS):
        if context.get_remaining_time_in_millis() < 25000:
            break
        destination, desired, actual = _observe(xray)
        if (
            destination.get("Destination") == "CloudWatchLogs"
            and destination.get("Status") == "ACTIVE"
            and desired == percentage
            and actual == percentage
        ):
            return {
                "PhysicalResourceId": PHYSICAL_ID,
                "Data": {
                    "Destination": destination["Destination"],
                    "Status": destination["Status"],
                    "DesiredIndexingPercentage": str(desired),
                    "ActualIndexingPercentage": str(actual),
                },
            }
        if attempt < MAX_POLLS - 1 and context.get_remaining_time_in_millis() > 30000:
            time.sleep(POLL_SECONDS)
        else:
            break
    raise TimeoutError("Transaction Search readiness not observed within bounded wait")
