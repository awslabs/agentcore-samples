# Self-Hosted Agent Observability using AgentCore

This section demonstrates AgentCore Observability for popular open-source agent frameworks **not** hosted on Amazon Bedrock AgentCore Runtime. Learn to add comprehensive observability to your existing agents using OpenTelemetry and Amazon CloudWatch.

## Available Frameworks

### CrewAI
- **Notebook**: `CrewAI_Observability.ipynb`
- **Description**: Autonomous AI agents working in teams
- **Features**: Multi-agent collaboration with custom instrumentation

### LangGraph
- **Notebook**: `Langgraph_Observability.ipynb`
- **Description**: Stateful, multi-actor LLM applications
- **Features**: Complex reasoning systems with trace visualization

### LlamaIndex
- **Notebook**: `LlamaIndex_Observability.ipynb`
- **Description**: LLM-powered agents over data
- **Features**: Function agents with session tracking
- **Additional**: Detailed README with architecture diagrams

### Strands Agents
- **Notebook**: `Strands_Observability.ipynb`
- **Description**: Model-driven agentic development
- **Features**: Complex workflow agents with custom spans

## Getting Started

1. Choose your framework directory
2. Install requirements: `pip install -r requirements.txt`
3. Configure AWS credentials
4. Copy `.env.example` to `.env` and update variables
5. Enable CloudWatch Transaction Search
6. Run the Jupyter notebook


## Prerequisites

- AWS account with Bedrock and CloudWatch access with the right permissions
- Python 3.10+
- AWS CloudWatch Transaction Search enabled
- Framework-specific dependencies

## Cleanup

After completing examples:
1. Delete CloudWatch log groups
2. Remove any created AWS resources
3. Clean up local environment files
