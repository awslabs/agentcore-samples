# AgentCore runtime + AWS Lambda observability

Invoke an AgentCore-hosted Strands MCP agent from an AWS Lambda function with end-to-end
CloudWatch Gen AI observability via the AWS Distro for OpenTelemetry (ADOT) Lambda Layer.

## Architecture

```
User / API gateway
  └── AWS Lambda (lambda_handler.py)
        ├── ADOT Layer → trace context propagation
        ├── X-Ray active tracing
        └── AgentCore runtime (mcp_agent.py)
              ├── AWS Documentation MCP Server
              └── AWS CDK MCP Server
                    └── CloudWatch Gen AI observability
```

The ADOT Lambda Layer automatically injects trace context so that spans from Lambda and
AgentCore runtime appear as a single connected trace in CloudWatch.

## Prerequisites

- Python 3.10+, uv, Docker (not required — uv builds for arm64)
- AWS credentials configured
- Amazon Bedrock Claude model enabled in your account

## Quick Start

```bash
pip install bedrock-agentcore boto3 python-dotenv
python deploy.py     # Phase 1: AgentCore runtime + Phase 2: Lambda function
python invoke.py
python cleanup.py
```

## Files

| File | Description |
|:-----|:------------|
| `utils/mcp_agent.py` | Strands MCP agent (AWS Docs + CDK servers) hosted on AgentCore runtime |
| `utils/lambda_handler.py` | Lambda function handler that invokes the AgentCore runtime |
| `deploy.py` | Deploys AgentCore runtime and creates the Lambda function |
| `invoke.py` | Invokes the Lambda function with test prompts |
| `cleanup.py` | Deletes all created AWS resources |
| `requirements.txt` | Dependencies for the AgentCore runtime agent package |

## How observability Works

### ADOT Lambda Layer
The `AWS_LAMBDA_EXEC_WRAPPER=/opt/otel-instrument` environment variable activates the ADOT
Lambda Layer which auto-instruments the Lambda function. This propagates the W3C Trace Context
header to AgentCore runtime, linking Lambda and runtime spans in the same trace.

### X-Ray Active Tracing
`TracingConfig: Mode=Active` on the Lambda function sends Lambda segment data to X-Ray.

### AgentCore runtime
The runtime sends Gen AI spans to CloudWatch automatically. With trace context propagated
from Lambda, these spans appear as children of the Lambda span in the trace timeline.

## Viewing Traces

After running `invoke.py`:

1. **CloudWatch Gen AI observability**: CloudWatch → Application Signals → Gen AI observability
   - View connected Lambda → AgentCore runtime trace timelines
   - Session view, token usage, tool invocations

2. **X-Ray Service Map**: CloudWatch → X-Ray → Service Map
   - Visualize Lambda → AgentCore runtime topology

3. **Lambda Logs**: CloudWatch → Log groups → `/aws/lambda/<function-name>`

## Additional Resources

- [AWS Lambda ADOT Layer](https://aws-otel.github.io/docs/getting-started/lambda/lambda-python)
- [CloudWatch Gen AI observability](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/GenAI-observability.html)
- [AgentCore observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
