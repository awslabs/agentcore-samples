# AgentCore observability — CrewAI Agent (Non-runtime Hosted)

Instrument a CrewAI agent running **outside** AgentCore runtime so its traces appear
in the CloudWatch GenAI observability dashboard.

## How It Works

```
CrewAI Crew
  └── opentelemetry-instrument python crewai_travel_agent.py
        ├── CrewAIInstrumentor()        ← bridges CrewAI spans to OTel
        └── AWS Distro for OpenTelemetry (ADOT)
              └── CloudWatch (aws/spans log group)
                    └── GenAI observability dashboard
```

CrewAI's built-in telemetry (`CREWAI_DISABLE_TELEMETRY=true`) is disabled to avoid
conflicts. The `opentelemetry-instrumentation-crewai` package bridges CrewAI into the
standard OTel pipeline.

## Prerequisites

- Python 3.10+
- AWS credentials configured
- Amazon Bedrock model access (Claude Haiku 4.5)
- **CloudWatch Transaction Search enabled** — required before traces appear:
  - IaC: [`05-infrastructure-as-code/01-enable-transaction-search/`](../../../../05-infrastructure-as-code/01-enable-transaction-search/)
  - Console: [CloudWatch > X-Ray settings > Transaction Search](https://console.aws.amazon.com/cloudwatch/home#xray:settings/transaction-search)

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Create CloudWatch log group and stream
python setup.py

# 3. Configure environment variables
cp .env.example .env
# Edit .env if needed

# 4. Run with ADOT instrumentation (basic)
opentelemetry-instrument python crewai_travel_agent.py

# 5. Run with session tracking
opentelemetry-instrument python crewai_travel_agent_with_session.py --session-id "session-001"
```

## Scripts

| Script | Description |
|:-------|:------------|
| `crewai_travel_agent.py` | Travel agent crew with ADOT auto-instrumentation |
| `crewai_travel_agent_with_session.py` | Same crew with OTel baggage session tracking |
| `setup.py` | Creates the CloudWatch log group and log stream |

## Key Notes

- `CREWAI_DISABLE_TELEMETRY=true` must be set before importing crewai
- `CrewAIInstrumentor().instrument()` must be called before any crew is created
- The `CrewAI` model string format is `bedrock/<model_id>` (not the bare model ID)

## Additional Resources

- [AgentCore observability — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
- [CloudWatch GenAI observability](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AgentCore-Agents.html)
- [AWS Distro for OpenTelemetry](https://aws-otel.github.io/docs/getting-started/python-sdk)
- [CrewAI](https://docs.crewai.com/)
