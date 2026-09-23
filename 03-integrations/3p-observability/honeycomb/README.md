# AgentCore + Honeycomb observability

Deploy a Strands travel agent to AgentCore runtime with traces sent to [Honeycomb](https://honeycomb.io/)
via OTLP HTTP.

## Architecture

```
AgentCore runtime → travel_agent.py
  └── Custom OTelTracerProvider + BedrockInstrumentor
        └── OTLPSpanExporter → https://api.honeycomb.io/v1/traces
              headers: x-honeycomb-team, x-honeycomb-dataset
                └── Honeycomb → your dataset → Traces
```

`DISABLE_ADOT_OBSERVABILITY=true` bypasses the default CloudWatch ADOT pipeline.
`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` enables GenAI semantic conventions.
`BedrockInstrumentor(capture_content=True)` adds Bedrock-specific span attributes.

## Prerequisites

- Python 3.10+, uv
- AWS credentials configured
- Honeycomb account with API key

## Quick Start

```bash
pip install bedrock-agentcore boto3 python-dotenv
cp .env.example .env
# Edit .env: set HONEYCOMB_API_KEY
python deploy.py
python invoke.py
# View traces: https://ui.honeycomb.io → your dataset → Traces
python cleanup.py
```

## Files

| File | Description |
|:-----|:------------|
| `utils/travel_agent.py` | Agent with Honeycomb OTel TracerProvider + BedrockInstrumentor |
| `deploy.py` | Deploys to AgentCore runtime with Honeycomb env vars |
| `invoke.py` | Invokes the deployed agent |
| `cleanup.py` | Deletes all created AWS resources |

## Additional Resources

- [Honeycomb LLM Documentation](https://docs.honeycomb.io/send-data/llm/)
- [AgentCore observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
