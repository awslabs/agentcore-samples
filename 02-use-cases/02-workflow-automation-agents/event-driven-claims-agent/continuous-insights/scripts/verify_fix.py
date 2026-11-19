"""Check that a fix worked: the Insights you exported before the fix should stop growing.

Continuous Insights keeps its groups, so a fixed failure does not make an Insight vanish.
Instead, after you redeploy and send the same traffic again:
  - the Insights' affected-session counts and last-seen times stop moving, and
  - no new Insight appears,
  - while the evaluator demonstrably *did* look at the new sessions.
The last point matters: a quiet Insight list also happens when nothing was evaluated yet.

Usage:
    python scripts/verify_fix.py --name ClaimsAgent --baseline insights/<timestamp> [--wait-minutes 25]

Exit code: 0 = fixed, 1 = Insights still growing or new ones appeared, 2 = inconclusive.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from common import ROOT, client, evaluation_clients, load_state, poll, validate_name
from export_insights import list_all


def parse(ts: str) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


def sessions_sent_since(name: str, since: datetime) -> int:
    path = ROOT / f"sessions_{name}.jsonl"
    if not path.exists():
        return 0
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sum(1 for r in rows if r.get("ok") and (parse(r["ts"]) or since) >= since)


def evaluated_since(logs, config_id: str, since: datetime) -> int:
    """Number of evaluation result records written after `since` (one per evaluated session)."""
    group = f"/aws/bedrock-agentcore/evaluations/results/{config_id}"
    start = int(since.timestamp())
    query_id = logs.start_query(
        logGroupName=group, startTime=start, endTime=int(time.time()), queryString="stats count(*) as n"
    )["queryId"]

    def finished() -> dict | None:
        result = logs.get_query_results(queryId=query_id)
        return result if result["status"] == "Complete" else None

    result = poll(finished, timeout_s=120, interval_s=3, what="the CloudWatch Logs query")
    return int(result["results"][0][0]["value"]) if result["results"] else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True)
    parser.add_argument("--baseline", required=True, help="directory written by export_insights.py before the fix")
    parser.add_argument("--since", help="ISO time the post-fix traffic started (default: baseline export time)")
    parser.add_argument("--wait-minutes", type=int, default=0, help="poll up to this long for evaluation to catch up")
    args = parser.parse_args()
    state = load_state(validate_name(args.name))
    _, data = evaluation_clients(state["region"])
    logs = client(state["region"], "logs")

    baseline_dir = Path(args.baseline)
    baseline = json.loads((baseline_dir / "summary.json").read_text(encoding="utf-8"))
    since = (
        parse(args.since)
        if args.since
        else datetime.fromtimestamp((baseline_dir / "summary.json").stat().st_mtime, tz=UTC)
    )
    expected = sessions_sent_since(args.name, since)
    if expected == 0:
        print("No post-baseline sessions found in the session log. Redeploy, then run scripts/drive.py first.")
        return 2

    progress = {"evaluated": 0}

    def covered() -> int | None:
        progress["evaluated"] = evaluated_since(logs, state["online_config_id"], since)
        if progress["evaluated"] < expected:
            print(f"  evaluated {progress['evaluated']}/{expected} post-fix sessions; waiting ...")
            return None
        return progress["evaluated"]

    poll(
        covered,
        timeout_s=args.wait_minutes * 60,
        interval_s=60,
        what="the evaluator to cover the replayed sessions",
        timeout_ok=True,
    )
    evaluated = progress["evaluated"]
    print(f"Post-fix sessions sent: {expected}; evaluated: {evaluated}")
    if evaluated < expected:
        print("INCONCLUSIVE: the evaluator has not covered the new sessions yet. Re-run with --wait-minutes.")
        return 2

    current = {i["agentInsightId"]: i for i in list_all(data, state["service_name"])}
    problems = []
    for insight_id, before in baseline.items():
        now = current.get(insight_id)
        if now is None:
            continue  # archived or removed: not growing
        grew = now.get("affectedSessionCount", 0) > before["affectedSessionCount"]
        later = (parse(str(now.get("lastSeenAt", ""))) or since) > (parse(before["lastSeenAt"]) or since)
        status = "STILL FAILING" if (grew or later) else "quiet"
        print(
            f"  {status:<13} {before['name']}  sessions {before['affectedSessionCount']} -> "
            f"{now.get('affectedSessionCount', 0)}  last seen {now.get('lastSeenAt', 'n/a')}"
        )
        if grew or later:
            problems.append(before["name"])
    new = [i for i_id, i in current.items() if i_id not in baseline]
    for insight in new:
        print(f"  NEW           {insight['name']}")
        problems.append(insight["name"])

    print(
        "\nPASS: no Insight grew and no new Insight appeared."
        if not problems
        else f"\nFAIL: {len(problems)} Insight(s) still growing or new: {problems}"
    )
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
