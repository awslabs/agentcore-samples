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

> **Starter Toolkit retirement:** For a new project, follow the [CLI v1 getting-started sample](./00-getting-started/). The [migration guide](./MIGRATION.md) and some existing Harness labs target CLI v0; follow their version-specific prerequisites rather than mixing v0 and v1 commands. Some older examples still use Starter Toolkit; check the selected sample’s setup rather than installing it repository-wide. `bedrock-agentcore` is the supported Python Runtime SDK, distinct from both the deprecated Toolkit and the Node.js [AgentCore CLI](https://github.com/aws/agentcore-cli).

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

## Quick start with the AgentCore CLI

Follow [Build a customer-support agent](./00-getting-started/README.md) for one complete path: install the tested CLI version without replacing your global installation, run the offline tests, try the agent locally, deploy it in a sandbox, and clean up. Local model calls and deployed resources can incur charges. Check the sample’s prerequisites and verify the account before deployment; don’t grant broad administrator or model permissions just to follow an example.

Already using CDK, CloudFormation, Terraform or direct service APIs? Use the setup that matches your chosen sample. A working infrastructure-as-code example does not need an additional CLI project.

## Running a notebook

Use a **separate environment per sample**. Start from the notebook’s README, not a repository-wide collection of frameworks. The root `requirements.txt` installs only JupyterLab and its Python kernel; it does not install the Runtime SDK, model providers or deployment tooling.

For a sample that uses pip (Bash, from the repository root):

```bash
repo=$PWD
cd path/to/the/notebook-directory  # replace with the actual directory
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r "$repo/requirements.txt"  # optional notebook UI and kernel
# Install the requirements specified by THIS sample, e.g. when it has a requirements.txt:
python -m pip install -r requirements.txt
jupyter lab
```

If the example uses uv and a lockfile, follow its frozen install instructions instead. In Jupyter, select the kernel for that sample environment. Read setup, cost and cleanup steps before executing cells: some notebooks provision billable resources or contact external providers. Do not paste credentials or tokens into notebook cells or outputs.

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
