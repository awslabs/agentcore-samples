# LlamaIndex Agent with Amazon Bedrock AgentCore Runtime and Observability

This tutorial demonstrates how to deploy a [LlamaIndex agent](https://developers.llamaindex.ai/python/framework/use_cases/agents/) to Amazon Bedrock AgentCore Runtime with comprehensive observability and telemetry collection.

## Overview

Learn how to:
- Create a LlamaIndex FunctionAgent with arithmetic tools
- Deploy the agent to AgentCore Runtime with automatic observability
- Capture detailed telemetry data including agent workflows, tool calls, and LLM interactions
- View traces and metrics in Amazon CloudWatch GenAI Observability dashboard

## What You'll Build

A LlamaIndex arithmetic agent that can:
- Perform addition and multiplication operations using function tools
- Run on Amazon Bedrock AgentCore Runtime with built-in scalability
- Automatically generate comprehensive observability data
- Be monitored through CloudWatch dashboards with detailed trace information

## Key Features

- **LlamaIndex Integration**: Uses LlamaIndex FunctionAgent with async workflows
- **Automatic Observability**: Built-in telemetry collection with LlamaIndex OpenTelemetry instrumentation
- **CloudWatch Integration**: View agent performance in GenAI Observability dashboard

## Prerequisites

- AWS account with appropriate permissions
- Amazon Bedrock model access (Claude Haiku)
- Python 3.10+
- AWS credentials configured
- Enable [transaction search](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Enable-TransactionSearch.html) on Amazon CloudWatch

## Quick Start

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Run the notebook:
   ```bash
   jupyter notebook runtime_with_llamaindex_and_bedrock_models.ipynb
   ```

3. Follow the step-by-step tutorial to deploy your agent with observability

## Architecture

The tutorial covers:
- Local development and testing with LlamaIndex instrumentation
- AgentCore Runtime deployment with automatic observability
- CloudWatch dashboard access for trace analysis
- Manual span creation for enhanced telemetry

## Files

- `runtime_with_llamaindex_and_bedrock_models.ipynb` - Main tutorial notebook
- `requirements.txt` - Python dependencies including LlamaIndex observability
- `README.md` - This documentation

## Observability Features

- **Agent Workflow Traces**: Complete execution flow of LlamaIndex FunctionAgent
- **Tool Call Monitoring**: Track arithmetic function invocations
- **LLM Interaction Traces**: Bedrock model calls with input/output tracking

## Next Steps

After completing this tutorial, you can:
- Add more complex tools and workflows to your LlamaIndex agent
- Implement multi-agent architectures with detailed observability
- Set up custom alerts and monitoring based on trace data
- Scale your agent for production workloads with full visibility