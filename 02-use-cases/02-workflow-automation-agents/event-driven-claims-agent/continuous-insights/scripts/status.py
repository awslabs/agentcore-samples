"""Show where Continuous Insights is in its pipeline for the deployed agent.

An empty Insight list does not mean something is broken: Insights only appear after the
service has seen enough repeated failures. This script prints the evidence a person
would otherwise piece together by hand.

Usage:
    python scripts/status.py --name ClaimsAgent
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from common import client, evaluation_clients, load_state, validate_name

METRIC_NAMESPACE = "Bedrock-AgentCore/Evaluations"


def sessions_evaluated(cloudwatch, application: str, label: str, hours: int = 24) -> int:
    """Sessions Failure Analysis has labeled `label` ("Failures Found" or "No Failures Found") in the last `hours`.

    Failure Analysis publishes one CloudWatch metric data point per evaluated session, so SampleCount is a session
    count. Continuous Insights waits for enough failed sessions before it creates Insights, and this is the
    number to watch.
    """
    end = datetime.now(UTC)
    stats = cloudwatch.get_metric_statistics(
        Namespace=METRIC_NAMESPACE,
        MetricName="Builtin.FailureAnalysis",
        Dimensions=[{"Name": "label", "Value": label}, {"Name": "service.name", "Value": application}],
        StartTime=end - timedelta(hours=hours),
        EndTime=end,
        Period=3600,
        Statistics=["SampleCount"],
    )
    return int(sum(point["SampleCount"] for point in stats["Datapoints"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    state = load_state(validate_name(args.name))
    control, data = evaluation_clients(state["region"])

    store = control.get_agent_insight_store(agentInsightStoreId="default-insight-store")
    print(f"Insight Store          : {store['status']} / {store['enablementStatus']}")
    config_id = state.get("online_config_id")
    if config_id:
        config = control.get_online_evaluation_config(onlineEvaluationConfigId=config_id)
        print(
            f"Online evaluation      : {config['status']} / {config['executionStatus']}"
            + (f" ({config['failureReason']})" if config.get("failureReason") else "")
        )
    else:
        print("Online evaluation      : not created (run scripts/setup_insights.py)")

    cloudwatch = client(state["region"], "cloudwatch")
    failed = sessions_evaluated(cloudwatch, state["service_name"], "Failures Found")
    clean = sessions_evaluated(cloudwatch, state["service_name"], "No Failures Found")
    print(f"Sessions evaluated     : {failed} with failures, {clean} clean (last 24 hours)")

    insights, token = [], None
    while True:
        page = data.list_agent_insights(
            applicationName=state["service_name"], maxResults=50, **({"nextToken": token} if token else {})
        )
        insights += page["agentInsights"]
        token = page.get("nextToken")
        if not token:
            break
    print(f"Active Agent Insights  : {len(insights)}  (application {state['service_name']})")
    for item in sorted(insights, key=lambda i: -i.get("affectedSessionCount", 0)):
        print(
            f"  {item['agentInsightId']}  sessions={item.get('affectedSessionCount', 0):<3} "
            f"last seen {item.get('lastSeenAt', 'n/a')}  {item['name']}"
        )
    if not insights:
        print(
            "  none yet. Insights appear after about 20 failed sessions have been evaluated. "
            "Send more claims, or wait for the evaluations of recent sessions to finish."
        )


if __name__ == "__main__":
    main()
