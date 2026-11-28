# ADR 0011: Externalized Configuration via Environment Variables

**Status:** Accepted  
**Date:** 2026-06-24

## Context

Without externalized configuration, several operational parameters would be hardcoded in Python and TypeScript:
- Confidence threshold for auto-approval (`80` in `routing.py`)
- Single model selection (no cost routing)
- Lambda timeouts (`10s` in CDK)
- SNS topic name, S3 inbox prefix, destroy-on-delete behavior
- Memory retrieval parameters (`top_k`, `relevance_score`)

This made it impossible to tune behavior per-deployment without code changes. For a reference sample, users should be able to customize the agent for their domain by changing environment variables alone — before needing to touch code.

## Decision

Externalize all operational knobs to environment variables read via `config.py` (agent-side) or `process.env` (CDK-side), with safe defaults that preserve existing behavior.

### Agent-side (Runtime env vars)
| Variable | Default | Effect |
|----------|---------|--------|
| `AGENT_MODEL_ID` | `global.anthropic.claude-sonnet-4-6` | Primary reasoning model |
| `FAST_MODEL_ID` | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | Fast model for Phase 2 validation |
| `AUTO_APPROVE_THRESHOLD` | `80` | Confidence score cutoff for auto-approval |
| `MEMORY_RETRIEVAL_TOP_K` | `5` | Number of facts/sessions retrieved |
| `MEMORY_RETRIEVAL_RELEVANCE` | `0.5` | Minimum relevance score |

### CDK-side properties

Lambda timeouts, the inbox prefix and removal policy are explicit properties in
`infra-construct.ts`/`cdk-stack.ts`, not implemented environment variables.
Physical names derive from the fresh project/target identity; notification mode
is journaled in `.claims-v1.json`. `SENDER_EMAIL` is the optional synth-time setting
for a separately authorized notification-enabled demonstration.

## Reasoning

1. **Configuration-first customization** — users can adapt behavior without understanding the code. Lower barrier to "make it your own".
2. **Per-environment tuning** — dev vs. staging vs. production can have different thresholds without code branches.
3. **Cost routing** — `FAST_MODEL_ID` enables significant cost savings (Haiku is ~10x cheaper than Sonnet) for low-stakes claims without sacrificing quality on complex ones.
4. **Safe defaults** — every variable has a sensible default matching the sample's standard behavior. Deploying without overriding any variable produces the documented default behavior.

## Alternatives Considered

### CDK context parameters instead of env vars
- **Rejected:** CDK context requires `--context key=value` at synth time, doesn't work with `.env` files, and isn't readable from agent Python code. Env vars are universally supported.

### AWS Systems Manager Parameter Store
- **Rejected:** Adds latency at agent startup, requires IAM permissions, and is overkill for a sample. Parameter Store is appropriate for secrets (handled by Identity vault) but not for simple config knobs.

### Config file (JSON/YAML) baked into the container
- **Rejected:** Requires container rebuild to change any value. Env vars can be overridden per-deployment via `agentcore.json` envVars without rebuilding.

## Consequences

- **Positive:** Deployed runtime settings are explicit `agentcore.json` envVars. The v1 lifecycle never sources `.env`; model changes must also update the runtime IAM boundary.
- **Positive:** `config.py` becomes the single source of truth for all runtime configuration — easy to audit.
- **Negative:** More env vars to document. Mitigated by `.env.example` with descriptions and the CONFIGURATION.md reference.
- **Watch:** If the number of env vars grows past ~20, consider grouping into a config file loaded at startup. Currently at 12 (manageable).
