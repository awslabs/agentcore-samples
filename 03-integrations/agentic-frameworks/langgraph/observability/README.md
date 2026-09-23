# AgentCore observability — LangGraph Agent (Non-runtime Hosted)

Instrument a LangGraph agent running **outside** AgentCore runtime so its traces appear
in the CloudWatch GenAI observability dashboard.

## How It Works

```
LangGraph graph.invoke(...)
  └── opentelemetry-instrument python langgraph_travel_agent.py
        ├── LANGSMITH_OTEL_ENABLED=true   ← bridges LangGraph spans to OTel
        └── AWS Distro for OpenTelemetry (ADOT)
              └── CloudWatch (aws/spans log group)
                    └── GenAI observability dashboard
```

Setting `LANGSMITH_OTEL_ENABLED=true` in your environment causes LangGraph to emit
standard OTel spans. ADOT then exports those spans to CloudWatch.

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
opentelemetry-instrument python langgraph_travel_agent.py

# 5. Run with session tracking
opentelemetry-instrument python langgraph_travel_agent_with_session.py --session-id "session-001"
```

## Scripts

| Script | Description |
|:-------|:------------|
| `langgraph_travel_agent.py` | Travel agent graph with ADOT auto-instrumentation |
| `langgraph_travel_agent_with_session.py` | Same graph with OTel baggage session tracking |
| `setup.py` | Creates the CloudWatch log group and log stream |

## Key Notes

- `LANGSMITH_OTEL_ENABLED=true` must be set before the graph is built
- The model string format for `init_chat_model` is the bare model ID; the provider is `bedrock_converse`
- Session tracking uses OTel baggage (`session.id`) and also the LangGraph `configurable` dict (`session_id`)

## Additional Resources

- [AgentCore observability — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
- [CloudWatch GenAI observability](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AgentCore-Agents.html)
- [AWS Distro for OpenTelemetry](https://aws-otel.github.io/docs/getting-started/python-sdk)
- [LangGraph](https://langchain-ai.github.io/langgraph/)
