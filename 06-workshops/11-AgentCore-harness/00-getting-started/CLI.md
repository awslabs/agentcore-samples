# Getting Started with AgentCore CLI

[AgentCore CLI](https://github.com/aws/agentcore-cli/) helps you create, develop locally, and deploy agents to AgentCore with minimal configuration.

## Prerequisites

To get started, you will need:

- Node.js 20.x or later
- uv for Python agents ([install](https://docs.astral.sh/uv/getting-started/installation/))

Then, **install agentcore-cli**:

```Bash
npm i -g @aws/agentcore@0.30.0

# Verify
agentcore --version
```

## Create and Invoke an Agent using Bedrock model provider

In this step-by-step tutorial, you will create a simple agent, with bedrock model provider.

To create your project:

```bash
agentcore create --name HarnessBedrock --memory "none" --model-provider bedrock

```

To deploy it:
```bash
cd HarnessBedrock

agentcore deploy
```

After, you can test it with `invoke` command:
```bash
agentcore invoke --harness HarnessBedrock "What is 2+2?"

```

## Create and Invoke an Agent using OpenAI model provider

In this step-by-step tutorial, you will create a simple agent, with openAI model provider.

Firstly, create a Secret in Secrets Manager to store OpenAI API Key:

```bash
SECRET_ARN=$(aws secretsmanager create-secret \
    --name "openai-api-key" \
    --description "OpenAI API Key" \
    --secret-string "sk-your-api-key-here" \
    --query 'ARN' \
    --output text)


```

To create your project:

```bash

agentcore create --name HarnessOpenAI --memory "none" --model-provider OpenAI --api-key-arn $SECRET_ARN

```

To deploy it:
```bash

cd HarnessOpenAI

agentcore deploy
```

After, you can test it with `invoke` command:
```bash

agentcore invoke --harness HarnessOpenAI "Who are you? and what is 2+2?"

```