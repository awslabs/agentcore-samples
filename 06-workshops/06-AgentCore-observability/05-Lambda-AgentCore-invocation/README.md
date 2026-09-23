# Lambda AgentCore Invocation with CloudWatch Observability

This tutorial demonstrates how to invoke Strands agents hosted on Amazon Bedrock AgentCore Runtime from AWS Lambda functions, with full CloudWatch Gen AI Observability enabled.

## Overview

Learn how to build a serverless architecture where Lambda functions invoke MCP-enabled agents running on AgentCore Runtime, with complete visibility into both Lambda execution and agent behavior through CloudWatch.

## Project Structure
```
05-Lambda-AgentCore-invocation/
├── agentcore_observability_lambda.ipynb  # Main tutorial notebook
├── lambda_agentcore_invoker.py           # Lambda function code
├── mcp_agent_multi_server.py             # Agent with multiple MCP servers
├── requirements.txt                      # Python dependencies
├── .gitignore                            # Git ignore patterns
└── README.md                             # This file

Note: Dockerfile is generated dynamically in the notebook and not tracked in git.
```

## Tutorial Details

| Information         | Details                                                                          |
|:-------------------|:----------------------------------------------------------------------------------|
| Tutorial type      | Conversational                                                                   |
| Agent type         | Single                                                                           |
| Agentic Framework  | Strands Agents                                                                   |
| LLM model          | Anthropic Claude Haiku 4.5                                                      |
| Tutorial components| Lambda invocation, AgentCore Runtime, MCP servers, CloudWatch Observability     |
| Example complexity | Advanced                                                                         |
| SDK used           | Amazon BedrockAgentCore Python SDK, boto3, AWS Lambda                           |

## Architecture
```
┌─────────┐      ┌────────────────┐      ┌──────────────────┐      ┌─────────────────┐
│   API   │─────>│  AWS Lambda    │─────>│  AgentCore       │─────>│  Strands Agent  │
│  /User  │      │  (Invoker)     │      │  Runtime         │      │  + MCP Servers  │
└─────────┘      └────────────────┘      └──────────────────┘      └─────────────────┘
                        │                         │                          │
                        ▼                         ▼                          ▼
                 ┌─────────────────────────────────────────────────────────────┐
                 │            CloudWatch Observability                         │
                 │       • Gen AI Traces     • Metrics     • Logs              │
                 └─────────────────────────────────────────────────────────────┘
```

## Key Features

* Integrating multiple MCP servers (AWS Documentation + AWS CDK) with Strands Agents
* Hosting agents on Amazon Bedrock AgentCore Runtime
* Invoking hosted agents from AWS Lambda functions
* Configuring CloudWatch Gen AI Observability for comprehensive agent monitoring
* Viewing traces, spans, and metrics in CloudWatch console

## What You'll Learn

1. How to deploy an MCP-enabled agent to AgentCore Runtime
2. How to create a Lambda function that invokes the runtime agent
3. How to enable CloudWatch Gen AI Observability for your agents
4. How to view and analyze traces showing agent execution flow

## Prerequisites

* Python 3.10+
* AWS credentials configured with appropriate permissions
* Amazon Bedrock AgentCore SDK
* Permissions to create Lambda functions and IAM roles
* CloudWatch Transaction Search enabled (see tutorial for setup instructions)

## Getting Started

1. Install the required packages:
```bash
   pip install -r requirements.txt
```

2. Enable CloudWatch Transaction Search (one-time setup per AWS account via console)

3. Open and run the Jupyter notebook:
```bash
   jupyter notebook agentcore_observability_lambda.ipynb
```

4. Follow the step-by-step instructions in the notebook to:
   - Create and deploy the MCP agent
   - Build and deploy the Lambda function
   - Test the integration
   - View traces in CloudWatch

## Components

### Lambda Function (`lambda_agentcore_invoker.py`)
Serverless function that receives user prompts and invokes the AgentCore Runtime agent. Includes error handling and comprehensive logging.

### MCP Agent (`mcp_agent_multi_server.py`)
Strands agent configured with multiple MCP servers (AWS Documentation and AWS CDK) and OpenTelemetry instrumentation for observability.

## Usage

The Lambda function expects the following event format:
```json
{
  "prompt": "Your question here",
  "sessionId": "optional-session-id"
}
```

Response format:
```json
{
  "statusCode": 200,
  "body": {
    "response": "Agent's response",
    "sessionId": "session-id"
  }
}
```

## Observability Features

* **Gen AI Traces**: Visualize complete agent workflow with span timelines
* **CloudWatch Logs**: Detailed logging of Lambda and agent execution
* **Performance Metrics**: Track token usage, duration, and error rates
* **Transaction Search**: Query and analyze traces across your application

## Clean Up

After completing the tutorial, delete the following resources to avoid unnecessary charges:

1. Lambda function and associated IAM roles
2. AgentCore Runtime agent and endpoint
3. CloudWatch Log groups
4. Container images in ECR (if applicable)

## Additional Resources

- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore.html)
- [CloudWatch Gen AI Observability Guide](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/GenAI-observability.html)

## License

This project is licensed under the terms specified in the repository.