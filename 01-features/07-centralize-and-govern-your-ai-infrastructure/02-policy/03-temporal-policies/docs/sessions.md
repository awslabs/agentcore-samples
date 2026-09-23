# Policy Sessions

A **policy session** is the unit of history for temporal evaluation. It is a sequence of related Gateway invocations grouped under a single session ID. Temporal conditions see only the events recorded for the same session as the current request.

## Supplying the Session ID

You supply the session ID on each request using the HTTP header:

```bash
x-amzn-bedrock-agentcore-policy-session-id: <your-session-id>
```

If you omit this header and temporal policies are configured on the gateway, the gateway creates a session and returns its ID in the response header for you to carry forward on subsequent calls.

## Session Scope

Temporal history is scoped to the session. A condition that checks "did X happen within the last hour?" only considers events from the same session, not from any other session or any other caller. This is the fundamental unit of isolation for temporal enforcement.

A session is never defined by its ID alone. AgentCore combines the session ID with the end user's identity to produce a unique session. Two different identities presenting the same session ID are treated as entirely separate sessions with separate histories.

## Session Invalidation

If you add, update, or remove a temporal policy on the policy engine, all currently active sessions for that engine are invalidated. The next request that reuses an invalidated session returns HTTP 409.

To recover: start a new session and resend the request. The new session begins with an empty history and is evaluated against your updated policies. This prevents the engine from evaluating accumulated history against rules that were not in effect when that history was recorded.

## Security Note

The session ID is caller-supplied. A temporal rate limit such as "at most 3 calls per session" constrains activity within that session; a caller who starts a new session begins a fresh count. Temporal policies are designed for within-session behavioral shaping, not as a cross-session rate limiter against an adversarial caller.

For best throughput, choose a granular session ID scoped to a single user conversation or task, not to an entire application. A coarse session ID shared across many concurrent users serializes their evaluations and causes contention (the policy engine performs one concurrent temporal evaluation per session ID).

---

Previous: [The Dogwood Policy Language](./dogwood.md) | Next: [Predicate Anatomy and Event Recording](./predicates.md)
