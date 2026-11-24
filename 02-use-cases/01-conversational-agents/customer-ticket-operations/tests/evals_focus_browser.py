"""Wide-surface focus contract in installed Chrome, under both motion preferences.

After the wide Evals/Architecture surface is closed and the
page sits idle, re-opening it (Evals button, V5 "Evaluations architecture", nested Architecture
roundtrip) and opening ordinary run-hop / CloudWatch / controls drawers must move keyboard focus
onto the drawer's Close button synchronously, exactly as the deployed console does. Under
`prefers-reduced-motion: reduce` a global 1ms transition rule applies, so a visibility transition
on the closed surface would let `#drClose.focus()` run while the button is still hidden, leaving
focus on the trigger behind the surface where Tab reaches the page player.

Loopback mocks only; no downloads; no network beyond 127.0.0.1. UI_SCREENSHOTS keeps PNG evidence.
"""

from __future__ import annotations

import os
import sys
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
import evals_density_fixtures as fixtures  # noqa: E402

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
VIEWPORTS = [(1920, 1080), (1440, 900), (1280, 800), (834, 900), (390, 844)]
MOTION = ("reduce", "no-preference")
IDLE_MS = 450  # longer than any closing transition the surface ever had (220 ms delay + duration)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def active(page) -> str:
    return page.evaluate(
        "() => { const a = document.activeElement; if(!a) return 'none'; if(a.id) return '#' + a.id; if(a.dataset?.key) return '[data-key=' + a.dataset.key + ']'; if(a.dataset?.id) return '.node[' + a.dataset.id + ']'; return a.tagName.toLowerCase(); }"
    )


def exercise(browser, origin: str, shots: Path | None):
    payload = fixtures.load("v0_fail_current_hist4")
    state = {
        "active_stage": "v5",
        "model": "llama4-maverick",
        "persona": {"username": "alice", "customer_id": "customer-a"},
        "tickets": [
            {"ticket_id": "A-41", "subject": "Delivery delayed", "status": "OPEN", "origin": "seed"}
        ],
        "other_tickets": [],
        "approvals": [],
        "store_comparison": {},
        "last_run": None,
        "recorded_run": None,
    }
    calls, errors, unexpected = [], [], []

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
        calls.append((request.method, path))
        if path == "/api/state":
            route.fulfill(json=state)
        elif path == "/api/evals":
            route.fulfill(json={"builds": payload["builds"], "dataset": payload["dataset"]})
        elif path == "/api/code":
            route.fulfill(
                json={
                    "stage": "v5",
                    "title": "Evidence",
                    "controls": [{"name": "Release gate", "on": True, "detail": "mock"}],
                    "policies": [],
                    "policy_engine": "mock",
                }
            )
        elif path.startswith("/api/runs/") and path.endswith("/events"):
            route.fulfill(json={"run_id": path.split("/")[3], "events": []})
        else:
            unexpected.append(path)
            route.abort()

    checks = {"n": 0}

    def expect_focus(page, where: str, target: str, what: str):
        got = active(page)
        assert got == target, f"[{where}] {what}: focus on {got}, expected {target}"
        checks["n"] += 1

    def closed_wide_unreachable(page, where: str, what: str):
        """Behavioural: the closed surface is inert, hidden from AT and not reachable by Tab."""
        drawer = page.evaluate(
            "() => { const d = document.querySelector('#drawer'), cs = getComputedStyle(d); return {gone: cs.display === 'none' || cs.visibility === 'hidden', inert: d.inert, hidden: d.getAttribute('aria-hidden'), open: d.classList.contains('open')}; }"
        )
        assert drawer == {"gone": True, "inert": True, "hidden": "true", "open": False}, (
            where,
            what,
            drawer,
        )
        page.locator("#evalBtn").focus()
        for _ in range(3):
            page.keyboard.press("Tab")
            assert not page.evaluate(
                "document.querySelector('#drawer').contains(document.activeElement)"
            ), f"[{where}] {what}: Tab reached the closed surface"
        page.locator("#evalBtn").focus()
        checks["n"] += 1

    def run_viewport(page, motion: str, width: int, height: int):
        where = f"{motion} {width}px"
        page.set_viewport_size({"width": width, "height": height})

        def idle():
            page.wait_for_timeout(IDLE_MS)  # wall-clock: CSS transitions ignore any test clock

        # First open from the keyboard: Close is focused at once, then Tab stays inside.
        page.locator("#evalBtn").focus()
        page.keyboard.press("Enter")
        page.wait_for_function("!!document.querySelector('#drBody .evtool')")
        expect_focus(page, where, "#drClose", "first Evals open")
        page.keyboard.press("Tab")
        expect_focus(page, where, "#evalRun", "Tab after first open")
        # Layered Escape: a detail closes first, then the surface; focus returns to the trigger.
        page.locator('[data-key="v0:benign_summary"]').click()
        expect(page.locator("#evalDetail")).to_have_count(1)
        page.keyboard.press("Escape")
        expect(page.locator("#evalDetail")).to_have_count(0)
        expect_focus(
            page, where, "[data-key=v0:benign_summary]", "detail Escape returns to its cell"
        )
        page.keyboard.press("Escape")
        expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "true")
        expect_focus(page, where, "#evalBtn", "focus return after closing Evals")
        idle()
        closed_wide_unreachable(page, where, "closed Evals")
        # Re-open after idling: focus must land on Close, not stay behind the surface.
        page.keyboard.press("Enter")
        page.wait_for_function("!!document.querySelector('#drBody .evtool')")
        expect_focus(page, where, "#drClose", f"Evals re-open after {IDLE_MS}ms idle")
        page.keyboard.press("Tab")
        expect_focus(page, where, "#evalRun", "Tab after re-open (must not reach the page player)")
        if shots and width in (1920, 390) and motion == "reduce":
            page.screenshot(
                path=str(shots / f"focus-reopen-{motion}-{width}x{height}.png"),
                animations="disabled",
            )
        # Nested Architecture roundtrip keeps focus inside, then hands back to the Evals button.
        page.locator("#evalArchBtn").click()
        expect(page.locator("#drawer")).to_have_attribute("data-from", "evalarch")
        expect_focus(page, where, "#drClose", "nested Architecture open")
        page.keyboard.press("Escape")
        expect(page.locator("#drawer")).to_have_attribute("data-from", "evals")
        expect_focus(page, where, "#evalArchBtn", "Architecture to matrix return")
        page.keyboard.press("Escape")
        expect_focus(page, where, "#evalBtn", "closing Evals after the roundtrip")
        idle()
        closed_wide_unreachable(page, where, "closed after roundtrip")
        # V5 "Evaluations architecture" from the toolbar after an idle wide close.
        page.locator("#nextFix").focus()
        page.keyboard.press("Enter")
        expect(page.locator("#drawer")).to_have_attribute("data-from", "evalarch")
        expect_focus(page, where, "#drClose", "nextFix Architecture open after idle")
        page.keyboard.press("Tab")
        assert page.evaluate(
            "document.querySelector('#drawer').contains(document.activeElement)"
        ), f"[{where}] Tab left the Architecture surface"
        page.keyboard.press("Escape")
        expect_focus(page, where, "#nextFix", "focus return from Architecture")
        idle()
        closed_wide_unreachable(page, where, "closed Architecture")
        # Ordinary run-hop traces after a wide close: panel-sized drawer, Close focused.
        page.evaluate(
            "RUN = {run_id: 'mock-run', stage: 'v5', events: []}; STEPS = []; N = 0; renderPanel()"
        )
        for node in ("policy", "cw"):
            page.locator(f'.node[data-id="{node}"]').click()
            expect(page.locator("#drawer")).to_have_attribute("data-from", node)
            expect_focus(page, where, "#drClose", f"{node} trace open after wide close")
            assert not page.evaluate("document.querySelector('#drawer').classList.contains('wide')")
            page.keyboard.press("Escape")
            expect_focus(page, where, f".node[{node}]", f"focus return from {node} trace")
            idle()
        # Controls drawer (openCode): same contract, not wide.
        page.evaluate("openCode()")
        expect(page.locator("#drTitle")).to_contain_text("Controls · V5")
        expect_focus(page, where, "#drClose", "controls drawer open")
        assert not page.evaluate("document.querySelector('#drawer').classList.contains('wide')")
        page.keyboard.press("Escape")
        expect_focus(page, where, ".node[agent]", "focus return from controls drawer")
        idle()
        # The wide surface again straight after an ordinary drawer: still immediate.
        page.locator("#evalBtn").focus()
        page.keyboard.press("Enter")
        page.wait_for_function("!!document.querySelector('#drBody .evtool')")
        expect_focus(page, where, "#drClose", "Evals open after ordinary drawers")
        page.keyboard.press("Escape")
        expect_focus(page, where, "#evalBtn", "final close")
        idle()
        closed_wide_unreachable(page, where, "final closed state")

    for motion in MOTION:
        context = browser.new_context(
            viewport={"width": 1440, "height": 900}, reduced_motion=motion
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        context.route("**/*", intercept)
        page.goto(origin, wait_until="domcontentloaded")
        expect(page.locator("#custTitle")).to_have_text("Region A tickets (operator alice)")
        assert page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches") is (
            motion == "reduce"
        ), motion
        page.wait_for_function("!!EVALS")  # V5 preloads the suites; the Evals button then renders
        for width, height in VIEWPORTS:
            run_viewport(page, motion, width, height)
        assert errors == [] and unexpected == [], (errors, unexpected)
        context.unroute_all(behavior="wait")
        context.close()
    assert all(method == "GET" for method, _ in calls), calls
    print(
        f"PASS: wide-surface focus contract · motion {'/'.join(MOTION)} × {len(VIEWPORTS)} viewports · "
        f"{checks['n']} focus/unreachable checks · idle {IDLE_MS}ms between close and re-open · "
        f"{len(calls)} mock GETs, 0 POSTs."
    )


def main():
    output = os.environ.get("UI_SCREENSHOTS")
    shots = Path(output) / "evals-density" if output else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
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
            exercise(browser, f"http://127.0.0.1:{server.server_port}", shots)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    main()
