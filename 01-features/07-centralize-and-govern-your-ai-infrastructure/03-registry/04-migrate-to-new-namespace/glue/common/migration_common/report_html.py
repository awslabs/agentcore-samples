"""Render a load attempt's JSON report as a page a person can read.

``summary.json`` holds everything about a run, but verifying a migration from it means knowing which
of its fields matter and what each one should say. This turns the same data into the review itself:
a list of named checks, each already answered against this run, then the numbers behind them.

Deliberately self-contained -- one file, inline styles, no scripts and no requests -- because it is
opened straight off a filesystem or downloaded out of S3, often from an account with no internet
route. Nothing here decides anything; it reads the report the load stage already wrote, so the page
and the JSON cannot disagree.
"""

from __future__ import annotations

import html
from typing import Any

# What a reviewer is being asked to conclude, and how each conclusion is reached.
OK = "ok"
ATTENTION = "attention"
INFO = "info"

_STYLE = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem 1.25rem 4rem; font: 15px/1.55 -apple-system, BlinkMacSystemFont,
  "Segoe UI", Roboto, Helvetica, Arial, sans-serif; color: #16191f; background: #f7f8fa; }
main { max-width: 46rem; margin: 0 auto; }
h1 { font-size: 1.3rem; margin: 0 0 1rem; font-weight: 600; }
h2 { font-size: 1rem; margin: 2rem 0 .75rem; font-weight: 700; color: #40485a; }
table { width: 100%; border-collapse: collapse; margin: 0 0 .5rem; font-size: .92rem; }
th, td { text-align: left; padding: .5rem .6rem; border-bottom: 1px solid #eceff3;
  vertical-align: top; }
th { font-weight: 600; color: #545b64; font-size: .82rem; text-transform: uppercase;
  letter-spacing: .04em; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
code, .mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .88em;
  word-break: break-all; }
.badge { display: inline-block; padding: .15rem .55rem; border-radius: 999px; font-size: .78rem;
  font-weight: 700; letter-spacing: .02em; }
.badge.succeeded { background: #dbf3e2; color: #0b6b2f; }
.badge.failed { background: #fbdad7; color: #a4160f; }
.badge.partial { background: #fbe8c6; color: #8a5a00; }
.badge.dry { background: #e4e8ef; color: #40485a; }
.badge.live { background: #d6e6fc; color: #12457f; }
.badge.mode { background: #e4e8ef; color: #40485a; }
.card { background: #fff; border: 1px solid #e4e7ec; border-radius: 14px; padding: 1.25rem 1.5rem;
  margin: 0 0 1.25rem; box-shadow: 0 1px 2px rgba(16, 24, 40, .04); }
.hero { display: flex; align-items: center; gap: 1rem; border-radius: 14px; padding: 1.25rem 1.5rem;
  margin: 0 0 1.25rem; }
.hero.ok { background: linear-gradient(135deg, #e7f6ec, #f3fbf5); border: 1px solid #bfe6ca; }
.hero.attention { background: linear-gradient(135deg, #fdecea, #fef5f4); border: 1px solid #f3c1bc; }
.hero .icon { flex: 0 0 auto; width: 2.5rem; height: 2.5rem; border-radius: 999px;
  display: flex; align-items: center; justify-content: center; font-size: 1.3rem; font-weight: 700; }
.hero.ok .icon { background: #0b6b2f; color: #fff; }
.hero.attention .icon { background: #a4160f; color: #fff; }
.hero .headline { font-size: 1.05rem; font-weight: 700; margin: 0 0 .15rem; }
.hero.ok .headline { color: #0b6b2f; }
.hero.attention .headline { color: #a4160f; }
.hero .sub { margin: 0; color: #40485a; font-size: .92rem; }
.check { display: flex; gap: .7rem; padding: .75rem 0; border-bottom: 1px solid #eef0f3; }
.check:last-child { border-bottom: none; padding-bottom: 0; }
.check .mark { flex: 0 0 1.5rem; height: 1.5rem; border-radius: 999px; display: flex;
  align-items: center; justify-content: center; font-size: .82rem; font-weight: 700; }
.check.ok .mark { background: #dbf3e2; color: #0b6b2f; }
.check.attention .mark { background: #fbdad7; color: #a4160f; }
.check.info .mark { background: #e4e8ef; color: #545b64; }
.check .what { font-weight: 600; }
.check .detail { color: #40485a; }
.check .todo { color: #545b64; font-size: .88rem; margin-top: .25rem; padding: .5rem .65rem;
  background: #f7f8fa; border-radius: 8px; }
.meta { display: grid; grid-template-columns: max-content 1fr; gap: .35rem 1.2rem;
  margin: 0; font-size: .9rem; }
.meta dt { color: #767d86; }
.meta dd { margin: 0; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(7rem, 1fr));
  gap: .6rem; margin: 0 0 1rem; }
.stat { background: #fff; border: 1px solid #e4e7ec; border-top: 3px solid #c7ccd6;
  border-radius: 10px; padding: .7rem .8rem; text-align: left; }
.stat .n { display: block; font-size: 1.7rem; font-weight: 800; line-height: 1.15;
  font-variant-numeric: tabular-nums; color: #16191f; }
.stat .l { display: block; font-size: .76rem; color: #767d86; text-transform: uppercase;
  letter-spacing: .04em; margin-top: .15rem; }
.stat.good { border-top-color: #0b6b2f; }
.stat.warn { border-top-color: #a4160f; }
.stat.warn .n { color: #a4160f; }
.bar { display: flex; height: .55rem; border-radius: 999px; overflow: hidden; background: #eceff3;
  margin: .75rem 0 1rem; }
.bar span { display: block; height: 100%; }
.bar .seg-created { background: #0b6b2f; }
.bar .seg-updated { background: #12457f; }
.bar .seg-unchanged { background: #c7ccd6; }
.bar .seg-dryrun { background: #8a5a00; }
.bar .seg-failed { background: #a4160f; }
.legend { display: flex; flex-wrap: wrap; gap: .9rem; font-size: .82rem; color: #545b64;
  margin: 0 0 1.25rem; }
.legend .dot { display: inline-block; width: .6rem; height: .6rem; border-radius: 999px;
  margin-right: .35rem; }
.reference { margin-top: 1.25rem; }
.reference summary, .artifacts summary { cursor: pointer; color: #545b64; font-size: .88rem;
  padding: .3rem 0; font-weight: 600; }
.reference .item { padding: .6rem .1rem .6rem 1.1rem; border-bottom: 1px solid #eceff3;
  color: #545b64; font-size: .9rem; }
.reference .item:last-child { border-bottom: none; }
.reference .item .what { font-weight: 600; color: #40485a; }
.lede { color: #545b64; font-size: .9rem; margin: .4rem 0 .8rem; }
.failure-reason { white-space: pre-wrap; overflow-wrap: anywhere; }
.artifacts { margin: 1.5rem 0; }
footer { margin-top: 2.5rem; color: #98a0aa; font-size: .82rem; text-align: center; }
@media (prefers-color-scheme: dark) {
  body { background: #0f1216; color: #e6e8eb; }
  h2 { color: #c3c9d2; }
  th, td, .check { border-color: #23272e; }
  th, .meta dt, .check .todo, footer, .reference summary, .artifacts summary,
    .reference .item, .lede { color: #a4aab3; }
  .card, .stat { background: #171b20; border-color: #262b32; }
  .stat .n { color: #e6e8eb; }
  .check .todo { background: #1d2127; }
  .bar { background: #262b32; }
  .hero.ok { background: linear-gradient(135deg, #0f2a18, #101a13); border-color: #1f4a2c; }
  .hero.attention { background: linear-gradient(135deg, #341311, #1c1210); border-color: #5c231d; }
  .hero .sub { color: #c3c9d2; }
}
"""


def render_report(report: dict[str, Any], extract_summary: dict[str, Any] | None = None) -> str:
    """Return a complete HTML page for one load attempt: the "is this done, or do I fix something?" decision."""
    checks = build_checks(report, extract_summary)
    status = str(report.get("status", "UNKNOWN"))
    dry_run = bool(report.get("dryRun"))
    attention = sum(1 for check in checks if check["status"] == ATTENTION)

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Agent Registry migration -- {_e(report.get('runId', ''))}</title>",
        f"<style>{_STYLE}</style></head><body><main>",
        "<h1>AWS Agent Registry migration report</h1>",
        _render_load_hero(report, status, dry_run, attention),
        f'<div class="card">{_render_meta(report, status, dry_run)}</div>',
        _render_load_stats(report),
        _render_load_bar(report),
        (
            f'<div class="card"><h2 style="margin-top:0">Migration review and required actions</h2>'
            f"{_render_checks(checks)}{_render_reference_notes()}</div>"
        ),
        "<h2>Per registry</h2>",
        _render_registries(report),
        _render_status_section(report),
        _render_failures(report),
        _render_artifacts(report),
        (
            "<footer>Generated by the AWS Agent Registry migration tool from "
            "<code>summary.json</code>. Every number here comes from that file.</footer>"
        ),
        "</main></body></html>",
    ]
    return "\n".join(part for part in parts if part) + "\n"


def _load_totals(report: dict[str, Any]) -> dict[str, int]:
    """Roll the per-registry outcome counts up to run totals.

    Shared by the hero and the stat tiles so the headline sentence and the numbers underneath it can
    never disagree about what this run did.
    """
    registries = [r for r in report.get("registries", []) if isinstance(r, dict)]
    return {
        key: sum(int(r.get(key, 0)) for r in registries)
        for key in ("extracted", "created", "updated", "existing", "dryRun", "failed")
    }


def _attention_sentence(attention: int) -> str:
    """ "1 check needs your attention", pluralised so the noun and the verb agree."""
    plural = attention != 1
    return f"{attention} check{'s' if plural else ''} need{'' if plural else 's'} your attention -- see below."


def _render_load_hero(report: dict[str, Any], status: str, dry_run: bool, attention: int) -> str:
    """The one thing to read first: what this run did to the target registry, and whether anything needs attention.

    States the outcome in records rather than opening with a count of internal checks: a reviewer's
    first question is "what is in the target registry now, and what is missing", not "how many checks scored".
    """
    totals = _load_totals(report)
    failed = totals["failed"]
    in_target = totals["created"] + totals["updated"] + totals["existing"]

    if dry_run:
        headline = "Dry run -- nothing was written to the target registry"
        would = totals["dryRun"]
        sentences = [
            f"A live run of this extract would create {would} record(s) in your target registries."
            if would
            else "A live run of this extract would create no records."
        ]
        if failed:
            sentences.append(f"{failed} record(s) would fail and would not be created.")
        if attention:
            sentences.append(_attention_sentence(attention))
        sentences.append("Re-run the same extract with --live to apply it.")
        variant = "attention" if attention else "ok"
        icon = "&#8226;"
    else:
        if in_target and failed:
            headline = f"{in_target} of {totals['extracted']} records are now in your target registries"
        elif in_target:
            headline = f"All {in_target} records are now in your target registries"
        elif failed:
            headline = "Nothing was written to the target registry -- every record failed"
        else:
            headline = "This attempt wrote no records to the target registry"
        sentences = []
        if failed:
            sentences.append(
                f"{failed} record(s) failed and were not created in the target registry. "
                "The rest are unaffected and were not rolled back."
            )
        if attention:
            sentences.append(_attention_sentence(attention))
        if not sentences:
            sentences.append("Every check passed, so there is nothing to follow up on.")
        variant = "attention" if attention else "ok"
        icon = "!" if attention else "&#10003;"

    sub = " ".join(sentences)
    return (
        f'<div class="hero {variant}"><div class="icon">{icon}</div>'
        f'<div><p class="headline">{_e(headline)}</p><p class="sub">{_e(sub)}</p></div></div>'
    )


def render_extract_report(extract_summary: dict[str, Any]) -> str:
    """Return a complete HTML page for one extract run: the "should I load this?" decision.

    Written the moment extraction finishes -- before any load attempt exists to attach a report to.
    Separate from :func:`render_report` on purpose: extraction and load are two different decisions
    made at two different times, often by whoever is reviewing before deciding to move forward, and a
    page that only exists once loading has already happened cannot inform that first decision.
    """
    checks = _extract_checks(extract_summary)
    status = str(extract_summary.get("status", "UNKNOWN"))
    ready = bool(extract_summary.get("readyForTransform"))
    attention = sum(1 for check in checks if check["status"] == ATTENTION)
    registries = [r for r in extract_summary.get("registries", []) if isinstance(r, dict)]
    totals = extract_summary.get("totals") if isinstance(extract_summary.get("totals"), dict) else {}
    staged = int(totals.get("records", sum(int(r.get("recordCount", 0)) for r in registries)))

    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>Agent Registry migration -- extract {_e(extract_summary.get('runId', ''))}</title>",
        f"<style>{_STYLE}</style></head><body><main>",
        "<h1>AWS Agent Registry extraction report</h1>",
        _render_extract_hero(ready, attention, staged),
        f'<div class="card">{_render_extract_meta(extract_summary, status, ready)}</div>',
        _render_extract_stats(extract_summary),
        (
            f'<div class="card"><h2 style="margin-top:0">What to check before you load</h2>'
            f"{_render_checks(checks)}</div>"
        ),
        "<h2>Per registry</h2>",
        _render_extract_registries(extract_summary),
        _render_artifacts(extract_summary),
        (
            "<footer>Generated by the AWS Agent Registry migration tool from "
            "<code>extract-summary.json</code>. Every number here comes from that file.</footer>"
        ),
        "</main></body></html>",
    ]
    return "\n".join(part for part in parts if part) + "\n"


def _render_extract_hero(ready: bool, attention: int, staged: int = 0) -> str:
    """What this extract produced, and whether it is safe to load.

    Leads with how many records were staged, because "is this the number I expected" is the decision
    being made here, and an extract that read nothing looks identical to a healthy one otherwise.
    """
    if not ready:
        return (
            '<div class="hero attention"><div class="icon">!</div><div>'
            '<p class="headline">Not ready to load -- extraction did not fully succeed</p>'
            '<p class="sub">Nothing has been written to the target registry. Loading this extract could migrate only '
            "part of your data, so review the per-registry errors below, fix the cause, and extract "
            "again.</p></div></div>"
        )
    if attention:
        return (
            f'<div class="hero attention"><div class="icon">!</div><div>'
            f'<p class="headline">Ready to load -- {staged} record(s) staged</p>'
            f'<p class="sub">Extraction only reads; nothing has been written to the target registry yet. '
            f"{_e(_attention_sentence(attention))}</p></div></div>"
        )
    return (
        f'<div class="hero ok"><div class="icon">&#10003;</div><div>'
        f'<p class="headline">Ready to load -- {staged} record(s) staged</p>'
        f'<p class="sub">Extraction only reads; nothing has been written to the target registry yet. Confirm the '
        f"count above matches what you expected, then run the load stage.</p></div></div>"
    )


def _render_extract_meta(extract_summary: dict[str, Any], status: str, ready: bool) -> str:
    badge = "succeeded" if ready else "failed"
    load = extract_summary.get("load") if isinstance(extract_summary.get("load"), dict) else {}
    mode = str(load.get("mode", "")).upper()
    window = extract_summary.get("incrementalWindow")
    window_row = ""
    if mode == "INCREMENTAL" and isinstance(window, dict):
        window_row = (
            "<dt>Changed after</dt>"
            f'<dd class="mono">{_e(window.get("changedAfter") or "varies by registry -- see below")}</dd>'
        )
    return (
        '<dl class="meta">'
        f'<dt>Status</dt><dd><span class="badge {badge}">{_e(status)}</span> '
        f'<span class="badge mode">{_e(mode or "FULL")}</span></dd>'
        f'<dt>Run</dt><dd class="mono">{_e(extract_summary.get("runId", ""))}</dd>'
        f"<dt>Started</dt><dd>{_e(extract_summary.get('startedAt', ''))}</dd>"
        f"<dt>Completed</dt><dd>{_e(extract_summary.get('completedAt', ''))}</dd>"
        f"{window_row}"
        "</dl>"
    )


def _render_extract_stats(extract_summary: dict[str, Any]) -> str:
    totals = extract_summary.get("totals") if isinstance(extract_summary.get("totals"), dict) else {}
    tiles = [
        ("Records read", int(totals.get("records", 0)), ""),
        ("Registries", int(totals.get("registries", 0)), ""),
        ("Warnings", int(totals.get("warnings", 0)), "warn" if totals.get("warnings") else ""),
        (
            "Failed registries",
            int(totals.get("failedRegistries", 0)),
            "warn" if totals.get("failedRegistries") else "",
        ),
    ]
    stats = "".join(
        f'<div class="stat {variant}"><span class="n">{_e(n)}</span><span class="l">{_e(label)}</span></div>'
        for label, n, variant in tiles
    )
    return f'<div class="stats">{stats}</div>'


def _render_extract_registries(extract_summary: dict[str, Any]) -> str:
    header = (
        "<tr><th>Registry pair</th><th>Source</th><th>Target</th><th>Status</th><th>Records</th><th>Warnings</th></tr>"
    )
    rows = []
    for registry in extract_summary.get("registries", []):
        if not isinstance(registry, dict):
            continue
        rows.append(
            "<tr>"
            f'<td class="mono">{_e(registry.get("mappingId", ""))}</td>'
            f'<td class="mono">{_e(_endpoint(registry.get("source")))}</td>'
            f'<td class="mono">{_e(_endpoint(registry.get("target")))}</td>'
            f"<td>{_e(registry.get('status', ''))}</td>"
            f'<td class="num">{_e(registry.get("recordCount", 0))}</td>'
            f'<td class="num">{_e(len(registry.get("warnings") or []))}</td>'
            "</tr>"
        )
    return f"<table>{header}{''.join(rows)}</table>"


def build_checks(
    report: dict[str, Any],
    extract_summary: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """The review, as a list of already-answered questions.

    Each entry is what to check, what this run says about it, and -- when it needs attention -- what
    to do. Kept as data so the page and the tests agree on what a reviewer is being told.

    ``extract_summary`` adds the questions only the read side can answer: whether reading the source
    was clean, and whether anything was flagged there. A load report alone cannot tell you that a
    registry was read with warnings, and a page that skips it would look complete while hiding it.
    """
    registries = [r for r in report.get("registries", []) if isinstance(r, dict)]
    approval = report.get("approval") if isinstance(report.get("approval"), dict) else {}
    dry_run = bool(report.get("dryRun"))
    checks: list[dict[str, str]] = []

    if isinstance(extract_summary, dict):
        checks.extend(_extract_checks(extract_summary))

    created = sum(int(r.get("created", 0)) for r in registries)
    updated = sum(int(r.get("updated", 0)) for r in registries)
    existing = sum(int(r.get("existing", 0)) for r in registries)
    would_write = sum(int(r.get("dryRun", 0)) for r in registries)
    failed = sum(int(r.get("failed", 0)) for r in registries)
    extracted = sum(int(r.get("extracted", 0)) for r in registries)
    accounted = created + updated + existing + would_write + failed
    outcomes = (
        f"{created} created, {updated} updated, {existing} already present, "
        f"{would_write} would be written, and {failed} failed"
        if dry_run
        else f"{created} created, {updated} updated, {existing} already present, and {failed} failed"
    )
    checks.append(
        _check(
            "Load attempted every extracted record",
            OK if accounted == extracted else ATTENTION,
            (
                f"All {extracted} extracted record(s) reached a final outcome: {outcomes}. "
                "No staged records were skipped."
                if accounted == extracted
                else f"Only {accounted} of {extracted} extracted record(s) reached a final outcome: {outcomes}."
            ),
            ""
            if accounted == extracted
            else "The load did not reach every staged record. Re-run the load for this run id; "
            "records that already arrived are recognised, not duplicated.",
        )
    )

    checks.append(
        _check(
            "Failed records",
            OK if not failed else ATTENTION,
            f"{failed} of {extracted} extracted record(s) failed during transformation or loading.",
            ""
            if not failed
            else "Review the failed-record section below for each record and its error reason. "
            "The full failure artifact includes the payload and traceback. Fix the cause and run "
            "again; records that succeeded are left alone.",
        )
    )

    if not dry_run and registries:
        checks.append(
            _check(
                "Verify each target contains the expected records",
                INFO,
                "The per-registry table summarizes this migration attempt; it does not "
                "independently count every record currently stored in Preview and the target registry.",
                "After resolving any failures, list each target and confirm it contains every "
                "expected Preview record. The target total can be higher if it already contains "
                "unrelated records: aws agent-registry-control list-registry-records "
                "--registry-id <new-registry-id>",
            )
        )

    if approval:
        not_applied = int(approval.get("statusesNotApplied", 0))
        needing = int(approval.get("recordsNeedingResubmission", 0))
        checks.append(
            _check(
                "Approval status carried across",
                OK if not (not_applied or needing) else ATTENTION,
                str(approval.get("note", "")),
                ""
                if not (not_applied or needing)
                else "A record left in DRAFT is not returned by data-plane search or the browsing "
                "APIs. See statusError per record in the record-comparison artifact, then finish "
                "those with submit-registry-record-for-approval.",
            )
        )

    if dry_run:
        checks.append(
            _check(
                "Nothing was written",
                INFO,
                "This was a dry run.",
                "When the numbers above look right, load exactly these records: "
                "agent-registry-migration run --live --resume",
            )
        )
    return checks


def _extract_checks(extract_summary: dict[str, Any]) -> list[dict[str, str]]:
    """The questions about reading the source, answered from the extract report."""
    registries = [r for r in extract_summary.get("registries", []) if isinstance(r, dict)]
    totals = extract_summary.get("totals") if isinstance(extract_summary.get("totals"), dict) else {}
    checks: list[dict[str, str]] = []

    registry_count = len(registries)
    registry_word = "registry" if registry_count == 1 else "registries"
    read = int(totals.get("records", sum(int(r.get("recordCount", 0)) for r in registries)))
    ready = bool(extract_summary.get("readyForTransform"))
    checks.append(
        _check(
            "Preview source records were extracted successfully",
            OK if ready else ATTENTION,
            (
                f"Extraction completed successfully: {read} record(s) were read from "
                f"{registry_count} configured Preview source {registry_word}."
                if ready
                else "Extraction did not complete successfully, so this report may cover only "
                "part of the configured Preview source data."
            ),
            ""
            if ready
            else "Read extract-summary.json for the error reported for each Preview source "
            "registry, fix the cause, and extract again before trusting this load.",
        )
    )

    types = {}
    for registry in registries:
        for name, count in (registry.get("recordTypeCounts") or {}).items():
            types[str(name)] = types.get(str(name), 0) + int(count)
    if types:
        checks.append(
            _check(
                "Record types were inferred from each record's descriptor shape",
                INFO,
                "Counted using Preview type names: "
                + ", ".join(f"{name}: {count}" for name, count in sorted(types.items())),
                "Preview records carry no recordType, so each one is inferred from its descriptor "
                "shape. Two of these names change in the target registry: A2A becomes AGENT, and AGENT_SKILLS "
                "becomes SKILL. MCP and CUSTOM keep their names. Confirm these totals are the "
                "split you expect before you rely on filtering by type in the target registry; if one looks wrong, "
                "compare that record in the record-comparison artifact.",
            )
        )

    warnings = int(totals.get("warnings", 0))
    if warnings:
        checks.append(
            _check(
                "Nothing was flagged while reading",
                ATTENTION,
                f"{warnings} warning(s) were raised during extraction.",
                "Each one names the record it concerns in extract-summary.json. They are not "
                "failures, but they are the places where the migration had to make a decision.",
            )
        )

    window = extract_summary.get("incrementalWindow")
    if isinstance(window, dict) and window:
        checks.append(
            _check(
                "The incremental window is the one you meant",
                INFO,
                f"Covered records changed at or after {window.get('changedAfter', 'an unset cutoff')}.",
                "Records outside this window are not in this run at all. A full load establishes the "
                "watermark; a catch-up moves from it.",
            )
        )
    return checks


def _check(what: str, status: str, detail: str, todo: str) -> dict[str, str]:
    return {"what": what, "status": status, "detail": detail, "todo": todo}


def _render_checks(checks: list[dict[str, str]]) -> str:
    marks = {OK: "&#10003;", ATTENTION: "!", INFO: "&#8226;"}
    rows = []
    for check in checks:
        todo = f'<div class="todo">{_e(check["todo"])}</div>' if check["todo"] else ""
        rows.append(
            f'<div class="check {check["status"]}">'
            f'<div class="mark">{marks.get(check["status"], "&#8226;")}</div>'
            f'<div><div class="what">{_e(check["what"])}</div>'
            f'<div class="detail">{_e(check["detail"])}</div>{todo}</div></div>'
        )
    return "".join(rows)


def _render_load_stats(report: dict[str, Any]) -> str:
    """The headline numbers as tiles: what happened, at a glance, before the per-check detail.

    Tile labels deliberately match the per-registry table headings. They described the same counts
    with different words ("Unchanged" here, "Already present" there), which reads as two different
    measurements rather than one.
    """
    totals = _load_totals(report)
    tiles = [
        ("Created", totals["created"], ""),
        ("Updated", totals["updated"], ""),
        ("Already present", totals["existing"], ""),
    ]
    if report.get("dryRun"):
        tiles.append(("Would write", totals["dryRun"], ""))
    tiles.append(("Failed", totals["failed"], "warn" if totals["failed"] else "good"))
    stats = "".join(
        f'<div class="stat {variant}"><span class="n">{_e(n)}</span><span class="l">{_e(label)}</span></div>'
        for label, n, variant in tiles
    )
    return f'<div class="stats">{stats}</div>'


def _render_reference_notes() -> str:
    """Background that is true of every migration, not a verdict on this one.

    These three used to sit in the checklist as INFO rows on every single run, unconditionally --
    which is the opposite of a checklist: nothing about them ever changes based on what happened, so
    they never helped decide the next step. Kept here, out of the way, for whoever wants the
    context without it competing with the things that actually vary run to run.
    """
    notes = [
        (
            "Descriptors changed shape",
            (
                "The target descriptor shape differs from preview by design. Spot-check a few records "
                "in the record-comparison artifact below -- each entry has the preview record, the "
                "transformed payload, and the resulting target record."
            ),
        ),
        (
            "Every record has a new recordId",
            (
                "Target records are created fresh. Anything referencing a preview recordId -- client "
                "configuration, infrastructure as code, links between records -- has to be "
                "repointed using the crosswalk below."
            ),
        ),
        (
            "The service namespace changes",
            (
                "bedrock-agentcore becomes agent-registry. Update IAM policies, endpoints, SDK "
                "clients, CLI commands and resource ARNs, then confirm your applications can read "
                "and write through the new namespace."
            ),
        ),
    ]
    items = "".join(
        f'<div class="item"><span class="what">{_e(what)}.</span> {_e(detail)}</div>' for what, detail in notes
    )
    return (
        '<details class="reference"><summary>Background that applies to every migration '
        f"(not specific to this run)</summary>{items}</details>"
    )


def _render_meta(report: dict[str, Any], status: str, dry_run: bool) -> str:
    badge = {"SUCCEEDED": "succeeded", "FAILED": "failed", "PARTIAL_SUCCESS": "partial"}.get(status, "partial")
    mode = '<span class="badge dry">DRY RUN</span>' if dry_run else '<span class="badge live">LIVE</span>'
    return (
        '<dl class="meta">'
        f'<dt>Status</dt><dd><span class="badge {badge}">{_e(status)}</span> {mode}</dd>'
        f'<dt>Run</dt><dd class="mono">{_e(report.get("runId", ""))}</dd>'
        f'<dt>Attempt</dt><dd class="mono">{_e(report.get("attemptId", ""))}</dd>'
        f"<dt>Started</dt><dd>{_e(report.get('startedAt', ''))}</dd>"
        f"<dt>Completed</dt><dd>{_e(report.get('completedAt', ''))}</dd>"
        f"<dt>Records processed</dt><dd>{_e(report.get('processedRecordCount', 0))}</dd>"
        "</dl>"
    )


def _render_load_bar(report: dict[str, Any]) -> str:
    """A single proportional bar showing where every record landed, at a glance.

    Pure CSS -- flex children sized by percentage -- so it stays a plain, self-contained page with
    no scripts or images. Omitted when there is nothing to show a proportion of.
    """
    registries = [r for r in report.get("registries", []) if isinstance(r, dict)]
    created = sum(int(r.get("created", 0)) for r in registries)
    updated = sum(int(r.get("updated", 0)) for r in registries)
    existing = sum(int(r.get("existing", 0)) for r in registries)
    dry = sum(int(r.get("dryRun", 0)) for r in registries)
    failed = sum(int(r.get("failed", 0)) for r in registries)
    total = created + updated + existing + dry + failed
    if total <= 0:
        return ""
    segments = [
        ("seg-created", created, f"{created} created"),
        ("seg-updated", updated, f"{updated} updated"),
        ("seg-unchanged", existing, f"{existing} unchanged"),
        ("seg-dryrun", dry, f"{dry} would write"),
        ("seg-failed", failed, f"{failed} failed"),
    ]
    bar = "".join(
        f'<span class="{css_class}" style="width:{100 * count / total:.2f}%" title="{_e(label)}"></span>'
        for css_class, count, label in segments
        if count
    )
    # Inline swatches (not the .seg-* classes above) so a legend dot's colour is correct without
    # depending on a second stylesheet lookup, and stays right in both the light and dark variants.
    swatch = {
        "seg-created": "#0b6b2f",
        "seg-updated": "#12457f",
        "seg-unchanged": "#c7ccd6",
        "seg-dryrun": "#8a5a00",
        "seg-failed": "#a4160f",
    }
    legend = "".join(
        f'<span><span class="dot" style="background:{swatch[css_class]}"></span>{_e(label)}</span>'
        for css_class, count, label in segments
        if count
    )
    return f'<div class="bar">{bar}</div><div class="legend">{legend}</div>'


def _endpoint(value: Any) -> str:
    """Render one side of a registry pair as "account / region / registryId".

    The summary stores each endpoint as an object. Interpolating that object straight into the page
    printed a Python dict, so the column a reader uses to confirm they migrated the right registry
    was the least readable thing on the page.
    """
    if isinstance(value, dict):
        ordered = (value.get("accountId"), value.get("region"), value.get("registryId"))
        return " / ".join(str(part) for part in ordered if part)
    return "" if value is None else str(value)


def _render_registries(report: dict[str, Any]) -> str:
    header = (
        "<tr><th>Registry pair</th><th>Preview source</th><th>target</th>"
        "<th>Extracted</th><th>Created</th><th>Updated</th><th>Already present</th>"
        "<th>Dry run</th><th>Failed</th></tr>"
    )
    rows = []
    for registry in report.get("registries", []):
        if not isinstance(registry, dict):
            continue
        rows.append(
            "<tr>"
            f'<td class="mono">{_e(registry.get("mappingId", ""))}</td>'
            f'<td class="mono">{_e(_endpoint(registry.get("source")))}</td>'
            f'<td class="mono">{_e(_endpoint(registry.get("target")))}</td>'
            f'<td class="num">{_e(registry.get("extracted", 0))}</td>'
            f'<td class="num">{_e(registry.get("created", 0))}</td>'
            f'<td class="num">{_e(registry.get("updated", 0))}</td>'
            f'<td class="num">{_e(registry.get("existing", 0))}</td>'
            f'<td class="num">{_e(registry.get("dryRun", 0))}</td>'
            f'<td class="num">{_e(registry.get("failed", 0))}</td>'
            "</tr>"
        )
    return f"<table>{header}{''.join(rows)}</table>"


def _render_status_section(report: dict[str, Any]) -> str:
    approval = report.get("approval")
    if not isinstance(approval, dict):
        return ""
    source = approval.get("sourceStatusCounts") or {}
    target = approval.get("targetStatusCounts") or {}
    statuses = sorted(set(source) | set(target))
    rows = "".join(
        "<tr>"
        f"<td>{_e(status)}</td>"
        f'<td class="num">{_e(source.get(status, 0))}</td>'
        f'<td class="num">{_e(target.get(status, 0))}</td>'
        "</tr>"
        for status in statuses
    )
    return (
        "<h2>Approval status</h2>"
        "<table><tr><th>Status</th><th>In preview</th><th>In target</th></tr>"
        f"{rows}</table>"
        f'<p class="lede">{_e(approval.get("note", ""))}</p>'
    )


def _render_failures(report: dict[str, Any]) -> str:
    failures = [
        registry
        for registry in report.get("registries", [])
        if isinstance(registry, dict) and int(registry.get("failed", 0))
    ]
    if not failures:
        return ""

    detail_rows: list[str] = []
    diagnostic_rows: list[str] = []
    omitted_notes: list[str] = []
    for registry in failures:
        mapping_id = registry.get("mappingId", "")
        failed = int(registry.get("failed", 0))
        details = registry.get("failureDetails")
        compact_details = details if isinstance(details, list) else []
        shown = 0
        for detail in compact_details:
            if not isinstance(detail, dict):
                continue
            shown += 1
            name = detail.get("name") or "Name unavailable"
            identifiers = []
            if detail.get("oldRecordId"):
                identifiers.append(f"Preview ID: {_e(detail['oldRecordId'])}")
            if detail.get("newRecordId"):
                identifiers.append(f"Target ID: {_e(detail['newRecordId'])}")
            record = _e(name)
            if identifiers:
                record += '<br><span class="mono">' + "<br>".join(identifiers) + "</span>"
            detail_rows.append(
                "<tr>"
                f'<td class="mono">{_e(mapping_id)}</td>'
                f"<td>{record}</td>"
                f"<td>{_e(detail.get('recordType') or 'Unknown')}</td>"
                f'<td class="failure-reason">{_e(detail.get("error") or "No error reason was recorded.")}</td>'
                "</tr>"
            )
        if not shown:
            detail_rows.append(
                "<tr>"
                f'<td class="mono">{_e(mapping_id)}</td>'
                '<td colspan="2">Record details unavailable in this summary</td>'
                '<td class="failure-reason">Open the full diagnostic artifact for the error '
                "reason. This report may have been generated by an older version of the tool.</td>"
                "</tr>"
            )
        elif shown < failed:
            omitted_notes.append(
                f"{mapping_id}: showing {shown} of {failed} failures inline; the full artifact "
                "contains every failed record."
            )
        diagnostic_rows.append(
            "<tr>"
            f'<td class="mono">{_e(mapping_id)}</td>'
            f'<td class="num">{_e(failed)}</td>'
            f'<td class="mono">{_locations(registry.get("failures"))}</td>'
            "</tr>"
        )

    omitted = "".join(f'<p class="lede">{_e(note)}</p>' for note in omitted_notes)
    return (
        "<h2>Failed records and error reasons</h2>"
        '<p class="lede">Each row identifies a failed record and the reason transformation or '
        "loading failed. Fix the reported cause, then rerun the same extract; successful records "
        "are recognized and are not duplicated.</p>"
        "<table><tr><th>Registry mapping</th><th>Record</th><th>Type</th>"
        f"<th>Error reason</th></tr>{''.join(detail_rows)}</table>"
        f"{omitted}"
        '<p class="lede">Full diagnostic artifacts include the submitted payload and traceback.</p>'
        "<table><tr><th>Registry mapping</th><th>Failed</th><th>Full diagnostics</th></tr>"
        f"{''.join(diagnostic_rows)}</table>"
    )


def _render_artifacts(report: dict[str, Any]) -> str:
    """Every file this run wrote, collapsed by default: navigation, not part of the decision."""
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        return ""
    rows = "".join(
        f'<tr><td class="mono">{_e(location)}</td><td>{_e(description)}</td></tr>'
        for location, description in artifacts.items()
    )
    table = f"<table><tr><th>Location</th><th>What it holds</th></tr>{rows}</table>"
    return f'<details class="artifacts"><summary>Where everything is</summary>{table}</details>'


def _e(value: Any) -> str:
    """Escape a value for HTML. Report content includes registry ids, names and error text."""
    return html.escape(str(value), quote=True)


def _locations(value: Any) -> str:
    """Render an artifact location, or a list of them, one per line.

    Artifacts that can span several parts (the failure rows, the record comparison) are recorded as
    a list of locations; a single location is still accepted so a report written by an older version
    renders unchanged.
    """
    if value in (None, "", []):
        return ""
    if isinstance(value, (list, tuple)):
        return "<br>".join(_e(item) for item in value)
    return _e(value)
