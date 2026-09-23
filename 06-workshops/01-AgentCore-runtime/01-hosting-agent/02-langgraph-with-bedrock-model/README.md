# Hosting LangGraph agent with Amazon Bedrock models in Amazon Bedrock AgentCore Runtime

## Overview

In this tutorial we will learn how to host your existing agent, using Amazon Bedrock AgentCore Runtime. 

We will focus on a LangGraph with Amazon Bedrock model example. For Strands Agents with Amazon Bedrock model check [here](../01-strands-with-bedrock-model)
and for a Strands Agents with an OpenAI model check [here](../03-strands-with-openai-model).

### Tutorial Details

| Information         | Details                                                                      |
|:--------------------|:-----------------------------------------------------------------------------|
| Tutorial type       | Conversational                                                               |
| Agent type          | Single                                                                       |
| Agentic Framework   | LangGraph                                                                    |
| LLM model           | Anthropic Claude Haiku 4.5                                                    |
| Tutorial components | Hosting agent on AgentCore Runtime. Using LangGraph and Amazon Bedrock Model |
| Tutorial vertical   | Cross-vertical                                                               |
| Example complexity  | Easy                                                                         |
| SDK used            | Amazon BedrockAgentCore Python SDK and boto3                                 |

### Tutorial Architecture

In this tutorial we will describe how to deploy an existing agent to AgentCore runtime. 

For demonstration purposes, we will  use a LangGraph agent using Amazon Bedrock models

In our example we will use a very simple agent with two tools: `get_weather` and `get_time`. 

<div style="text-align:left">
    <img src="images/architecture_runtime.png" width="100%"/>
</div>

### Tutorial Key Features

* Hosting Agents on Amazon Bedrock AgentCore Runtime
* Using Amazon Bedrock models
* Using LangGraph
