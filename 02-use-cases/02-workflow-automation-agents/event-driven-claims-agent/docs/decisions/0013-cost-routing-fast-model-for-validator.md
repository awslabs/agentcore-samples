# ADR-0013: Cost-Based Model Routing — Fast Model for Validation Agent

## Status

Accepted

## Context

The claims agent processes every event through 3 sequential LLM invocations (Processor → Validator → Executor), all using Claude Sonnet. The Validation Agent (Phase 2) is a classification task: it reviews the Processor's decision, assigns a confidence score (0–100), and routes to AUTO_APPROVE or HUMAN_REVIEW. It does not call any external tools — only the co-located `submit_validation` structured-output tool.

This means Phase 2 uses Sonnet's full reasoning capacity for a task that doesn't require it, adding 6–14s latency and ~$0.01 per invocation unnecessarily.

## Decision

Introduce `FAST_MODEL_ID` environment variable (default: `us.anthropic.claude-haiku-4-5-20251001-v1:0`) and route the Validation Agent to use this cheaper/faster model. The Processor (Phase 1) and Executor (Phase 3) continue using Sonnet for complex reasoning and tool orchestration.

The `load_model(fast: bool = False)` function selects the appropriate model based on the caller's needs.

## Alternatives Rejected

1. **Use Haiku for all phases** — Rejected because Phases 1 and 3 require tool use orchestration and nuanced coverage assessment that benefit from Sonnet's capabilities.
2. **Single model with no routing** — The previous approach. Simple but wastes ~$0.01 and 3–8s per request on a classification task.
3. **Priority-based routing (like IT Incident agent)** — Rejected because in the claims agent the differentiation is by *phase* (which agent), not by *input priority*. All claims deserve full Sonnet reasoning for the decision; only validation is suitable for a lighter model.

## Consequences

- **Latency:** Phase 2 drops from ~6–14s to ~2–6s (Haiku is 3–5x faster for classification).
- **Cost:** Phase 2 cost drops ~80% ($0.01 → $0.002 per invocation).
- **Accuracy risk:** Low. Validation is a bounded classification task (score + route). If accuracy regresses, operators can set `FAST_MODEL_ID` back to Sonnet without code changes.
- **Configuration:** New env var `FAST_MODEL_ID` in `agentcore.json`, `config.py`, and `.env.example`. Fully parameterized — no code change needed to switch models.
