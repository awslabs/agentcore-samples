"""Re-score saved receipt traces without re-running the agent.

The traces from `run_deployed.py` are on disk, so new evaluators can be added and scored
against receipts that have already been processed. No Textract, no extraction model, no
waiting. Only the judge calls an evaluator makes itself.

What it adds over the run itself:

  Routing outcome, four labels, from the code-based evaluator. Receipts whose right
      answer depends on another receipt (label `cross_receipt`) are left out, because the
      validator sees one receipt at a time and cannot know
  Right reason, `Builtin.GoalSuccessRate` with assertions on the session through the
      validator: did the validator name the actual problem, not only route correctly. Each
      receipt's assertions are the problems on its face (label `reason_assertions`) plus one
      per field extraction accuracy found wrong in this run. Receipts with no assertion are not
      scored, because an assertion that never engages passes and means nothing
  Invented values, `Builtin.ToolParameterAccuracy` on the extractor's submit_expense
      call: did the model put a value into the record that its own input never contained.
      Read beside extraction accuracy, it splits the blame for a wrong field between the model
      and the OCR

ToolParameterAccuracy is sent the extractor's part of the trace only. On the whole session
the judge also sees the validator's and the note writer's inputs, which repeat the
extractor's own values, so an invented value looks sourced: on the whole session it passed
invented dates, and on the extractor's part alone it caught every one. For the same reason
it cannot run in an online config, which scores the whole session.

Usage:
    python score_saved.py --run out/deployed-<id>
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _note_writer_part(spans: list[dict]) -> set[str]:
    """Span ids of the note writer's run and the human_review call that carries its note.

    The validator acts through its decision tools, so both run inside the validator's own
    send_to_review call. Log records carry the span id of the span they belong to. The
    human_review call is the MCP instrumentation's `mcp tools/call human-review___human_review`
    span.
    """
    roots = {
        s["spanId"]
        for s in spans
        if s.get("spanId")
        and (
            (s.get("attributes") or {}).get("gen_ai.agent.name") == "reviewer-note"
            or str((s.get("attributes") or {}).get("gen_ai.tool.name", "")).endswith("human_review")
        )
        and s.get("name", "").startswith(("invoke_agent", "mcp tools/call"))
    }
    children: dict[str, list[str]] = {}
    for s in spans:
        if s.get("spanId") and s.get("parentSpanId") and "startTimeUnixNano" in s:
            children.setdefault(s["parentSpanId"], []).append(s["spanId"])
    part, stack = set(), list(roots)
    while stack:
        span_id = stack.pop()
        if span_id not in part:
            part.add(span_id)
            stack.extend(children.get(span_id, []))
    return part


def _through_agent(spans: list[dict], name: str) -> list[dict]:
    """The session up to the end of the named agent (extractor or validator), without the note writer.

    Keeps the root invocation span so the session still has its receipts.* attributes.
    """
    agent = next(
        s
        for s in spans
        if s.get("name", "").startswith("invoke_agent") and (s.get("attributes") or {}).get("gen_ai.agent.name") == name
    )
    cutoff = agent["endTimeUnixNano"]
    excluded = _note_writer_part(spans)
    return [
        s
        for s in spans
        if s.get("name") == "receipts.invocation"
        or ((s.get("startTimeUnixNano", s.get("timeUnixNano")) or 0) <= cutoff and s.get("spanId") not in excluded)
    ]


def through_validator(spans: list[dict]) -> list[dict]:
    """Leaves out the note writer, which repeats the validator's concern and could earn its credit."""
    return _through_agent(spans, "validator")


FIELD_NAMES = {"date": "transaction date"}


def reason_assertions(label: dict, row: dict) -> list[str]:
    """Problems the validator should name: those on the receipt, plus each field extracted wrong."""
    found = list(label.get("reason_assertions") or [])
    explanation = row.get("extraction_explanation") or ""
    if "Also wrong:" in explanation:
        for item in explanation.split("Also wrong:", 1)[1].split(".", 1)[0].split(";"):
            field = item.strip().split(" ", 1)[0]
            if field:
                found.append(
                    f"identifies that the {FIELD_NAMES.get(field, field)} is not supported by the receipt text"
                )
    if (row.get("dollar_gap") or 0) >= 0.005:
        found.append("identifies that the total is not supported by the receipt text")
    return [f"The validator, in its decision (approve_expense or send_to_review), {text}." for text in found]


def extractor_only(spans: list[dict]) -> list[dict]:
    """The session up to the end of the extractor agent.

    Keeps the root invocation span so the session still has its receipts.* attributes.
    """
    return _through_agent(spans, "extractor")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=Path, required=True, help="A run directory written by run_deployed.py")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--fixtures", type=Path, default=HERE / "fixtures")
    parser.add_argument(
        "--skip-parameters", action="store_true", help="Skip ToolParameterAccuracy on the extractor (judge tokens)"
    )
    parser.add_argument("--skip-reasons", action="store_true", help="Skip the right-reason check (judge tokens)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sys.path.insert(0, str(HERE.parent / "evaluators" / "business_outcomes"))

    import boto3
    from bedrock_agentcore.evaluation.custom_code_based_evaluators import EvaluatorInput
    from business_outcomes import handler

    labels = {entry["id"]: entry for entry in json.loads((args.fixtures / "labels.json").read_text())}
    results = json.loads((args.run / "results.json").read_text())
    needs_aws = not args.skip_parameters or not args.skip_reasons
    client = boto3.client("bedrock-agentcore", region_name=args.region) if needs_aws else None

    def invented_values(spans: list[dict]):
        """ToolParameterAccuracy on the extractor's submit_expense call, extractor spans only."""
        spans = extractor_only(spans)
        target = next(
            s["spanId"] for s in spans if (s.get("attributes") or {}).get("gen_ai.tool.name") == "submit_expense"
        )
        try:
            response = client.evaluate(
                evaluatorId="Builtin.ToolParameterAccuracy", evaluationInput={"sessionSpans": spans}
            )
        except Exception as exc:  # noqa: BLE001 — report and carry on through the set
            return f"call failed: {str(exc)[:60]}", ""
        for result in response.get("evaluationResults") or []:
            if result.get("context", {}).get("spanContext", {}).get("spanId") == target:
                return result.get("label"), result.get("explanation", "")
        return "no result", ""

    def right_reason(spans: list[dict], session_id: str, assertions: list[str]):
        """GoalSuccessRate against this receipt's assertions, on the session through the validator."""
        try:
            response = client.evaluate(
                evaluatorId="Builtin.GoalSuccessRate",
                evaluationInput={"sessionSpans": through_validator(spans)},
                evaluationReferenceInputs=[
                    {
                        "context": {"spanContext": {"sessionId": session_id}},
                        "assertions": [{"text": a} for a in assertions],
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001 — report and carry on through the set
            return f"call failed: {str(exc)[:60]}", ""
        first = (response.get("evaluationResults") or [{}])[0]
        return first.get("label") or first.get("errorMessage", "no result"), first.get("explanation", "")

    rows = []
    for row in results:
        fixture_id = row["id"]
        label = labels[fixture_id]
        spans = json.loads((args.run / fixture_id / "adot.json").read_text())

        if label.get("cross_receipt"):
            routing_label = "n/a (cross-receipt)"
        else:
            routing_label = handler.unwrapped(
                EvaluatorInput(
                    evaluation_level="SESSION",
                    session_spans=spans,
                    evaluator_name="ReceiptsRoutingOutcome",
                    reference_inputs=[
                        {
                            "context": {"spanContext": {"sessionId": row["session_id"]}},
                            "expectedResponse": {"text": json.dumps(label)},
                        }
                    ],
                ),
                None,
            ).label

        assertions = reason_assertions(label, row)
        reason, reason_why = ("skipped" if args.skip_reasons else "n/a"), ""
        if assertions and not args.skip_reasons:
            reason, reason_why = right_reason(spans, row["session_id"], assertions)

        parameters, parameters_why = "skipped", ""
        if not args.skip_parameters:
            parameters, parameters_why = invented_values(spans)

        rows.append(
            {
                **row,
                "routing": routing_label,
                "right_reason": reason,
                "right_reason_assertions": assertions,
                "right_reason_explanation": reason_why,
                "tool_parameter_accuracy": parameters,
                "tool_parameter_accuracy_explanation": parameters_why,
            }
        )
        print(f"  {fixture_id:16s} {routing_label:20s} reason={reason:8s} parameters={parameters}")

    (args.run / "scored.json").write_text(json.dumps(rows, indent=2, default=str))

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["routing"]] = counts.get(row["routing"], 0) + 1

    review_correct = counts.get("ReviewCorrect", 0)
    false_alarm = counts.get("FalseAlarm", 0)
    reviewed = review_correct + false_alarm

    print("\nReview-queue precision")
    for name in ("AutoPersistCorrect", "ReviewCorrect", "FalseAlarm", "FalseClear", "NoOutcome"):
        if counts.get(name):
            print(f"  {name:20s} {counts[name]}")
    if reviewed:
        print(
            f"  precision            {review_correct / reviewed:.0%}  ({review_correct} of {reviewed} escalations were needed)"
        )
    if counts.get("FalseClear"):
        exposure = sum(r["true_total"] for r in rows if r["routing"] == "FalseClear")
        print(f"  false-clear exposure {exposure:.2f} committed without a check")
    excluded = [r["id"] for r in rows if r["routing"] == "n/a (cross-receipt)"]
    if excluded:
        print(f"  not scored           {', '.join(excluded)}: the right answer depends on another receipt")

    if not args.skip_reasons:
        checked = [r for r in rows if r["right_reason"] not in ("n/a", "skipped")]
        named = [r["id"] for r in checked if r["right_reason"] == "Yes"]
        missed = [r["id"] for r in checked if r["right_reason"] != "Yes"]
        print("\nRight reason, GoalSuccessRate on the validator")
        print(f"  named the actual problem            {len(named)}/{len(checked)}  {', '.join(named)}")
        print(f"  missed it                           {len(missed)}  {', '.join(missed)}")
        print(f"  nothing to name, not scored         {len(rows) - len(checked)}")

    if not args.skip_parameters:
        # Beside extraction accuracy, the two verdicts say whose fault a wrong field is.
        wrong = {r["id"] for r in rows if "Also wrong:" in (r.get("extraction_explanation") or "")}
        wrong |= {r["id"] for r in rows if (r.get("dollar_gap") or 0) >= 0.005}
        invented = [r["id"] for r in rows if r["tool_parameter_accuracy"] == "No" and r["id"] in wrong]
        ocr = [r["id"] for r in rows if r["tool_parameter_accuracy"] == "Yes" and r["id"] in wrong]
        false_alarm = [r["id"] for r in rows if r["tool_parameter_accuracy"] == "No" and r["id"] not in wrong]
        print("\nInvented values, ToolParameterAccuracy on the extractor")
        print(f"  model invented a wrong field        {len(invented)}  {', '.join(invented)}")
        print(f"  wrong field copied from the OCR     {len(ocr)}  {', '.join(ocr)}")
        print(f"  flagged, extraction actually right  {len(false_alarm)}  {', '.join(false_alarm)}")
        if false_alarm:
            print("  (so far these have been the model's own confidence, which has no source to trace)")

    print(f"\nWritten to {args.run / 'scored.json'}")


if __name__ == "__main__":
    main()
