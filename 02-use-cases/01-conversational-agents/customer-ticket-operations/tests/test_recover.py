"""`make recover` against an in-memory state table: no AWS account, credentials or network."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ticketops_recover_cli", ROOT / "scripts/recover.py")
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


class Conflict(Exception):
    pass


class Table:
    """Just enough of a DynamoDB Table for the recovery update and its condition."""

    meta = SimpleNamespace(
        client=SimpleNamespace(exceptions=SimpleNamespace(ConditionalCheckFailedException=Conflict))
    )

    def __init__(self, *rows):
        self.rows = {r["customer_id"]: dict(r) for r in rows}

    def scan(self, **_):
        return {"Items": [dict(r) for r in self.rows.values()]}

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeValues):
        row = self.rows[Key["customer_id"]]
        owner, _, expiry = ConditionExpression.partition(" AND ")
        assert owner == "busy_owner = :owner"
        assert expiry in ("busy_until = :until", "attribute_not_exists(busy_until)")
        if row.get("busy_owner") != ExpressionAttributeValues[":owner"]:
            raise Conflict
        if row.get("busy_until") != ExpressionAttributeValues.get(":until"):
            raise Conflict
        set_part, remove_part = UpdateExpression.removeprefix("SET ").split(" REMOVE ")
        assert set_part == "policy_session = :session"
        row["policy_session"] = ExpressionAttributeValues[":session"]
        for field in remove_part.split(", "):
            row.pop(field, None)


def stuck(**over):
    return {
        "customer_id": "customer-a",
        "busy_owner": "run-1",
        "busy_until": 1000,
        "busy_stage": "v3",
        "busy_sub": "sub-1",
        "exclusion_disposition": "UNKNOWN",
        "exclusion_reason": "Runtime/approval or evidence transition incomplete",
        "policy_session": "old-session",
        "approvals": "[]",
        "active_stage": "v3",
        **over,
    }


def test_only_tenant_rows_a_run_still_owns_are_found():
    table = Table(
        stuck(),
        {"customer_id": "customer-b"},
        {"customer_id": "evals#v5", "busy_owner": "suite-1", "suite": "{}"},
    )
    assert [r["customer_id"] for r in cli.locked(table)] == ["customer-a"]


def test_lock_past_its_time_is_released_and_the_policy_session_replaced():
    table = Table(stuck())
    assert cli.release(table, table.rows["customer-a"], force=False, now=1001) == "released"
    row = table.rows["customer-a"]
    assert not any(k.startswith(("busy_", "exclusion_")) for k in row)
    assert row["policy_session"] != "old-session" and row["policy_session"][14] == "7"
    # tickets, cards and the build are the operator's to reset, not this script's
    assert row["approvals"] == "[]" and row["active_stage"] == "v3"
    assert cli.locked(table) == []


def test_lock_whose_time_is_not_up_waits_unless_forced():
    table = Table(stuck(busy_until=1500))
    assert cli.release(table, table.rows["customer-a"], force=False, now=1001) == "wait 499"
    assert table.rows["customer-a"]["busy_owner"] == "run-1"
    assert cli.release(table, table.rows["customer-a"], force=True, now=1001) == "released"


def test_lock_taken_by_another_run_meanwhile_is_left_alone():
    table = Table(stuck())
    shown = dict(table.rows["customer-a"])
    table.rows["customer-a"]["busy_owner"] = "run-2"  # a new run started after the lock was read
    assert cli.release(table, shown, force=True, now=2000) == "changed"
    assert table.rows["customer-a"]["busy_owner"] == "run-2"
    assert table.rows["customer-a"]["policy_session"] == "old-session"


def test_same_owner_with_a_newer_expiry_is_left_alone():
    table = Table(stuck(busy_owner="eval-suite"))
    shown = dict(table.rows["customer-a"])
    table.rows["customer-a"]["busy_until"] = 5000  # a second suite locked after the first ended
    assert cli.release(table, shown, force=True, now=2000) == "changed"
    assert table.rows["customer-a"]["busy_until"] == 5000


def test_suite_lock_is_released_only_when_forced():
    for owner in ("eval-suite", "eval-cleanup#0190"):
        table = Table(stuck(busy_owner=owner))
        assert cli.release(table, table.rows["customer-a"], force=False, now=2000) == "suite"
        assert table.rows["customer-a"]["busy_owner"] == owner
        assert cli.release(table, table.rows["customer-a"], force=True, now=2000) == "released"


def test_failed_eval_run_marker_is_removed_with_the_lock():
    table = Table(stuck(eval_note_probe="run-1"))
    assert cli.release(table, table.rows["customer-a"], force=False, now=1001) == "released"
    assert "eval_note_probe" not in table.rows["customer-a"]


def test_lock_without_an_expiry_is_released():
    row = stuck()
    del row["busy_until"]
    table = Table(row)
    assert cli.release(table, table.rows["customer-a"], force=False, now=1001) == "released"
