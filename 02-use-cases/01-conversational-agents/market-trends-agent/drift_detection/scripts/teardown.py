"""Remove everything the drift detector created, leaving the sample intact.

Deletes only resources this feature owns: the detector Lambda, its role and
schedule, the state table, the drift alarms, and the dashboard.

Does not touch the agent, its runtime, the evaluators, the evaluator Lambdas, the
online evaluation config, or the evaluation results. Use the sample's own
cleanup.py and the evaluator cleanup steps for those.

Usage:
  teardown.py --dry-run
  teardown.py --yes
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import List

import boto3
from botocore.exceptions import ClientError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("teardown-drift-detector")

REGION = os.environ.get("AWS_REGION", "us-east-1")
AGENT_ROOT = Path(__file__).resolve().parent.parent.parent

TABLE_NAME = "market-trends-drift-detector-state"
ROLE_NAME = "MarketTrendsDriftDetectorRole"
ROLE_POLICY_NAME = "MarketTrendsDriftDetectorPermissions"
FUNCTION_NAME = "market-trends-drift-detector"
SCHEDULE_NAME = "market-trends-drift-detector-schedule"
DASHBOARD_NAME = "MarketTrendsDriftDetection"
ALARM_PREFIX = "MarketTrends-Drift"
LOG_GROUP = f"/aws/lambda/{FUNCTION_NAME}"


def _skip_missing(action, *codes: str):
    """Run a delete, treating "already gone" as success."""
    tolerated = set(codes) | {"ResourceNotFoundException", "NoSuchEntity"}
    try:
        action()
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] in tolerated:
            return False
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="list what would be deleted")
    group.add_argument("--yes", action="store_true", help="actually delete")
    args = parser.parse_args()

    session = boto3.Session(region_name=REGION)
    cw = session.client("cloudwatch")

    alarms: List[str] = []
    token = None
    while True:
        resp = cw.describe_alarms(AlarmNamePrefix=ALARM_PREFIX, **({"NextToken": token} if token else {}))
        alarms.extend(a["AlarmName"] for a in resp.get("MetricAlarms", []))
        token = resp.get("NextToken")
        if not token:
            break

    planned = {
        "lambda": FUNCTION_NAME,
        "lambdaLogGroup": LOG_GROUP,
        "scheduleRule": SCHEDULE_NAME,
        "iamRole": ROLE_NAME,
        "stateTable": TABLE_NAME,
        "alarms": alarms,
        "dashboard": DASHBOARD_NAME,
        "preserved": [
            "the agent and its AgentCore Runtime",
            "the evaluators and their Lambdas",
            "the online evaluation config and its results",
            "published metrics in the drift namespace (metrics cannot be deleted, they expire on their own retention)",
        ],
    }

    if args.dry_run:
        print(json.dumps(planned, indent=2))
        return 0

    events = session.client("events")
    lam = session.client("lambda")
    iam = session.client("iam")
    ddb = session.client("dynamodb")
    logs = session.client("logs")

    # Targets must be removed before the rule can be deleted.
    _skip_missing(lambda: events.remove_targets(Rule=SCHEDULE_NAME, Ids=["detector"]))
    _skip_missing(lambda: events.delete_rule(Name=SCHEDULE_NAME))
    LOG.info("Removed schedule %s", SCHEDULE_NAME)

    _skip_missing(lambda: lam.delete_function(FunctionName=FUNCTION_NAME))
    LOG.info("Removed Lambda %s", FUNCTION_NAME)

    _skip_missing(lambda: logs.delete_log_group(logGroupName=LOG_GROUP))
    LOG.info("Removed log group %s", LOG_GROUP)

    # Inline policies must be dropped before the role can be deleted.
    _skip_missing(lambda: iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY_NAME))
    _skip_missing(lambda: iam.delete_role(RoleName=ROLE_NAME))
    LOG.info("Removed role %s", ROLE_NAME)

    _skip_missing(lambda: ddb.delete_table(TableName=TABLE_NAME))
    LOG.info("Removed state table %s", TABLE_NAME)

    if alarms:
        cw.delete_alarms(AlarmNames=alarms)
        LOG.info("Removed %d alarms", len(alarms))

    _skip_missing(lambda: cw.delete_dashboards(DashboardNames=[DASHBOARD_NAME]))
    LOG.info("Removed dashboard %s", DASHBOARD_NAME)

    out = AGENT_ROOT / "drift_detection" / "scripts" / ".deploy_output.json"
    if out.exists():
        out.unlink()

    print(json.dumps({"tornDown": True, "preserved": planned["preserved"]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
