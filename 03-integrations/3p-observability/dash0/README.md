# AgentCore + Dash0 observability

Deploy a Strands travel agent to AgentCore Runtime with traces, metrics, and logs sent to [Dash0](https://www.dash0.com/) via OTLP HTTP.

## Architecture

```
AgentCore runtime → utils/travel_agent.py
  └── OTel SDK
        ├── OTLPSpanExporter   → {DASH0_OTLP_ENDPOINT}/v1/traces
        ├── OTLPMetricExporter → {DASH0_OTLP_ENDPOINT}/v1/metrics
        └── OTLPLogExporter    → {DASH0_OTLP_ENDPOINT}/v1/logs
              headers: Authorization: Bearer <token>, Dash0-Dataset: <dataset>
                └── Dash0 → Tracing, Metrics, Logs
```

`DISABLE_ADOT_OBSERVABILITY=true` bypasses the default CloudWatch ADOT pipeline so Dash0 receives all telemetry.

## Prerequisites

- Python 3.10+, [uv](https://docs.astral.sh/uv/)
- AWS credentials configured
- [Dash0 account](https://www.dash0.com/)

## Quick Start

```bash
pip install bedrock-agentcore boto3 python-dotenv
cp .env.example .env
# Edit .env: set DASH0_AUTH_TOKEN and DASH0_OTLP_ENDPOINT for your region
python deploy.py
python invoke.py
# View telemetry: https://app.dash0.com → Tracing / Metrics / Logs
python cleanup.py
```

## Dash0 Regions

| Region | DASH0_OTLP_ENDPOINT |
|:-------|:--------------------|
| US West 2 (default) | `https://ingress.us-west-2.aws.dash0.com` |
| EU West 1 | `https://ingress.eu-west-1.aws.dash0.com` |

> Find your endpoint at **app.dash0.com → Settings → Endpoints**.

## Environment Variables

| Variable | Description | Default |
|:---------|:------------|:--------|
| `DASH0_AUTH_TOKEN` | Auth token from **Settings → Auth Tokens** | _(required)_ |
| `DASH0_OTLP_ENDPOINT` | OTLP ingress base URL for your region | `https://ingress.us-west-2.aws.dash0.com` |
| `DASH0_DATASET` | Dataset to route telemetry to | `default` |
| `OTEL_SERVICE_NAME` | Service name shown in Dash0 | `agentcore-travel-agent` |

## Files

| File | Description |
|:-----|:------------|
| `utils/travel_agent.py` | Agent with Dash0 OTel setup (traces, metrics, logs) |
| `deploy.py` | Deploys to AgentCore Runtime with Dash0 env vars |
| `invoke.py` | Invokes the deployed agent with sample travel prompts |
| `cleanup.py` | Deletes all created AWS resources |

## Additional Resources

- [Dash0 Documentation](https://dash0.com/docs)
- [Dash0 Endpoints glossary](https://dash0.com/docs/dash0/miscellaneous/glossary/endpoints)
- [AgentCore observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
