# AgentCore observability — Strands Agent (Non-runtime Hosted)

Instrument a Strands agent running **outside** AgentCore runtime (e.g., on EC2, Lambda,
or your local machine) to surface traces in the CloudWatch GenAI observability dashboard.

## How It Works

```
Strands Agent
  └── opentelemetry-instrument python strands_travel_agent.py
        └── AWS Distro for OpenTelemetry (ADOT)
              └── CloudWatch (aws/spans log group)
                    └── GenAI observability dashboard
```

The `opentelemetry-instrument` command wraps your agent with ADOT auto-instrumentation,
which captures Strands operations, Bedrock API calls, and tool invocations without any
code changes.

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
# Edit .env — update OTEL_EXPORTER_OTLP_LOGS_HEADERS if needed

# 4. Run with ADOT instrumentation (basic)
opentelemetry-instrument python strands_travel_agent.py

# 5. Run with session tracking
opentelemetry-instrument python strands_travel_agent_with_session.py --session-id "session-001"
```

## Scripts

| Script | Description |
|:-------|:------------|
| `strands_travel_agent.py` | Travel agent with ADOT auto-instrumentation |
| `strands_travel_agent_with_session.py` | Same agent with OTel baggage session tracking |
| `setup.py` | Creates the CloudWatch log group and log stream |

## Session Tracking

Session IDs group related traces in the dashboard. The `_with_session` script attaches
the ID to OTel baggage so all spans in a single run share the same `session.id` attribute:

```python
from opentelemetry import baggage, context
ctx = baggage.set_baggage("session.id", session_id)
token = context.attach(ctx)
```

## Custom Metadata

Add extra baggage attributes for filtering, A/B testing, or offline evaluation:

```bash
# Different experiments
opentelemetry-instrument python strands_travel_agent_with_session.py \
  --session-id "session-123"

# Evaluate offline against a golden dataset
opentelemetry-instrument python strands_travel_agent_with_session.py \
  --session-id "eval-001"
```

## View in CloudWatch

Navigate to **CloudWatch > GenAI observability > Bedrock AgentCore** to see:
- Session list with duration and token counts
- Trace waterfall (model calls, tool invocations)
- Per-span attributes and events

## Additional Resources

- [AgentCore observability — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
- [CloudWatch GenAI observability](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AgentCore-Agents.html)
- [AWS Distro for OpenTelemetry](https://aws-otel.github.io/docs/getting-started/python-sdk)
- [Strands Agents](https://strandsagents.com/latest/)
