# ADR-0006: S3 + EventBridge Over Direct Invoke

**Status:** Accepted
**Date:** 2026-06-24

## Context

Receipts arrive as image/PDF uploads. The front door could invoke the Runtime directly (an API/Lambda that takes the bytes and calls the agent), or land the file in S3 first and react to the storage event.

## Decision

A receipt is uploaded to an S3 inbox bucket; S3 emits an `Object Created` event; an EventBridge rule (scoped to the `receipts/` prefix) fires the trigger Lambda, which invokes the Runtime with `{s3_uri, user_id}`. Not a direct invoke with the bytes. (Carries the claims sample's ADR-0006; claims uses SES→S3, receipts uses upload→S3.)

## Reasoning

S3 gives a durable audit trail — every receipt is stored as a file you can re-process. It removes any payload-size limit (receipt images are larger than an inline event comfortably carries), and Textract reads the object straight from S3 anyway (`analyze_expense` with an `S3Object`), so the agent never moves the bytes. EventBridge enables fan-out — other consumers can react to new receipts without touching the trigger. And the `user_id` rides in the key (`receipts/<user_id>/<file>`), so the storage layout carries the routing.

## Alternatives Considered

A direct invoke (API Gateway / Lambda that forwards the bytes): one fewer hop, but no audit trail, a payload ceiling, and the bytes would have to be marshalled into the agent rather than read from S3 by Textract.

## Consequences

Three hops instead of one (S3 → EventBridge → trigger → Runtime). For local testing, drop a file straight into the bucket — no upload UI needed. The trigger has a dead-letter queue and retries, so a failed invoke is visible rather than a silently dropped receipt. The Runtime is invoked with `bedrock-agentcore:InvokeAgentRuntime` via boto3 (the same call the L4 drain consumer uses — see [ADR-0011](0011-l4-sqs-jittered-drain.md)).
