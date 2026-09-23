# AgentCore + Arize observability

Deploy a Strands travel agent to AgentCore runtime with traces sent to [Arize](https://arize.com/)
via the OpenInference OTel bridge (gRPC OTLP).

## Architecture

```
AgentCore runtime → travel_agent.py
  └── StrandsAgentsToOpenInferenceProcessor  ← converts Strands spans to OpenInference format
  └── OTLPSpanExporter (gRPC)
        └── https://otlp.arize.com:443
              └── Arize dashboard → Traces
```

`DISABLE_ADOT_OBSERVABILITY=true` bypasses the default CloudWatch ADOT pipeline so the custom
Arize TracerProvider is used instead.

## Prerequisites

- Python 3.10+, uv, Docker (not required — uses code deployment)
- AWS credentials configured
- Arize account with API key and space ID

## Quick Start

```bash
# 1. Install local dependencies (for deploy.py/invoke.py/cleanup.py)
pip install bedrock-agentcore boto3 python-dotenv

# 2. Set your Arize credentials
cp .env.example .env
# Edit .env: set ARIZE_API_KEY and ARIZE_SPACE_ID

# 3. Deploy to AgentCore runtime
python deploy.py

# 4. Invoke the agent
python invoke.py

# 5. View traces in Arize
#    → https://app.arize.com → your project → Traces

# 6. Clean up
python cleanup.py
```

## Files

| File | Description |
|:-----|:------------|
| `utils/travel_agent.py` | Agent with Arize OTel setup (gRPC + OpenInference) |
| `deploy.py` | Creates IAM role, builds zip, deploys to AgentCore runtime |
| `invoke.py` | Invokes the deployed agent with sample prompts |
| `cleanup.py` | Deletes all created AWS resources |

## Key Configuration

```python
# In utils/travel_agent.py — Arize TracerProvider setup
from openinference.instrumentation.strands_agents import StrandsAgentsToOpenInferenceProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

provider = TracerProvider(resource=Resource.create({"model_id": arize_project}))
provider.add_span_processor(StrandsAgentsToOpenInferenceProcessor())
exporter = OTLPSpanExporter(
    endpoint="https://otlp.arize.com:443",
    headers=f"space_id={ARIZE_SPACE_ID},api_key={ARIZE_API_KEY}",
)
provider.add_span_processor(BatchSpanProcessor(exporter))
```

## Additional Resources

- [Arize Documentation](https://docs.arize.com/arize)
- [OpenInference for Strands](https://github.com/Arize-ai/openinference/tree/main/python/instrumentation/openinference-instrumentation-strands-agents)
- [AgentCore observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
