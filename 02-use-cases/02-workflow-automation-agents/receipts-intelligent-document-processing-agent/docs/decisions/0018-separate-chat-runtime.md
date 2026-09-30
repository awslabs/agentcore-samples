# ADR-0018: A Separate Runtime for Chat, With Multi-Turn History

**Status:** Accepted
**Date:** 2026-09-23

## Context

The receipt pipeline and the chat assistant ran on one Runtime, and every chat question opened a fresh session with a memoryless agent. That caused two problems.

1. **No follow-ups.** "And at Starbucks?" after "how much at Mr D.I.Y.?" had nothing to refer to. Self-service resolution was capped by the design.
2. **Mixed traffic in evaluation.** An online evaluation configuration selects sessions by the Runtime's service name. With one Runtime, a chat judge would also score receipt sessions, and a receipt monitor would also score chat sessions.

## Decision

- **Chat keeps its history within a session.** It is held in process, keyed by the *verified* user and the Runtime session id, and trimmed by Strands' sliding window, which never separates a tool call from its result. `scripts/chat.py` keeps one Runtime session for the whole conversation. A one-shot `ask.py` question stands alone.
- **Chat runs on its own Runtime** (`receiptschat`), built from the same code as the pipeline Runtime (`receiptsagent`). Each online evaluation config is bound to one Runtime's service name, so each scores only its own workload.
- **Chat stays read-only** (ADR-0016). Chat questions no longer write a row to the receipt run ledger.

## Reasoning

- **In-process history is the platform's own session model.** A Runtime session is pinned to one microVM for its lifetime, so history lives there with no new infrastructure.
- **Keying on the verified user keeps the IDOR guarantee.** A session id replayed under another identity starts empty.
- **AgentCore Memory was not used for chat history.** Its strategies here are namespaced for receipt recall, and chat turns would pollute them.
- **A separate Runtime is the documented way to separate online evaluation by workload.** Online configs have no documented filter keys, and running every judge on every session would put receipt sessions into chat scores and the reverse.

## Alternatives Considered

- **One Runtime with a code-level guard** in each evaluator ("not a chat session, skip"). This covers only code-based evaluators; the managed chat judges cannot be told to skip.
- **Two endpoints on one Runtime.** Service names are per endpoint, so this would separate traffic too. But the endpoint field is not exposed in the CLI schema used here, and a second Runtime is simpler to read.
- **AgentCore Memory for chat history.** Rejected above.

## Consequences

- Two Runtime images are built per deploy, from the same source.
- History is lost if a session's microVM is recycled, which a chat can tolerate.
- Scripts address the chat Runtime through the `ChatRuntimeArn` stack output, and the pipeline through `RuntimeArn`.
