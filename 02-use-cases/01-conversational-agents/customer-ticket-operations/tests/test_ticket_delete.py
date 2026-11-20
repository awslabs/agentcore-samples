"""Offline API ticket deletion through the real handler and conditional in-memory tables."""

import json

import pytest

from tests.test_security_regressions import ALICE, Conflict

pytest_plugins = ["tests.test_security_regressions"]


def submit(api, monkeypatch, text="new ticket"):
    customer = {**ALICE, "role": "customer", "username": "visitor"}
    return api.app.submit_ticket(customer, {"subject": "Portal", "body": text})["ticket"][
        "ticket_id"
    ]


def delete(api, ticket_id):
    return api.app.lambda_handler(
        {
            "requestContext": {"http": {"method": "POST"}},
            "rawPath": "/api/tickets/delete",
            "body": json.dumps({"ticket_id": ticket_id}),
            "headers": {"x-ticketops-access-token": "offline"},
        },
        None,
    )


def status(response):
    return response["statusCode"], json.loads(response["body"])


def assert_http(api, action, code):
    with pytest.raises(api.app.HttpError) as e:
        action()
    assert e.value.status == code


def test_delete_only_own_portal_ticket_preserves_control_and_run_history(api, monkeypatch):
    ticket = submit(api, monkeypatch)
    state = api.app.demo_state("customer-a")
    state.update(
        {
            "capture": json.dumps(
                {"stage": "v5", "run_id": "old", "calls": [], "fingerprint": "old"}
            ),
            "last_run": json.dumps({"status": "NO_EFFECT"}),
            "run_id": "old-run",
            "breaker_open": True,
            "stop_requested": True,
            "policy_session": "ps-1",
            "approvals": json.dumps([{"ticket_id": ticket, "status": "REJECTED", "id": "older"}]),
        }
    )
    api.tables["state"].put_item(Item=state)
    code, result = status(delete(api, ticket))
    assert (code, result) == (200, {"deleted": ticket, "source": "operator"})
    assert not api.tables["tickets"].get_item(
        Key={"customer_id": "customer-a", "ticket_id": ticket}
    )
    st = api.app.demo_state("customer-a")
    for key in (
        "active_stage",
        "last_run",
        "run_id",
        "breaker_open",
        "stop_requested",
        "policy_session",
        "approvals",
    ):
        assert st[key] == state[key]
    assert "capture" not in st and "busy_owner" not in st
    assert st["store_change_source"] == f"Operator deleted portal ticket {ticket}"
    assert not api.app.public_state(ALICE)["capture_available"]
    assert not any(t["ticket_id"] == ticket for t in api.app.public_state(ALICE)["tickets"])
    assert_http(api, lambda: api.app.delete_portal_ticket(ALICE, {"ticket_id": ticket}), 404)
    next_ticket = submit(api, monkeypatch)
    assert next_ticket != ticket and int(next_ticket.split("-")[1]) > int(ticket.split("-")[1])


def test_delete_refuses_seed_cross_tenant_missing_invalid_and_nonoperator(api, monkeypatch):
    ticket = submit(api, monkeypatch)
    other = api.app.submit_ticket(
        {**ALICE, "role": "customer", "customer_id": "customer-b", "username": "visitor-b"},
        {"subject": "Other", "body": "private"},
    )["ticket"]["ticket_id"]
    assert_http(api, lambda: api.app.delete_portal_ticket(ALICE, {"ticket_id": "A-41"}), 403)
    assert_http(api, lambda: api.app.delete_portal_ticket(ALICE, {"ticket_id": other}), 404)
    assert api.tables["tickets"].get_item(Key={"customer_id": "customer-b", "ticket_id": other})
    assert_http(api, lambda: api.app.delete_portal_ticket(ALICE, {"ticket_id": "A-999999"}), 404)
    for invalid in ("", "../A-41", "a-90", "A-1' OR '1", 42):
        assert_http(
            api,
            lambda invalid=invalid: api.app.delete_portal_ticket(ALICE, {"ticket_id": invalid}),
            400,
        )
    assert_http(
        api,
        lambda: api.app.delete_portal_ticket({**ALICE, "role": "customer"}, {"ticket_id": ticket}),
        403,
    )
    # A forged row with a protected seed ID cannot bypass the immutable seed-id fence.
    api.tables["tickets"].rows[("customer-a", "A-41")]["origin"] = "form"
    assert_http(api, lambda: api.app.delete_portal_ticket(ALICE, {"ticket_id": "A-41"}), 403)
    assert api.tables["tickets"].get_item(Key={"customer_id": "customer-a", "ticket_id": ticket})


def test_delete_respects_busy_recovery_and_pending_approval(api, monkeypatch):
    ticket = submit(api, monkeypatch)
    api.app.claim_run("customer-a", "live-owner")
    assert_http(api, lambda: api.app.delete_portal_ticket(ALICE, {"ticket_id": ticket}), 409)
    api.app.release_run("customer-a", "live-owner")
    table = api.tables["state"]
    st = api.app.demo_state("customer-a")
    st["exclusion_disposition"] = "UNKNOWN"
    table.put_item(Item=st)
    assert_http(api, lambda: api.app.delete_portal_ticket(ALICE, {"ticket_id": ticket}), 409)
    st.pop("exclusion_disposition")
    st["approvals"] = json.dumps(
        [
            {
                "ticket_id": ticket,
                "status": "PENDING",
                "id": "card",
                "created_at": int(api.app.time.time()),
            }
        ]
    )
    table.put_item(Item=st)
    assert_http(api, lambda: api.app.delete_portal_ticket(ALICE, {"ticket_id": ticket}), 409)
    assert not api.app.demo_state("customer-a").get("busy_owner")
    assert api.tables["tickets"].get_item(Key={"customer_id": "customer-a", "ticket_id": ticket})


def test_delete_conditional_conflict_releases_owner_and_ambiguous_failure_retains_it(
    api, monkeypatch
):
    ticket = submit(api, monkeypatch)
    original = api.tables["tickets"].delete_item

    def conflict(**kw):
        raise Conflict()

    monkeypatch.setattr(api.tables["tickets"], "delete_item", conflict)
    assert_http(api, lambda: api.app.delete_portal_ticket(ALICE, {"ticket_id": ticket}), 409)
    assert not api.app.demo_state("customer-a").get("busy_owner")

    def ambiguous(**kw):
        raise TimeoutError("write outcome unknown")

    monkeypatch.setattr(api.tables["tickets"], "delete_item", ambiguous)
    code, body = status(delete(api, ticket))
    assert code == 503 and "write outcome unknown" not in str(body)
    st = api.app.demo_state("customer-a")
    assert st["busy_owner"] and st["exclusion_disposition"] == "UNKNOWN"
    monkeypatch.setattr(api.tables["tickets"], "delete_item", original)
    assert_http(api, lambda: api.app.delete_portal_ticket(ALICE, {"ticket_id": ticket}), 409)


def test_state_write_failure_after_delete_keeps_exclusion_for_reconciliation(api, monkeypatch):
    ticket = submit(api, monkeypatch)
    table = api.tables["state"]
    original = table.update_item

    def fail_record(**kw):
        if "REMOVE capture" in kw["UpdateExpression"]:
            raise RuntimeError("state unavailable")
        return original(**kw)

    monkeypatch.setattr(table, "update_item", fail_record)
    code, body = status(delete(api, ticket))
    assert code == 503 and "state unavailable" not in str(body)
    assert not api.tables["tickets"].get_item(
        Key={"customer_id": "customer-a", "ticket_id": ticket}
    )
    st = api.app.demo_state("customer-a")
    assert st["busy_owner"] and st["exclusion_disposition"] == "UNKNOWN"


def test_legacy_highest_deleted_id_never_reused_and_history_remains(api, monkeypatch):
    """Portal rows written before portal_ticket_seq existed have no sequence in tenant state."""
    ticket = "A-90"
    api.tables["tickets"].put_item(
        Item={
            "customer_id": "customer-a",
            "ticket_id": ticket,
            "origin": "form",
            "submitted_by": "customer",
            "status": "OPEN",
            "subject": "old",
            "body": "old",
        }
    )
    state = api.app.demo_state("customer-a")
    assert "portal_ticket_seq" not in state
    state.update(
        {
            "last_run": json.dumps(
                {"proposals": [{"ticket_id": ticket, "operation": "add_note"}], "effects": []}
            ),
            "approvals": json.dumps([{"ticket_id": ticket, "status": "REJECTED", "id": "legacy"}]),
        }
    )
    api.tables["state"].put_item(Item=state)
    assert status(delete(api, ticket)) == (200, {"deleted": ticket, "source": "operator"})
    assert api.app.demo_state("customer-a")["portal_ticket_seq"] == 90
    customer = {**ALICE, "role": "customer", "username": "visitor"}
    new_id = api.app.submit_ticket(customer, {"subject": "New", "body": "after delete"})["ticket"][
        "ticket_id"
    ]
    assert new_id == "A-91"
    assert status(delete(api, new_id))[0] == 200
    before_reset = api.app.demo_state("customer-a")
    assert before_reset["portal_ticket_seq"] == 91
    assert before_reset["last_run"] == state["last_run"]
    assert before_reset["approvals"] == state["approvals"]
    # Local in-memory reset of the demo does not reset this monotonic identity marker.
    api.app.reset_demo(ALICE)
    again = api.app.submit_ticket(customer, {"subject": "Newer", "body": "after reset"})["ticket"][
        "ticket_id"
    ]
    assert again == "A-92"
    st = api.app.demo_state("customer-a")
    assert st["portal_ticket_seq"] == 92
    assert "busy_owner" not in st
    # Reset deliberately clears run history; before it, deletion retained it unchanged.


def test_delete_legacy_high_water_includes_other_live_ids_and_existing_seq(api, monkeypatch):
    for ticket in ("A-90", "A-98"):
        api.tables["tickets"].put_item(
            Item={
                "customer_id": "customer-a",
                "ticket_id": ticket,
                "origin": "form",
                "submitted_by": "customer",
                "status": "OPEN",
            }
        )
    state = api.app.demo_state("customer-a")
    state["portal_ticket_seq"] = 96
    api.tables["state"].put_item(Item=state)
    assert status(delete(api, "A-90"))[0] == 200
    assert api.app.demo_state("customer-a")["portal_ticket_seq"] == 98
    assert status(delete(api, "A-98"))[0] == 200
    assert api.app.demo_state("customer-a")["portal_ticket_seq"] == 98
    assert submit(api, monkeypatch) == "A-99"


def test_legacy_high_water_write_failure_keeps_owner_and_no_duplicate(api, monkeypatch):
    api.tables["tickets"].put_item(
        Item={
            "customer_id": "customer-a",
            "ticket_id": "A-90",
            "origin": "form",
            "submitted_by": "customer",
            "status": "OPEN",
        }
    )
    table = api.tables["state"]
    original = table.update_item

    def fail(**kw):
        if "store_change_source = :source" in kw["UpdateExpression"]:
            raise TimeoutError("high-water update uncertain")
        return original(**kw)

    monkeypatch.setattr(table, "update_item", fail)
    code, body = status(delete(api, "A-90"))
    assert code == 503 and "high-water update uncertain" not in str(body)
    assert api.app.demo_state("customer-a")["busy_owner"]
    assert api.app.demo_state("customer-a")["exclusion_disposition"] == "UNKNOWN"
    assert_http(api, lambda: submit(api, monkeypatch), 409)


def post_as(api, monkeypatch, path, body, who):
    monkeypatch.setattr(api.app, "persona", lambda event: dict(who))
    return status(
        api.app.lambda_handler(
            {
                "requestContext": {"http": {"method": "POST"}},
                "rawPath": path,
                "body": json.dumps(body),
                "headers": {"x-ticketops-access-token": "offline"},
            },
            None,
        )
    )


def test_reset_first_legacy_rows_preserve_identity_before_removal(api, monkeypatch):
    """Real reset/submit HTTP handlers: old A-90/A-98 events cannot become a new ticket."""
    for ticket in ("A-90", "A-98"):
        api.tables["tickets"].put_item(
            Item={
                "customer_id": "customer-a",
                "ticket_id": ticket,
                "origin": "form",
                "submitted_by": "customer",
                "status": "OPEN",
                "subject": "Legacy",
                "body": "Old",
            }
        )
    old_run = "019a2900-0000-7000-8000-000000000001"
    event = {
        "run_id": old_run,
        "sk": "1#tool",
        "kind": "tool.called",
        "effect": {"ticket_id": "A-98"},
    }
    api.tables["events"].put_item(Item=event)
    st = api.app.demo_state("customer-a")
    assert "portal_ticket_seq" not in st
    st.update(
        {
            "active_stage": "v5",
            "stop_requested": True,
            "breaker_open": True,
            "policy_session": "old-session",
            "last_run": json.dumps({"status": "NO_EFFECT"}),
            "approvals": json.dumps([{"status": "REJECTED", "ticket_id": "A-98"}]),
        }
    )
    api.tables["state"].put_item(Item=st)
    # Customer B is locked while seeds are restored, but its portal rows/sequence are not reset.
    api.tables["tickets"].put_item(
        Item={
            "customer_id": "customer-b",
            "ticket_id": "B-90",
            "origin": "form",
            "submitted_by": "customer",
            "status": "OPEN",
        }
    )
    api.tables["state"].put_item(Item={"customer_id": "customer-b", "portal_ticket_seq": 250})
    assert post_as(api, monkeypatch, "/api/reset", {}, ALICE) == (200, {"reset": True})
    after = api.app.demo_state("customer-a")
    assert after["portal_ticket_seq"] == 98 and after["active_stage"] == "v0"
    assert after["stop_requested"] is False and after["breaker_open"] is False
    assert after["policy_session"] != "old-session"
    assert "last_run" not in after and "approvals" not in after
    assert not any(x["ticket_id"] in ("A-90", "A-98") for x in api.app.tickets_for("customer-a"))
    assert api.tables["events"].get_item(Key={"run_id": old_run, "sk": "1#tool"})["Item"] == event
    customer = {**ALICE, "role": "customer", "username": "visitor"}
    created = post_as(
        api, monkeypatch, "/api/portal/tickets", {"subject": "New", "body": "New"}, customer
    )
    assert created == (200, {"ticket": {"ticket_id": "A-99"}})
    assert post_as(api, monkeypatch, "/api/tickets/delete", {"ticket_id": "A-99"}, ALICE) == (
        200,
        {"deleted": "A-99", "source": "operator"},
    )
    assert post_as(api, monkeypatch, "/api/reset", {}, ALICE) == (200, {"reset": True})
    assert api.app.demo_state("customer-a")["portal_ticket_seq"] == 99
    assert post_as(
        api, monkeypatch, "/api/portal/tickets", {"subject": "Again", "body": "Again"}, customer
    ) == (200, {"ticket": {"ticket_id": "A-100"}})
    assert api.app.demo_state("customer-b")["portal_ticket_seq"] == 250
    assert api.tables["tickets"].get_item(Key={"customer_id": "customer-b", "ticket_id": "B-90"})
    other = {**customer, "customer_id": "customer-b"}
    assert post_as(
        api, monkeypatch, "/api/portal/tickets", {"subject": "B", "body": "B"}, other
    ) == (200, {"ticket": {"ticket_id": "B-251"}})


def test_reset_preserves_greater_sequence_and_reads_all_pages(api, monkeypatch):
    for ticket in ("A-90", "A-98"):
        api.tables["tickets"].put_item(
            Item={
                "customer_id": "customer-a",
                "ticket_id": ticket,
                "origin": "form",
                "submitted_by": "customer",
                "status": "OPEN",
            }
        )
    state = api.app.demo_state("customer-a")
    state["portal_ticket_seq"] = 200
    api.tables["state"].put_item(Item=state)
    original = api.tables["tickets"].query
    seen = []

    def paged(**kw):
        if kw.get("ConsistentRead"):
            seen.append(kw)
            rows = original(**kw)["Items"]
            forms = [x for x in rows if x["ticket_id"] in ("A-90", "A-98")]
            if "ExclusiveStartKey" not in kw:
                return {"Items": forms[:1], "LastEvaluatedKey": {"ticket_id": "A-90"}}
            return {"Items": forms[1:]}
        return original(**kw)

    monkeypatch.setattr(api.tables["tickets"], "query", paged)
    assert post_as(api, monkeypatch, "/api/reset", {}, ALICE) == (200, {"reset": True})
    assert len(seen) == 2 and all(c["ConsistentRead"] for c in seen)
    assert api.app.demo_state("customer-a")["portal_ticket_seq"] == 200
    customer = {**ALICE, "role": "customer", "username": "visitor"}
    assert post_as(
        api, monkeypatch, "/api/portal/tickets", {"subject": "New", "body": "New"}, customer
    ) == (200, {"ticket": {"ticket_id": "A-201"}})


@pytest.mark.parametrize("fault", [Conflict, TimeoutError])
def test_reset_sequence_conditional_or_transport_failure_precedes_any_delete(
    api, monkeypatch, fault
):
    api.tables["tickets"].put_item(
        Item={
            "customer_id": "customer-a",
            "ticket_id": "A-98",
            "origin": "form",
            "submitted_by": "customer",
            "status": "OPEN",
        }
    )
    state_table = api.tables["state"]
    update = state_table.update_item

    def fail(**kw):
        if kw["UpdateExpression"] == "SET portal_ticket_seq = :next":
            assert kw["ConditionExpression"] == "busy_owner = :owner"
            raise fault("sequence persistence unknown")
        return update(**kw)

    monkeypatch.setattr(state_table, "update_item", fail)
    code, body = post_as(api, monkeypatch, "/api/reset", {}, ALICE)
    assert code == 503 and "sequence persistence unknown" not in str(body)
    assert api.tables["tickets"].get_item(Key={"customer_id": "customer-a", "ticket_id": "A-98"})
    assert api.app.demo_state("customer-a")["busy_owner"]  # uncertain reset holds exclusion
    assert api.app.demo_state("customer-b")["busy_owner"]
    customer = {**ALICE, "role": "customer", "username": "visitor"}
    assert (
        post_as(api, monkeypatch, "/api/portal/tickets", {"subject": "N", "body": "N"}, customer)[0]
        == 409
    )


def test_reset_delete_failure_keeps_saved_sequence_and_owner(api, monkeypatch):
    api.tables["tickets"].put_item(
        Item={
            "customer_id": "customer-a",
            "ticket_id": "A-98",
            "origin": "form",
            "submitted_by": "customer",
            "status": "OPEN",
        }
    )
    original = api.tables["tickets"].delete_item

    def uncertain(**kw):
        if kw["Key"]["ticket_id"] == "A-98":
            raise TimeoutError("delete may have happened")
        return original(**kw)

    monkeypatch.setattr(api.tables["tickets"], "delete_item", uncertain)
    code, body = post_as(api, monkeypatch, "/api/reset", {}, ALICE)
    assert code == 503 and "delete may have happened" not in str(body)
    st = api.app.demo_state("customer-a")
    assert st["portal_ticket_seq"] == 98 and st["busy_owner"]
    assert api.app.demo_state("customer-b")["busy_owner"]
