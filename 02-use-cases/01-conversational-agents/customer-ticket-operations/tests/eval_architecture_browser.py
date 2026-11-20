"""Exercise the shipped console in installed Chrome with loopback-only mock APIs.

Run with a Python that already has Playwright installed. No browser/tool downloads.
UI_SCREENSHOTS optionally keeps desktop/small-viewport evidence outside the repo.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from collections import Counter
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
DATASET = json.loads((ROOT / "evals/dataset.json").read_text())
VERSION = DATASET["version"]
_spec = spec_from_file_location("browser_eval_contract", ROOT / "functions/api/eval_contract.py")
_contract = module_from_spec(_spec)
_spec.loader.exec_module(_contract)
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OWNED_RUN = "01a1b2bb-9913-7804-8a9b-b70cf4f92260"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def exercise(browser, origin, screenshots):
    state = {
        "active_stage": "v0",
        "model": "llama4-maverick",
        "persona": {"username": "alice", "customer_id": "customer-a"},
        "tickets": [
            {
                "ticket_id": "A-41",
                "subject": "Delivery delayed",
                "status": "OPEN",
                "origin": "seed",
            },
            {
                "ticket_id": "A-90",
                "subject": "Portal request",
                "body": "Please investigate delivery.",
                "status": "OPEN",
                "origin": "form",
                "submitted_by": "customer",
            },
        ],
        "other_tickets": [],
        "approvals": [],
        "store_comparison": {},
        "last_run": None,
        "recorded_run": None,
    }
    calls, errors, unexpected, pending = [], [], [], {}
    fixture = {
        "evals": {},
        "hold_state": True,
        "hold_evals": False,
        "hold_trace": False,
        "fail_delete": False,
    }
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, reduced_motion="reduce")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.clock.install()

    def intercept(route):
        request = route.request
        url = urlsplit(request.url)
        if f"{url.scheme}://{url.netloc}" != origin:
            unexpected.append(request.url)
            route.abort()
            return
        path = url.path
        if not path.startswith(("/api/", "/auth/")):
            route.continue_()
            return
        body = request.post_data_json if request.post_data else None
        calls.append((request.method, path, body))
        if path == "/api/state":
            if fixture["hold_state"]:
                pending.setdefault("state", []).append(route)
                return
            result = state
        elif path == "/api/evals":
            if fixture["hold_evals"]:
                pending.setdefault("evals", []).append(route)
                return
            result = {"builds": fixture["evals"], "dataset": VERSION}
        elif path == "/api/patch":
            assert request.method == "POST"
            assert body == {} or body.get("stage") in [f"v{i}" for i in range(6)]
            state["active_stage"] = body.get("stage", f"v{int(state['active_stage'][1]) + 1}")
            state["last_run"] = None
            state["recorded_run"] = None
            result = {"active_stage": state["active_stage"], "title": "Mock build"}
        elif path == "/api/tickets/delete":
            assert request.method == "POST" and body == {"ticket_id": "A-90"}
            if fixture["fail_delete"]:
                route.fulfill(status=503, json={"error": "simulated transient"})
                return
            state["tickets"] = [
                ticket for ticket in state["tickets"] if ticket["ticket_id"] != "A-90"
            ]
            result = {"deleted": "A-90"}
        elif path == "/api/evals/run":
            assert request.method == "POST" and body == {}
            result = {"status": "QUEUED"}
        elif path == "/api/run":
            assert request.method == "POST" and body["mode"] in ("live", "replay", "approve")
            if body["mode"] == "approve":
                for approval in state["approvals"]:
                    if approval["id"] == body["approval"]:
                        approval["status"] = "APPROVED"
            state["run_id"] = body["run_id"]
            state["recorded_run"] = {"run_id": body["run_id"], "stage": state["active_stage"]}
            state["last_run"] = {
                "run_id": body["run_id"],
                "stage": state["active_stage"],
                "status": "NO_EFFECT",
                "answer": "Mock tickets summarized.",
                "effects": [],
                "proposals": [],
                "controls": [],
            }
            result = {"result": state["last_run"]}
        elif path == "/api/stop":
            assert request.method == "POST" and isinstance(body["stop"], bool)
            state["stop_requested"] = body["stop"]
            result = {"stop_requested": body["stop"]}
        elif path == "/api/breaker/reset":
            assert request.method == "POST" and state.get("breaker_open")
            state["breaker_open"] = False
            result = {"breaker_open": False}
        elif path == "/api/reset":
            assert request.method == "POST" and body == {}
            state.update(
                active_stage="v0",
                stop_requested=False,
                breaker_open=False,
                capture_available=False,
                last_run=None,
                recorded_run=None,
            )
            state.pop("run_id", None)
            result = {"reset": True}
        elif path == "/api/approvals/reject":
            assert request.method == "POST" and body["id"]
            for a in state["approvals"]:
                if a["id"] == body["id"]:
                    a["status"] = "REJECTED"
            result = {"approvals": state["approvals"]}
        elif path == "/api/trace":
            if fixture["hold_trace"]:
                pending.setdefault("trace", []).append(route)
                return
            result = {
                "run_id": parse_qs(url.query).get("run", [""])[0],
                "region": "mock-region",
                "sources": [],
            }
        elif path == "/api/runs/mock-run/events":
            result = {
                "events": [
                    {
                        "kind": "auth.verified",
                        "node": "ingress",
                        "label": "Signed in",
                        "status": "ok",
                    }
                ]
            }
        elif path.startswith("/api/runs/") and path.endswith("/events"):
            result = {
                "run_id": path.split("/")[3],
                "events": [
                    {
                        "sk": "000#api#run",
                        "kind": "run.completed",
                        "node": "agent",
                        "status": "ok",
                        "label": "mock completion",
                        "verdict": "NO_EFFECT",
                    }
                ],
            }
        elif path == "/auth/me":
            result = {
                "presenter": True,
                "acting_as": {"op": state["persona"]["username"]},
                "personas": [
                    {"role": "operator", "username": "alice", "customer_id": "customer-a"},
                    {"role": "operator", "username": "bob", "customer_id": "customer-b"},
                ],
            }
        elif path == "/auth/switch":
            user = parse_qs(url.query)["u"][0]
            assert user in ("alice", "bob")
            state["persona"] = {
                "username": user,
                "customer_id": "customer-a" if user == "alice" else "customer-b",
            }
            state["tickets"] = [
                {
                    "ticket_id": "A-41" if user == "alice" else "B-77",
                    "subject": "Delivery delayed" if user == "alice" else "Billing question",
                    "status": "OPEN",
                    "origin": "seed",
                }
            ]
            state["last_run"] = None
            state["recorded_run"] = None
            state.pop("run_id", None)
            route.fulfill(status=302, headers={"Location": "/"})
            return
        else:
            unexpected.append(path)
            route.abort()
            return
        route.fulfill(json=result)

    context.route("**/*", intercept)
    page.goto(origin, wait_until="domcontentloaded")
    expect(page.locator("#custTitle")).to_have_text("Tickets · loading identity…")
    assert page.locator("#codeBtn").count() == 0
    fixture["hold_state"] = False
    for route in pending.pop("state"):
        route.fulfill(json=state)
    expect(page.locator("#custTitle")).to_have_text("Region A tickets (operator alice)")
    assert page.evaluate("typeof openCode") == "function"
    assert not errors, errors
    assert page.evaluate("mode") == "auto"
    expect(page.locator("#mAuto")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#mStep")).to_have_attribute("aria-pressed", "false")

    def mutations():
        return [call for call in calls if call[0] != "GET"]

    def refresh():
        page.evaluate("refresh(false)")

    def screenshot(name):
        if screenshots:
            page.clock.run_for(4300)  # Let real UI toasts expire before capturing the page.
            page.screenshot(
                path=str(screenshots / name), full_page=page.viewport_size["width"] > 600
            )

    quality_file = os.environ.get("UI_QUALITY_RECORD")
    if quality_file:
        info = json.loads(Path(quality_file).read_text())
        record = info["record"]
        assert _contract.classify_default(record, DATASET, "v5") == "current"
        fixture["evals"] = {"v5": {**record, "dataset_status": "current"}}
        state["active_stage"] = "v5"
        page.evaluate("refresh(false)")
        page.locator("#evalBtn").click()
        page.wait_for_function("id => EVALS?.v5?.suite_id === id", arg=record["suite_id"])
        page.evaluate("renderEvals(); renderGate()")
        verdicts = {r["id"]: r for r in record["gate"]["cases"]}
        for case in record["cases"]:
            if case["kind"] != "benign":
                continue
            q = verdicts[case["id"]]["quality"]
            cell = page.locator(f'[data-key="v5:{case["id"]}"]')
            quality = cell.locator(".ml", has_text="Quality").locator(".v")
            expect(quality).to_have_count(1)
            if q["status"] == "FAIL":
                expect(quality).to_have_class(re.compile(r"\bbad\b"))
                expect(quality).to_contain_text("✕")
                assert cell.locator(".ml", has_text="Quality").locator(".v.ok").count() == 0, (
                    "invalid quality was presented green: " + cell.inner_text()
                )
            else:
                expect(quality).to_have_class(re.compile(r"\bok\b"))
                expect(quality).to_contain_text("✓")
            expect(quality).to_contain_text("Unscored" if q["mean"] is None else str(q["mean"]))
            assert f"quality {q['status']}" in cell.get_attribute("aria-label")
            # Eligibility, floor and the backend reason are disclosed on click, not hover.
            cell.click()
            detail = page.locator("#evalDetail")
            expect(detail.locator(".emd-h b")).to_have_text(f"{case['title']} · V5")
            expect(detail).to_contain_text(f"{q['status']} · {q['eligible']}/{q['k']} eligible")
            if q["mean"] is None:
                expect(detail).to_contain_text(f"Helpfulness unscored (floor {q['floor']})")
            else:
                expect(detail).to_contain_text(
                    f"Helpfulness {q['mean']} {('<' if q['mean'] < q['floor'] else '≥')} floor {q['floor']}"
                )
            expect(detail).to_contain_text(q["reason"])
            assert page.locator("#drBody .c").filter(has_text="eligible").count() == 0
            page.locator("#evalDetailClose").click()
            expect(cell).to_be_focused()
        pill = page.locator("#gatePill")
        expect(pill).to_contain_text(
            f"Release gate: {'PASS' if record['gate']['passed'] else 'FAIL'}"
        )
        assert not mutations() and not unexpected and not errors
        screenshot(f"quality-{info['variant']}.png")
        print(
            f"PASS: shipped backend-owned case quality {info['variant']} · "
            f"release {record['gate']['passed']} · 0 POSTs · {len(calls)} mock GETs"
        )
        context.unroute_all(behavior="wait")
        context.close()
        return

    # Auto plays only recorded events. It never starts a run, patch, reset or eval.
    page.evaluate("RUN = {run_id:'mock-run',events:[]}; STEPS = []; N = 0")
    page.evaluate("pollOnce()")
    page.wait_for_function("STEPS.length > 0")
    page.clock.run_for(1000)
    assert page.evaluate("N") == 1
    assert mutations() == []
    page.locator("#mStep").click()
    expect(page.locator("#mStep")).to_have_attribute("aria-pressed", "true")
    page.evaluate("STEPS.push(STEPS[0]); schedule()")
    page.clock.run_for(1300)
    assert page.evaluate("N") == 1
    page.locator("#mAuto").click()
    page.wait_for_function("N === 2")
    page.evaluate(
        "STEPS.push(STEPS[0]); schedule(); cancelPlayback(); RUN = null; STEPS = []; N = 0"
    )
    page.clock.run_for(1300)
    assert page.evaluate("N") == 0 and mutations() == []

    # Historical FILTERED is not green if its recorded ApplyGuardrail action was UNAVAILABLE.
    state["active_stage"] = "v1"
    state["last_run"] = {
        "status": "FILTERED",
        "controls": [{"control": "Bedrock Guardrails", "status": "UNAVAILABLE · fails closed"}],
    }
    refresh()
    legacy = [
        {
            "kind": "guardrail.intervened",
            "node": "guardrail",
            "status": "denied",
            "label": "unreachable · fails closed",
            "payload": '{"action":"UNAVAILABLE","error":"ValidationException: internal detail withheld"}',
        },
        {
            "kind": "run.completed",
            "node": "agent",
            "status": "denied",
            "verdict": "FILTERED",
            "label": "old run",
            "payload": '{"controls":[{"control":"Bedrock Guardrails","status":"UNAVAILABLE · fails closed"}]}',
        },
    ]
    page.evaluate(
        "evs => { cancelPlayback(); RUN = {run_id:'mock-run',events:evs}; STEPS=toSteps(evs); N=STEPS.length; renderPanel(); }",
        legacy,
    )
    expect(page.locator("#verdict")).to_have_text("DEPENDENCY ERROR")
    assert "dependency error" in page.evaluate("caption(STEPS[0])[1]")
    assert "not a detected prompt attack" in page.evaluate("caption(STEPS[1])[1]")
    page.locator('.node[data-id="guardrail"]').click()
    expect(page.locator("#drBody")).to_contain_text("recorded Guardrail action")
    expect(page.locator("#drBody")).to_contain_text("UNAVAILABLE")
    expect(page.locator("#drBody")).to_contain_text("ValidationException")
    assert "internal detail withheld" not in page.locator("#drBody").inner_text()
    page.keyboard.press("Escape")
    # Five selected seed tickets remain exact and in order; a sixth is disabled, while
    # deselecting a selected one always remains possible. No run/model POST occurs.
    previous_tickets = copy.deepcopy(state["tickets"])
    state["active_stage"] = "v3"
    state["tickets"] += [
        {
            "customer_id": "customer-a",
            "ticket_id": t,
            "subject": t,
            "status": "OPEN",
            "origin": "seed",
        }
        for t in ("A-42", "A-43", "A-44", "A-45", "A-46")
    ]
    refresh()
    for t in ("A-41", "A-42", "A-43", "A-44", "A-45"):
        page.locator(f'[data-ask="{t}"]').click()
    assert page.evaluate("[...ASKS]") == ["A-41", "A-42", "A-43", "A-44", "A-45"]
    expect(page.locator('[data-ask="A-46"]')).to_be_disabled()
    page.locator('[data-ask="A-41"]').click()
    expect(page.locator('[data-ask="A-46"]')).to_be_enabled()
    page.locator('[data-ask="A-46"]').click()
    assert page.evaluate("[...ASKS]") == ["A-42", "A-43", "A-44", "A-45", "A-46"]
    page.evaluate("ASKS.clear(); renderPanel()")
    state["tickets"] = previous_tickets
    # A trusted Ask that returned function JSON as text is incomplete, not a held close.
    state["active_stage"] = "v3"
    state["approvals"] = []
    state["last_run"] = {
        "status": "NO_EFFECT",
        "note": "Operator close request incomplete: the model made no native tool call; no approval card or ticket change.",
        "answer": json.loads((ROOT / "tests/fixtures/model_text_only_r8.json").read_text())["text"],
        "proposals": [],
        "effects": [],
    }
    refresh()
    incomplete = {
        "kind": "run.completed",
        "node": "agent",
        "status": "error",
        "verdict": "NO_EFFECT",
        "label": "operator close request incomplete · no held close or approval card",
    }
    page.evaluate(
        "e => { cancelPlayback(); RUN = {run_id:'mock-ask',events:[e]}; STEPS=toSteps([e]); N=STEPS.length; renderPanel(); }",
        incomplete,
    )
    expect(page.locator("#verdict")).to_have_text("NO EFFECT")
    assert "no eligible native close was held" in page.evaluate("caption(STEPS[0])[1]")
    assert "nothing changed" in page.evaluate("caption(STEPS[0])[1]")
    assert state["approvals"] == [] and mutations() == []
    first = page.evaluate(
        "caption({events:[{kind:'model.task_incomplete',label:'verified close task incomplete after screened own read'}]})[1]"
    )
    repair = page.evaluate(
        "caption({events:[{kind:'model.completion_repair',label:'one owned-conversation model continuation admitted'}]})[1]"
    )
    exhausted = page.evaluate(
        "caption({events:[{kind:'model.task_incomplete',label:'native close proposal still missing · repair budget exhausted'}]})[1]"
    )
    assert "task is incomplete" in first and "one more model turn" in repair
    assert "no eligible native close" in exhausted and mutations() == []
    state["active_stage"] = "v5"
    state["last_run"] = {
        "status": "UNAVAILABLE",
        "guardrail_unavailable": True,
        "controls": [{"control": "Bedrock Guardrails", "status": "UNAVAILABLE · fails closed"}],
    }
    refresh()
    new_events = [
        {
            "kind": "guardrail.unavailable",
            "node": "guardrail",
            "status": "error",
            "label": "dependency unavailable",
            "payload": '{"action":"UNAVAILABLE","guardrailVersion":"DRAFT","client_region":"us-east-1","request_id":"request-12345"}',
        },
        {
            "kind": "run.completed",
            "node": "agent",
            "status": "error",
            "verdict": "UNAVAILABLE",
            "label": "dependency error",
        },
    ]
    page.evaluate(
        "evs => { cancelPlayback(); RUN={run_id:'mock-run',events:evs}; STEPS=toSteps(evs); N=STEPS.length; renderPanel(); }",
        new_events,
    )
    expect(page.locator("#verdict")).to_have_text("DEPENDENCY ERROR")
    assert "a detected prompt attack" in page.evaluate("caption(STEPS[1])[1]")
    assert "guardrail unavailable" in page.locator("#said").inner_text().lower()
    page.locator('.node[data-id="guardrail"]').click()
    expect(page.locator("#drBody")).to_contain_text("request-12345")
    expect(page.locator("#drBody")).to_contain_text("DRAFT")
    page.keyboard.press("Escape")
    note_hold = [
        {
            "kind": "tool.skipped",
            "node": "agent",
            "status": "denied",
            "tool": "add_note",
            "label": "add_note A-90 not evaluated",
            "detail": {
                "reason": "not evaluated: add_note has no trusted operator task or suite grant"
            },
        }
    ]
    page.evaluate(
        "evs => { cancelPlayback(); RUN={run_id:'mock-run',events:evs}; STEPS=toSteps(evs); N=1; renderPanel(); }",
        note_hold,
    )
    expect(page.locator("#capx")).to_contain_text("no trusted operator task")
    expect(page.locator("#capx")).to_contain_text("No Gateway call or policy DENY")
    assert "breaker already ended" not in page.locator("#capx").inner_text()
    limited = [
        {
            "kind": "tool.invalid_arguments",
            "node": "agent",
            "status": "error",
            "label": "customer_id must be a nonempty string",
        },
        {
            "kind": "tool.skipped",
            "node": "agent",
            "status": "halted",
            "label": "later foreign read not evaluated",
            "detail": {"reason": "argument repair limit reached; later proposal not evaluated"},
        },
        {
            "kind": "run.completed",
            "node": "agent",
            "status": "error",
            "verdict": "ARGUMENT_ERROR",
            "label": "bounded repair exhausted",
        },
    ]
    state["last_run"] = {
        "status": "ARGUMENT_ERROR",
        "effects": [],
        "answer": "later proposals withheld",
    }
    refresh()
    page.evaluate(
        "evs => { cancelPlayback(); RUN={run_id:'mock-run',events:evs}; STEPS=toSteps(evs); N=2; renderPanel(); }",
        limited,
    )
    expect(page.locator("#capx")).to_contain_text("not evaluated")
    expect(page.locator("#capx")).to_contain_text("earlier Gateway results remain")
    page.evaluate("N=STEPS.length; renderPanel()")
    expect(page.locator("#verdict")).to_have_text("MODEL ARGUMENT ERROR")
    expect(page.locator("#capx")).to_contain_text("Later proposals were withheld locally")
    assert "No Gateway dispatch" not in page.locator("#capx").inner_text()
    state["active_stage"] = "v0"
    state["last_run"] = None
    page.evaluate("cancelPlayback(); RUN = null; STEPS=[]; N=0")
    refresh()
    assert mutations() == []

    # Rendering and background GET refreshes never apply fixes. Each V0-V4 user click does.
    assert mutations() == []
    for build in range(5):
        expect(page.locator("#nextFix")).to_contain_text(f"Apply V{build + 1}")
        refresh()
        assert len(mutations()) == build
        page.locator("#nextFix").click()
        expect(page.locator(f'.step[data-stage="v{build + 1}"]')).to_have_attribute(
            "aria-current", "true"
        )
        assert mutations()[-1] == ("POST", "/api/patch", {})
    expect(page.locator("#nextFix")).to_have_text("Evaluations architecture")
    expect(page.locator("#nextFix")).to_be_enabled()
    assert len(mutations()) == 5

    # A prior run and pending playback must survive opening and closing the static diagram.
    state["last_run"] = {
        "run_id": OWNED_RUN,
        "stage": "v5",
        "status": "NO_EFFECT",
        "answer": "Recorded answer",
        "effects": [],
    }
    state["run_id"] = OWNED_RUN
    state["recorded_run"] = {"run_id": OWNED_RUN, "stage": "v5"}
    page.evaluate("refresh()")
    page.evaluate("N = 0; renderPanel()")
    history = page.evaluate(
        "JSON.stringify({RUN, STEPS, N, last: STATE.last_run, asks: [...ASKS]})"
    )
    mutation_count = len(mutations())
    for key in ("Enter", "Space"):
        page.locator("#nextFix").focus()
        page.keyboard.press(key)
        expect(page.locator("#drawer")).to_have_attribute("data-from", "evalarch")
        expect(page.locator("#drClose")).to_be_focused()
        assert (
            page.evaluate("JSON.stringify({RUN, STEPS, N, last: STATE.last_run, asks: [...ASKS]})")
            == history
        )
        assert len(mutations()) == mutation_count
        arch = page.locator("#drBody")
        expect(arch).to_contain_text("static explanation, not live evidence")
        expect(arch).to_contain_text("100% sampling configured, with a 2-minute session timeout")
        expect(arch).to_contain_text("No automatic enforcement")
        expect(arch).to_contain_text("not the LLM’s self-report")
        expect(arch).to_contain_text(
            "every benign sample needs a valid Helpfulness score, with mean ≥ 0.5"
        )
        expect(arch).to_contain_text("not proof that every session was scored")
        expect(arch).to_contain_text("k10 variance study is separate")
        assert page.locator(".ea img").count() == 8
        page.wait_for_function(
            "[...document.querySelectorAll('.ea img')].every(i => i.complete && i.naturalWidth > 0)"
        )
        screenshot(f"desktop-v5-architecture-{key.lower()}.png")
        if key == "Enter":
            page.locator("#drBody").evaluate("el => el.scrollTop = el.scrollHeight")
            screenshot("desktop-v5-architecture-evaluators.png")
        if key == "Enter":
            page.locator("#drClose").click()
        else:
            page.keyboard.press("Escape")
        expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "true")
        expect(page.locator("#nextFix")).to_be_focused()

    # V5 architecture is read-only and stays available even while a run is polling.
    page.evaluate("polling = 123; renderChrome()")
    expect(page.locator("#nextFix")).to_be_enabled()
    page.locator("#nextFix").click()
    page.keyboard.press("Escape")
    page.evaluate("polling = null")
    assert len(mutations()) == mutation_count

    # On V0 the Evals drawer offers the same diagram, without a build switch.
    state["active_stage"] = "v0"
    refresh()
    for key in ("Enter", "Space"):
        page.locator("#evalBtn").click()
        expect(page.locator("#evalArchBtn")).to_be_visible()
        page.locator("#evalArchBtn").focus()
        if key == "Enter":
            screenshot("desktop-v0-evals-link.png")
        # Polling's matrix re-render preserves focused controls.
        page.evaluate("renderEvals()")
        expect(page.locator("#evalArchBtn")).to_be_focused()
        page.keyboard.press(key)
        expect(page.locator("#drawer")).to_have_attribute("data-from", "evalarch")
        static_html = page.locator("#drBody").inner_html()
        page.evaluate("renderEvals(); renderChrome(); renderPanel()")
        assert page.locator("#drBody").inner_html() == static_html
        if key == "Enter":
            page.locator("#drClose").click()
        else:
            page.keyboard.press("Escape")
        expect(page.locator("#drawer")).to_have_attribute("data-from", "evals")
        expect(page.locator("#evalArchBtn")).to_be_focused()
        page.keyboard.press("Escape")
        expect(page.locator("#evalBtn")).to_be_focused()
    assert state["active_stage"] == "v0"
    assert len(mutations()) == mutation_count
    assert page.evaluate("N") == 0

    # Late Evals responses and scheduled eval polling cannot replace the architecture/trace.
    state["active_stage"] = "v5"
    refresh()
    fixture["hold_evals"] = True
    page.locator("#evalBtn").click()
    expect(page.locator("#drBody")).to_contain_text("Loading eval suites")
    # The wide Evals surface covers the toolbar for the pointer; the keyboard still reaches it.
    page.locator("#nextFix").focus()
    page.keyboard.press("Enter")
    fixture["hold_evals"] = False
    fixture["evals"] = {"v5": {"status": "RUNNING"}}
    for route in pending.pop("evals"):
        route.fulfill(json={"builds": fixture["evals"]})
    page.wait_for_function("EVALS?.v5?.status === 'RUNNING'")
    page.clock.run_for(5100)
    expect(page.locator("#drTitle")).to_have_text("Evaluations architecture")
    page.keyboard.press("Escape")
    page.locator('.node[data-id="policy"]').click()
    page.clock.run_for(5100)
    expect(page.locator("#drTitle")).to_contain_text("Trace · AgentCore Policy")
    page.keyboard.press("Escape")
    assert len(mutations()) == mutation_count

    # CloudWatch's actual drawer still renders for a currently owned recorded run;
    # an unmatched local mock id is never a CloudWatch read authority.
    page.evaluate(
        "id => { RUN={run_id:id,stage:'v5',events:[]}; STEPS=[]; N=0; renderPanel(); }", OWNED_RUN
    )
    page.locator('.node[data-id="cw"]').click()
    expect(page.locator("#drBody")).to_contain_text("AWS records can arrive later")
    expect(page.locator("#drBody")).to_contain_text("Policy (agent inference)")
    page.keyboard.press("Escape")
    expect(page.locator('.node[data-id="cw"]')).to_be_focused()
    fixture["hold_trace"] = True
    page.locator('.node[data-id="cw"]').click()
    expect(page.locator("#drBody")).to_contain_text("Querying CloudWatch")
    page.locator("#nextFix").click()
    for route in pending.pop("trace"):
        route.fulfill(json={"run_id": "mock-run", "sources": []})
    expect(page.locator("#drTitle")).to_have_text("Evaluations architecture")
    expect(page.locator("#drBody")).to_contain_text("static explanation")
    fixture["hold_trace"] = False
    page.keyboard.press("Escape")

    # A prior green V5 is historical only; current safety-green/control-failing rows
    # never render as an all-green release gate or merge mismatched case revisions.
    fixture["evals"] = {
        "v5": {
            "status": "DONE",
            "dataset": "2026-10-26.1",
            "dataset_status": "historical",
            "suite_id": "old",
            "gate": {
                "passed": True,
                "cases": [{"id": "benign_summary", "safety": "PASS", "passed": 1, "k": 1}],
            },
            "cases": [
                {"id": "benign_summary", "title": "Old summary", "kind": "benign", "samples": []}
            ],
        }
    }
    page.locator("#evalBtn").click()
    page.wait_for_function("EVALS?.v5?.suite_id === 'old'")
    page.evaluate("renderEvals(); renderGate()")
    expect(page.locator(".em thead th").nth(6)).to_contain_text("Historical 2026-10-26.1")
    expect(page.locator('[data-key="v5:gate"]')).to_have_text("Not current")
    expect(page.locator("#drBody .evsum")).to_contain_text("historical · not current")
    expect(page.locator("#gatePill")).to_contain_text("not current")
    page.keyboard.press("Escape")
    current_cases = [
        {
            "id": c["id"],
            "title": c["title"],
            "kind": c["kind"],
            **({"family": c["family"]} if c.get("family") else {}),
            "samples": [
                {
                    "run_id": f"browser-{c['id']}-{i}",
                    "verdict": "NO_EFFECT",
                    "local": "PASS",
                    "screen": {"status": "PASS"},
                    "useful_evidence": True,
                    "scores": {
                        "no_forbidden_effect": {"label": "PASS", "value": 1.0},
                        **(
                            {"Builtin.Helpfulness": {"label": "SCORED", "value": 0.0}}
                            if c["kind"] == "benign"
                            else {}
                        ),
                    },
                    **(
                        {
                            "control": {
                                "family": c["family"],
                                "status": "FAIL",
                                "reason": "no Gateway denial",
                                "tool_outcomes": [],
                            }
                        }
                        if c.get("family")
                        else {}
                    ),
                }
                for i in range(c["samples"])
            ],
        }
        for c in DATASET["cases"]
    ]
    current_gate = _contract.gate(current_cases)
    fixture["evals"] = {
        "v2": fixture["evals"]["v5"],
        "v5": {
            "status": "DONE",
            "build": "v5",
            "source_version": VERSION,
            "dataset": VERSION,
            "dataset_name": "default",
            "dataset_status": "current",
            "suite_id": "new",
            "gate": current_gate,
            "cases": current_cases,
        },
    }
    assert _contract.classify_default(fixture["evals"]["v5"], DATASET, "v5") == "current"
    fixture["hold_evals"] = True
    page.locator("#evalBtn").click()
    proof = page.evaluate(
        "rows => { EVALS = rows; renderEvals(); renderGate(); return {body: document.querySelector('#drBody').textContent, pill: document.querySelector('#gatePill').textContent, gate: document.querySelector('[data-key=\"v5:gate\"]').textContent, red: [...document.querySelectorAll('#drBody .cell:not(.old) .ml')].some(e => e.textContent.startsWith('Control') && e.querySelector('.v.bad'))}; }",
        fixture["evals"],
    )
    assert "Other revision" in proof["body"] and "Not current" in proof["body"]
    assert proof["gate"] == "FAIL"  # the badge is the short status only; the reason is disclosed
    assert proof["red"] and "safety PASS" in proof["pill"]
    assert "helpfulness 0" in proof["pill"] and "controls FAIL" in proof["pill"]
    case_cell = page.locator('[data-key="v5:write_cap"]')
    expect(case_cell.locator(".ml", has_text="Control").locator(".v")).to_have_text("✕ 0/1")
    case_cell.click()
    expect(page.locator("#evalDetail")).to_contain_text("cap · FAIL 0/1 · no Gateway denial")
    expect(page.locator("#evalDetail")).to_contain_text("Runtime replay / Gateway probe")
    expect(page.locator("#evalDetail")).to_contain_text("no Gateway denial")
    page.locator('[data-key="v2:benign_summary"]').click()
    expect(page.locator("#evalDetail")).to_contain_text(
        "Case rows are never mixed across revisions"
    )
    corrupt = copy.deepcopy(fixture["evals"])
    corrupt["v5"]["cases"][-1]["family"] = "wrong-probe"
    corrupt["v5"]["gate"]["passed"] = True
    assert _contract.classify_default(corrupt["v5"], DATASET, "v5") == "invalid"
    corrupt["v5"]["dataset_status"] = "invalid"  # exact API classification
    invalid = page.evaluate(
        "rows => { EVALS=rows; renderEvals(); renderGate(); return {body:document.querySelector('#drBody').textContent,pill:document.querySelector('#gatePill').textContent}; }",
        corrupt,
    )
    assert "invalid · no case evidence" in invalid["body"]
    assert "invalid · no release evidence" in invalid["pill"]
    assert "Release gate: PASS" not in invalid["pill"]
    page.keyboard.press("Escape")
    fixture["hold_evals"] = False
    for route in pending.pop("evals", []):
        route.fulfill(json={"builds": fixture["evals"], "dataset": VERSION})

    # The eval execution button requires its own explicit click and confirmation.
    fixture["evals"] = {}
    page.locator("#evalBtn").click()
    expect(page.locator("#evalRun")).to_be_enabled()
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("#evalRun").click()
    assert len(mutations()) == mutation_count
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#evalRun").click()
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('Eval suite started')"
    )
    assert mutations()[-1] == ("POST", "/api/evals/run", {})
    page.keyboard.press("Escape")

    # No default Region or fake identity before/without an authenticated persona.
    for persona, label in (
        (None, "Unknown Region tickets (operator unknown)"),
        (
            {"username": "carol", "customer_id": "unmapped"},
            "Unknown Region tickets (operator carol)",
        ),
        ({"customer_id": "customer-b"}, "Region B tickets (operator unknown)"),
        (
            {"username": '<img src=x onerror="window.pwned=1">', "customer_id": "customer-b"},
            'Region B tickets (operator <img src=x onerror="window.pwned=1">)',
        ),
    ):
        state["persona"] = persona
        refresh()
        expect(page.locator("#custTitle")).to_have_text(label)
        page.evaluate("N = STEPS.length; renderPanel()")
        assert page.locator("#custTitle img, #capx img").count() == 0
        # A persona change drops any previous operator's recorded trace, not relabels it.
        assert page.evaluate("RUN") is None
        expect(page.locator("#capx")).to_contain_text("Run agent")
        assert page.evaluate("window.pwned") is None
        assert page.evaluate("STATE.persona") == persona

    state["persona"] = {"username": "alice", "customer_id": "customer-a"}
    state["active_stage"] = "v0"
    refresh()
    screenshot("desktop-alice-v0.png")
    # Portal deletion UI cancels safely and issues only the intended delete on confirm.
    page.once("dialog", lambda dialog: dialog.dismiss())
    before = copy.deepcopy(mutations())
    page.locator('[data-delete="A-90"]').click()
    assert mutations() == before
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('[data-delete="A-90"]').click()
    expect(page.locator('[data-delete="A-90"]')).to_have_count(0)
    assert mutations()[-1] == ("POST", "/api/tickets/delete", {"ticket_id": "A-90"})

    page.locator("#gearBtn").click()
    page.locator('#personas a:has-text("bob")').click()
    expect(page.locator("#custTitle")).to_have_text("Region B tickets (operator bob)")
    assert page.evaluate("STATE.persona.customer_id") == "customer-b"
    expect(page.locator("#tickets")).to_contain_text("B-77")
    screenshot("desktop-bob-v0.png")

    # Use the actual page at a phone-sized viewport, not an isolated diagram fixture.
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    screenshot("small-v0-console.png")
    page.locator("#evalBtn").click()
    screenshot("small-v0-evals-link.png")
    page.locator("#evalArchBtn").click()
    screenshot("small-v0-architecture-top.png")
    page.locator(".eal").nth(1).evaluate("el => el.scrollIntoView({block: 'start'})")
    screenshot("small-v0-architecture-online.png")
    page.locator("#drBody").evaluate("el => el.scrollTop = el.scrollHeight")
    screenshot("small-v0-architecture-evaluators.png")
    assert page.locator("#drBody").evaluate("el => el.scrollWidth <= el.clientWidth"), (
        "architecture overflows"
    )
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "page overflows"
    page.locator("#drClose").click()
    expect(page.locator("#evalArchBtn")).to_be_focused()
    assert errors == [], errors
    assert unexpected == [], unexpected
    assert [call[1] for call in mutations()] == ["/api/patch"] * 5 + [
        "/api/evals/run",
        "/api/tickets/delete",
    ]

    # Every visible architecture node opens its own read-only trace and restores focus.
    page.keyboard.press("Escape")  # nested Architecture close returns to Evals first
    expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "true")
    page.locator("#gearBtn").click()
    page.locator('#personas a:has-text("alice")').click()
    expect(page.locator("#custTitle")).to_have_text("Region A tickets (operator alice)")
    before_clicks = len(mutations())
    state["active_stage"] = "v5"
    refresh()
    for node in page.locator(".node:visible").all():
        label = node.get_attribute("data-id")
        node.click()
        expect(page.locator("#drawer")).to_have_attribute("data-from", label)
        page.locator("#drClose").click()
        expect(node).to_be_focused()
    assert len(mutations()) == before_clicks
    # Stage selector buttons each require a click; merely looking at V0-V5 is read-only.
    for version in range(6):
        page.locator(f'#steps [data-stage="v{version}"]').click()
        expect(page.locator(f'#steps [data-stage="v{version}"]')).to_have_attribute(
            "aria-current", "true"
        )
        assert mutations()[-1] == ("POST", "/api/patch", {"stage": f"v{version}"})
    # Payload details and keyboard player never invoke a mutating endpoint.
    page.evaluate(
        "cancelPlayback(); RUN={run_id:'mock-run',events:[]}; STEPS=[{events:[{kind:'auth.verified',node:'ingress',status:'ok',sk:'long',payload:JSON.stringify({text:'x'.repeat(6000)})}]}]; N=1; renderPanel()"
    )
    expect(page.locator("#payloads .x")).to_be_visible()
    page.locator("#payloads .x").click()
    expect(page.locator("#payloads .x")).to_have_attribute("aria-expanded", "true")
    page.locator("#payloads .x").click()
    expect(page.locator("#payloads .x")).to_have_attribute("aria-expanded", "false")
    page.locator("#mStep").click()
    page.evaluate("N=0; renderPanel()")
    expect(page.locator("#back")).to_be_disabled()
    page.locator("#next").click()
    assert page.evaluate("N") == 1
    expect(page.locator("#next")).to_be_disabled()
    page.locator("#back").click()
    assert page.evaluate("N") == 0
    page.keyboard.press("ArrowRight")  # Back button retains focus; global player arrow still works
    assert page.evaluate("N") == 1
    page.keyboard.press("ArrowLeft")  # focus is still Back; does not move the page caret
    assert page.evaluate("N") == 0
    page.locator("#next").click()
    page.keyboard.press("ArrowLeft")  # Next button retains focus too
    assert page.evaluate("N") == 0
    page.locator("h1").first.click()
    page.keyboard.press("ArrowRight")
    assert page.evaluate("N") == 1
    page.keyboard.press("ArrowLeft")
    assert page.evaluate("N") == 0
    page.locator("#mAuto").click()
    page.wait_for_function("N === 1")
    assert len(mutations()) == before_clicks + 6
    # Operator menu actions: stop/clear and breaker recovery have distinct POSTs.
    page.locator("#gearBtn").click()
    page.locator("#mStop").click()
    expect(page.locator("#stopPill")).to_be_visible()
    expect(page.locator("#stopPill")).to_have_attribute(
        "title", re.compile("already-admitted work may finish")
    )
    assert mutations()[-1] == ("POST", "/api/stop", {"stop": True})
    page.locator("#gearBtn").click()
    page.locator("#mStop").click()
    expect(page.locator("#stopPill")).to_be_hidden()
    assert mutations()[-1] == ("POST", "/api/stop", {"stop": False})
    state["breaker_open"] = True
    refresh()
    page.locator("#gearBtn").click()
    page.locator("#mBreaker").click()
    expect(page.locator("#breakerPill")).to_be_hidden()
    assert mutations()[-1] == ("POST", "/api/breaker/reset", {})
    page.locator("#gearBtn").click()
    before_reset = len(mutations())
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("#mReset").click()
    assert len(mutations()) == before_reset
    page.locator("#gearBtn").click()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#mReset").click()
    expect(page.locator('#steps [data-stage="v0"]')).to_have_attribute("aria-current", "true")
    assert mutations()[-1] == ("POST", "/api/reset", {})
    assert page.evaluate("N") == 0  # pending Auto timer cancelled by reset
    page.locator('#steps [data-stage="v3"]').click()
    expect(page.locator('[data-ask="A-41"]')).to_be_visible()
    before_ask = len(mutations())
    page.locator('[data-ask="A-41"]').click()
    expect(page.locator('[data-ask="A-41"]')).to_have_attribute("aria-pressed", "true")
    assert len(mutations()) == before_ask  # asking is not approval or dispatch
    page.locator("#runBtn").click()
    page.wait_for_function("polling === null && RUN?.events?.length > 0")
    live = mutations()[-1]
    assert live[1] == "/api/run" and live[2]["mode"] == "live"
    assert live[2]["requests"] == ["A-41"] and not page.evaluate("ASKS.size")
    state.update(capture_available=True, capture_stale=True, capture_source="v3")
    refresh()
    expect(page.locator("#replayBtn")).to_be_disabled()
    state["capture_stale"] = False
    refresh()
    page.locator("#replayBtn").click()
    page.wait_for_function("polling === null")
    replay = mutations()[-1]
    assert replay[1] == "/api/run" and replay[2]["mode"] == "replay"
    assert "requests" not in replay[2]
    state["approvals"] = [
        {
            "id": "card-approve",
            "tool": "close_ticket",
            "ticket_id": "A-41",
            "status": "PENDING",
            "reason": "operator requested",
            "requested_by": "operator",
        },
        {
            "id": "card-reject",
            "tool": "close_ticket",
            "ticket_id": "A-41",
            "status": "PENDING",
            "reason": "untrusted ticket text",
            "requested_by": "ticket",
        },
    ]
    page.evaluate("cancelPlayback(); RUN=null; STEPS=[]; N=0")
    refresh()
    page.locator('[data-reject="card-reject"]').click()
    expect(page.locator("#tickets")).to_contain_text("rejected by")
    assert mutations()[-1] == ("POST", "/api/approvals/reject", {"id": "card-reject"})
    page.locator('[data-approve="card-approve"]').click()
    page.wait_for_function("polling === null")
    assert mutations()[-1][1] == "/api/run" and mutations()[-1][2]["mode"] == "approve"
    assert mutations()[-1][2]["approval"] == "card-approve"
    state["tickets"].append(
        {
            "ticket_id": "A-90",
            "subject": "Mock portal ticket",
            "body": "Ordinary customer request",
            "status": "OPEN",
            "origin": "form",
            "submitted_by": "customer",
        }
    )
    fixture["fail_delete"] = True
    refresh()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('[data-delete="A-90"]').click()
    expect(page.locator("#toast")).to_contain_text("simulated transient")
    expect(page.locator('[data-delete="A-90"]')).to_have_count(1)
    assert mutations()[-1] == ("POST", "/api/tickets/delete", {"ticket_id": "A-90"})
    fixture["fail_delete"] = False
    state["busy"] = True
    refresh()
    expect(page.locator('[data-delete="A-90"]')).to_be_disabled()
    state["busy"] = False
    refresh()
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator('[data-delete="A-90"]').click()
    expect(page.locator('[data-delete="A-90"]')).to_have_count(0)
    # External navigation is intentionally blocked in this local mock; inspect destinations only.
    # Real portal/login/logout click-through needs an authenticated deployed session.
    page.locator("#gearBtn").click()
    expect(page.locator('a[href="/portal/"]')).to_have_attribute("target", "ticketops_portal")
    expect(page.locator('a[href="/auth/logout"]')).to_have_count(1)
    assert errors == [] and unexpected == []
    print(
        "PASS: shipped full-script boot, five click-only fixes, read-only architecture from both triggers,"
    )
    print(
        "Enter/Space/Escape and focus, polling races, authenticated headings/persona switch, CloudWatch,"
    )
    print(
        "Evals confirmation, deletion, desktop/small screenshots; no external requests or JS errors."
    )
    observed_posts = mutations()
    by_path = Counter(path for _, path, _ in observed_posts)
    run_modes = Counter(body["mode"] for _, path, body in observed_posts if path == "/api/run")
    assert set(by_path) <= {
        "/api/patch",
        "/api/evals/run",
        "/api/tickets/delete",
        "/api/run",
        "/api/stop",
        "/api/breaker/reset",
        "/api/reset",
        "/api/approvals/reject",
    }
    print(
        f"Mock API calls: {len(calls)}; mutations: {len(observed_posts)} explicit POSTs; "
        f"by endpoint: {dict(sorted(by_path.items()))}; run modes: {dict(sorted(run_modes.items()))}."
    )
    context.unroute_all(behavior="wait")
    context.close()


def main():
    output = os.environ.get("UI_SCREENSHOTS")
    screenshots = Path(output) if output else None
    if screenshots:
        screenshots.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "web"))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.environ.get("UI_CHROME", CHROME), headless=True
            )
            exercise(browser, f"http://127.0.0.1:{server.server_port}", screenshots)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
