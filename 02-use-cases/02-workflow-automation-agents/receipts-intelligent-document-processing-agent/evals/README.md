# Evaluation harness

The evaluators that need a right answer run here, against labelled receipts and scripted
conversations. The evaluators that need none run live in AgentCore (see the main README).
Why each evaluator exists, and what the contrast tests found, is in
[ADR-0017](../docs/decisions/0017-evaluators-from-business-outcomes.md).

## Setup

```bash
cd evals
uv venv --python 3.12
uv pip install -r ../app/receiptsagent/requirements.txt "bedrock-agentcore>=1.22" pillow
```

AWS credentials with AgentCore, CloudWatch Logs, S3 and DynamoDB access are needed, and the
stack deployed with `./deploy.sh`.

## Run

```bash
.venv/bin/python run_deployed.py                                  # receipts and chat
.venv/bin/python score_saved.py --run out/deployed-<id>
.venv/bin/python score_chat.py  --run out/deployed-chat-<id>
```

`run_deployed.py` uploads each labelled receipt to the S3 inbox, which triggers the
deployed pipeline exactly as a real receipt would. It then waits for the run ledger, finds
each receipt's session through the `receipts.s3_uri` span attribute, and collects its
trace from CloudWatch. After the receipts it reads the Expenses table for the two failures
that exist only between receipts, a duplicate overwriting the first copy and a bill split
under the Cedar limit. It also seeds the conversation user and runs each conversation as
one chat Runtime session. `--only receipts` or `--only chat` runs one workload.

Keep the machine awake for the whole run (`caffeinate -i` on macOS): each chat turn carries
an identity token that expires after 15 minutes, and a request sent after a sleep is
rejected.

## What each script scores

| Script | Evaluators |
|---|---|
| `run_deployed.py` | `ReceiptsExtractionAccuracy`, `ReceiptsThresholdControl`, called in-process; the cross-receipt duplicate and split checks |
| `score_saved.py` | `ReceiptsRoutingOutcome`; `Builtin.GoalSuccessRate` with per-receipt assertions, on the trace through the validator; `Builtin.ToolParameterAccuracy` on the extractor's part of the trace |
| `score_chat.py` | `ThirdParty.DeepEval.ConversationCompleteness`, `ThirdParty.DeepEval.KnowledgeRetention`, and `Builtin.Correctness` per turn against `fixtures/conversations.json` |

The code-based evaluators live in `../evaluators/business_outcomes/`. The same code is
deployed as the evaluator Lambdas, so a score from the harness and a score from AgentCore come
from one implementation. `tests/test_e2e_evaluators_live.py` checks that: it calls all three deployed evaluators
through the AgentCore Evaluate API, so AgentCore invokes the real Lambdas, and asserts the
labels the local handler gives.

## The labelled data

- **`fixtures/labels.json`**, from `make_fixtures.py`: 9 receipts built around failures, not
  the happy path.
  - `clean`: the control case.
  - `non_reconciling`: the totals don't add up.
  - `over_threshold`: $2,400.
  - `duplicate_a` and `duplicate_b`: one purchase submitted twice.
  - `split_a` and `split_b`: one dinner split across two checks.
  - `injected`: a phishing line printed on the receipt.
  - `pii_heavy`: a card number and address printed on it.

  `cross_receipt` marks receipts whose right answer depends on another receipt, which
  routing does not score. `reason_assertions` lists problems printed on the receipt that
  the validator should name.
- **`fixtures/conversations.json`**: one seeded user, 9 expenses, and 5 scripted
  conversations, with an expected answer per turn.

Nine receipts and five conversations show that each evaluator works. They are not enough
for rates to quote. Grow the sets before relying on the numbers.

Run the labelled set with the degradation ladder at its default rung, L0 (`activeRung` in the
AppConfig ladder profile). Lower rungs change the pipeline: L2 and below send every receipt to
review without the validator, which routing would score as the validator's calls. See
"The evaluators assume the default rung" in the main README.

## Adding an evaluator

1. Name the model decision it judges and what it compares against: a label, or the model's
   own input.
2. Check that no existing evaluator already answers the same question.
3. Contrast-test it before trusting it. Take real saved traces, copy them, change exactly one
   thing (a value, an answer, an assertion), and require the verdict to flip on that change
   and on nothing else. A judge that returns plausible scores regardless of input is worse
   than none.
4. For a judge scoring one model in the pipeline, send it only the trace up to that model
   (`_through_agent` in `score_saved.py`). Later agents repeat earlier outputs.
