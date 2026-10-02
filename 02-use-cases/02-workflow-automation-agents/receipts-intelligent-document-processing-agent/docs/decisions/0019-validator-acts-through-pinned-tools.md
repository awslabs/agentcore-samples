# ADR-0019: The Validator Acts on Its Decision Through Pinned Tools

**Status:** Accepted
**Date:** 2026-09-23

## Context

The validator decided whether a receipt was saved or sent to review, but it did not act on that decision. It reported a verdict through a `submit_validation` tool (`routing: AUTO_PERSIST` or `NEEDS_REVIEW`), and orchestrator code then called `save_expense` or `human_review` through the Gateway.

That split had two costs:

1. **The decision was not an agent action in the trace.** Strands traces the tools a model calls. Tools that code calls are not traced, so the orchestrator hand-emitted spans for the Gateway calls. The trace showed a validator verdict, then calls made by no agent.
2. **The sample read as a classifier with a script behind it,** not as an agent acting through the Gateway.

The decision was already the validator's. The question was only who executes it.

## Decision

The validator ends its review by calling exactly one of two tools (`app/receiptsagent/decision.py`):

- **`approve_expense(confidence, notes)`** saves the expense through the Gateway. Cedar still gates the save.
- **`send_to_review(confidence, notes, concerns)`** writes the reviewer note and files a review through the Gateway.

The tools are **pinned**, as the chat tools pin the verified user id (ADR-0016). They take the validator's confidence and reasoning, never an amount or a field; the expense they save is the extractor's, supplied by the orchestrator. The model chooses; it cannot change what is written.

The orchestrator keeps every guarantee it had:

- **One outcome.** A second decision is refused, not acted on.
- **Fail safe.** If the validator decides nothing, the receipt goes to review.
- **Degraded rungs review everything.** With `forceReview`, only `send_to_review` is offered. With the validator shed, code files the review, as before.
- **A Cedar denial files a review,** inside `approve_expense`, and the model is told the save was blocked.
- **A Gateway failure is an error,** re-raised after the agent returns, not swallowed as a failed tool call.

The agents are named (`extractor`, `validator`, `reviewer-note`) so evaluation can select one agent's part of the trace by name.

## Reasoning

- **Moving execution does not move authority.** The validator already chose the outcome; code only carried it out. Pinning keeps the model's power exactly where it was: choose between two outcomes, nothing else.
- **The trace now matches the architecture.** The decision appears as the validator's own `execute_tool approve_expense` or `send_to_review` span, with the Gateway's `save_expense` or `human_review` call, and on review the note writer, nested beneath it.
- **Every safety claim is enforced in code, not in the prompt.** Which tools are offered, how often they act, and what they write are decided by the orchestrator.

## Alternatives Considered

- **A separate persistence agent.** It would read the verdict and call the matching tool. That is a model doing an `if`: slower, costlier, and a new way to get a deterministic step wrong.
- **Unpinned tools** that take the expense fields as arguments. The model could then alter an amount on the way to the ledger, and Cedar would check the altered value.
- **Leave it in code.** Safe, but the decision stays invisible as an agent action, which is the problem this ADR addresses.

## Consequences

- The note writer and the `human_review` call now run inside the validator's tool call. `evals/score_saved.py` selects the validator's part of the trace by agent name and excludes both, so the note writer's text cannot earn the validator credit for naming a problem. The right-reason assertions name the decision tools, and were contrast-tested again (vague concern, wrong problem, terse correct concern, real trace: 8 of 8 as expected, on both a local and a deployed trace). The exclusion is necessary, not cautious: with the validator's concern made vague, the trimmed trace scores No, and the whole trace scores Yes, because the note writer's note names the $8 gap.
- Traces from before this change, where agents were unnamed, cannot be rescored with the new trimming.
- What the validator decides is unchanged: the same prompt criteria, the same conservatism.
