# ADR-0006: S3 + EventBridge Over Direct SES Lambda Integration

**Status:** Accepted  
**Date:** 2025-06-17

## Context

Claim submissions arrive via email. SES can deliver to Lambda directly or write to S3 first.

## Decision

SES delivers emails to S3; EventBridge triggers the Trigger Lambda. Not SES → Lambda directly.

## Reasoning

S3 provides a durable audit trail — every claim submission is stored as a file. EventBridge enables fan-out (multiple consumers can react to new claims without modifying the SES rule). The S3 object also allows the Trigger Lambda to handle large email payloads that exceed SES → Lambda's 256KB direct event limit.

## Alternatives Considered

Direct SES → Lambda integration would reduce hops but loses the audit trail and limits payload size.

## Consequences

Three hops instead of one (SES → S3 → EventBridge → Lambda). SES requires verified domain or email identity, which is out-of-scope for a demo. For testing, files can be uploaded directly to S3 (bypassing SES).
