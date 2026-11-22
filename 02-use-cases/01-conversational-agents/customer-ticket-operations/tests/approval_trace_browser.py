"""Shipped Chrome: authorized approval run survives policy-session rotation.

Only loopback API/event fixtures; this deliberately issues one simulated approve POST.
It is separate from the zero-mutation recorded-reload browser regression.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
TRACE = json.loads((ROOT / "tests/fixtures/recorded_trace_r12.json").read_text())
HELD = TRACE["run_id"]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def main():
    late_events = "--late-events" in sys.argv or "--hold-full" in sys.argv
    hold_full = "--hold-full" in sys.argv
    case = next((arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--case=")), "success")
    invalidation = next(
        (arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--invalidate=")), ""
    )
    late_reply = next(
        (arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--reply=")), "success"
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(ROOT / "web")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    card = {
        "id": HELD + "-1",
        "run_id": HELD,
        "ticket_id": "A-41",
        "tool": "close_ticket",
        "customer_id": "customer-a",
        "status": "PENDING",
        "reason": "issue resolved",
        "requested_by": "operator",
    }
    state = {
        "persona": {"username": "alice", "customer_id": "customer-a", "role": "operator"},
        "active_stage": "v3",
        "model": "llama4-maverick",
        "policy_session": "held-session",
        "tickets": [
            {
                "ticket_id": "A-41",
                "subject": "Package arrived late",
                "status": "OPEN",
                "origin": "seed",
                "customer_id": "customer-a",
            }
        ],
        "approvals": [card],
        "last_run": {
            "run_id": HELD,
            "stage": "v3",
            "status": "AWAITING_APPROVAL",
            "answer": "Native close held; waiting for separate approval.",
            "effects": [],
        },
        "recorded_run": {"run_id": HELD, "stage": "v3"},
    }
    new = {"id": "", "events": [], "late": False}
    pending_full = []
    requests, blocked, errors, trace_queries, event_queries = [], [], [], [], []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("UI_CHROME", CHROME), headless=True
            )
            context = browser.new_context(
                viewport={"width": 1200, "height": 800}, reduced_motion="reduce"
            )
            page = context.new_page()
            page.clock.install()
            page.on("pageerror", lambda error: errors.append(str(error)))

            def serve(route):
                req, parsed = route.request, urlsplit(route.request.url)
                if f"{parsed.scheme}://{parsed.netloc}" != origin:
                    blocked.append(req.url)
                    route.abort()
                    return
                path = parsed.path
                if not path.startswith(("/api/", "/auth/")):
                    route.continue_()
                    return
                requests.append((req.method, path))
                if req.method == "GET" and path == "/api/state":
                    route.fulfill(json=state)
                elif req.method == "GET" and path == f"/api/runs/{HELD}/events":
                    route.fulfill(json={"run_id": HELD, "events": TRACE["events"]})
                elif req.method == "GET" and path == f"/api/runs/{new['id']}/events" and new["id"]:
                    event_queries.append(parsed.query)
                    if hold_full and new["late"] and not parsed.query:
                        pending_full.append(route)
                        return
                    # Polls may precede event ingestion; after a successful POST the
                    # owner-proven full GET provides the one bounded hydration attempt.
                    rows = (
                        [] if new["late"] and parsed.query.startswith("after=") else new["events"]
                    )
                    route.fulfill(json={"run_id": new["id"], "events": rows})
                elif req.method == "GET" and path == "/api/trace":
                    assert parsed.query == "run=" + new["id"]
                    trace_queries.append(parsed.query)
                    route.fulfill(json={"run_id": new["id"], "region": "us-east-1", "sources": []})
                elif req.method == "GET" and path == "/auth/me":
                    route.fulfill(json={"presenter": False})
                elif req.method == "POST" and path == "/api/run":
                    body = req.post_data_json
                    assert isinstance(body["run_id"], str) and body["run_id"] != HELD
                    if body["mode"] == "live":
                        assert new["id"] and body["run_id"] != new["id"]
                        new["id"] = body["run_id"]
                        new["late"] = False
                        new["events"] = [
                            {
                                "sk": "001#api#new",
                                "node": "ingress",
                                "source": "api",
                                "kind": "auth.verified",
                                "status": "ok",
                                "label": "alice started the new live run",
                            },
                            {
                                "sk": "002#api#done",
                                "node": "agent",
                                "source": "api",
                                "kind": "run.completed",
                                "status": "ok",
                                "verdict": "NO_EFFECT",
                                "label": "new live run completed",
                            },
                        ]
                        state["last_run"] = {
                            "run_id": new["id"],
                            "stage": "v3",
                            "status": "NO_EFFECT",
                            "answer": "New run summarized.",
                            "effects": [],
                        }
                        state["recorded_run"] = {"run_id": new["id"], "stage": "v3"}
                        route.fulfill(json={"result": state["last_run"]})
                        return
                    assert body["mode"] == "approve" and body["approval"] == card["id"]
                    assert new["id"] == ""  # never duplicate an approval request
                    new["id"] = body["run_id"]
                    new["late"] = late_events
                    new["events"] = [
                        {
                            "sk": "001#api#auth",
                            "node": "ingress",
                            "source": "api",
                            "kind": "auth.verified",
                            "status": "ok",
                            "label": "alice approved this new run",
                        },
                        {
                            "sk": "002#api#grant",
                            "node": "ingress",
                            "source": "api",
                            "kind": "approval.granted",
                            "status": "ok",
                            "label": "alice approved close_ticket A-41",
                        },
                        {
                            "sk": "003#api#done",
                            "node": "agent",
                            "source": "api",
                            "kind": "run.completed",
                            "status": "ok",
                            "verdict": "CONFIRMED",
                            "label": "one approved close done",
                        },
                    ]
                    card["status"] = "APPROVED"
                    state.update(
                        policy_session="fresh-session",
                        recorded_run={"run_id": new["id"], "stage": "v3"},
                        last_run={
                            "run_id": new["id"],
                            "stage": "v3",
                            "status": "CONFIRMED",
                            "answer": "Approved close done.",
                            "effects": [
                                {
                                    "operation": "close_ticket",
                                    "target": "A-41",
                                    "status": "EXECUTED",
                                    "before": "OPEN",
                                    "after": "CLOSED",
                                    "confirmed": True,
                                }
                            ],
                        },
                    )
                    state["tickets"][0]["status"] = "CLOSED"
                    route.fulfill(json={"result": state["last_run"]})
                elif req.method == "POST" and path == "/api/reset":
                    assert req.post_data_json == {}
                    state.update(
                        active_stage="v0",
                        policy_session="reset-session",
                        last_run=None,
                        recorded_run=None,
                        approvals=[],
                    )
                    state["tickets"][0]["status"] = "OPEN"
                    route.fulfill(json={"reset": True})
                elif req.method == "POST" and path == "/api/patch":
                    assert req.post_data_json == {"stage": "v4"}
                    state.update(
                        active_stage="v4",
                        policy_session="patched-session",
                        last_run=None,
                        recorded_run=None,
                    )
                    route.fulfill(json={"active_stage": "v4", "title": "Contain"})
                else:
                    blocked.append((req.method, path))
                    route.abort()

            context.route("**/*", serve)
            page.goto(origin, wait_until="domcontentloaded")
            page.wait_for_function("RUN?.run_id === '" + HELD + "' && RUN.events.length === 19")
            page.clock.run_for(20000)
            page.locator('.node[data-id="ingress"]').click()
            expect(page.locator("#drBody")).to_contain_text("alice signed in")
            # The actual shipped approval handler clears an old open run drawer before
            # its one loopback POST; do not forge RUN/STATE or hand-insert events.
            page.evaluate("id => { runAgent('approve', {approval:id}); }", card["id"])
            expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "true")
            expect(page.locator("#drBody")).to_be_empty()
            if late_events:
                page.clock.run_for(5000)
            if hold_full:
                page.wait_for_function(
                    "RUN?.run_id === STATE?.recorded_run?.run_id && RUN.events.length === 0"
                )
                page.wait_for_timeout(150)
                assert len(pending_full) == 1 and event_queries.count("") == 1
                before_tick = page.evaluate("viewEpoch")
                page.clock.run_for(10000)  # crosses the ordinary 8-second periodic refresh
                assert page.evaluate("viewEpoch") == before_tick
                assert page.evaluate("traceLoading") > 0
                assert len(pending_full) == 1 and event_queries.count("") == 1
                full = pending_full.pop()
                if invalidation:
                    stale_id, stale_events = new["id"], list(new["events"])
                    if invalidation == "bob":
                        state.update(
                            persona={
                                "username": "bob",
                                "customer_id": "customer-b",
                                "role": "operator",
                            },
                            recorded_run=None,
                            last_run=None,
                        )
                        page.evaluate("refresh(false)")
                    elif invalidation == "proof":
                        state.update(recorded_run=None, last_run=None)
                        page.evaluate("refresh(false)")
                    elif invalidation == "reset":
                        page.once("dialog", lambda dialog: dialog.accept())
                        page.evaluate("() => { resetDemo(); }")
                        page.wait_for_function("STATE?.active_stage === 'v0' && RUN === null")
                    elif invalidation == "stage":
                        page.evaluate("() => { patch('v4'); }")
                        page.wait_for_function("STATE?.active_stage === 'v4' && RUN === null")
                    elif invalidation == "new_run":
                        page.evaluate("() => { runAgent('live'); }")
                        page.wait_for_function(
                            "RUN?.run_id === STATE?.recorded_run?.run_id && RUN?.events?.length === 2"
                        )
                        assert new["id"] != stale_id
                    else:
                        raise AssertionError("unexpected invalidation case")
                    assert page.evaluate("traceLoading") == 0
                    if late_reply == "error":
                        full.fulfill(status=503, json={"error": "stale prior-owner response"})
                    else:
                        full.fulfill(json={"run_id": stale_id, "events": stale_events})
                    page.wait_for_timeout(150)
                    if invalidation == "new_run":
                        assert page.evaluate("RUN.run_id") == new["id"] != stale_id
                        assert page.evaluate("RUN.events.length") == 2
                    else:
                        assert page.evaluate("RUN") is None and page.evaluate("STEPS.length") == 0
                        assert state["recorded_run"] is None
                    assert event_queries.count("") == 1 and not blocked and not errors
                    if invalidation != "new_run":
                        # The next signed-in operator state supplies a different exact
                        # owner-proven run; no old trace or client-side id fallback is used.
                        fresh_id = "01a1b318-6781-7197-b31c-19e40c8c2715"
                        assert fresh_id != stale_id
                        new["id"] = fresh_id
                        new["late"] = False
                        new["events"] = [
                            {
                                "sk": "001#api#fresh",
                                "node": "ingress",
                                "source": "api",
                                "kind": "auth.verified",
                                "status": "ok",
                                "label": "newly authorized own trace",
                            }
                        ]
                        state.update(
                            persona={
                                "username": "alice",
                                "customer_id": "customer-a",
                                "role": "operator",
                            },
                            policy_session="new-own-session",
                            active_stage="v3",
                            recorded_run={"run_id": fresh_id, "stage": "v3"},
                            last_run={"run_id": fresh_id, "stage": "v3", "status": "NO_EFFECT"},
                        )
                        page.reload(wait_until="domcontentloaded")
                        page.wait_for_function(
                            "RUN?.run_id === '" + fresh_id + "' && RUN.events.length === 1"
                        )
                        page.locator('.node[data-id="ingress"]').click()
                        expect(page.locator("#drBody")).to_contain_text(
                            "newly authorized own trace"
                        )
                        expect(page.locator("#drBody")).not_to_contain_text(
                            "alice approved this new run"
                        )
                    context.close()
                    browser.close()
                    print(
                        "PASS: stale new-owner full trace reply ignored across authoritative invalidation"
                    )
                    return
                if case == "success":
                    full.fulfill(json={"run_id": new["id"], "events": new["events"]})
                elif case == "empty":
                    full.fulfill(json={"run_id": new["id"], "events": []})
                elif case == "wrong_id":
                    full.fulfill(json={"run_id": HELD, "events": new["events"]})
                elif case == "malformed":
                    full.fulfill(json={"run_id": new["id"], "events": "not a list"})
                else:
                    full.fulfill(status=int(case), json={"error": "prior owner trace unavailable"})
                if case != "success":
                    page.wait_for_function("traceLoading === 0 && !!traceError")
                    assert (
                        page.evaluate("RUN?.run_id") == new["id"]
                        and page.evaluate("STEPS.length") == 0
                    )
                    expected = "No recorded events" if case == "empty" else "trace unavailable"
                    expect(page.locator("#capx")).to_contain_text(expected)
                    page.clock.run_for(10000)
                    assert event_queries.count("") == 1
                    assert [m for m, path in requests if path == "/api/run"] == ["POST"]
                    assert not blocked and not errors
                    context.close()
                    browser.close()
                    print(
                        "PASS: one owned full trace GET returns explicit empty/error without retry or borrowed authority"
                    )
                    return
            page.wait_for_function(
                "RUN?.run_id === STATE?.recorded_run?.run_id && RUN?.events?.length === 3",
                timeout=8000,
            )
            assert page.evaluate("STATE.policy_session") == "fresh-session"
            assert page.evaluate("RUN.run_id") == new["id"] != HELD
            assert page.evaluate("STEPS.length") == 3
            assert event_queries.count("") == (1 if late_events else 0)
            page.clock.run_for(6000)
            expect(page.locator("#count")).to_have_text("3 / 3")
            page.locator("#mStep").click()
            page.locator("#back").click()
            expect(page.locator("#count")).to_have_text("2 / 3")
            page.locator("#next").click()
            expect(page.locator("#count")).to_have_text("3 / 3")
            page.locator('.node[data-id="ingress"]').click()
            expect(page.locator("#drBody")).to_contain_text("approval.granted")
            expect(page.locator("#drBody")).not_to_contain_text("alice signed in")
            page.keyboard.press("Escape")
            page.locator('.node[data-id="cw"]').click()
            expect(page.locator("#drBody")).to_contain_text("AWS records can arrive later")
            assert trace_queries == ["run=" + new["id"]]
            approval_run = new["id"]
            # A new live Run replaces the just-approved run. Its old open CW drawer
            # is invalidated before one deliberately mocked live POST is sent.
            page.evaluate("() => { runAgent('live'); }")
            expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "true")
            expect(page.locator("#drBody")).to_be_empty()
            page.wait_for_function(
                "RUN?.run_id === STATE?.recorded_run?.run_id && RUN?.events?.length === 2",
                timeout=8000,
            )
            live_run = new["id"]
            assert live_run != approval_run
            expect(page.locator("#count")).to_have_text("0 / 2")  # Step remained selected
            page.locator("#next").click()
            page.locator("#next").click()
            expect(page.locator("#count")).to_have_text("2 / 2")
            page.locator('.node[data-id="ingress"]').click()
            expect(page.locator("#drBody")).to_contain_text("alice started the new live run")
            expect(page.locator("#drBody")).not_to_contain_text("approval.granted")
            page.keyboard.press("Escape")
            page.locator('.node[data-id="cw"]').click()
            expect(page.locator("#drBody")).to_contain_text("AWS records can arrive later")
            assert trace_queries == ["run=" + approval_run, "run=" + live_run]
            page.keyboard.press("Escape")
            page.locator('.node[data-id="ingress"]').click()
            page.once("dialog", lambda dialog: dialog.accept())
            page.evaluate("() => { resetDemo(); }")
            expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "true")
            expect(page.locator("#drBody")).to_be_empty()
            page.wait_for_function("STATE?.active_stage === 'v0' && RUN === null")
            assert state["recorded_run"] is None
            # Backend fixture presents a fresh authorized recorded run to exercise
            # the actual stage-change control, not a client-side synthetic trace.
            state.update(
                active_stage="v3",
                policy_session="fresh-session",
                recorded_run={"run_id": live_run, "stage": "v3"},
                last_run={"run_id": live_run, "stage": "v3", "status": "NO_EFFECT"},
            )
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("RUN?.run_id === '" + live_run + "' && RUN.events.length === 2")
            page.locator('.node[data-id="ingress"]').click()
            page.evaluate("() => { patch('v4'); }")
            expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "true")
            expect(page.locator("#drBody")).to_be_empty()
            page.wait_for_function("STATE?.active_stage === 'v4' && RUN === null")
            assert [m for m, p in requests if p == "/api/run"] == ["POST", "POST"]
            assert [m for m, p in requests if p in ("/api/reset", "/api/patch")] == ["POST", "POST"]
            assert not blocked and not errors, (blocked, errors)
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print(
        "PASS: one loopback approval POST rotates session; new authorized run/events/Auto/Step/ingress/CloudWatch survive, old held trace never returns"
    )


if __name__ == "__main__":
    main()
