"""Count how the claims agent routed each claim: approved automatically, sent to human review, or rejected.

Insight counts tell you which failures stopped. This script shows what that means for the claims:
it reads the claims agent's runtime log for a time window and counts the outcome of each session,
grouped by the scenario that drive.py put in the session ID. It only reads from your account.

Usage:
    python scripts/outcomes.py --name ClaimsAgent --since 2026-01-01T10:00:00Z [--until 2026-01-01T10:45:00Z]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from botocore.exceptions import BotoCoreError, ClientError
from common import client, load_state, poll, validate_name

# The claims agent logs one of these messages for each claim it finishes.
OUTCOMES = {
    "Claim created (auto-approved)": "approved automatically",
    "Human review requested": "sent to human review",
    "Rejection notification sent": "rejected",
}
# Only the JSON log records carry the session ID; the plain-text copies of the same message do not.
QUERY = (
    'filter @message like /"sessionId"/ and @message like '
    r"/Claim created \(auto-approved\)|Human review requested|Rejection notification sent/"
    " | fields @message | limit 10000"
)
SESSION = re.compile(r'"sessionId": "ci-(?P<scenario>[a-z-]+)-[0-9a-f]{32}"')


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def classify(message: str) -> tuple[str, str] | None:
    """Return (scenario, outcome) for one log record, or None if it is not an outcome record."""
    match = SESSION.search(message)
    outcome = next((label for marker, label in OUTCOMES.items() if marker in message), None)
    if not match or not outcome:
        return None
    return match.group("scenario"), outcome


def tally(messages: list[str]) -> dict[str, Counter]:
    """Outcome counts per scenario. Each session is counted once, by its first outcome record."""
    by_scenario: dict[str, Counter] = defaultdict(Counter)
    seen: set[str] = set()
    for message in messages:
        result = classify(message)
        session = SESSION.search(message)
        if result is None or session is None or session.group(0) in seen:
            continue
        seen.add(session.group(0))
        scenario, outcome = result
        by_scenario[scenario][outcome] += 1
    return by_scenario


def fetch(logs, log_group: str, start: datetime, end: datetime) -> list[str]:
    query_id = logs.start_query(
        logGroupName=log_group, startTime=int(start.timestamp()), endTime=int(end.timestamp()), queryString=QUERY
    )["queryId"]

    def done() -> dict | None:
        result = logs.get_query_results(queryId=query_id)
        return result if result["status"] in ("Complete", "Failed", "Cancelled", "Timeout") else None

    result = poll(done, timeout_s=300, interval_s=3, what="the CloudWatch Logs Insights query")
    if result["status"] != "Complete":
        sys.exit(f"The log query ended with status {result['status']}.")
    return [field["value"] for row in result["results"] for field in row if field["field"] == "@message"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--since", required=True, help="ISO time when the claims started, for example 2026-01-01T10:00:00Z"
    )
    parser.add_argument("--until", help="ISO end of the window (default: --since plus 60 minutes)")
    args = parser.parse_args()
    state = load_state(validate_name(args.name))
    start = parse_time(args.since)
    end = parse_time(args.until) if args.until else start + timedelta(minutes=60)
    try:
        counts = tally(fetch(client(state["region"], "logs"), state["log_group"], start, end))
    except (ClientError, BotoCoreError) as err:
        sys.exit(f"Could not read the runtime log: {err}")
    if not counts:
        print("No finished claims found in this window. Check --since and --until.")
        return 1
    labels = list(OUTCOMES.values())
    print(f"{'Scenario':28s}" + "".join(f"{label:>24s}" for label in labels))
    totals: Counter = Counter()
    for scenario in sorted(counts):
        totals.update(counts[scenario])
        print(f"{scenario:28s}" + "".join(f"{counts[scenario][label]:>24d}" for label in labels))
    print(f"{'Total':28s}" + "".join(f"{totals[label]:>24d}" for label in labels))
    return 0


if __name__ == "__main__":
    sys.exit(main())
