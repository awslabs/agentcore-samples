# AgentCore observability for EKS-Hosted Agent

Deploy a Strands Travel Agent to Amazon EKS with CloudWatch Gen AI observability via the AWS
Distro for OpenTelemetry (ADOT).

## Architecture

```
User
  └── kubectl port-forward / ALB
        └── EKS Pod: opentelemetry-instrument python app.py
              └── Strands Travel Agent (FastAPI)
                    ├── web_search, get_climate_data, search_flight_info
                    ├── convert_currency, calculate_trip_budget
                    └── ADOT → CloudWatch Gen AI observability
```

The agent runs as a FastAPI service inside a Docker container. The Dockerfile uses `opentelemetry-instrument`
to auto-instrument the application. OTEL traces are exported to CloudWatch using the `aws_distro`
configurator, which sends Gen AI spans to the AgentCore observability pipeline.

## Prerequisites

- [AWS CLI](https://aws.amazon.com/cli/) installed and configured
- [eksctl](https://eksctl.io/installation/) (v0.208+) installed
- [Helm](https://helm.sh/) (v3+) installed
- [kubectl](https://docs.aws.amazon.com/eks/latest/userguide/install-kubectl.html) installed
- Docker installed and running
- Amazon Bedrock Claude model enabled in your account
- CloudWatch Transaction Search enabled (one-time per account/region setup)

## Quick Start

```bash
# 1. Configure deployment
cp config.env.example config.env
# Edit config.env with your preferred cluster name, region, etc.

# 2. Deploy (takes ~20-30 min including EKS cluster creation)
bash deploy.sh

# 3. Start port-forward and test
kubectl port-forward service/strands-agents-travel 8080:80 &
python invoke.py

# 4. Clean up all resources
bash cleanup.sh
```

## Files

| File | Description |
|:-----|:------------|
| `docker/app/app.py` | Strands travel agent as FastAPI service |
| `docker/Dockerfile` | Container with ADOT auto-instrumentation |
| `docker/requirements.txt` | Python dependencies for the container |
| `chart/` | Helm chart for Kubernetes deployment |
| `config.env.example` | Configuration template |
| `deploy.sh` | Full deployment: CloudWatch → EKS → ECR → IAM → Helm |
| `invoke.py` | HTTP test against the deployed service |
| `cleanup.sh` | Delete all AWS resources |

## How observability Works

The Dockerfile sets these environment variables to activate ADOT CloudWatch export:

```dockerfile
ENV OTEL_PYTHON_DISTRO=aws_distro
ENV OTEL_PYTHON_CONFIGURATOR=aws_configurator
ENV OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
ENV OTEL_TRACES_EXPORTER=otlp
ENV AGENT_OBSERVABILITY_ENABLED=true
ENV OTEL_RESOURCE_ATTRIBUTES=service.name=strands-agents-travel
ENV OTEL_EXPORTER_OTLP_LOGS_HEADERS=x-aws-log-group=<log-group>,x-aws-log-stream=<log-stream>,...
```

`opentelemetry-instrument` (the CMD entrypoint) auto-instruments the FastAPI + Strands application,
creating Gen AI spans for each agent invocation and tool call.

## Viewing Traces

After running `invoke.py`:

1. **CloudWatch Gen AI observability**: CloudWatch → Application Signals → Gen AI observability
   - Agent invocations with session IDs
   - Tool call timelines (web_search, get_climate_data, etc.)
   - Token usage and latency metrics

2. **CloudWatch Logs**: Log group `/strands-agents/travel`
   - Raw agent execution logs

## Optional: CloudWatch observability Addon

The CloudWatch observability addon is **not required** for AgentCore observability — the agent
sends telemetry directly via ADOT. Install the addon only if you need additional
Kubernetes-level metrics (pod CPU/memory, node health, etc.):

```bash
eksctl create podidentityassociation --cluster $CLUSTER_NAME \
  --namespace amazon-cloudwatch \
  --service-account-name cloudwatch-agent \
  --permission-policy-arns arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy \
  --role-name eks-cloudwatch-agent \
  --region $AWS_REGION

aws eks create-addon \
  --addon-name amazon-cloudwatch-observability \
  --cluster-name $CLUSTER_NAME \
  --region $AWS_REGION
```

## Additional Resources

- [Amazon EKS Auto Mode](https://docs.aws.amazon.com/eks/latest/userguide/automode.html)
- [AWS Distro for OpenTelemetry](https://aws-otel.github.io/)
- [AgentCore observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html)
- [CloudWatch Gen AI observability](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/GenAI-observability.html)
