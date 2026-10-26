# Limits and Constraints

## What Temporal Policies Cannot Do

Temporal policies are authorization controls, not general-purpose workflow logic. Setting the right expectations prevents common design mistakes.

**Cannot govern activity outside AgentCore Gateway.** Temporal policies intercept calls that route through the gateway. If an LLM has built-in local file access or a tool is called directly without going through Gateway, those actions are invisible to temporal evaluation.

**Cannot perform regex matching or arithmetic.** Predicates match on exact field equality or numeric comparisons against recorded values. Complex text parsing or computed expressions are not supported.

**Cannot inspect events older than 24 hours.** The maximum time window is 24 hours. Session state is deleted after 24 hours, and the policy engine has no visibility beyond that.

**Cannot initiate side effects.** A temporal policy can only return PERMIT or DENY for the current request. It cannot trigger a tool call, send a notification, request an approval, or drive the next step in a workflow.

**Cannot span sessions.** A count or sum condition accumulates only within the current session. Starting a new session begins a new counter. Use this for within-session behavioral shaping, not as a cross-session rate limiter against an adversarial caller.

**Cannot aggregate across principals.** Policy evaluation is scoped by session ID. A `count` condition counts events in the session regardless of which principal made them; it cannot enforce that N events came from N distinct principals.

**Cannot reference past guardrail scores.** You cannot write a rule like "block writes if a prior read in this session had a high prompt-injection score." You can, however, combine a temporal condition with a guardrail condition on the current request; both must hold for the policy to apply.

---

## Quotas and Limits

The following limits apply when authoring a temporal policy:

| Quota | Value |
|---|---|
| Temporal operators per policy | 3 |
| Maximum nesting depth of temporal operators | 1 |
| Maximum time window per temporal condition | 24 hours |

Additional service quotas apply per policy engine and per account (including throughput). For the current values and which quotas support increase requests, see the AWS Service Quotas console and the [Amazon Bedrock AgentCore quotas documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/quotas.html).

For best performance, choose a granular session ID scoped to a single user conversation or task, not to an entire application. A coarse session ID that spans many concurrent users will serialize their evaluations and cause contention (the policy engine performs one concurrent temporal evaluation per session ID).

---

Previous: [Common Use Cases](./patterns.md) | Back to [Key Concepts index](./advance.md)
