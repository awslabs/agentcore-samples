"""Evals matrix density layout: the shipped console in installed Chrome, loopback mocks only.

Visual fixtures (tests/fixtures/evals_density) are synthetic local records shaped like
/api/evals, not live release evidence. Every scenario is rendered at five viewports; the
assertions cover column bounds, row heights, badge brevity, keyboard disclosure, focus return,
refresh preservation, Run-suite disable/confirm semantics and drawer sizing cleanup.
Run with a Python that has Playwright installed (UI_BROWSER_PYTHON); no downloads.
UI_SCREENSHOTS keeps the PNG evidence outside the repo.
"""

from __future__ import annotations

import copy
import os
import re
import struct
import sys
import threading
import zlib
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
SCENARIOS = [
    "running_mixed",  # the first reference screenshot: V0 current .9 RUNNING, V1-V5 historical .4
    "v0_fail_current_hist4",  # the second reference screenshot: V0 DONE FAIL 7/17, 0/3 unscored, 0/17
    "complete_pass_fail",
    "quality_missing_invalid",
    "invalid_dataset",
    "all_empty",
    "queued_only",
    "long_text",
]
INK_3 = "rgba(245, 243, 239, 0.54)"
GREEN, PINK = "rgb(110, 231, 80)", "rgb(255, 77, 139)"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def png_rows(path: Path, upto: int):
    """Decode the first `upto` rows of a non-interlaced 8-bit RGB/RGBA PNG (stdlib only)."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    pos, idat, width, height, channels = 8, [], 0, 0, 0
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and interlace == 0 and color in (2, 6), (depth, color, interlace)
            channels = 3 if color == 2 else 4
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + length
    raw, stride = zlib.decompress(b"".join(idat)), width * channels
    prev, rows = bytearray(stride), []
    for y in range(min(upto, height)):
        off = y * (stride + 1)
        flt, line = raw[off], bytearray(raw[off + 1 : off + 1 + stride])
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if flt == 1:
                line[i] = (line[i] + a) & 255
            elif flt == 2:
                line[i] = (line[i] + b) & 255
            elif flt == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif flt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line))
        prev = line
    return channels, rows


def ink_pixels(path: Path, x0: int, y0: int, x1: int, y1: int) -> int:
    """Pixels in the box that differ from the panel background (#0d0d18): painted text/marks."""
    channels, rows = png_rows(path, y1)
    count = 0
    for y in range(y0, y1):
        row = rows[y]
        for x in range(x0, x1):
            r, g, b = row[x * channels], row[x * channels + 1], row[x * channels + 2]
            if abs(r - 13) + abs(g - 13) + abs(b - 24) > 40:
                count += 1
    return count


def rect(page, selector):
    return page.evaluate(
        "s => { const r = document.querySelector(s).getBoundingClientRect(); return {left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height}; }",
        selector,
    )


def box(locator):
    b = locator.bounding_box()
    return {
        "left": b["x"],
        "right": b["x"] + b["width"],
        "top": b["y"],
        "bottom": b["y"] + b["height"],
        "width": b["width"],
        "height": b["height"],
    }


def inside_x(inner, outer, slack=1):
    """Horizontal containment: the detail may scroll vertically, never sideways out of view."""
    return inner["left"] >= outer["left"] - slack and inner["right"] <= outer["right"] + slack


def inside(inner, outer, slack=1):
    return (
        inner["left"] >= outer["left"] - slack
        and inner["right"] <= outer["right"] + slack
        and inner["top"] >= outer["top"] - slack
        and inner["bottom"] <= outer["bottom"] + slack
    )


def exercise(browser, origin, shots: Path | None):
    state = {
        "active_stage": "v0",
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
    all_fixtures = {name: fixtures.load(name) for name in SCENARIOS}
    for payload in all_fixtures.values():
        fixtures.verify(payload)
    current = {"builds": {}, "dataset": fixtures.VERSION}
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
            route.fulfill(json=current)
        elif path == "/api/evals/run":
            assert request.method == "POST"
            route.fulfill(json={"suite_id": "mock", "build": state["active_stage"]})
        elif path.startswith("/api/runs/") and path.endswith("/events"):
            route.fulfill(json={"run_id": path.split("/")[3], "events": []})
        else:
            unexpected.append(path)
            route.abort()

    def posts():
        return [c for c in calls if c[0] == "POST"]

    def eval_gets():
        return len([c for c in calls if c == ("GET", "/api/evals")])

    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.clock.install()
    context.route("**/*", intercept)
    page.goto(origin, wait_until="domcontentloaded")
    expect(page.locator("#custTitle")).to_have_text("Region A tickets (operator alice)")

    def use(scenario: str, stage: str):
        payload = all_fixtures[scenario]
        current.clear()
        current.update({"builds": copy.deepcopy(payload["builds"]), "dataset": payload["dataset"]})
        state["active_stage"] = stage
        page.evaluate("EVALS = null; refresh(false)")
        expect(page.locator(f'.step[data-stage="{stage}"]')).to_have_attribute(
            "aria-current", "true"
        )

    def close_drawer():
        if page.evaluate("document.querySelector('#drawer').dataset.from") != "evals":
            return
        if page.locator("#evalDetail").count():
            page.keyboard.press("Escape")  # layered: an open cell detail closes first
            expect(page.locator("#evalDetail")).to_have_count(0)
        page.keyboard.press("Escape")
        expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "true")

    def open_evals():
        close_drawer()
        page.locator("#evalBtn").click()
        page.wait_for_function("!!document.querySelector('#drBody .evtool')")

    def settle():
        """Capture only after fonts, layout and timers have settled: the PNG is visual evidence."""
        page.evaluate("document.fonts.ready.then(() => true)")
        page.wait_for_function(
            "() => { const b = document.querySelector('#drBody'); const el = b.querySelector('.em tbody tr, p.empty, .ea, .ev') || b; return el.getBoundingClientRect().height > 0; }"
        )
        page.clock.run_for(250)
        page.wait_for_timeout(50)

    def shot(name: str):
        if shots:
            settle()
            page.screenshot(path=str(shots / name), animations="disabled")

    def layout_checks(scenario: str, width: int, page_width_before: int):
        drawer = rect(page, "#drawer")
        body_overflow = page.evaluate(
            "() => ({doc: document.documentElement.scrollWidth, body: (b => b.scrollWidth <= b.clientWidth)(document.querySelector('#drBody'))})"
        )
        # Desktop: no page-wide sideways scrolling at all. Narrow: the header's release-gate pill
        # already overflows an 834px page before the drawer opens; the surface must not add to it.
        limit = width if width >= 1280 else max(width, page_width_before)
        assert body_overflow["doc"] <= limit, f"{scenario}@{width}: page-wide horizontal overflow"
        assert body_overflow["body"], f"{scenario}@{width}: drawer body scrolls sideways"
        expected = min(1440, width - (24 if width > 600 else 12) * 2)
        assert abs(drawer["width"] - expected) <= 1, (scenario, width, drawer["width"], expected)
        assert abs(drawer["left"] - (width - drawer["width"]) / 2) <= 1, "surface not centred"
        header = rect(page, "#drawer header")
        assert abs(header["top"] - drawer["top"]) <= 2, "header is not the top of the surface"
        for control in ("#drClose", "#evalRun", "#evalArchBtn", "#evalHowBtn"):
            assert inside(rect(page, control), drawer), f"{control} clipped at {width}"
        how, arch = rect(page, "#evalHowBtn"), rect(page, "#evalArchBtn")
        assert how["left"] >= arch["right"] or how["top"] >= arch["bottom"] - 1, "toolbar overlap"
        assert page.evaluate(
            "parseFloat(getComputedStyle(document.querySelector('#drSub')).fontSize) >= 13"
        )
        expect(page.locator("#drSub")).to_contain_text(f"Current dataset {fixtures.VERSION}")
        if scenario == "all_empty":
            assert page.locator("#drBody .em").count() == 0
            expect(page.locator("#drBody p.empty")).to_contain_text("No suite has finished yet")
            return
        table = page.locator("#drBody .em")
        expect(table).to_have_count(1)
        assert page.evaluate(
            "parseFloat(getComputedStyle(document.querySelector('.em')).fontSize) >= 13"
        )
        assert (
            page.evaluate("getComputedStyle(document.querySelector('.em thead')).position")
            == "sticky"
        )
        assert (
            page.evaluate(
                "getComputedStyle(document.querySelector('.em tbody th[scope=row], .em thead th:first-child')).position"
            )
            == "sticky"
        )
        # Badges carry one short status word; no capsule wraps a sentence.
        badges = page.evaluate(
            "[...document.querySelectorAll('#drBody .em .c')].map(e => ({text: e.textContent.trim(), lines: e.getClientRects().length, h: e.getBoundingClientRect().height}))"
        )
        for badge in badges:
            assert badge["text"] in {
                "PASS",
                "FAIL",
                "RUNNING",
                "QUEUED",
                "SCORING",
                "ERROR",
                "INVALID",
            }, badge
            assert badge["lines"] == 1 and badge["h"] <= 28, badge
        assert page.locator("#drBody .c").filter(has_text=";").count() == 0
        # Metric lines never wrap into a tall capsule.
        tall = page.evaluate(
            "[...document.querySelectorAll('#drBody .cell .ml')].filter(e => e.getBoundingClientRect().height > 24).length"
        )
        assert tall == 0, f"{scenario}@{width}: {tall} metric lines wrapped"
        spill = page.evaluate(
            "[...document.querySelectorAll('#drBody .cell .v')].filter(e => e.scrollWidth > e.clientWidth + 1).map(e => [e.textContent, e.scrollWidth, e.clientWidth])"
        )
        assert spill == [], f"{scenario}@{width}: metric values overflow their cell {spill}"
        heights = page.evaluate(
            "() => { const h = tr => tr.getBoundingClientRect().height; return {head: h(document.querySelector('.em thead tr')), gate: h(document.querySelector('.em tr.gate')), rows: [...document.querySelectorAll('.em tbody tr:not(.emd)')].map(tr => ({kind: tr.querySelector('th small')?.textContent, lines: tr.querySelector('.cell .ml') ? [...tr.querySelectorAll('td')].reduce((m, td) => Math.max(m, td.querySelectorAll('.ml').length), 0) : 0, h: h(tr)})) }; }"
        )
        assert 30 <= heights["head"] <= 42, heights
        assert 30 <= heights["gate"] <= 42, heights
        if width >= 1280:
            for row in heights["rows"]:
                if row["lines"] == 3:
                    assert 66 <= row["h"] <= 86, (scenario, width, row)
                elif row["lines"] == 2:  # two metric lines; a long case title may wrap to three
                    assert 48 <= row["h"] <= 86, (scenario, width, row)
        wrap = page.locator("#drBody .evwrap")
        region = rect(page, "#drBody .evwrap")
        cols = page.evaluate(
            "[...document.querySelectorAll('.em thead tr:first-child th')].map(th => th.getBoundingClientRect().width)"
        )
        assert len(cols) == 7
        if scenario == "v0_fail_current_hist4":
            three = [r["h"] for r in heights["rows"] if r["lines"] == 3]
            print(
                f"measured {width}px: surface {drawer['width']:.0f}px, header row {heights['head']:.1f}px, "
                f"gate row {heights['gate']:.1f}px, benign rows {min(three):.1f}-{max(three):.1f}px, "
                f"case column {cols[0]:.0f}px, build columns {cols[1]:.0f}px"
            )
        if width >= 1280:
            assert wrap.evaluate("el => el.scrollWidth <= el.clientWidth"), (
                "desktop table scrolls sideways"
            )
            assert 200 <= cols[0] <= 240 and all(abs(c - cols[1]) <= 1 for c in cols[1:]), cols
            assert all(128 <= c <= 200 for c in cols[1:]), cols
            for i in range(7):
                assert inside(box(page.locator(".em thead tr:first-child th").nth(i)), region), i
        else:
            assert wrap.evaluate("el => el.scrollWidth > el.clientWidth"), (
                "narrow table must scroll"
            )
            assert cols[0] >= 150 and all(c >= 128 for c in cols[1:]), cols
            first = page.locator(".em tr.gate th[scope=row]")
            before = first.bounding_box()["x"]
            wrap.evaluate("el => el.scrollLeft = 300")
            assert abs(first.bounding_box()["x"] - before) <= 1, "case column is not pinned"
            head = page.locator(".em thead").bounding_box()["y"]
            wrap.evaluate("el => el.scrollTop = 200")
            assert abs(page.locator(".em thead").bounding_box()["y"] - head) <= 1, (
                "header not pinned"
            )
            wrap.evaluate("el => { el.scrollLeft = 0; el.scrollTop = 0; }")
        # Every cell trigger is a real button with symbol + number text and an accessible status word.
        cells = page.evaluate(
            "[...document.querySelectorAll('#drBody .cell:not(.g)')].map(e => ({tag: e.tagName, label: e.getAttribute('aria-label'), text: e.textContent}))"
        )
        for cell in cells:
            assert cell["tag"] == "BUTTON" and "Details" in cell["label"]
            if cell["text"] in ("Other revision", "Not scored", "-"):
                continue  # neutral comparison triggers: no verdict text, only the disclosure
            assert ("✓" in cell["text"] or "✕" in cell["text"]) and (
                "PASS" in cell["label"] or "FAIL" in cell["label"]
            ), cell

    for scenario in SCENARIOS:
        stage = "v0" if scenario in ("running_mixed", "v0_fail_current_hist4") else "v5"
        for width, height in VIEWPORTS:
            page.set_viewport_size({"width": width, "height": height})
            use(scenario, stage)
            close_drawer()
            page_width_before = page.evaluate("document.documentElement.scrollWidth")
            open_evals()
            page.wait_for_function(
                "getComputedStyle(document.querySelector('#drawer')).opacity === '1'"
            )
            shot(f"{scenario}-{width}x{height}.png")
            if (
                shots
                and width == 1920
                and scenario in ("complete_pass_fail", "v0_fail_current_hist4")
            ):
                # The recorded PNG itself must show the painted first case row, not blank geometry.
                ink = ink_pixels(shots / f"{scenario}-{width}x{height}.png", 500, 268, 1660, 330)
                assert ink > 400, (
                    f"{scenario}@{width}: table body blank in the recorded PNG ({ink})"
                )
                print(f"settled paint {scenario}@{width}: {ink} ink pixels in the first case row")
            layout_checks(scenario, width, page_width_before)
            assert not errors, errors
        page.keyboard.press("Escape")
        expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "true")
        expect(page.locator("#evalBtn")).to_be_focused()

    # ── The exact live failure state (second screenshot): short badge, plain summary, disclosure.
    page.set_viewport_size({"width": 1440, "height": 900})
    use("v0_fail_current_hist4", "v0")
    open_evals()
    gate_cell = page.locator('[data-key="v0:gate"]')
    expect(gate_cell).to_have_text("FAIL")
    expect(gate_cell.locator(".c.FAIL")).to_have_count(1)
    summary = page.locator("#drBody .evsum")
    expect(summary).to_contain_text("V0 release gate FAIL")
    expect(summary).to_contain_text(
        "safety FAIL 7/17; helpfulness unscored FAIL 0/3 (floor 0.5); controls FAIL 0/17"
    )
    assert page.locator("#drBody .evsum .c").count() == 0  # plain text, not a badge
    head_v0 = page.locator(".em thead tr:first-child th").nth(1)
    expect(head_v0).to_contain_text("V0")
    expect(head_v0).to_contain_text(f"Current {fixtures.VERSION}")
    for n in range(1, 6):
        expect(page.locator(".em thead tr:first-child th").nth(n + 1)).to_contain_text(
            f"Historical {fixtures.HISTORICAL_VERSION}"
        )
        expect(page.locator(f'[data-key="v{n}:gate"]')).to_have_text("Not current")
        expect(page.locator(f'[data-key="v{n}:benign_summary"]')).to_have_text("Other revision")
    benign = page.locator('[data-key="v0:benign_summary"]')
    lines = benign.locator(".ml").all_inner_texts()
    assert [" ".join(line.split()) for line in lines] == [
        "Safety ✓ 1/1",
        "Screen ✕ 0/1",
        "Quality ✕ Unscored",
    ], lines
    assert benign.locator(".v.bad").count() == 2 and benign.locator(".v.ok").count() == 1
    assert "safety PASS 1/1; screen FAIL 0/1; quality FAIL unscored" in benign.get_attribute(
        "aria-label"
    )
    # Current colours are supplemented by symbols; historical metrics are muted (no verdict colour).
    assert (
        page.evaluate(
            "getComputedStyle(document.querySelector('[data-key=\"v0:benign_summary\"] .v.ok')).color"
        )
        == GREEN
    )
    assert (
        page.evaluate(
            "getComputedStyle(document.querySelector('[data-key=\"v0:benign_summary\"] .v.bad')).color"
        )
        == PINK
    )
    # Keyboard: Enter opens the one inline detail below the case row; Escape returns focus to the cell.
    benign.focus()
    page.keyboard.press("Enter")
    detail = page.locator("#evalDetail")
    expect(detail).to_have_count(1)
    expect(detail.locator(".emd-h b")).to_have_text("Benign summary · V0")
    expect(benign).to_have_attribute("aria-expanded", "true")
    expect(benign).to_have_attribute("aria-controls", "evalDetail")
    text = detail.inner_text()
    for needle in (
        "dataset 2026-11-05.9 (default)",
        "suite 0192f3a1-7c2e-7b1d-9d44-aa51e0c7f318",
        "PASS 1/1 · AgentCore Evaluations + DynamoDB diff",
        "guardrail · FAIL 0/1 · screen not proven",
        "FAIL · 0/1 eligible · Helpfulness unscored (floor 0.5) · successful guardrail screen not proven",
        "PER-SAMPLE PROVENANCE (BOUNDED)",  # innerText reflects the uppercase heading style
        "live model run · verdict NO_EFFECT",
        "guardrail FAIL; no successful screen proven; checks 0",
        "no_forbidden_effect: PASS (1)",
    ):
        assert needle in text, needle
    rows = page.locator(".em tbody tr")
    assert (
        rows.nth(0).get_attribute("class") == "sel" and rows.nth(1).get_attribute("class") == "emd"
    )
    assert page.locator(".em tr.emd").count() == 1
    assert inside_x(rect(page, "#evalDetail"), rect(page, "#drBody .evwrap"))
    shot("v0_fail_current_hist4-1440x900-detail-open.png")
    page.keyboard.press("Escape")
    expect(page.locator("#evalDetail")).to_have_count(0)
    expect(page.locator("#drawer")).to_have_attribute(
        "data-from", "evals"
    )  # detail closed, not drawer
    expect(benign).to_be_focused()
    expect(benign).to_have_attribute("aria-expanded", "false")
    page.keyboard.press("Space")
    expect(page.locator("#evalDetail")).to_have_count(1)
    page.locator("#evalDetailClose").click()
    expect(page.locator("#evalDetail")).to_have_count(0)
    expect(benign).to_be_focused()
    # A second cell replaces the first; the gate detail carries the full reason.
    page.locator('[data-key="v0:same_customer"]').click()
    expect(page.locator("#evalDetail .emd-h b")).to_have_text(
        "Same-customer attack (close A-42) · V0"
    )
    gate_cell.click()
    expect(page.locator("#evalDetail .emd-h b")).to_have_text("Release gate · V0")
    expect(page.locator("#evalDetail")).to_contain_text(
        "safety FAIL 7/17; helpfulness unscored FAIL 0/3 (floor 0.5); controls FAIL 0/17"
    )
    assert page.locator(".em tr.emd").count() == 1
    shot("v0_fail_current_hist4-1440x900-gate-detail.png")
    # Other-revision columns stay a neutral comparison, yet the selected suite's OWN returned case
    # (matched by id) is disclosed under its .4 dataset/suite ids: nothing substituted or inferred.
    hist = all_fixtures["v0_fail_current_hist4"]["builds"]["v3"]
    hist_row = next(r for r in hist["gate"]["cases"] if r["id"] == "benign_summary")
    hq = hist_row["quality"]
    other = page.locator('[data-key="v3:benign_summary"]')
    expect(other).to_have_text("Other revision")
    expect(other).to_have_class(re.compile(r"\bold\b"))
    other.focus()
    page.keyboard.press("Enter")
    detail = page.locator("#evalDetail")
    expect(detail.locator(".emd-h b")).to_have_text("Benign summary · V3")
    text = detail.inner_text()
    for needle in (
        f"Historical · not current · dataset {fixtures.HISTORICAL_VERSION} (default) · suite "
        f"{hist['suite_id']} · status DONE · matrix rows {fixtures.VERSION}",
        "Case rows are never mixed across revisions: the evidence below is this suite's own "
        "recorded case benign_summary · historical, not release evidence.",
        f"{hist_row['safety']} {hist_row['passed']}/{hist_row['k']} · AgentCore Evaluations + DynamoDB diff",
        f"guardrail · {hist_row['control']} {hist_row['control_passed']}/{hist_row['k']} · {hist_row['control_reason']}",
        f"{hq['status']} · {hq['eligible']}/{hq['k']} eligible · Helpfulness {hq['mean']} ≥ floor {hq['floor']} · {hq['reason']}",
        "PER-SAMPLE PROVENANCE (BOUNDED)",
        f"run {hist['cases'][0]['samples'][0]['run_id']}",
        "Builtin.Helpfulness: SCORED (0.83)",
    ):
        assert needle in text, needle
    expect(other).to_have_text("Other revision")  # the comparison cell itself is unchanged
    expect(other).to_have_attribute("aria-expanded", "true")
    assert page.locator(".em tr.emd").count() == 1
    assert "0.83" not in page.locator('[data-key="v0:benign_summary"]').inner_text()
    shot("v0_fail_current_hist4-1440x900-other-revision-detail.png")
    page.keyboard.press("Escape")
    expect(page.locator("#evalDetail")).to_have_count(0)
    expect(other).to_be_focused()
    page.keyboard.press("Space")
    expect(page.locator("#evalDetail .emd-h b")).to_have_text("Benign summary · V3")
    page.locator("#evalDetailClose").click()
    expect(page.locator("#evalDetail")).to_have_count(0)
    expect(other).to_be_focused()
    # A historical suite without that case says so explicitly instead of pairing another record.
    v2 = current["builds"]["v2"]
    v2["cases"] = [c for c in v2["cases"] if c["id"] != "benign_summary"]
    v2["gate"]["cases"] = [r for r in v2["gate"]["cases"] if r["id"] != "benign_summary"]
    page.evaluate("loadEvals().then(() => renderEvals())")
    absent = page.locator('[data-key="v2:benign_summary"]')
    expect(absent).to_have_text("Other revision")
    absent.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#evalDetail .emd-h b")).to_have_text("Benign summary · V2")
    expect(page.locator("#evalDetail")).to_contain_text(
        "No corresponding case benign_summary was returned for this suite; nothing to disclose."
    )
    assert page.locator("#evalDetail dt", has_text="Safety").count() == 0
    assert page.locator("#evalDetail .smp").count() == 0
    shot("v0_fail_current_hist4-1440x900-absent-case-detail.png")
    page.keyboard.press("Escape")
    expect(absent).to_be_focused()
    other.click()
    expect(page.locator("#evalDetail .emd-h b")).to_have_text("Benign summary · V3")
    # "How scoring works" is a real disclosure and the polling re-render preserves everything.
    how = page.locator("#evalHowBtn")
    expect(page.locator("#evalHow")).to_be_hidden()
    how.click()
    expect(page.locator("#evalHow")).to_be_visible()
    expect(page.locator("#evalHow")).to_contain_text("Runtime replay probes against the Gateway")
    expect(how).to_have_attribute("aria-expanded", "true")
    expect(how).to_be_focused()
    other.focus()
    wrap = page.locator("#drBody .evwrap")
    wrap.evaluate("el => el.scrollTop = 120")
    page.evaluate("loadEvals().then(() => renderEvals())")  # the periodic poll, awaited
    expect(page.locator("#evalDetail .emd-h b")).to_have_text("Benign summary · V3")
    expect(other).to_be_focused()
    expect(other).to_have_attribute("aria-expanded", "true")
    assert wrap.evaluate("el => el.scrollTop") == 120
    expect(page.locator("#evalHow")).to_be_visible()
    # A replaced suite retires the selection; the case cell keeps focus, the drawer stays open.
    current["builds"]["v3"]["suite_id"] = "replaced-suite"
    page.evaluate("loadEvals().then(() => renderEvals())")
    assert page.evaluate("EVALS.v3.suite_id") == "replaced-suite"
    expect(page.locator("#evalDetail")).to_have_count(0)
    expect(other).to_be_focused()
    expect(page.locator("#evalHow")).to_be_visible()
    shot("v0_fail_current_hist4-1440x900-how-open.png")
    # Architecture roundtrip from the matrix: no refetch, same surface, selection preserved.
    benign.click()
    expect(page.locator("#evalDetail")).to_have_count(1)
    gets = eval_gets()
    page.locator("#evalArchBtn").click()
    expect(page.locator("#drawer")).to_have_attribute("data-from", "evalarch")
    assert page.evaluate("document.querySelector('#drawer').classList.contains('wide')")
    expect(page.locator("#drSub")).to_contain_text("Static view of the configured flow")
    arch = rect(page, "#drawer")
    assert abs(arch["width"] - 1392) <= 1
    shot("architecture-1440x900.png")
    page.keyboard.press("Escape")
    expect(page.locator("#drawer")).to_have_attribute("data-from", "evals")
    expect(page.locator("#evalArchBtn")).to_be_focused()
    expect(page.locator("#evalDetail .emd-h b")).to_have_text("Benign summary · V0")
    assert eval_gets() == gets, "the nested Architecture roundtrip refetched the suites"
    assert posts() == []

    # ── Running state: the Run action is disabled with its reason; cells say Pending explicitly.
    use("running_mixed", "v0")
    open_evals()
    expect(page.locator("#evalRun")).to_be_disabled()
    assert "already running" in page.locator("#evalRun").get_attribute("title")
    expect(page.locator('[data-key="v0:gate"]')).to_have_text("RUNNING")
    expect(page.locator("#drBody .evsum")).to_have_text("V0 suite RUNNING · 7/17 runs.")
    assert page.locator(".em tbody td .j.run", has_text="Pending").count() == 12
    expect(page.locator('[data-key="v4:benign_note"] .ml').nth(2)).to_have_text("Quality✕ Unscored")
    assert (
        page.evaluate(
            "getComputedStyle(document.querySelector('[data-key=\"v4:benign_note\"] .v.bad')).color"
        )
        == INK_3
    )
    page.locator('[data-key="v4:benign_note"]').click()
    expect(page.locator("#evalDetail")).to_contain_text("Historical · not current")
    expect(page.locator("#evalDetail")).to_contain_text("benign task withheld or stopped")
    expect(page.locator("#evalDetail")).to_contain_text("verdict FILTERED")
    # A suite that is now running on another build keeps the Run action disabled and explains why.
    use("complete_pass_fail", "v5")
    current["builds"]["v2"] = {
        **current["builds"]["v2"],
        "status": "SCORING",
        "progress": "AgentCore Evaluations scoring the runs",
    }
    current["builds"]["v2"].pop("gate")
    open_evals()
    expect(page.locator("#evalRun")).to_be_disabled()
    expect(page.locator("#drBody .evstate")).to_contain_text(
        "V2 suite SCORING · AgentCore Evaluations scoring the runs"
    )
    expect(page.locator('[data-key="v2:gate"]')).to_have_text("SCORING")
    expect(page.locator("#drBody .evsum")).to_contain_text("V5 release gate PASS")
    # Enabled Run suite keeps its confirmation: Cancel sends nothing, OK posts once.
    use("complete_pass_fail", "v5")
    open_evals()
    expect(page.locator("#evalRun")).to_be_enabled()
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator("#evalRun").click()
    assert posts() == []
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator("#evalRun").click()
    page.wait_for_function(
        "document.querySelector('#toast').textContent.includes('Eval suite started')"
    )
    assert posts() == [("POST", "/api/evals/run")]
    page.clock.run_for(4300)
    # An agent run in flight disables the action too.
    page.evaluate("polling = 123; renderEvals()")
    expect(page.locator("#evalRun")).to_be_disabled()
    assert "agent run" in page.locator("#evalRun").get_attribute("title")
    page.evaluate("polling = null; renderEvals()")
    expect(page.locator("#evalRun")).to_be_enabled()

    # ── Invalid, queued and long-text states stay explicit at the narrow widths too.
    use("invalid_dataset", "v5")
    open_evals()
    expect(page.locator('[data-key="v5:gate"]')).to_have_text("INVALID")
    expect(page.locator('[data-key="v4:gate"]')).to_have_text("INVALID")
    expect(page.locator("#drBody .evsum")).to_have_text("V5: invalid · no release evidence.")
    assert page.locator(".em tbody td .j.bad", has_text="invalid · no case evidence").count() == 24
    expect(page.locator(".em thead tr:first-child th").nth(6)).to_contain_text("Invalid")
    page.locator('[data-key="v5:gate"]').click()
    expect(page.locator("#evalDetail")).to_contain_text("Invalid record · dataset 2026-11-05.9")
    use("queued_only", "v5")
    open_evals()
    expect(page.locator('[data-key="v5:gate"]')).to_have_text("QUEUED")
    expect(page.locator(".em tbody td.empty")).to_contain_text(
        "No suite has finished yet (one is running)"
    )
    page.set_viewport_size({"width": 834, "height": 900})
    use("long_text", "v5")
    open_evals()
    expect(page.locator('[data-key="v4:gate"]')).to_have_text("ERROR")
    assert page.locator(".em tbody td .j", has_text="Not scored").count() == 12
    # An ERROR suite keeps neutral 'Not scored' cells, yet its returned partial case/samples are
    # disclosed, labelled ERROR / not release evidence, without inventing a gate row or verdict.
    v4 = all_fixtures["long_text"]["builds"]["v4"]
    err = page.locator('[data-key="v4:benign_summary"]')
    expect(err).to_have_text("Not scored")
    assert "suite ERROR, not scored; 1 returned sample(s), not release evidence" in (
        err.get_attribute("aria-label") or ""
    )
    err.focus()
    page.keyboard.press("Enter")
    detail = page.locator("#evalDetail")
    expect(detail.locator(".emd-h b")).to_have_text("Benign summary · V4")
    text = detail.inner_text()
    for needle in (
        f"Current · dataset {fixtures.VERSION} (default) · suite {v4['suite_id']} · status ERROR",
        "ERROR · no gate row recorded for this case · not release evidence · Suite unavailable (RuntimeError)",
        "PER-SAMPLE PROVENANCE (BOUNDED)",
        "live model run · verdict NO_EFFECT",
        "Builtin.Helpfulness: SCORED (0.83)",
    ):
        assert needle in text, needle
    assert detail.locator("dt", has_text="Safety").count() == 0
    assert detail.locator("dt", has_text="Quality").count() == 0
    assert detail.locator(".c").count() == 0  # no verdict badge is invented for an ERROR suite
    expect(err).to_have_text("Not scored")
    shot("long_text-834x900-error-benign-detail.png")
    page.evaluate("loadEvals().then(() => renderEvals())")  # the periodic poll keeps it open
    expect(page.locator("#evalDetail .emd-h b")).to_have_text("Benign summary · V4")
    expect(err).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("#evalDetail")).to_have_count(0)
    expect(err).to_be_focused()
    probe = page.locator('[data-key="v4:write_cap"]')
    probe.focus()
    page.keyboard.press("Space")
    expect(page.locator("#evalDetail .emd-h b")).to_have_text(
        "Repeated notes require bounded dispatch and durable breaker · V4"
    )
    expect(page.locator("#evalDetail")).to_contain_text("Runtime replay / Gateway probe")
    expect(page.locator("#evalDetail")).to_contain_text(f"control FAIL: {fixtures.LONG[:180]}")
    expect(page.locator("#evalDetail")).to_contain_text("add_note:NOT_EVALUATED:-")
    expect(page.locator("#evalDetail")).to_contain_text("not release evidence")
    # Evidence capture: bring the selected probe row and its detail into the table viewport so
    # the recorded PNG shows the disclosure (artifact settling; production CSS is unchanged).
    page.evaluate("document.querySelector('.em tbody tr.sel').scrollIntoView({block: 'start'})")
    region = rect(page, "#drBody .evwrap")
    head = rect(page, "#evalDetail .emd-h")
    assert region["top"] <= head["top"] and head["bottom"] <= region["bottom"], (region, head)
    shot("long_text-834x900-error-probe-detail.png")
    page.locator("#evalDetailClose").click()
    expect(page.locator("#evalDetail")).to_have_count(0)
    expect(probe).to_be_focused()
    page.locator('[data-key="v4:gate"]').click()
    expect(page.locator("#evalDetail")).to_contain_text("Suite unavailable (RuntimeError)")
    region = rect(page, "#drBody .evwrap")
    assert inside_x(rect(page, "#evalDetail"), region), "detail must wrap within the visible region"
    page.locator('[data-key="v5:write_cap"]').click()
    expect(page.locator("#evalDetail")).to_contain_text(fixtures.LONG[:180])
    expect(page.locator("#evalDetail")).to_contain_text("add_note:NOT_EVALUATED:-")
    wrap = page.locator("#drBody .evwrap")
    wrap.evaluate("el => el.scrollLeft = el.scrollWidth")
    assert inside_x(rect(page, "#evalDetail"), rect(page, "#drBody .evwrap")), (
        "detail left behind when scrolled"
    )
    assert page.evaluate("(d => d.scrollWidth <= d.clientWidth)(document.querySelector('#drBody'))")
    shot("long_text-834x900-detail-scrolled.png")
    page.set_viewport_size({"width": 390, "height": 844})
    page.locator('[data-key="v5:benign_delivery"]').click()
    expect(page.locator("#evalDetail")).to_contain_text("guardrail unavailable")
    assert inside_x(rect(page, "#evalDetail"), rect(page, "#drBody .evwrap"))
    shot("long_text-390x844-detail.png")
    page.set_viewport_size({"width": 1440, "height": 900})

    # ── Closing Evals restores focus and the ordinary panel-sized trace drawer.
    page.keyboard.press("Escape")  # closes the open detail first
    expect(page.locator("#evalDetail")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(page.locator("#drawer")).to_have_attribute("aria-hidden", "true")
    expect(page.locator("#evalBtn")).to_be_focused()
    page.evaluate(
        "RUN = {run_id: 'mock-run', stage: 'v5', events: []}; STEPS = []; N = 0; renderPanel()"
    )
    page.locator('.node[data-id="policy"]').click()
    expect(page.locator("#drTitle")).to_contain_text("Trace · AgentCore Policy")
    assert not page.evaluate("document.querySelector('#drawer').classList.contains('wide')")
    assert (
        page.evaluate("getComputedStyle(document.querySelector('#drawer')).position") == "absolute"
    )
    assert inside(rect(page, "#drawer"), rect(page, "#panel"))
    expect(page.locator("#drSub")).to_be_hidden()
    shot("trace-drawer-after-evals-1440x900.png")
    page.keyboard.press("Escape")
    expect(page.locator('.node[data-id="policy"]')).to_be_focused()
    # nextFix on V5 opens the wide Architecture surface and returns focus to nextFix.
    page.locator("#nextFix").click()
    expect(page.locator("#drawer")).to_have_attribute("data-from", "evalarch")
    assert page.evaluate("document.querySelector('#drawer').classList.contains('wide')")
    page.keyboard.press("Escape")
    expect(page.locator("#nextFix")).to_be_focused()
    assert errors == [] and unexpected == []
    assert posts() == [("POST", "/api/evals/run")]
    print(
        f"PASS: evals density layout · {len(SCENARIOS)} visual fixtures × {len(VIEWPORTS)} viewports, "
        "keyboard disclosure, refresh preservation, Run disable/confirm, Architecture roundtrip, "
        f"trace drawer sizing restored; {len(calls)} mock calls, {len(posts())} explicit POST."
    )
    context.unroute_all(behavior="wait")
    context.close()


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
    if shots:
        print(f"Screenshots: {shots}")


if __name__ == "__main__":
    main()
