# Hosting Spring AI Agents with Amazon Bedrock models in Amazon Bedrock AgentCore Runtime

## Overview

In this tutorial we will learn how to host a Java/Spring AI agent on Amazon Bedrock AgentCore Runtime.

This is the Java equivalent of the Python [Strands with Bedrock model](../../01-strands-with-bedrock-model) tutorial.

### Tutorial Details

| Information         | Details                                                                                  |
|:--------------------|:-----------------------------------------------------------------------------------------|
| Tutorial type       | Hosting Tools                                                                            |
| Agent type          | Single                                                                                   |
| Agentic Framework   | Spring AI                                                                                |
| LLM model           | Anthropic Claude Haiku 4.5                                                               |
| Tutorial components | Hosting agent on AgentCore Runtime. Using Spring AI ChatClient and Amazon Bedrock Model  |
| Tutorial vertical   | Cross-vertical                                                                           |
| Example complexity  | Easy                                                                                     |
| SDK used            | spring-ai-agentcore-runtime-starter (Java) and AWS CDK                                   |

### Library

This tutorial uses the [spring-ai-agentcore](https://github.com/spring-ai-community/spring-ai-agentcore) community library, a Spring Boot starter that auto-configures AgentCore Runtime endpoints and provides the `@AgentCoreInvocation` annotation.

### Tutorial Key Features

* First Java-based tutorial in the AgentCore samples repository
* Hosting Spring Boot agents on Amazon Bedrock AgentCore Runtime
* Using Spring AI `ChatClient` with Amazon Bedrock models
* `@AgentCoreInvocation` annotation as the Java equivalent of Python's `@app.entrypoint`
* Corretto 21 Docker image
* CDK infrastructure with `CfnRuntime` L1 construct

## Prerequisites

* Java 21 (Amazon Corretto recommended)
* Maven 3.9+
* Docker
* Node.js 18+ and npm (for CDK)
* AWS CLI configured with appropriate credentials
* AWS CDK CLI (`npm install -g aws-cdk`)

## Project Structure

```
01-springai-with-bedrock-model/
├── README.md
├── agent/
│   ├── pom.xml
│   ├── Dockerfile
│   ├── build-and-push.sh
│   └── src/main/
│       ├── java/com/example/agent/AgentApplication.java
│       └── resources/application.yml
└── infra/
    ├── bin/app.ts
    ├── lib/agentcore-stack.ts
    ├── package.json
    ├── tsconfig.json
    └── cdk.json
```

## Step-by-Step Deployment

### 1. Install CDK dependencies

```bash
cd infra
npm install
```

### 2. Deploy ECR repository (first run)

```bash
cdk deploy -c firstRun=true
```

Note the `EcrRepositoryUri` from the output.

### 3. Build and push the Docker image

```bash
cd ../agent
chmod +x build-and-push.sh
./build-and-push.sh -r us-east-1 -u <EcrRepositoryUri>
```

### 4. Deploy the full stack

```bash
cd ../infra
cdk deploy
```

### 5. Invoke the agent

```bash
RUNTIME_ARN=$(aws cloudformation describe-stacks \
  --stack-name AgentCoreStack \
  --query 'Stacks[0].Outputs[?OutputKey==`AgentRuntimeArn`].OutputValue' \
  --output text)

aws bedrock-agentcore invoke-agent-runtime \
  --agent-runtime-arn "$RUNTIME_ARN" \
  --cli-binary-format raw-in-base64-out \
  --content-type "application/json" \
  --payload '{"message": "What is Amazon Bedrock AgentCore?"}' \
  /dev/stdout
```

## How It Works

The agent is a single Spring Boot application with one class:

1. `AgentApplication` — boots Spring and defines a `ConversationalAgent` inner service
2. `@AgentCoreInvocation` — marks the `chat()` method as the AgentCore Runtime entry point
3. `spring-ai-agentcore-runtime-starter` — auto-configures `/invoke` and `/ping` endpoints

The CDK stack provisions:
- ECR repository for the container image
- IAM role with Bedrock InvokeModel and CloudWatch Logs permissions
- `CfnRuntime` resource pointing to the ECR image

## Cleanup

```bash
cd infra
cdk destroy
```
