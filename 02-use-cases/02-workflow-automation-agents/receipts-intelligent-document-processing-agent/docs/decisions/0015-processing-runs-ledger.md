# ADR-0015: A Per-Receipt ProcessingRuns Ledger, Fed Asynchronously

**Status:** Accepted
**Date:** 2026-06-26

## Context

The system persists **expenses** — the Expenses table is keyed by `userId` + `expenseId`, where `expenseId = hash(user|merchant|date|total)`. That content key is correct for dedup ([ADR-0011] idempotency): a retried or near-duplicate receipt collapses onto one row instead of double-counting.

But it means the table does **not** record *what happened to each receipt*. Two gaps surfaced in real use processing a batch of scanned receipts:
1. A receipt that **errors before persisting** (OCR failure, extractor never submits) leaves **no row at all**.
2. Two receipts that hash to the same `expenseId` **overwrite** — the second silently replaces the first, which vanishes from the table.

So answering "what happened to receipt `012.jpg`?" required cross-referencing **three CloudWatch log groups** (trigger, runtime, tool Lambda) and hand-reconstructing the dedup key. That is fine once; it does not scale to an admin triaging failures daily.

## Decision

Add a second table, **`ProcessingRuns`**, keyed by **`receiptId = hash(s3_uri)`** — one row per *receipt*, distinct from the content-keyed *expense*. The agent emits one **best-effort EventBridge event per run** (`Source: receipts.agent`, `DetailType: ReceiptProcessed`); a **ledger-writer Lambda** upserts the row; a **`status = error` rule fans out to SNS**. A `status`/`processedAt` GSI answers "list every error / everything in review." A `scripts/receipt_status.py` makes lookup a one-liner.

Separate the **operational/audit record** (one row per receipt, never deduped) from the **financial record** (one row per expense, deduped). Two tables, two responsibilities.

## Reasoning

- **Per-receipt key never collides.** `hash(s3_uri)` is unique per upload, so every receipt — including an error-before-persist and a deduped duplicate — gets exactly one durable fate row. This is precisely what the content-keyed table cannot do.
- **Asynchronous + best-effort = no bottleneck.** The agent fires the event and returns; it never blocks on, and never fails because of, the ledger (same discipline as the `ModelStepDowns` metric emit, [ADR-0010]). The writer Lambda, EventBridge, and on-demand DynamoDB all scale independently of the hot path. A ledger outage degrades observability, not receipt processing.
- **Push, don't poll (Operational Excellence).** The `status=error` → SNS rule notifies an admin instead of requiring them to go look. The GSI + script turn the forensic dig into `receipt_status.py --status error`.
- **Consistent with the architecture.** EventBridge is already the control-loop backbone; the agent already does best-effort emits. This composes with both rather than introducing a new pattern.

## Alternatives Considered

- **Reuse the Expenses table** (add a status field): can't — the content key dedups and overwrites exactly the rows we need to keep distinct, and an error has no expense to key on.
- **Agent writes the ledger synchronously (DynamoDB directly):** simplest, but couples the hot path to a write and would need DynamoDB Streams for alerting later. The async event path decouples cleanly and gets SNS alerting in the same rule fabric.
- **Logs-only (CloudWatch Insights + dashboard):** least infra, but keeps per-receipt lookup a query rather than an O(1) GetItem — it makes the dig *cleaner*, not *gone*.

## Consequences

A second table + a writer Lambda + a bus + two rules + an SNS topic. The ledger only covers runs **after** it was deployed (older receipts have no row — by design). Subscribe an email/Chatbot endpoint to the `ReceiptsAgent-RunErrors` topic to receive alerts. This is a **post-M3 operational enhancement**, added after the 8 build phases in response to a real scaling pain, not part of the original spec.
