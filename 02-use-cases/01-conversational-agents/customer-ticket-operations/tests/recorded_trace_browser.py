"""Shipped Chrome: tenant-owned recorded run hydration, playback and stale GET fences.

The 19-hop fixture is an allowlisted projection of a recorded V3 held run;
all HTTP is intercepted on 127.0.0.1. This does not query AWS or run a model.
"""

from __future__ import annotations

import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
TRACE = json.loads((ROOT / "tests/fixtures/recorded_trace_r12.json").read_text())
RUN_ID = TRACE["run_id"]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(ROOT / "web")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    state = {
        "persona": {"username": "alice", "customer_id": "customer-a", "role": "operator"},
        "active_stage": "v3",
        "model": "llama4-maverick",
        "tickets": [
            {
                "ticket_id": "A-41",
                "subject": "Package arrived late",
                "status": "OPEN",
                "customer_id": "customer-a",
                "origin": "seed",
            },
        ],
        "approvals": [],
        "last_run": {
            "run_id": RUN_ID,
            "stage": "v3",
            "status": "AWAITING_APPROVAL",
            "answer": "Proposal held for separate approval.",
            "effects": [],
        },
        "recorded_run": {"run_id": RUN_ID, "stage": "v3"},
        "policy_session": "policy-1",
        "observability": {"ticketops_run": RUN_ID},
    }
    flags = {
        "events_status": 200,
        "hold_events": False,
        "pending": [],
        "hold_trace": False,
        "pending_trace": [],
    }
    requests, blocked, js_errors = [], [], []
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
            page.on("pageerror", lambda error: js_errors.append(str(error)))

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
                if req.method != "GET":
                    blocked.append(path)
                    route.abort()
                elif path == "/api/state":
                    route.fulfill(json=state)
                elif path == f"/api/runs/{RUN_ID}/events":
                    if flags["hold_events"]:
                        flags["pending"].append(route)
                    elif flags["events_status"] != 200:
                        route.fulfill(status=flags["events_status"], json={"error": "no such run"})
                    else:
                        route.fulfill(json={"run_id": RUN_ID, "events": TRACE["events"]})
                elif path == "/api/trace":
                    if flags["hold_trace"]:
                        flags["pending_trace"].append(route)
                    else:
                        route.fulfill(json={"run_id": RUN_ID, "region": "us-east-1", "sources": []})
                elif path == "/api/evals":
                    route.fulfill(json={"builds": {}, "dataset": "2026-11-05.9"})
                elif path == "/auth/me":
                    route.fulfill(json={"presenter": False})
                else:
                    blocked.append(path)
                    route.abort()

            context.route("**/*", serve)
            for attempt in (1, 2):
                page.goto(origin, wait_until="domcontentloaded") if attempt == 1 else page.reload(
                    wait_until="domcontentloaded"
                )
                page.wait_for_function(
                    "RUN?.run_id === STATE?.recorded_run?.run_id && RUN.events.length === 19 && STEPS.length === 16",
                    timeout=8000,
                )
                assert page.evaluate("mode") == "auto"
                page.clock.run_for(20000)
                expect(page.locator("#count")).to_have_text("16 / 16")
                assert page.evaluate("RUN.events.length") == 19
                assert sum(path.endswith("/events") for _, path in requests) == attempt
                assert page.evaluate("traceBuild()") == 3
            page.locator("#mStep").click()
            expect(page.locator("#mStep")).to_have_attribute("aria-pressed", "true")
            page.locator("#back").click()
            expect(page.locator("#count")).to_have_text("15 / 16")
            page.locator("#next").focus()
            page.keyboard.press("ArrowRight")
            expect(page.locator("#count")).to_have_text("16 / 16")
            page.evaluate("N=3; renderPanel()")
            page.locator("#back").click()
            expect(page.locator("#count")).to_have_text("2 / 16")
            page.locator("#mAuto").click()
            assert page.evaluate("mode") == "auto"
            page.clock.run_for(20000)
            expect(page.locator("#count")).to_have_text("16 / 16")
            page.evaluate("N=3; renderPanel()")
            expandable = page.locator("#payloads .pl button.x:not([hidden])")
            expect(expandable).to_have_count(1)
            expandable.click()
            expect(expandable).to_have_attribute("aria-expanded", "true")
            expandable.click()
            expect(expandable).to_have_attribute("aria-expanded", "false")
            page.evaluate("N=STEPS.length; renderPanel()")
            for hop in (
                "ingress",
                "acid",
                "agent",
                "bedrock",
                "guardrail",
                "gateway",
                "policy",
                "lambda",
                "ddb",
                "cw",
            ):
                page.locator(f'.node[data-id="{hop}"]').click()
                expect(page.locator("#drBody")).not_to_contain_text("No events for this hop yet")
                page.keyboard.press("Escape")
                expect(page.locator(f'.node[data-id="{hop}"]')).to_be_focused()
            assert sum(path == "/api/trace" for _, path in requests) == 1
            assert sum(path.endswith("/events") for _, path in requests) == 2

            # Current stage governs actions; retained trace is labelled by the recorded stage.
            state["active_stage"] = "v5"
            page.evaluate("refresh(false)")
            page.wait_for_function("STATE.active_stage === 'v5'")
            assert page.evaluate("build()") == 5 and page.evaluate("traceBuild()") == 3
            expect(page.locator("#nextFix")).to_have_text("Evaluations architecture")
            page.locator('.node[data-id="ingress"]').click()
            expect(page.locator("#drBody")).to_contain_text("alice signed in")
            expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "false")
            state.update(
                persona={"username": "bob", "customer_id": "customer-b", "role": "operator"},
                recorded_run=None,
                last_run=None,
                active_stage="v0",
            )
            page.evaluate("refresh(false)")
            page.wait_for_function("STATE.persona.username === 'bob'")
            assert page.evaluate("RUN") is None and page.evaluate("STEPS.length") == 0
            expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "true")
            expect(page.locator("#drBody")).to_be_empty()
            expect(page.locator('.node[data-id="ingress"]')).to_be_focused()
            page.locator('.node[data-id="ingress"]').click()
            expect(page.locator("#drBody")).not_to_contain_text("alice")
            page.keyboard.press("Escape")
            assert sum(path.endswith("/events") for _, path in requests) == 2

            state.update(
                persona={"username": "alice", "customer_id": "customer-a", "role": "operator"},
                recorded_run={"run_id": RUN_ID, "stage": "v3"},
                last_run={"run_id": RUN_ID, "stage": "v3", "status": "AWAITING_APPROVAL"},
                active_stage="v3",
            )
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("RUN?.run_id === '" + RUN_ID + "' && RUN.events.length === 19")
            page.locator('.node[data-id="ingress"]').click()
            state["policy_session"] = "retired-session"
            page.evaluate("refresh(false)")
            expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "true")
            expect(page.locator("#drBody")).to_be_empty()
            assert page.evaluate("RUN") is None
            state["policy_session"] = "policy-1"

            # Every other run-correlated hop shares the same invalidation; restoring
            # Alice is a new authenticated state load, not a localStorage replay.
            other_hops = (
                "acid",
                "agent",
                "bedrock",
                "guardrail",
                "gateway",
                "policy",
                "lambda",
                "ddb",
            )
            for hop in other_hops:
                state.update(
                    persona={"username": "alice", "customer_id": "customer-a", "role": "operator"},
                    recorded_run={"run_id": RUN_ID, "stage": "v3"},
                    last_run={"run_id": RUN_ID, "stage": "v3", "status": "AWAITING_APPROVAL"},
                    active_stage="v3",
                )
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function(
                    "RUN?.run_id === '" + RUN_ID + "' && RUN.events.length === 19"
                )
                page.locator(f'.node[data-id="{hop}"]').click()
                expect(page.locator("#drawer")).to_have_attribute("data-run-trace", "1")
                before_switch = sum(path.endswith("/events") for _, path in requests)
                state.update(
                    persona={"username": "bob", "customer_id": "customer-b", "role": "operator"},
                    recorded_run=None,
                    last_run=None,
                    active_stage="v0",
                )
                page.evaluate("refresh(false)")
                expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "true")
                expect(page.locator("#drBody")).to_be_empty()
                expect(
                    page.locator(
                        "#runBtn" if hop in ("guardrail", "policy") else f'.node[data-id="{hop}"]'
                    )
                ).to_be_focused()
                assert page.evaluate("RUN") is None
                assert sum(path.endswith("/events") for _, path in requests) == before_switch

            # Static Evals is not a run-correlated drawer: a persona change preserves
            # its normal Close/Escape/focus behavior while clearing the backing trace.
            state.update(
                persona={"username": "alice", "customer_id": "customer-a", "role": "operator"},
                recorded_run={"run_id": RUN_ID, "stage": "v3"},
                last_run={"run_id": RUN_ID, "stage": "v3", "status": "AWAITING_APPROVAL"},
                active_stage="v3",
            )
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("RUN?.run_id === '" + RUN_ID + "' && RUN.events.length === 19")
            page.locator("#evalBtn").click()
            expect(page.locator("#drawer")).to_have_attribute("data-from", "evals")
            state.update(
                persona={"username": "bob", "customer_id": "customer-b", "role": "operator"},
                recorded_run=None,
                last_run=None,
                active_stage="v0",
            )
            page.evaluate("refresh(false)")
            expect(page.locator("#drawer")).to_have_attribute("data-from", "evals")
            expect(page.locator("#drTitle")).to_contain_text("Evaluations")
            expect(page.locator("#drSub")).to_contain_text("Current dataset")  # header line
            assert page.evaluate("RUN") is None
            page.keyboard.press("Escape")
            expect(page.locator("#evalBtn")).to_be_focused()

            # A delayed owned CloudWatch response (success OR error) cannot fill a
            # newly opened Bob drawer after authoritative tenant invalidation.
            for outcome in ("success", "error"):
                flags["hold_trace"] = True
                state.update(
                    persona={"username": "alice", "customer_id": "customer-a", "role": "operator"},
                    recorded_run={"run_id": RUN_ID, "stage": "v3"},
                    last_run={"run_id": RUN_ID, "stage": "v3", "status": "AWAITING_APPROVAL"},
                    active_stage="v3",
                )
                page.reload(wait_until="domcontentloaded")
                page.wait_for_function(
                    "RUN?.run_id === '" + RUN_ID + "' && RUN.events.length === 19"
                )
                page.locator('.node[data-id="cw"]').click()
                page.wait_for_timeout(150)
                assert len(flags["pending_trace"]) == 1
                assert sum(path == "/api/trace" for _, path in requests) == (
                    2 if outcome == "success" else 3
                )
                state.update(
                    persona={"username": "bob", "customer_id": "customer-b", "role": "operator"},
                    recorded_run=None,
                    last_run=None,
                    active_stage="v0",
                )
                page.evaluate("refresh(false)")
                expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "true")
                expect(page.locator("#drBody")).to_be_empty()
                assert page.evaluate("RUN") is None
                page.locator('.node[data-id="cw"]').click()
                expect(page.locator("#drBody")).not_to_contain_text("alice")
                if outcome == "success":
                    flags["pending_trace"].pop().fulfill(
                        json={"run_id": RUN_ID, "region": "us-east-1", "sources": []}
                    )
                else:
                    flags["pending_trace"].pop().fulfill(
                        status=503, json={"error": "old Alice query failed"}
                    )
                page.wait_for_timeout(150)
                expect(page.locator("#drBody")).not_to_contain_text("alice")
                expect(page.locator("#drBody")).not_to_contain_text("AWS records can arrive later")
                assert sum(path == "/api/trace" for _, path in requests) == (
                    2 if outcome == "success" else 3
                )
                page.keyboard.press("Escape")
                flags["hold_trace"] = False
            events_after_cloud = sum(path.endswith("/events") for _, path in requests)
            assert events_after_cloud == 3 + len(other_hops) + 1 + 2

            state.update(
                persona={"username": "alice", "customer_id": "customer-a", "role": "operator"},
                recorded_run=None,
                last_run=None,
                active_stage="v3",
            )
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("STATE?.persona?.username === 'alice'")
            assert page.evaluate("RUN") is None
            expect(page.locator("#count")).to_have_text("0 / 0")
            assert sum(path.endswith("/events") for _, path in requests) == events_after_cloud
            flags["events_status"] = 404
            state["recorded_run"] = {"run_id": RUN_ID, "stage": "v3"}
            state["last_run"] = {"run_id": RUN_ID, "stage": "v3", "status": "AWAITING_APPROVAL"}
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("STATE?.recorded_run?.run_id === '" + RUN_ID + "'")
            page.wait_for_function(
                "document.querySelector('#capx').textContent.includes('unavailable')"
            )
            assert page.evaluate("RUN") is None
            assert sum(path.endswith("/events") for _, path in requests) == events_after_cloud + 1
            page.clock.run_for(10000)
            assert sum(path.endswith("/events") for _, path in requests) == events_after_cloud + 1

            # An older trace GET cannot overwrite a newer Run or Reset view.
            flags["events_status"], flags["hold_events"] = 200, True
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("STATE?.recorded_run?.run_id === '" + RUN_ID + "'")
            page.wait_for_timeout(150)
            assert len(flags["pending"]) == 1
            page.evaluate(
                "viewEpoch++; RUN={run_id:'new-run',stage:'v3',events:[]}; STEPS=[]; N=0; renderPanel()"
            )
            flags["pending"].pop().fulfill(json={"run_id": RUN_ID, "events": TRACE["events"]})
            page.wait_for_timeout(150)
            assert page.evaluate("RUN.run_id") == "new-run" and page.evaluate("STEPS.length") == 0
            page.evaluate("() => { refresh(); }")
            page.wait_for_timeout(150)
            assert len(flags["pending"]) == 1
            page.evaluate("viewEpoch++; RUN=null; STEPS=[]; N=0; renderPanel()")
            flags["pending"].pop().fulfill(json={"run_id": RUN_ID, "events": TRACE["events"]})
            page.wait_for_timeout(150)
            assert page.evaluate("RUN") is None and page.evaluate("STEPS.length") == 0
            assert not blocked and not js_errors, (blocked, js_errors)
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    print(
        "PASS: two real Chrome reloads hydrate actual 19-hop allowlisted run; Auto/Step/keyboard/drawers/payload, explicit empty/failure, tenant and race fences; no app mutation or external HTTP"
    )


if __name__ == "__main__":
    main()
