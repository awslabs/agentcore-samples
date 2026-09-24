"""Phase 3 and 4: compare candidate judges with the human reference and check the gates.

Joins judge results to the human reference by case, then reports per-case differences and
aggregate measures for each candidate: weighted kappa, Spearman rank correlation, mean
absolute error, severe false passes (with a Wilson upper bound), repeatability, and results
by risk tier. When a candidate ran more than once, its per-case score is the median run.

Usage:
    python 06_compare_judges.py --split tuning
    python 06_compare_judges.py --split holdout
    python 06_compare_judges.py --demo --split all

Output:
    output/comparison.json
    output/comparison_report.html   - open in a browser; not shown to reviewers during review
"""

import argparse
import html
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median_low

from common import (
    ACCEPTANCE_GATES_FILE,
    COMPARISON_FILE,
    DEMO_DIR,
    DEMO_OUTPUT_DIR,
    HUMAN_REFERENCE_FILE,
    JUDGE_RESULTS_FILE,
    SAMPLE_DIR,
    read_json,
    write_json,
)
from metrics import (
    exact_agreement,
    mean_absolute_error,
    quadratic_weighted_kappa,
    spearman,
    wilson_upper_bound,
    within_one,
)


def candidate_scores(results: list[dict], split: str) -> dict[str, dict[str, list[int]]]:
    """Return {evaluator: {case_id: [score per repeat]}} for results of the requested split."""
    scores: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for record in results:
        if record["score"] is not None and (split == "all" or record["split"] in (split, "all")):
            scores[record["evaluator"]][record["case_id"]].append(record["score"])
    return scores


def summarize(cases: list[dict], runs: dict[str, list[int]], thresholds: dict) -> dict:
    scored = [case for case in cases if runs.get(case["case_id"])]
    human = [case["human_reference"] for case in scored]
    judge = [median_low(runs[case["case_id"]]) for case in scored]
    severe = thresholds["severe_false_pass"]
    failures = [case for case in scored if case["human_reference"] <= severe["human_reference_at_most"]]
    false_passes = [
        case["case_id"] for case in failures if median_low(runs[case["case_id"]]) >= severe["judge_score_at_least"]
    ]
    critical = thresholds["missed_critical_failure"]
    critical_cases = [case for case in scored if case["human_reference"] <= critical["human_reference_at_most"]]
    missed_critical = [
        case["case_id"]
        for case in critical_cases
        if median_low(runs[case["case_id"]]) >= critical["judge_score_at_least"]
    ]
    repeated = [runs[case["case_id"]] for case in scored if len(runs[case["case_id"]]) > 1]
    by_risk = {}
    for risk in ("high", "medium", "low"):
        group = [(h, j) for case, h, j in zip(scored, human, judge) if case["risk"] == risk]
        if group:
            by_risk[risk] = {
                "cases": len(group),
                "mean_absolute_error": mean_absolute_error(*map(list, zip(*group))),
                "exact_agreement": exact_agreement(*map(list, zip(*group))),
            }
    matrix = [[0] * 5 for _ in range(5)]
    for h, j in zip(human, judge):
        matrix[h - 1][j - 1] += 1
    return {
        "cases_scored": len(scored),
        "cases_missing": [case["case_id"] for case in cases if not runs.get(case["case_id"])],
        "weighted_kappa": quadratic_weighted_kappa(human, judge) if scored else None,
        "spearman": spearman(human, judge) if scored else None,
        "mean_absolute_error": mean_absolute_error(human, judge) if scored else None,
        "exact_agreement": exact_agreement(human, judge) if scored else None,
        "within_one": within_one(human, judge) if scored else None,
        "severe_failures": len(failures),
        "severe_false_passes": false_passes,
        "severe_false_pass_upper_bound": wilson_upper_bound(len(false_passes), len(failures)),
        "critical_failures": len(critical_cases),
        "missed_critical_failures": missed_critical,
        "repeat_runs": max((len(r) for r in repeated), default=1),
        "repeatability_exact": exact_agreement([min(r) for r in repeated], [max(r) for r in repeated])
        if repeated
        else None,
        "repeatability_within_one": within_one([min(r) for r in repeated], [max(r) for r in repeated])
        if repeated
        else None,
        "by_risk": by_risk,
        "human_vs_judge_matrix": matrix,
    }


def check_gates(summary: dict, gates: dict) -> dict[str, bool | None]:
    def at_least(value, limit):
        return None if value is None else value >= limit

    def at_most(value, limit):
        return None if value is None else value <= limit

    def at_most_count(misses, eligible, limit):
        # A count gate is not measured when the split has no case that could fail it
        return None if eligible == 0 else len(misses) <= limit

    return {
        "weighted_kappa": at_least(summary["weighted_kappa"], gates["min_weighted_kappa"]),
        "mean_absolute_error": at_most(summary["mean_absolute_error"], gates["max_mean_absolute_error"]),
        "severe_false_passes": at_most_count(
            summary["severe_false_passes"], summary["severe_failures"], gates["max_severe_false_passes"]
        ),
        "missed_critical_failures": at_most_count(
            summary["missed_critical_failures"], summary["critical_failures"], gates["max_missed_critical_failures"]
        ),
        "repeatability": at_least(summary["repeatability_within_one"], gates["min_repeatability_within_one"]),
    }


# ---------------------------------------------------------------------------
# HTML report, styled like the per-case comparison figure in the blog post
# ---------------------------------------------------------------------------

STYLE = """
  :root { --ink:#16191f; --muted:#545b64; --line:#aab7b8; --soft:#f2f3f3; --navy:#232f3e;
          --teal:#01a88d; --teal-dark:#067f68; --red:#d13212; --red-soft:#fdecea; --green:#1d8102; }
  * { box-sizing:border-box; }
  body { margin:0; padding:28px; color:var(--ink); font:15px/1.45 Helvetica, Arial, sans-serif; }
  h1 { margin:0; font-size:26px; } h2 { margin:34px 0 10px; font-size:19px; }
  .sub { margin:4px 0 22px; color:var(--muted); }
  table { border-collapse:collapse; width:100%; }
  th { background:var(--navy); color:#fff; padding:12px 10px; text-align:center; font-size:14px; }
  th:first-child, td:first-child { text-align:left; }
  td { border:1px solid var(--line); padding:10px; text-align:center; }
  tbody tr:nth-child(even) td { background:#f8f9f9; }
  td.case strong { display:block; } td.case span { color:var(--muted); font-size:13px; }
  td.ref { background:#e9ebed !important; font-weight:700; }
  td.bad { background:var(--red-soft) !important; color:var(--red); font-weight:700; }
  tr.groups td { border:0; background:none !important; padding:6px 0 0; font-weight:700; font-size:14px; }
  .groups div { padding-top:8px; }
  .g-sme { border-top:3px solid var(--teal); color:var(--teal-dark); }
  .g-judge { border-top:3px solid #879196; color:var(--muted); }
  .legend { margin-top:14px; color:var(--muted); font-size:14px; }
  .legend b { color:var(--red); }
  .badge { display:inline-block; padding:2px 8px; border-radius:2px; font-size:13px; font-weight:700; }
  .pass { background:#e9f6e6; color:var(--green); } .fail { background:var(--red-soft); color:var(--red); }
  .na { background:var(--soft); color:var(--muted); }
  .matrices { display:flex; gap:28px; flex-wrap:wrap; }
  .matrix td { width:40px; height:34px; padding:4px; }
  .matrix th { padding:6px; }
  details { margin-top:8px; } summary { cursor:pointer; color:var(--muted); }
  .explain { text-align:left; font-size:13px; color:var(--muted); }
"""


def fmt(value, digits=2) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def badge(result: bool | None) -> str:
    if result is None:
        return '<span class="badge na">not measured</span>'
    return '<span class="badge pass">pass</span>' if result else '<span class="badge fail">fail</span>'


def render_report(report: dict, cases: list[dict], scores: dict, explanations: dict) -> str:
    candidates = list(report["candidates"])
    reviewers = report["human_agreement"]["reviewers"]
    severe = report["thresholds"]["severe_false_pass"]
    e = html.escape

    header = "<th>Case</th>" + "".join(f"<th>{e(r.upper().replace('-', ' '))}</th>" for r in reviewers)
    header += "<th>Human<br>reference</th>"
    header += "".join(f"<th>Candidate<br>judge {e(c)}</th><th>Difference</th>" for c in candidates)

    rows = []
    for case in cases:
        case_cell = (
            f'<td class="case"><strong>{e(case["title"])}</strong>'
            f"<span>{e(case['risk'].title())} risk &middot; {e(case['case_id'])}</span></td>"
        )
        cells = [case_cell]
        cells += [f"<td>{d['rating']}</td>" for d in case["decisions"]]
        cells.append(f'<td class="ref">{case["human_reference"]}</td>')
        for candidate in candidates:
            runs = scores.get(candidate, {}).get(case["case_id"])
            if not runs:
                cells.append("<td>n/a</td><td>n/a</td>")
                continue
            score = median_low(runs)
            difference = abs(score - case["human_reference"])
            false_pass = (
                case["human_reference"] <= severe["human_reference_at_most"] and score >= severe["judge_score_at_least"]
            )
            runs_note = f' <span title="repeat runs">({"/".join(map(str, runs))})</span>' if len(runs) > 1 else ""
            explanation = explanations.get((candidate, case["case_id"]), "")
            cells.append(f'<td title="{e(explanation)}">{score}{runs_note}</td>')
            cells.append(f'<td class="{"bad" if difference >= 2 or false_pass else ""}">{difference}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")

    groups = (
        f'<tfoot><tr class="groups"><td></td>'
        f'<td colspan="{len(reviewers) + 1}"><div class="g-sme">Independent SME review</div></td>'
        f'<td colspan="{2 * len(candidates)}"><div class="g-judge">AgentCore batch evaluation</div></td></tr></tfoot>'
    )

    metric_rows = [
        ("Weighted kappa (quadratic)", "weighted_kappa", "weighted_kappa"),
        ("Spearman rank correlation", "spearman", None),
        ("Mean absolute error", "mean_absolute_error", "mean_absolute_error"),
        ("Exact agreement", "exact_agreement", None),
        ("Within one point", "within_one", None),
        ("Severe false passes", None, "severe_false_passes"),
        ("Severe false-pass rate, 95% upper bound", "severe_false_pass_upper_bound", None),
        ("Critical failures scored 3 or higher", None, "missed_critical_failures"),
        ("Repeatability, within one point", "repeatability_within_one", "repeatability"),
    ]
    metric_header = "<th>Measure</th>" + "".join(f"<th>Candidate judge {e(c)}</th>" for c in candidates)
    metric_body = []
    for label, key, gate in metric_rows:
        cells = [f"<td>{label}</td>"]
        for candidate in candidates:
            summary = report["candidates"][candidate]
            if gate == "severe_false_passes":
                value = f"{len(summary['severe_false_passes'])} of {summary['severe_failures']}"
            elif gate == "missed_critical_failures":
                value = f"{len(summary['missed_critical_failures'])} of {summary['critical_failures']}"
            else:
                value = fmt(summary[key])
            gate_html = f" {badge(summary['gates'][gate])}" if gate else ""
            cells.append(f"<td>{value}{gate_html}</td>")
        metric_body.append("<tr>" + "".join(cells) + "</tr>")
    verdicts = "".join(f"<td>{badge(report['candidates'][c]['meets_all_gates'])}</td>" for c in candidates)
    metric_body.append(f"<tr><td><strong>Meets every gate</strong></td>{verdicts}</tr>")

    matrices = []
    for candidate in candidates:
        matrix = report["candidates"][candidate]["human_vs_judge_matrix"]
        top = max(max(row) for row in matrix) or 1
        body = "".join(
            f"<tr><th>{h + 1}</th>"
            + "".join(
                f'<td style="background:rgba(35,47,62,{matrix[h][j] / top * 0.85:.2f});'
                f'color:{"#fff" if matrix[h][j] / top > 0.5 else "inherit"}">{matrix[h][j] or ""}</td>'
                for j in range(5)
            )
            + "</tr>"
            for h in range(5)
        )
        matrices.append(
            f'<div><strong>Candidate judge {e(candidate)}</strong><table class="matrix" style="width:auto;margin-top:6px">'
            f"<tr><th>Human \\ Judge</th>{''.join(f'<th>{j}</th>' for j in range(1, 6))}</tr>{body}</table></div>"
        )

    explanation_rows = "".join(
        f"<tr><td>{e(case_id)}</td><td>{e(candidate)}</td><td class='explain'>{e(text)}</td></tr>"
        for (candidate, case_id), text in sorted(explanations.items(), key=lambda item: (item[0][1], item[0][0]))
    )
    kappa = report["human_agreement"]["mean_pairwise_weighted_kappa"]

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Per-case alignment: human reference and candidate judges</title><style>{STYLE}</style></head>
<body>
<h1>Per-case alignment: human reference and candidate judges</h1>
<p class="sub">{len(cases)} {e(report["split"])} cases. Difference is the absolute distance between a judge score
and the human reference. Human-to-human weighted kappa: {fmt(kappa)}. Ground truth version
{e(report["ground_truth_version"])}. Generated {e(report["generated_at"][:19])} UTC.</p>
<table><thead><tr>{header}</tr></thead><tbody>{"".join(rows)}</tbody>{groups}</table>
<p class="legend"><b>Red</b>: difference of 2 or more, or a severe false pass (human reference
1-{severe["human_reference_at_most"]}, judge {severe["judge_score_at_least"]}-5). Hover a judge score to see its explanation.</p>

<h2>Aggregate measures and acceptance gates</h2>
<table><thead><tr>{metric_header}</tr></thead><tbody>{"".join(metric_body)}</tbody></table>
<p class="legend">A 95% upper bound stays wide on small calibration sets even with zero observed false passes.
Report it rather than the point estimate when you make a release decision.</p>

<h2>Human reference versus judge score</h2>
<div class="matrices">{"".join(matrices)}</div>

<h2>Judge explanations</h2>
<details><summary>Show every explanation</summary>
<table style="margin-top:10px"><thead><tr><th>Case</th><th>Candidate</th><th>Explanation</th></tr></thead>
<tbody>{explanation_rows}</tbody></table></details>
</body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--split", choices=["tuning", "holdout", "all"], default="tuning")
    parser.add_argument("--demo", action="store_true", help="Use the recorded demo run in data/demo/")
    args = parser.parse_args()

    reference_file = DEMO_OUTPUT_DIR / "human_reference.json" if args.demo else HUMAN_REFERENCE_FILE
    results_file = DEMO_DIR / "judge_results.json" if args.demo else JUDGE_RESULTS_FILE
    output_file = DEMO_OUTPUT_DIR / "comparison.json" if args.demo else COMPARISON_FILE
    for path, hint in ((reference_file, "04_merge_reviews.py"), (results_file, "05_run_batch_evaluation.py")):
        if not path.exists():
            raise SystemExit(
                f"{path.relative_to(SAMPLE_DIR)} not found. Run {hint}{' --demo' if args.demo else ''} first."
            )

    reference = read_json(reference_file)
    thresholds = read_json(ACCEPTANCE_GATES_FILE)
    cases = [c for c in reference["cases"] if args.split == "all" or c["split"] == args.split]
    results = read_json(results_file)
    scores = candidate_scores(results, args.split)
    if not scores:
        raise SystemExit(f"No judge results for the {args.split} split.")
    explanations = {
        (r["evaluator"], r["case_id"]): r.get("explanation") or ""
        for r in results
        if args.split == "all" or r["split"] in (args.split, "all")
    }

    candidates = {}
    for candidate in sorted(scores):
        summary = summarize(cases, scores[candidate], thresholds)
        summary["gates"] = check_gates(summary, thresholds["gates"])
        summary["meets_all_gates"] = all(value is not False for value in summary["gates"].values())
        candidates[candidate] = summary

    report = {
        "schema_version": "1.0",
        "split": args.split,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ground_truth_version": reference["ground_truth_version"],
        "human_agreement": reference["human_agreement"],
        "thresholds": thresholds,
        "candidates": candidates,
    }
    write_json(output_file, report)
    report_file = output_file.with_name("comparison_report.html")
    report_file.write_text(render_report(report, cases, scores, explanations))
    print(f"  Wrote {report_file.relative_to(SAMPLE_DIR)}")

    for candidate, summary in candidates.items():
        print(
            f"  {candidate}: kappa={fmt(summary['weighted_kappa'])} spearman={fmt(summary['spearman'])} "
            f"MAE={fmt(summary['mean_absolute_error'])} severe false passes="
            f"{len(summary['severe_false_passes'])}/{summary['severe_failures']} "
            f"missed critical={len(summary['missed_critical_failures'])}/{summary['critical_failures']} "
            f"repeatability={fmt(summary['repeatability_within_one'])} "
            f"gates={'PASS' if summary['meets_all_gates'] else 'FAIL'}"
        )


if __name__ == "__main__":
    main()
