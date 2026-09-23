# Common Use Cases

Temporal policies support a wide range of patterns. Here is a catalog of the most useful ones:

## Workflow Sequencing

Enforce that tool A must complete before tool B is permitted. Useful for standard operating procedure adherence where a data lookup must precede a write action, or a client profile must be loaded before a trade executes.

Use `formerly` on the prerequisite's `::response` event. The dependent action is denied unless that event exists in the session history within the window.

## Output-to-Input Integrity

Require that an argument passed to the current tool exactly matches the output returned by a prior tool. This prevents an agent (whether hallucinating or prompt-injected) from substituting a fabricated or attacker-supplied value between steps.

Use `formerly` on the prerequisite's `::response` event with `output.<name>: context.input.<name>` in the predicate body. See [Predicate Anatomy](./predicates.md) for a detailed walk-through.

## Data Freshness

Require a data-fetch action to have completed within a tight window (e.g., 30 seconds) before a dependent action is authorized. Prevents decisions based on stale quotes, balances, or records.

Use `formerly within 30s` on the data-fetch `::response`. The dependent action is denied if the fetch happened more than 30 seconds ago.

## Session-Scoped Rate Limiting

Cap how many times a tool can be called within a rolling time window. Limits blast radius from runaway or hijacked agents.

Use `count` on the action's `::request` events. Note: because the session ID is caller-supplied, this constrains activity within a cooperative session; a caller can start a new session to reset the counter. See [Session Security Note](./sessions.md).

## One-Time-Use Approval

An explicit approval event (e.g., a human-in-the-loop confirmation) permits exactly one subsequent privileged action. Once that action completes, the approval is consumed and the next instance requires a fresh approval.

Use `!L since within W R` where R is the approval event and L is the consumption event. See [Temporal Operators](./operators.md) for a detailed walk-through.

## Cumulative Budget Cap

Sum a numeric input field (such as trade amount or token spend) across all matching events in the window. Block once the running total crosses a threshold. Prevents individually-valid actions from collectively exceeding a risk limit.

Use `sum` on the `::request` events for the action. See [Temporal Operators](./operators.md) for a detailed walk-through.

## Cool-Down

Prevent an action from being repeated within a fixed period after its last successful completion. Useful for enforcing minimum intervals between state-changing operations.

Use a `forbid` with `formerly within W` on the action's `::response`. As long as a recent `::response` exists in the session history, the `forbid` applies and repeats are blocked.

## Mutual Exclusion

Ensure two actions cannot both be requested within the same time window. Whichever runs first blocks the other.

Two symmetric `forbid` policies are needed for true bidirectional exclusion. See Scenario 3 in the [README](../README.md) for the full policy pair.

## Progressive Trust Decay

After a configurable period without a human interaction event in the session, revoke access to write or privileged operations. The agent converges to read-only behavior as it operates further from human oversight.

Use a `permit` for write operations conditioned on `formerly within W` on a human-interaction event. Once that event ages out of the window, the permit no longer matches and writes are denied.

## Block After Prior Denial

Forbid a sensitive action whenever an earlier action in the same session was denied (recorded as `::error`). A pattern of denials is a signal that something has gone wrong; this lets you escalate the response automatically.

Use a `forbid` with `formerly within W` on the suspicious action's `::error` event.

---

Previous: [Temporal Operators](./operators.md) | Next: [Limits and Constraints](./limits.md)
