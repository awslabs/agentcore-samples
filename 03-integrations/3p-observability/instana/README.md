# AgentCore + Instana observability

Deploy a Strands travel agent to AgentCore runtime with traces sent to [Instana](https://www.ibm.com/products/instana)
via OTLP HTTP.

## Architecture

```
AgentCore runtime → travel_agent.py
  └── StrandsTelemetry().setup_otlp_exporter()
        └── OTEL_EXPORTER_OTLP_ENDPOINT (your Instana OTLP endpoint)
              header: x-instana-key
                └── Instana → Analytics → Calls
```

## Prerequisites

- Python 3.10+, uv
- AWS credentials configured
- Instana account with agent key and OTLP endpoint

## Finding Your Instana Endpoint

1. Instana sidebar → **About Instana** → note Instance Region
2. Use the HTTP OTLP endpoint (port 4318) for your region from the [Instana endpoint docs](https://www.ibm.com/docs/en/instana-observability/1.0.309?topic=instana-backend)

## Finding Your Instana Key

1. Instana sidebar → **Agents & Collectors** → Linux – Automatic Installation
2. Copy the key after the `-a` flag

## Quick Start

```bash
pip install bedrock-agentcore boto3 python-dotenv
cp .env.example .env
# Edit .env: set INSTANA_KEY and OTEL_EXPORTER_OTLP_ENDPOINT
python deploy.py
python invoke.py
# View traces: Instana → Analytics → add filter: Service Name = strands-agents
python cleanup.py
```

## Files

| File | Description |
|:-----|:------------|
| `utils/travel_agent.py` | Agent with Instana OTLP setup via StrandsTelemetry |
| `deploy.py` | Deploys to AgentCore runtime with Instana env vars |
| `invoke.py` | Invokes the deployed agent |
| `cleanup.py` | Deletes all created AWS resources |

## Additional Resources

- [Instana Documentation](https://www.ibm.com/docs/en/instana-observability)
- [AgentCore observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
