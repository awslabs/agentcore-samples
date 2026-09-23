# Harness in AgentCore — Samples

Welcome to harness in AgentCore samples. 

In this folder there're samples with: Jupyter notebooks, CLI scripts, and apps for harness in Amazon Bedrock AgentCore.

For more information, visit [AWS documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness.html).

## Prerequisites

- AWS account with access to Amazon Bedrock AgentCore
- AWS CLI v2 configured with credentials
- Python 3.10+

Install dependencies:

```bash
pip install -r requirements.txt

# or
uv pip install -r requirements.txt
```

## 00 — Getting Started

Open [00-getting-started](00-getting-started) folder. It has a step-by-step on the [README](00-getting-started/README.md) file with instructions on how to get started with agentcore-cli and there is a [01_getting_started_bedrock](00-getting-started/01_getting_started_bedrock.ipynb) notebook with aws SDK for Python (boto3).

## 01 — Advanced Examples

Advanced examples showing specific configurations like VPC, parameters, and integrations. Each example lives in its own subfolder.

## 02 — Use Cases

Use-cases that can be solved with harness in AgentCore.

## IAM Permissions

Each example needs an IAM execution role (`HarnessExecutionRole`) with the following permissions:

| Permission | Purpose |
|---|---|
| `bedrock:InvokeModel`, `bedrock:InvokeModelWithResponseStream` | Model invocation (Claude, Llama, etc.) |
| `ecr:GetDownloadUrlForLayer`, `ecr:BatchGetImage`, `ecr:BatchCheckLayerAvailability`, `ecr:GetAuthorizationToken` | Pull custom container images from ECR |
| `ecr-public:GetAuthorizationToken`, `sts:GetServiceBearerToken` | Pull from public ECR |
| `xray:PutTraceSegments`, `xray:PutTelemetryRecords` | AgentCore Observability traces |
| `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | CloudWatch logs |
| `bedrock-agentcore:*Memory*`, `bedrock-agentcore:*Browser*`, etc. | AgentCore features (Memory, Browser, Gateway, CodeInterpreter) |

The role uses a trust policy that allows `bedrock-agentcore.amazonaws.com` to assume it. See [`helper/iam.py`](helper/iam.py) for the full policy documents.

## Cleanup

**Important: delete resources when you're done testing to avoid charges.**

Each notebook includes cleanup cells at the bottom. The CLI script cleans up automatically unless you pass `--skip-cleanup`.

```bash
# List all harnesses
aws bedrock-agentcore-control list-harnesses --region <your-region>

# Delete a specific Harness
aws bedrock-agentcore-control delete-harness --region <your-region> \
    --harness-id <HARNESS_ID>

```
