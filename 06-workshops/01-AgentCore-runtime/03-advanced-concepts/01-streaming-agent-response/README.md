# Streaming Responses with Strands Agents and Amazon Bedrock models in Amazon Bedrock AgentCore Runtime

## Overview

In this tutorial we will learn how to implement streaming responses using Amazon Bedrock AgentCore Runtime with your existing agents. 

We will focus on a Strands Agents with Amazon Bedrock model example that demonstrates real-time streaming capabilities. 

### Tutorial Details

| Information         | Details                                                                          |
|:--------------------|:---------------------------------------------------------------------------------|
| Tutorial type       | Conversational with Streaming                                                    |
| Agent type          | Single                                                                           |
| Agentic Framework   | Strands Agents                                                                   |
| LLM model           | Anthropic Claude Haiku 4.5                                                      |
| Tutorial components | Streaming responses with AgentCore Runtime. Using Strands Agent and Amazon Bedrock Model |
| Tutorial vertical   | Cross-vertical                                                                   |
| Example complexity  | Easy                                                                             |
| SDK used            | Amazon BedrockAgentCore Python SDK and boto3                                     |

### Tutorial Architecture

In this tutorial we will describe how to deploy a streaming agent to AgentCore runtime. 

For demonstration purposes, we will use a Strands Agent using Amazon Bedrock models with streaming capabilities.

In our example we will use a simple agent with three tools: `get_weather`, `get_time`, and `calculator`, but enhanced with real-time streaming response capabilities.

<div style="text-align:left">
    <img src="images/architecture_runtime.png" width="100%"/>
</div>

### Tutorial Key Features

* Implementing streaming responses on Amazon Bedrock AgentCore Runtime
* Real-time partial result delivery using Server-Sent Events (SSE)
* Using Amazon Bedrock models with streaming capabilities
* Using Strands Agents with async streaming support
* Enhanced user experience with progressive response display
