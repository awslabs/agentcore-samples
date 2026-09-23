# AgentCore Observability on Amazon CloudWatch for Bedrock AgentCore Runtime Agents 

This repository contains examples to showcase AgentCore Observability for Strands, CrewAI, and LlamaIndex agents hosted on Amazon Bedrock AgentCore Runtime using Amazon OpenTelemetry Python Instrumentation and Amazon CloudWatch. Observability helps developers trace, debug, and monitor agent performance in production through unified operational dashboards. With support for OpenTelemetry compatible telemetry and detailed visualizations of each step of the agent workflow, Amazon CloudWatch GenAI Observability enables developers to easily gain visibility into agent behavior and maintain quality standards at scale.

## Framework Examples

### Strands Agents
[Strands](https://strandsagents.com/latest/) provides a framework for building LLM applications with complex workflows, focusing on model-driven agentic development.

**Location**: `Strands Agents/`
- Tutorial: `runtime_with_strands_and_bedrock_models.ipynb`
- Features: Weather and calculator tools with Amazon Bedrock models

### CrewAI
[CrewAI](https://www.crewai.com/) enables multi-agent collaboration with role-based agent orchestration.

**Location**: `CrewAI/`
- Tutorial: `runtime-with-crewai-and-bedrock-models.ipynb`
- Features: Collaborative agent patterns

### LlamaIndex
[LlamaIndex](https://www.llamaindex.ai/) provides data framework for LLM applications with advanced retrieval and reasoning capabilities.

**Location**: `LlamaIndex/`
- Tutorial: `runtime_with_llamaindex_and_bedrock_models.ipynb`
- Features: FunctionAgent with arithmetic tools and comprehensive observability

## Getting Started

Each framework folder contains:
- A Jupyter notebook demonstrating AgentCore Runtime deployment and CloudWatch observability
- A requirements.txt file listing necessary dependencies
- A README.md with framework-specific instructions

## Usage

1. Navigate to the directory of the framework you want to explore
2. Install the requirements: `pip install -r requirements.txt`
3. Configure your AWS credentials 
4. Open and run the Jupyter notebook

## Key Features

- **Automatic Observability**: Built-in telemetry collection when agents run on AgentCore Runtime
- **CloudWatch Integration**: View traces and metrics in GenAI Observability dashboard
- **Framework Flexibility**: Support for multiple agentic frameworks

## Cleanup

After completing the examples:

1. Remove AgentCore Runtime deployments
2. Clean up any created ECR repositories
3. Delete CloudWatch log groups if no longer needed