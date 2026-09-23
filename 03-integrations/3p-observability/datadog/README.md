# AgentCore + Datadog LLM observability

Deploy a Strands travel agent to AgentCore runtime with traces sent to [Datadog LLM observability](https://docs.datadoghq.com/llm_observability/)
via OTLP HTTP.

## Architecture

```
AgentCore runtime → travel_agent.py
  └── Custom OTelTracerProvider
        └── OTLPSpanExporter → https://trace.agent.{DD_SITE}/v1/traces
              headers: dd-api-key, dd-otlp-source=llmobs
                └── Datadog LLM observability dashboard
```

`DISABLE_ADOT_OBSERVABILITY=true` bypasses the default CloudWatch ADOT pipeline.
`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` enables OTel v1.37+ GenAI
semantic conventions required for Datadog LLM observability views.

## Prerequisites

- Python 3.10+, uv
- AWS credentials configured
- Datadog account with API key

## Quick Start

```bash
pip install bedrock-agentcore boto3 python-dotenv
cp .env.example .env
# Edit .env: set DD_API_KEY (and optionally DD_SITE for non-US1 regions)
python deploy.py
python invoke.py
# View traces: https://app.datadoghq.com/llm/traces
python cleanup.py
```

## Datadog Regions

| Region | DD_SITE |
|:-------|:--------|
| US1 (default) | `datadoghq.com` |
| US3 | `us3.datadoghq.com` |
| US5 | `us5.datadoghq.com` |
| EU1 | `datadoghq.eu` |
| AP1 | `ap1.datadoghq.com` |

## Files

| File | Description |
|:-----|:------------|
| `utils/travel_agent.py` | Agent with Datadog OTel TracerProvider |
| `deploy.py` | Deploys to AgentCore runtime with Datadog env vars |
| `invoke.py` | Invokes the deployed agent |
| `cleanup.py` | Deletes all created AWS resources |

## Additional Resources

- [Datadog LLM observability OTEL Guide](https://docs.datadoghq.com/llm_observability/instrumentation/otel_instrumentation/)
- [AgentCore observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
