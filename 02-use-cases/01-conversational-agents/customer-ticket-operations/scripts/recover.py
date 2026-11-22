"""Release a demo that a failed run left locked ("Recovery required" in the console).

    make recover PROFILE=<aws-profile>            # show the lock; release it once its time is up
    make recover PROFILE=<aws-profile> FORCE=1    # release it now

A run that ends in an error keeps its lock on the tenant: the API cannot tell whether the agent is
still working, so it refuses every later run, replay, approval, reset and build change until an
operator releases the lock, which is what this script does. It removes the lock the failed run
holds and starts a new policy session, so an approval the failed run may have recorded cannot
authorize a later close. Tickets and run history are left alone: press Reset demo afterwards.

Without --force a lock is released only after the run's time allowance has passed. That does not
prove the Runtime has stopped, so check the run first. A lock held by an evaluation suite is
released only with --force, because a suite holds several tenants for longer than one allowance.
--force releases every lock it finds.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "functions/api"))
import runs  # noqa: E402  (the API's own session-id format)

# eval_note_probe is bound to the failed run's id; left behind, it blocks every later suite.
LOCK = (
    "busy_until, busy_owner, busy_stage, busy_sub, exclusion_disposition, exclusion_reason, "
    "eval_note_probe"
)
SUITE_OWNERS = ("eval-suite", "eval-cleanup#")
if sys.stdout.isatty():
    GREEN, PINK, DIM, RESET = "\033[38;2;110;231;80m", "\033[38;2;255;77;139m", "\033[2m", "\033[0m"
else:
    GREEN = PINK = DIM = RESET = ""


def locked(table: Any) -> list[dict[str, Any]]:
    """Tenant rows that a run still owns (suite records share the table and are skipped)."""
    rows, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        rows += page["Items"]
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return [r for r in rows if r.get("busy_owner") and "#" not in str(r["customer_id"])]


def release(table: Any, row: dict[str, Any], *, force: bool, now: float) -> str:
    """Release one lock, or say why not: "released", "wait <seconds>", "suite" or "changed"."""
    if str(row["busy_owner"]).startswith(SUITE_OWNERS) and not force:
        return "suite"
    wait = int(row.get("busy_until", 0)) - int(now)
    if wait > 0 and not force:
        return f"wait {wait}"
    # Only the lock that was shown: a suite reuses its owner name, so the expiry is compared too.
    condition = "busy_owner = :owner"
    values = {":owner": row["busy_owner"], ":session": runs.uuid7()}
    if "busy_until" in row:
        condition += " AND busy_until = :until"
        values[":until"] = row["busy_until"]
    else:
        condition += " AND attribute_not_exists(busy_until)"
    try:
        table.update_item(
            Key={"customer_id": row["customer_id"]},
            UpdateExpression=f"SET policy_session = :session REMOVE {LOCK}",
            ConditionExpression=condition,
            ExpressionAttributeValues=values,
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return "changed"
    return "released"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--project", default="ticketops")
    ap.add_argument("--env", default="demo")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--force", action="store_true", help="release a lock whose time is not up")
    a = ap.parse_args()

    import boto3

    ssm = boto3.client("ssm", region_name=a.region)
    name = ssm.get_parameter(Name=f"/{a.project}/{a.env}/app/state_table")["Parameter"]["Value"]
    table = boto3.resource("dynamodb", region_name=a.region).Table(name)

    rows = locked(table)
    if not rows:
        print(f"{GREEN}No run holds a lock: nothing to recover.{RESET}")
        return 0
    failed = 0
    for row in rows:
        print(
            f"{row['customer_id']}: locked by run {row['busy_owner']}"
            f" ({row.get('busy_stage', 'no build')}, {row.get('exclusion_disposition', 'RUNNING')})"
            f"\n  {DIM}{row.get('exclusion_reason', 'the run has not reported an outcome')}{RESET}"
        )
        outcome = release(table, row, force=a.force, now=time.time())
        if outcome == "released":
            print(f"  {GREEN}released; press Reset demo in the console{RESET}")
        elif outcome == "suite":
            failed += 1
            print(
                f"  {PINK}an evaluation suite holds this lock; let it finish,"
                f" or pass FORCE=1 if it has stopped{RESET}"
            )
        elif outcome == "changed":
            failed += 1
            print(f"  {PINK}the lock changed while this ran; run make recover again{RESET}")
        else:
            failed += 1
            print(
                f"  {PINK}the run may still be in flight for {outcome.split()[1]} s;"
                f" wait, or pass FORCE=1 if you know it has stopped{RESET}"
            )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
