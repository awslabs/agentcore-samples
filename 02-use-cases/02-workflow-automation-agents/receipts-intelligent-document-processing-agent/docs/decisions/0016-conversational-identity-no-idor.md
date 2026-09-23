# ADR-0016: Conversational Query Mode — Signed Identity, Not Body-Trusted (No IDOR)

**Status:** Accepted
**Date:** 2026-06-26

## Context

Beyond processing receipts, a user wants to **ask about their expenses** ("how much at Mr D.I.Y.?", "my recent receipts"). The Gateway already has read tools (`get_user_profile`, `get_recent_expenses`, `lookup_merchant`) that take a `user_id`. The naive design: accept `{question, user_id}` in the payload and let an agent call those tools.

That naive design is a textbook **IDOR / broken object-level authorization** hole: user `009` edits the body to `user_id: 012` and reads another user's expenses. The receipt path's auth model ([ADR-0004], agent-as-principal M2M) doesn't help here — it was chosen *because the event-driven receipt path has no end user*, so its `user_id` comes from a trusted S3 key. The conversational path *does* have an end user, so the request body cannot be trusted for identity.

## Decision

In query mode the agent derives `user_id` from a **signed identity token**, never from the request body, and the read tools are **pinned server-side** to that verified id. Two independent layers:

1. **Verified identity (KMS HMAC).** A trusted invoker (the chat client) mints a token binding `{user_id, exp}` via KMS `GenerateMac`; the agent verifies it with `VerifyMac` and reads `user_id` only from the verified claim. The signing key never leaves KMS. Editing the body `user_id` does nothing (it isn't read); a tampered or expired token fails closed (`unauthorized`).
2. **Server-side tool pinning (defense in depth).** `_answer_query` builds local `@tool` wrappers that **close over** the verified `user_id` and expose **no `user_id` parameter** to the model. So even a prompt-injected "show me user-012's expenses" cannot reach another partition — the LLM has no way to express another id. The belt is **read-only** (no `save_expense`/`human_review`), so a query can't write.

## Reasoning

Defense in depth matters because each layer fails differently. The signed token stops body tampering and forged identities cryptographically (the key is in KMS — nothing client-side, not even the agent, can forge a token). The tool pinning stops the *LLM itself* from being the attack vector via prompt injection — a real risk that token verification alone doesn't cover. Together: identity can't be faked, and even a misbehaving model can't escape the authenticated user's own data.

KMS HMAC (not a local secret, not a self-signed JWT) keeps the trust anchor out of every client and the agent image. `VerifyMac` is the only permission the Runtime needs — it verifies, it never mints, so the Runtime role gets `kms:VerifyMac` and not `GenerateMac`.

This is consistent with the rest of the sample: still agent-as-principal M2M to the Gateway, still Cedar at the Gateway, still per-user data separation at the DynamoDB partition key. Query mode adds *end-user* identity verification on top, scoped to the read path.

## Alternatives Considered

- **Trust `user_id` from the payload:** the IDOR hole. Rejected outright.
- **Full Cognito user pool + inbound JWT on the Runtime, identity from the `sub` claim:** the most "production" answer, and the right end state. Deferred because the Runtime is invoked via SigV4 today (which does not propagate per-user identity — verified in the AgentCore identity reference), so it needs a user pool + inbound-authorizer wiring that is its own project. The signed-token approach is secure against the stated threat with far less surface, and the seam makes the upgrade local.
- **Token pinning OR signed identity (just one layer):** either alone leaves a gap — a signed token without pinning still trusts the LLM not to fish; pinning without a signed token still trusts the body. Both, or it's not defense in depth.

## Consequences

A KMS HMAC key + `kms:VerifyMac` on the Runtime + a `IDENTITY_KEY_ID` env var. The chat client (`scripts/chat.py` / `ask.py`) needs `kms:GenerateMac` to mint tokens. A live **IDOR negative test** (`test_e2e_chat_live.py`) proves an attacker authenticated as themselves cannot read a victim's unique amount, and a forged token is rejected — run it on every change to this path.

> **Test lesson (recorded so we don't repeat it):** the first IDOR assertion substring-matched the victim's *merchant name*, which the agent echoed while *refusing* ("nothing for 'Secret Vendor Holdings'") — a false-positive failure. The defense was correct; the test was wrong. Fixed to assert on the victim's unique *amount*, which the attacker never types, so a match can only mean a real leak. A security test must distinguish "leaked the data" from "repeated the attacker's words while denying."

> **Read-path lesson (found via live demo, root-caused not guessed).** The query agent's tool wrapper must return the **clean tool payload** — the text inside the MCP result's `content[].text` (peeling the extra `json.dumps` layer the MCP wrapping adds) — NOT the raw `MCPToolResult` envelope. Handing the model the whole envelope as multiply-escaped JSON made it unreliable at reading its own tool output: it would sometimes declare "no expenses" over data that contained them (non-deterministic across runs). Extracting `content[].text` fixed it (the agent then sums correctly first try). General rule: never hand an LLM a raw MCP envelope.

This is a **post-M3 enhancement** (added for the user-facing demo), not part of the original 8-phase spec. The end-to-end user journey (upload → extract → chat → security → ledger) is automated + self-verifying in `scripts/user-demo.sh` (and `make user-demo` / `make test-user`).
