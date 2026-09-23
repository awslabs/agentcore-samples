# Frequently Asked Questions

Answers to common questions about temporal policies in Amazon Bedrock AgentCore. For the full conceptual walk-through, start at [Key Concepts](./advance.md).

## Overview

### What is AgentCore Policy?

AgentCore Policy helps you keep agents in bounds. It integrates with AgentCore Gateway to intercept every tool call in real time, so agents stay within defined boundaries without slowing them down. Teams define which tools and data agents can access (APIs, Lambda functions, MCP servers, or third-party services like Salesforce and Slack), what actions they can perform, and under what conditions.

Traditional fine-grained authorization puts the burden on developers to remember every call site, reason about complex agent flows, and trust that unexpected behavior will not bypass a check. Policy removes that burden by enforcing rules outside the agent's execution boundary rather than inside prompts, wrappers, or orchestration code. Every tool call is evaluated at the gateway and enforced consistently regardless of how the agent is implemented, so the rules keep applying even if prompts change, wrapper code drifts, or the agent behaves unpredictably. You can author rules in Cedar (AWS's open-source policy language) or in natural language that converts to Cedar, so development, security, and compliance teams can set up, understand, and audit rules without custom code.

### What is a temporal policy, and why does it exist?

Authorization policies and guardrails each evaluate a single request in isolation: who may call a tool, and whether that one request's content is safe. But many real risks are only visible across a *sequence* of calls, each of which looks fine on its own:

- The agent looks up account `ACC-8821`, then (through a bug, hallucination, or prompt injection) passes a different account to `transfer_funds`. Every call is individually valid; the funds move to the wrong place.
- The agent loops and executes 40 trades, each within limits, but the cumulative exposure is catastrophic.
- The agent approves a claim, then rejects the same claim seconds later. Each action passes its own check; the contradiction only shows up when you see both.

A stateless rule cannot catch these because it never sees the agent's earlier actions. A **temporal policy** is a `permit` or `forbid` rule with a `when temporal { }` block that evaluates the *trajectory*: the ordered history of actions in a session. It closes the gap between "each request is allowed" and "this sequence of requests is safe." See [The Missing Layer: Why Temporal?](../README.md#the-missing-layer-why-temporal) for worked scenarios.

### What are the key benefits?

1. **Govern behavior over time, not just per request.** Enforce that tool A precedes tool B, that an argument matches a value a prior tool actually returned, that a lookup is recent, that a session's cumulative spend stays under a cap, and that contradictory actions cannot both occur.
2. **Enforced outside the agent, so it cannot be bypassed.** Like all AgentCore policies, temporal policies run at the gateway perimeter. The agent never sees the logic and cannot reason around it, no matter how autonomously it operates.
3. **Composes with everything you already have.** Temporal conditions sit alongside standard Cedar `when`/`unless` and guardrail checks in the same policy and the same engine, sharing the LOG_ONLY to ENFORCE calibration workflow and the same observability pipeline.

### How does it relate to authorization policies and guardrails?

They are three layers in one engine. Authorization policies decide *who may call what* (stateless). Guardrails inspect *the content* of a request or response (prompt injection, PII, harmful content). Temporal policies evaluate *the sequence* of actions across a session. Detection in guardrails is probabilistic; policy enforcement, including temporal, stays deterministic and makes the final allow-or-deny decision. A single policy can combine all three condition types.

### Is Dogwood a different language from Cedar?

No. Temporal policies are authored in Dogwood, an open-source superset of Cedar that adds the `when temporal { }` block and its operators. Every valid Cedar policy is valid Dogwood, so your existing authorization policies keep working unchanged. See [The Dogwood Policy Language](./dogwood.md).

## Sessions

### What is a policy session?

The unit of history for temporal evaluation: a sequence of related gateway invocations grouped under one session ID. Temporal conditions see only events from the same session. See [Policy Sessions](./sessions.md).

### How do I supply a session ID?

Pass it on each request in the `x-amzn-bedrock-agentcore-policy-session-id` header. If you omit the header and temporal policies are configured, the gateway creates a session and returns its ID in the response header for you to carry forward. See [Policy Sessions](./sessions.md).

### What happens if I never send the session ID?

Every request starts a fresh session with empty history, which breaks enforcement in both directions. A `permit` that requires a prior event never matches, so legitimate actions are denied. A `forbid` that counts or sums prior events never triggers, so guards like rate limits and budget caps silently stop protecting anything. Related calls must share one session ID for temporal policies to work.

### Is the session ID trusted?

The session ID is caller-supplied, so a temporal rate limit constrains activity within a cooperative session; a caller can start a new session to reset a counter. AgentCore combines the session ID with the end user's identity, so two identities presenting the same ID are separate sessions. Use temporal policies for within-session behavioral shaping, not as a cross-session limiter against an adversarial caller. See [Policy Sessions](./sessions.md).

### Why did a request suddenly return HTTP 409?

Adding, updating, or removing a temporal policy invalidates all active sessions on that engine. The next request that reuses an invalidated session returns 409. Start a new session and resend; it begins with empty history evaluated against the updated policies. See [Policy Sessions](./sessions.md).

## Authoring

### What are the four temporal operators?

- `formerly within W <predicate>`: the matching event happened at least once in the last W.
- `!L since within W R`: anchor event R happened within W and event L has not occurred since (the basis of one-time-use approval).
- `count for ...`: how many matching events occurred in the window, compared against a threshold.
- `sum field for ...`: the running total of a numeric input field across matching events in the window.

See [Temporal Operators](./operators.md) for worked examples and gotchas.

### What is a predicate and why is `eventResource: resource` always required?

A predicate names an action (`TargetName___toolFunctionName`), an event kind (`::request`, `::response`, or `::error`), and a body of field constraints. `eventResource: resource` pins the lookup to the same gateway resource as the current request; a policy without it fails to create. See [Predicate Anatomy and Event Recording](./predicates.md).

### What is the difference between `::request`, `::response`, and `::error`?

`::request` is recorded once the engine permits a call, before the tool runs (input fields only). `::response` is recorded after the tool returns successfully (input and output fields; the only kind that carries outputs). `::error` is recorded when the engine denies the request or the tool itself errors (input fields only). See [Predicate Anatomy and Event Recording](./predicates.md).

### Why does my `::response` condition never match?

Two common causes. First, the prerequisite action has no `permit`, so it was denied and recorded as `::error`, never `::request` or `::response`. Every action you want in history needs its own permit (the "dependency trap"). Second, you are matching an `output.<field>` on an event kind other than `::response`; only `::response` carries output fields. See [Predicate Anatomy and Event Recording](./predicates.md).

### How do I require two prerequisites in any order?

Combine two `formerly` conditions with `&&` inside one `when temporal { }` block. Both must hold for the block to be true. See the parallel-prerequisites example in [Temporal Operators](./operators.md).

### Are `count` and `sum` self-referential?

Yes. Both include the current request being evaluated. With `count ... > 3` the fourth call is the first to be blocked, and a `sum` threshold counts the current amount toward the total. See [Temporal Operators](./operators.md).

### Can I combine a temporal condition with a regular Cedar or guardrail check?

Yes. A single policy can carry `when temporal { }`, `when { }`, and `unless { }` together; all present blocks must hold for the policy to apply. See [The Dogwood Policy Language](./dogwood.md).

## Patterns and limits

### Which patterns do temporal policies support?

Workflow sequencing, output-to-input integrity, data freshness, session-scoped rate limiting, one-time-use approval, cumulative budget caps, cool-down, mutual exclusion, progressive trust decay, and block-after-prior-denial. Each is described with the operator it uses in [Common Use Cases](./patterns.md).

### What can temporal policies not do?

They cannot govern activity outside the gateway, perform regex or arithmetic, inspect events older than 24 hours, initiate side effects (trigger a call, send a notification, request approval), span sessions, aggregate across principals, or reference the guardrail scores of past events. See [Limits and Constraints](./limits.md).

### Are there limits I should design around?

When authoring a policy: at most 3 temporal operators per policy, a maximum nesting depth of 1, and a maximum time window of 24 hours per condition. Additional service quotas apply per policy engine and per account (including throughput); for those values see the AWS Service Quotas console and the [Amazon Bedrock AgentCore quotas documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/quotas.html). See also [Limits and Constraints](./limits.md).

### How should I choose a session ID for good performance?

Scope it to a single user conversation or task, not an entire application. The engine performs one concurrent temporal evaluation per session ID, so a coarse ID shared across many concurrent users serializes their evaluations and causes contention. See [Limits and Constraints](./limits.md).

### Can I test a temporal policy before it blocks real traffic?

Yes. Attach it in LOG_ONLY mode to observe what it would have decided on live traffic, then switch to ENFORCE. This is the same calibration workflow as guardrails.

## Hands-on

### Where do I try this end to end?

The [Banking Assistant sample](../bankingassistant/README.md) deploys a gateway with MCP tools and a policy engine, then walks through authoring a temporal policy for each pattern above.

---

Back to [Key Concepts index](./advance.md)
