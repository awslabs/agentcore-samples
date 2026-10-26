# ADR-0003: Gateway Lambda Targets Over Co-Located Tools

**Status:** Accepted
**Date:** 2026-06-24

## Context

The agent needs tools: read a user's profile, read their recent expenses, look up a merchant, persist a validated expense, and route a receipt to human review. These could be Python functions co-located in the Runtime image, or separate Lambdas behind the AgentCore Gateway (MCP).

## Decision

Each tool is its own Lambda exposed as an AgentCore **Gateway** target (MCP), with a per-tool JSON schema in `lambdas/schemas/<tool>.json`. The agent reaches them through one MCP endpoint with an M2M token. (Carries the claims sample's ADR-0003.)

## Reasoning

Routing tools through the Gateway is what unlocks the rest of the architecture:
- **Cedar enforcement at one boundary.** `BlockExcessiveExpense` gates `save_expense` at the Gateway, before the Lambda runs — a deterministic guardrail independent of the agents (see [ADR-0012](0012-cedar-on-tool-input.md)). Co-located tools have no such choke point.
- **Per-tool least privilege.** Each Lambda gets exactly the DynamoDB grants it needs (`save_expense` read+write on Expenses; the read tools read-only). The Runtime never holds table credentials.
- **Independent evolution.** A tool's implementation can change without rebuilding the agent image.

## Alternatives Considered

Co-located Python tools in the Runtime: fewer moving parts and no network hop, but no single policy-enforcement point, and the Runtime would need broad data-layer permissions. Rejected — the Gateway is the architectural center of an AgentCore app.

## Consequences

Two config surfaces per tool stay in sync (see [ADR-0001](0001-agentcore-cli-plus-cdk.md)): a `PLACEHOLDER_<TOOL>` target in `agentcore.json` and the real ARN patched in `cdk-stack.ts`, plus the schema file. A tool call is an MCP round-trip, not a function call.
