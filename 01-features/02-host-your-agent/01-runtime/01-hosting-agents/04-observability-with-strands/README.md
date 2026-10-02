# Hosting a Strands Agent on AgentCore runtime with CloudWatch observability

Deploy a Strands travel agent to Amazon Bedrock AgentCore runtime and observe
its full execution — LLM calls, tool invocations, decision traces — in the
Amazon CloudWatch GenAI observability dashboard.

```
User ──> AgentCore runtime ──> Strands Travel Agent
                │                    │
                │             web_search / get_weather tools
                │
                └──ADOT/OTEL──> X-Ray ──> CloudWatch aws/spans
                                               │
                                               └──> GenAI observability Dashboard
                                                    (Sessions, Traces, Metrics)
```

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- AWS CLI configured with credentials
- Amazon Bedrock model access (Claude Haiku 4.5 in your region)
- **CloudWatch Transaction Search enabled** — see
  [`05-infrastructure-as-code/01-enable-transaction-search/`](../../../../05-infrastructure-as-code/01-enable-transaction-search/)
  or enable via the [AWS Console](https://console.aws.amazon.com/cloudwatch/home#xray:settings/transaction-search)

## Quick Start

```bash
pip install -r requirements.txt

# Deploy the travel agent to AgentCore runtime
python deploy.py

# Send travel queries (generates observable traces)
python invoke.py

# Clean up all AWS resources
python cleanup.py
```

## CLI Commands

Install the AgentCore CLI:

```bash
npm install -g @aws/agentcore@0.30.0
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
```

Deploy with the CLI:

```bash
# Scaffold the project (alphanumeric only, max 23 chars)
agentcore create --name travelobsagent --framework Strands --model-provider Bedrock --defaults

# Copy agent code
cp utils/travel_agent.py travelobsagent/app/travelobsagent/

# Populate aws-targets.json
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=$(aws configure get region)
echo "[{\"name\": \"default\", \"description\": \"Default target\", \"account\": \"$ACCOUNT_ID\", \"region\": \"$REGION\"}]" \
  > travelobsagent/agentcore/aws-targets.json

# Deploy
cd travelobsagent && agentcore deploy -y && agentcore status
```

Invoke:

```bash
agentcore invoke "What are the best beaches in Thailand in December?" --stream
```

## How It Works

### Automatic Instrumentation

When deployed to AgentCore runtime, the agent is launched with the
`opentelemetry-instrument` command (configured automatically by the runtime).
This activates the **AWS Distro for OpenTelemetry (ADOT)** which:

1. Intercepts all Strands agent calls, Bedrock LLM invocations, and tool executions
2. Creates OpenTelemetry spans for each operation
3. Exports spans via OTLP to X-Ray, which routes them to CloudWatch `aws/spans` log group

No code changes are required — observability is enabled purely by the deployment configuration.

### Viewing Traces

After invoking the agent:

1. Open [CloudWatch > GenAI observability > Bedrock AgentCore](https://console.aws.amazon.com/cloudwatch/home#genai:observability)
2. Find your agent by the service name (`strands-travel-agent`)
3. Click **Sessions** to see individual conversations
4. Click **Traces** to see the span hierarchy: invoke → tool calls → LLM responses

### Adding Session Correlation

Pass a `session_id` in the invocation payload to group related traces:

```python
client.invoke_agent_runtime(
    agentRuntimeArn=runtime_arn,
    runtimeSessionId="user-session-abc123",  # correlates traces
    payload=json.dumps({"prompt": "..."}).encode(),
)
```

### Custom Trace Attributes

Add custom metadata to traces for filtering and analysis:

```python
agent = Agent(
    model=model,
    tools=[web_search, get_weather],
    trace_attributes={
        "user.id": "user@example.com",
        "experiment.id": "v2-travel-agent",
        "tags": ["production", "travel"],
    },
)
```

## Files

| File | Description |
|:-----|:------------|
| `deploy.py` | Deploys the travel agent to AgentCore runtime (IAM, S3, runtime, endpoint) |
| `invoke.py` | Sends travel queries to the deployed agent |
| `cleanup.py` | Deletes all AWS resources created by deploy.py |
| `requirements.txt` | Python dependencies including `aws-opentelemetry-distro` |
| `utils/travel_agent.py` | Strands travel agent with web_search and get_weather tools |

## Sample Prompts

```bash
python invoke.py "What are the top travel destinations in Southeast Asia?"
python invoke.py "What is the weather like in Barcelona in October?"
python invoke.py "Recommend a 5-day itinerary for Tokyo, Japan."
```

## Additional Resources

- [AgentCore observability — Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
- [View Agent Data in CloudWatch](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-view.html)
- [CloudWatch GenAI observability — User Guide](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AgentCore-Agents.html)
- [AWS Distro for OpenTelemetry Python](https://aws-otel.github.io/docs/getting-started/python-sdk)
- [Strands Agents observability](https://strandsagents.com/latest/documentation/docs/user-guide/observability-evaluation/observability/)
- [OpenTelemetry GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
