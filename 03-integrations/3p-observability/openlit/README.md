# AgentCore + OpenLIT observability

Deploy a Strands travel agent to AgentCore runtime with traces sent to a self-hosted
[OpenLIT](https://github.com/openlit/openlit) instance via OTLP HTTP.

## Architecture

```
AgentCore runtime → travel_agent.py
  └── StrandsTelemetry().setup_otlp_exporter()
        └── OTEL_EXPORTER_OTLP_ENDPOINT (your OpenLIT host:4318)
              └── OpenLIT → Requests dashboard
```

## Prerequisites

- Python 3.10+, uv
- AWS credentials configured
- OpenLIT deployed and accessible from AgentCore runtime

## Deploying OpenLIT

OpenLIT must be reachable from AgentCore's network:

```bash
# Docker Compose (quick start — deploy on a public EC2 instance)
git clone https://github.com/openlit/openlit.git
cd openlit && docker compose up -d
# Endpoint: http://<ec2-public-ip>:4318

# Kubernetes (production)
helm repo add openlit https://openlit.github.io/helm-charts
helm install openlit openlit/openlit
# Endpoint: http://<lb-external-ip>:4318
```

## Quick Start

```bash
pip install bedrock-agentcore boto3 python-dotenv
cp .env.example .env
# Edit .env: set OTEL_EXPORTER_OTLP_ENDPOINT to your OpenLIT host
python deploy.py
python invoke.py
# View traces: http://your-openlit-host:3000 → Requests
python cleanup.py
```

## Files

| File | Description |
|:-----|:------------|
| `utils/travel_agent.py` | Agent with OpenLIT OTLP setup via StrandsTelemetry |
| `deploy.py` | Deploys to AgentCore runtime with OpenLIT env vars |
| `invoke.py` | Invokes the deployed agent |
| `cleanup.py` | Deletes all created AWS resources |

## Additional Resources

- [OpenLIT Documentation](https://docs.openlit.io/)
- [OpenLIT GitHub](https://github.com/openlit/openlit)
- [AgentCore observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
