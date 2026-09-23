# Auto-Register Bedrock Knowledge Bases on AgentCore Gateway

## Overview

This tutorial demonstrates how to build a self-registering Knowledge Base gateway using Amazon Bedrock AgentCore Gateway. When a new Bedrock Knowledge Base is created anywhere in the account, it is automatically discovered and exposed as an MCP tool on the gateway — no manual configuration needed.

The system uses EventBridge to detect Knowledge Base lifecycle events (create/delete) via CloudTrail, and a Lambda function to update the gateway's tool schema in real time.

![Architecture](images/architecture.png)

## Tutorial Details

| Information          | Details                                                                  |
|:---------------------|:-------------------------------------------------------------------------|
| Tutorial type        | Interactive                                                              |
| AgentCore components | AgentCore Gateway                                                        |
| Agentic Framework    | Strands Agents                                                           |
| Gateway Target type  | Lambda                                                                   |
| Agent                | Strands                                                                  |
| Inbound Auth IdP     | Amazon Cognito                                                           |
| Outbound Auth        | N/A (Lambda target uses IAM)                                             |
| LLM model            | Anthropic Claude Sonnet 4                                                |
| Tutorial components  | Creating AgentCore Gateway, Lambda targets, EventBridge auto-registration |
| Tutorial vertical    | Knowledge Management                                                     |
| Example complexity   | Intermediate                                                             |
| SDK used             | boto3                                                                    |

## Key Features

* Deploy a Lambda-backed AgentCore Gateway that routes queries to multiple Bedrock Knowledge Bases
* Automatically register new Knowledge Bases as gateway tools via EventBridge + CloudTrail
* Automatically unregister deleted Knowledge Bases
* Query Knowledge Bases through a Strands Agent connected to the gateway
* Create test Knowledge Bases with S3 Vectors storage

## Prerequisites

1. AWS credentials configured (`aws configure`) with permissions to create AgentCore resources, Lambda functions, IAM roles, EventBridge rules, and Cognito user pools
2. CloudTrail enabled in the account (required for EventBridge to detect KB lifecycle events)
3. Bedrock model access enabled (Claude Sonnet 4, Titan Embeddings v2)
4. Python 3.10+

## Tutorial

- [Auto-Register Bedrock Knowledge Bases on AgentCore Gateway](bedrock_kb_auto_register_gateway.ipynb)

## Resources

- [Amazon Bedrock Knowledge Bases](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base.html)
- [Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [Amazon EventBridge](https://docs.aws.amazon.com/eventbridge/latest/userguide/what-is-amazon-eventbridge.html)
- [AgentCore Gateway tutorials](https://github.com/awslabs/amazon-bedrock-agentcore-samples/tree/main/06-workshops/02-AgentCore-gateway)
- [Strands Agents](https://strandsagents.com/)
