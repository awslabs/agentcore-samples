"""Score saved chat conversations: resolution, retention, and answer accuracy.

  ConversationCompleteness  self-service resolution, live signal: of what the employee
                            asked across the conversation, how much was handled. Used on
                            chat instead of Builtin.GoalSuccessRate, not beside it
  KnowledgeRetention        diagnostic: does the assistant keep what the employee said
                            earlier, or ask for it again
  Builtin.Correctness       answer accuracy, offline: each turn's reply against the
                            expected answer in fixtures/conversations.json

The first two are managed DeepEval metrics with no ground truth: they judge whether the
conversation looks resolved and consistent, not whether any figure in it is right, so a
confidently wrong total can score as complete. Correctness is the one that knows the right
answer. It is TRACE level: each turn is its own trace, and one Evaluate call carries one
reference per turn, keyed by that turn's trace id. Turns with no expected answer are not
scored. Chat is a single agent with one session per conversation, so the per-agent trace
scoping the receipt pipeline needs does not apply.

Usage:
    python score_chat.py --run out/deployed-chat-<id>
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVALUATORS = ["ThirdParty.DeepEval.ConversationCompleteness", "ThirdParty.DeepEval.KnowledgeRetention"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=Path, required=True, help="A run directory written by run_deployed.py")
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--conversations", type=Path, default=HERE / "fixtures" / "conversations.json")
    return parser.parse_args()


def score(client, evaluator_id: str, spans: list[dict]) -> dict:
    try:
        response = client.evaluate(evaluatorId=evaluator_id, evaluationInput={"sessionSpans": spans})
    except Exception as exc:  # noqa: BLE001 — report and carry on through the set
        return {"label": "call failed", "value": None, "explanation": str(exc)[:200]}
    result = (response.get("evaluationResults") or [{}])[0]
    return {
        "label": result.get("label") or result.get("errorCode") or "no result",
        "value": result.get("value"),
        "explanation": result.get("explanation") or result.get("errorMessage") or "",
        "tokens": (result.get("tokenUsage") or {}).get("totalTokens"),
    }


def turn_traces(spans: list[dict]) -> list[str]:
    """Trace ids of the conversation's turns, in order.

    Each turn is its own trace, one invocation of the chat Runtime, so the turns are the
    distinct trace ids, ordered by when each began.
    """
    first_seen: dict[str, int] = {}
    for span in spans:
        trace, start = span.get("traceId"), span.get("startTimeUnixNano")
        if trace and start is not None:
            first_seen[trace] = min(start, first_seen.get(trace, start))
    return sorted(first_seen, key=first_seen.get)


def correctness(client, spans: list[dict], session_id: str, answers: list[str]) -> list[dict]:
    """Builtin.Correctness per turn, each against its own expected answer. Unanswered turns are skipped."""
    expected = {trace: answer for trace, answer in zip(turn_traces(spans), answers) if answer}
    if not expected:
        return []
    references = [
        {"context": {"spanContext": {"sessionId": session_id, "traceId": trace}}, "expectedResponse": {"text": answer}}
        for trace, answer in expected.items()
    ]
    try:
        response = client.evaluate(
            evaluatorId="Builtin.Correctness",
            evaluationInput={"sessionSpans": spans},
            evaluationReferenceInputs=references,
        )
    except Exception as exc:  # noqa: BLE001 — report and carry on through the set
        return [{"turn": None, "label": "call failed", "explanation": str(exc)[:200]}]
    turns = turn_traces(spans)
    verdicts = []
    for result in response.get("evaluationResults") or []:
        trace = (result.get("context") or {}).get("spanContext", {}).get("traceId")
        if trace in expected:
            verdicts.append(
                {
                    "turn": turns.index(trace) + 1,
                    "label": result.get("label") or result.get("errorCode"),
                    "expected": expected[trace],
                    "explanation": result.get("explanation") or result.get("errorMessage") or "",
                }
            )
    return sorted(verdicts, key=lambda v: v["turn"])


def main() -> None:
    args = parse_args()
    import boto3

    client = boto3.client("bedrock-agentcore", region_name=args.region)
    answers = {c["id"]: c.get("answers") or [] for c in json.loads(args.conversations.read_text())["conversations"]}
    rows = []
    print(f"{'conversation':20s} {'turns':>5s}  {'completeness':>12s}  {'retention':>9s}  {'correct':>7s}")
    for case_dir in sorted(p for p in args.run.iterdir() if (p / "adot.json").exists()):
        spans = json.loads((case_dir / "adot.json").read_text())
        saved = json.loads((case_dir / "transcript.json").read_text())
        turns = len(saved["transcript"])
        scores = {evaluator.split(".")[-1]: score(client, evaluator, spans) for evaluator in EVALUATORS}
        verdicts = correctness(client, spans, saved["session_id"], answers.get(case_dir.name, []))
        rows.append({"id": case_dir.name, "turns": turns, **scores, "Correctness": verdicts})
        c, k = scores["ConversationCompleteness"], scores["KnowledgeRetention"]
        right = sum(1 for v in verdicts if v["label"] == "Correct")
        print(f"{case_dir.name:20s} {turns:5d}  {str(c['value']):>12s}  {str(k['value']):>9s}  {right}/{len(verdicts)}")
        for v in verdicts:
            if v["label"] != "Correct":
                print(f"    turn {v['turn']} {v['label']}: {v['explanation'][:200]}")

    (args.run / "scored.json").write_text(json.dumps(rows, indent=2, default=str))
    print(f"\nWritten to {args.run / 'scored.json'}")


if __name__ == "__main__":
    main()
