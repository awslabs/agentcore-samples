# AgentCore observability — LlamaIndex Agent (Non-runtime Hosted)

Instrument a LlamaIndex `FunctionAgent` running **outside** AgentCore runtime so its
traces appear in the CloudWatch GenAI observability dashboard.

## How It Works

```
LlamaIndex FunctionAgent
  └── opentelemetry-instrument python llama_index_agent.py
        ├── LlamaIndexOpenTelemetry()   ← bridges LlamaIndex spans to OTel
        └── AWS Distro for OpenTelemetry (ADOT)
              └── CloudWatch (aws/spans log group)
                    └── GenAI observability dashboard
```

`LlamaIndexOpenTelemetry` instruments LlamaIndex internals and emits standard OTel spans.
ADOT then exports those spans to CloudWatch.

## Prerequisites

- Python 3.10+
- AWS credentials configured
- Amazon Bedrock model access (Claude Haiku 4.5)
- **CloudWatch Transaction Search enabled** — required before traces appear:
  - IaC: [`05-infrastructure-as-code/01-enable-transaction-search/`](../../../../05-infrastructure-as-code/01-enable-transaction-search/)
  - Console: [CloudWatch > X-Ray settings > Transaction Search](https://console.aws.amazon.com/cloudwatch/home#xray:settings/transaction-search)

## Quick Start

```bash
# 1. Create and activate a virtual environment (recommended for LlamaIndex)
python -m venv venv && source venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create CloudWatch log group and stream
python setup.py

# 4. Configure environment variables
cp .env.example .env
# Edit .env if needed

# 5. Run with ADOT instrumentation (basic)
opentelemetry-instrument python llama_index_agent.py

# 6. Run with session tracking
opentelemetry-instrument python llama_index_agent_with_session.py --session-id "session-001"
```

## Scripts

| Script | Description |
|:-------|:------------|
| `llama_index_agent.py` | Arithmetic FunctionAgent with ADOT auto-instrumentation |
| `llama_index_agent_with_session.py` | Same agent with OTel baggage session tracking |
| `setup.py` | Creates the CloudWatch log group and log stream |

## Key Notes

- `LlamaIndexOpenTelemetry(debug=True)` must be instantiated before the agent is created
- `instrumentor.start_registering()` must be called before invoking the agent
- `OTEL_PYTHON_DISABLED_INSTRUMENTATIONS=jinja2` reduces span noise from LlamaIndex templates
- The agent uses `asyncio.run()` because LlamaIndex FunctionAgent is async

## Additional Resources

- [AgentCore observability — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
- [CloudWatch GenAI observability](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AgentCore-Agents.html)
- [AWS Distro for OpenTelemetry](https://aws-otel.github.io/docs/getting-started/python-sdk)
- [LlamaIndex](https://docs.llamaindex.ai/en/stable/)
