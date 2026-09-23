<div align="center">
  <div>
    <a href="https://aws.amazon.com/bedrock/agentcore/">
      <img width="150" height="150" alt="image" src="https://github.com/user-attachments/assets/b8b9456d-c9e2-45e1-ac5b-760f21f1ac18" />
   </a>
  </div>

  <h1>
      Amazon Bedrock AgentCore Samples
  </h1>

  <h2>
    Deploy and operate AI agents securely at scale - using any framework and model
  </h2>

  <div align="center">
    <a href="https://github.com/awslabs/amazon-bedrock-agentcore-samples/graphs/commit-activity"><img alt="GitHub commit activity" src="https://img.shields.io/github/commit-activity/m/awslabs/amazon-bedrock-agentcore-samples"/></a>
    <a href="https://github.com/awslabs/amazon-bedrock-agentcore-samples/issues"><img alt="GitHub open issues" src="https://img.shields.io/github/issues/awslabs/amazon-bedrock-agentcore-samples"/></a>
    <a href="https://github.com/awslabs/amazon-bedrock-agentcore-samples/pulls"><img alt="GitHub open pull requests" src="https://img.shields.io/github/issues-pr/awslabs/amazon-bedrock-agentcore-samples"/></a>
    <a href="https://github.com/awslabs/amazon-bedrock-agentcore-samples/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/github/license/awslabs/amazon-bedrock-agentcore-samples"/></a>
  </div>

  <p>
    <a href="https://docs.aws.amazon.com/bedrock-agentcore/">Documentation</a>
    ◆ <a href="https://github.com/aws/bedrock-agentcore-sdk-python">Python SDK</a>
    ◆ <a href="https://github.com/aws/agentcore-cli">AgentCore CLI</a>
    ◆ <a href="https://discord.gg/strands">Discord</a>
  </p>
</div>

Welcome to the Amazon Bedrock AgentCore Samples repository!

Amazon Bedrock AgentCore is both framework-agnostic and model-agnostic, giving you the flexibility to deploy and operate advanced AI agents securely and at scale. Whether you’re building with [Strands Agents](https://strandsagents.com/latest/), [CrewAI](https://www.crewai.com/), [LangGraph](https://www.langchain.com/langgraph), [LlamaIndex](https://www.llamaindex.ai/), or any other framework—and running them on any Large Language Model (LLM)—Amazon Bedrock AgentCore provides the infrastructure to support them. By eliminating the undifferentiated heavy lifting of building and managing specialized agent infrastructure, Amazon Bedrock AgentCore lets you bring your preferred framework and model, and deploy without rewriting code.

This collection provides examples and tutorials to help you understand, implement, and integrate Amazon Bedrock AgentCore capabilities into your applications.

> **Migrating from the Starter Toolkit?** This repository is transitioning from the [Bedrock AgentCore Starter Toolkit](https://github.com/aws/bedrock-agentcore-starter-toolkit) to the new [AgentCore CLI](https://github.com/aws/agentcore-cli). Samples that still depend on the Starter Toolkit are in [`legacy/`](./legacy/) and will be updated over the coming weeks. See [`MIGRATION.md`](./MIGRATION.md) for the full old-path to new-path mapping.

## 🎥 Video

Build your first production-ready AI agent with Amazon Bedrock AgentCore. We’ll take you beyond prototyping and show you how to productionize your first agentic AI application using Amazon Bedrock AgentCore.

<p align="center">
  <a href="https://www.youtube.com/watch?v=wzIQDPFQx30"><img src="https://markdown-videos-api.jorgenkh.no/youtube/wzIQDPFQx30?width=640&height=360&filetype=jpeg" /></a>
</p>

## 📁 Repository Structure

### 🚀 [`getting-started/`](./getting-started/)

**Your First Agent in Minutes**

Get up and running with the [AgentCore CLI](https://github.com/aws/agentcore-cli) — the fastest way to create, develop, and deploy agents on Amazon Bedrock AgentCore.

- **[`python/`](./getting-started/python/)** — Python agent samples (Code Interpreter, Gateway, Memory, Identity, and more)
- **[`typescript/`](./getting-started/typescript/)** — TypeScript agent samples

### 🧩 [`features/`](./features/)

**AgentCore Capabilities Deep Dives**

Focused examples for individual AgentCore capabilities:

- **[Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)** — Secure, serverless runtime for deploying agents and tools at scale
- **[Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)** — Convert APIs, Lambda functions, and services into MCP-compatible tools
- **[Identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html)** — Agent identity and access management across AWS and third-party apps
- **[Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html)** — Managed memory infrastructure for personalized agent experiences
- **[Tools](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-tool.html)** — Built-in Code Interpreter, Browser Tool, Web Search Tool
- **[Observability](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability.html)** — Trace, debug, and monitor agent performance with OpenTelemetry
- **[Evaluation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations.html)** — Built-in and custom evaluators for on-demand and online evaluation
- **[Policy](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html)** — Fine-grained access control with Cedar policies

### 💡 [`end-to-end/`](./end-to-end/)

**Complete Applications**

Production-ready use cases that combine multiple AgentCore capabilities to solve real business problems. Each includes deployment instructions, architecture diagrams, and testing guides.

### 🔌 [`integrations/`](./integrations/)

**Connect AgentCore to Your Stack**

- **[`identity-providers/`](./integrations/identity-providers/)** — Okta, Entra, Cognito, and other IdP integrations
- **[`observability/`](./integrations/observability/)** — Grafana, Datadog, Dynatrace, and other monitoring platforms
- **[`data-platforms/`](./integrations/data-platforms/)** — Data lake, warehouse, and analytics integrations
- **[`ux-examples/`](./integrations/ux-examples/)** — Streamlit, AG-UI, and other frontend patterns

### 🏗️ [`infrastructure-as-code/`](./infrastructure-as-code/)

**Deployment Automation**

Production-ready templates for provisioning AgentCore resources with CloudFormation, AWS CDK, or Terraform.

### 🚀 [`blueprints/`](./blueprints/)

**Full-Stack Reference Applications**

Complete, deployment-ready agentic applications with integrated services, authentication, and business logic you can customize for your use case.

### 📦 [`workshops/`](./06-workshops/) - Deprecated legacy code

⚠️ Recommendation: The Starter Toolkit CLI is no longer supported. Please use the AgentCore CLI.

The AgentCore CLI (@aws/agentcore) is now the recommended way to create, develop, and deploy AI agents on Amazon Bedrock AgentCore. It supports a broader set of frameworks (Strands, LangGraph, LangChain, Google ADK, OpenAI Agents, and BYO), provides local development with hot reload, built-in evaluations, gateway management, and more.

For new projects, install the AgentCore CLI:

npm install -g @aws/agentcore
Already installed this toolkit? Once you've migrated, uninstall it:

pip uninstall bedrock-agentcore-starter-toolkit


**Starter Toolkit Samples (Migration complete)**

Please refer to this folder for [`Diving Deep  with AgentCore Workshop/`](./01-features/)

## Quick Start with the AgentCore CLI

The [AgentCore CLI](https://github.com/aws/agentcore-cli) is the recommended way to create, develop, and deploy agents on Amazon Bedrock AgentCore. It replaces the previous Starter Toolkit with a streamlined project-based workflow.

### Step 1: Prerequisites

- An [AWS account](https://signin.aws.amazon.com/signin?redirect_uri=https%3A%2F%2Fportal.aws.amazon.com%2Fbilling%2Fsignup%2Fresume&client_id=signup) with credentials configured (`aws configure`)
- [Node.js 20.x](https://nodejs.org/) or later
- [`uv`](https://docs.astral.sh/uv/) (for Python agents) or Node.js (for TypeScript agents)
- Model Access: Anthropic Claude 4.0 enabled in [Amazon Bedrock console](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access-modify.html)
- AWS Permissions:
  - `BedrockAgentCoreFullAccess` managed policy
  - `AmazonBedrockFullAccess` managed policy

### Step 2: Install the CLI and Create a Project

```bash
# Install the AgentCore CLI
npm install -g @aws/agentcore

# Create a new project (interactive wizard)
agentcore create
cd my-agent
```

The `create` wizard scaffolds a ready-to-run project with your choice of framework (Strands Agents, LangGraph, Google ADK, OpenAI, and more) and language (Python or TypeScript).

### Step 3: Develop Locally

```bash
# Start the local development server
agentcore dev
```

Your agent is now running locally. The CLI watches for file changes and provides a local invocation endpoint for testing.

### Step 4: Deploy to AWS

```bash
# Deploy to Amazon Bedrock AgentCore
agentcore deploy

# Test your deployed agent
agentcore invoke
```

### Add More Capabilities

```bash
agentcore add memory           # Add managed memory
agentcore add identity         # Add identity provider
agentcore add evaluator        # Add LLM-as-a-Judge evaluation
agentcore add online-eval      # Enable continuous evaluation
agentcore deploy               # Sync changes to AWS
```

Congratulations! Your agent is now running on Amazon Bedrock AgentCore runtime.

For the full CLI reference, see the [AgentCore CLI documentation](https://github.com/aws/agentcore-cli).

## Running a Notebook

Some samples in this repository are provided as Jupyter notebooks:

1. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

2. Install dependencies

```bash
pip install -r requirements.txt
```

3. Export/Activate required AWS Credentials for the notebook to run

4. Register your virtual environment as a kernel for Jupyter notebook to use

```bash
python -m ipykernel install --user --name=notebook-venv --display-name="Python (notebook-venv)"
```

You can list your kernels using:

```bash
jupyter kernelspec list
```

5. Run the notebook and ensure the correct kernel is selected

```bash
jupyter notebook path/to/your/notebook.ipynb
```

**Important:** After opening the notebook in Jupyter, make sure to select the correct kernel by going to `Kernel` → `Change kernel` → select "Python (notebook-venv)" to ensure your virtual environment packages are available.

## 🔗 Related Links

- [AgentCore CLI](https://github.com/aws/agentcore-cli)
- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock-agentcore/)
- [Getting started with Amazon Bedrock AgentCore - Workshop](https://catalog.workshops.aws/agentcore-getting-started/en-US)
- [Diving Deep into Bedrock AgentCore - Workshop](https://catalog.workshops.aws/agentcore-deep-dive/en-US)
- [Amazon Bedrock AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/)
- [Amazon Bedrock AgentCore FAQs](https://aws.amazon.com/bedrock/agentcore/faqs/)

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guidelines](CONTRIBUTING.md) for details on:

- Adding new samples
- Improving existing examples
- Reporting issues
- Suggesting enhancements

## 📄 License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## Contributors

<a href="https://github.com/awslabs/amazon-bedrock-agentcore-samples/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=awslabs/amazon-bedrock-agentcore-samples" />
</a>
