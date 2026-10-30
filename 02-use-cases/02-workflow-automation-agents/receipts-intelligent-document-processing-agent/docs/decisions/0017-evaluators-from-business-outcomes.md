# ADR-0017: Evaluators Chosen From Business Outcomes, and Contrast-Tested

**Status:** Accepted
**Date:** 2026-09-23

## Context

The first version of this sample shipped one LLM judge that blended five qualities into a single 1-to-5 score, and an online configuration running `Builtin.Helpfulness`, `Builtin.Correctness` and `Builtin.ToolSelectionAccuracy`. Those three were the same set as a sibling sample. `Helpfulness` scored a pipeline that has no user, and none of the scores could be tied to anything the business needs.

## Decision

Pick evaluators with two frameworks and four rules, and ship only the ones that survive.

**Framework 1, backwards from the business.** State each metric without naming the agent, its model or its tools. That gives the scoreboard:
- straight-through rate
- dollar-weighted extraction error
- control breaches
- review-queue precision
- chat self-service resolution
- chat answer accuracy

**Framework 2, forwards from the architecture.** Name the failures this design makes likely, and aim a diagnostic at each:
- invented values in extraction
- the right route for the wrong reason
- forgotten context in chat

**Rules:**
1. An evaluator judges a decision a model makes, against a right answer or a clear reference. The one exception is a control monitor, labelled as such.
2. One question, one evaluator.
3. Every judge passes a contrast test before it is trusted: pairs of cases that differ in one thing, where the verdict must flip.
4. Product changes only where a team would make them anyway.

The resulting set, by where it runs:

| Evaluator | Where it runs |
|---|---|
| `ReceiptsThresholdControl` (code) | Online config `ReceiptsLive`, on the pipeline Runtime |
| `ConversationCompleteness`, `KnowledgeRetention` (DeepEval) | Online config `ReceiptsAgent_ChatLive`, on the chat Runtime |
| `ReceiptsExtractionAccuracy`, `ReceiptsRoutingOutcome` (code) | Labelled data |
| `ToolParameterAccuracy`, `GoalSuccessRate` with assertions, `Correctness` with expected answers (built-in) | Labelled data |

## Reasoning

- **Online configurations cannot carry ground truth.** Anything that needs a right answer runs on demand against labelled receipts and conversations (`evals/`).
- **Code where the question is exact, judges where it is semantic.** Dollar error and routing are arithmetic against labels. "Did the validator name the actual problem" is not, which is why `GoalSuccessRate` runs there with per-receipt assertions.
- **The contrast tests changed the design:**
  - **`ToolParameterAccuracy` fails on the whole session.** The extractor, validator and note writer share one trace. The validator's input repeats the extractor's output, so the judge treated invented values as sourced. It failed 4 of 7 cases on the whole session and passed 7 of 7 on the extractor's part of the trace. The harness therefore sends each judge only the part of the trace up to the model it judges. For the same reason it cannot run online, where the whole session is scored.
  - **Routing must be judged on what the validator was shown.** When the extraction was wrong, review is the right call whatever the receipt's label says. Otherwise one extractor mistake is counted twice.
  - **ConversationCompleteness cannot tell a right answer from a wrong one.** It scored a wrong "most recent expense" answer as handled. `Correctness` with an expected answer catches it, and a correct refusal scores correct there. The two answer different questions, so both are kept.
  - **KnowledgeRetention passes a clean contrast but is noisy.** The same conversation scored differently across runs. It is a diagnostic only, never quoted per conversation.
- **The Cedar threshold is watched, not re-checked.** An evaluator that re-reads the total Cedar saw says nothing about the agent. But a policy can be edited, detached or left in log-only mode, and a monitor that needs no labels is the only live check that the control still holds.

## Alternatives Considered

- **The previous set** (`Helpfulness`, `Correctness` and `ToolSelectionAccuracy` online, one blended judge). It measured nothing the business needs; removed.
- **`GoalSuccessRate` asserting the $2,000 rule.** It passed because a $15.90 receipt never engaged the rule, so the same green meant "obeyed" and "not applicable", at judge cost, for a question code answers exactly.
- **DeepEval `ToolUse`, `PIILeakage`, AutoEval `Security`.** All failed their contrast tests:
  - `ToolUse` scored a correct run 0.25.
  - `Security` called every normal reviewer note malicious.
  - `PIILeakage` scored the note near real personal data as the cleanest.
- **`TrajectoryInOrderMatch`.** The pipeline's step order is fixed in code, so it passed by construction.
- **The other managed third-party evaluators.** None fits a single-turn workload. The two in use became applicable only once chat was made multi-turn.

## Consequences

- Three evaluators run live; five need labelled data. The labelled set is small (9 receipts, 5 conversations): it shows each evaluator works, not rates to quote.
- The code-based evaluators deploy as one Lambda, branching on the evaluator name AgentCore passes in.
- Failures the evaluators expose (invented dates, a duplicate receipt overwriting the first, reconciliation enforced only by a prompt, a tool that promises newest-first but sorts by a hash) are left in place as the evidence the suite exists to catch.
