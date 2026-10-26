# 📚 Amazon Bedrock AgentCore Tutorials

⚠️ Recommendation: [The Starter Toolkit CLI](https://github.com/aws/bedrock-agentcore-starter-toolkit) is no longer supported. Please use the AgentCore CLI.

The AgentCore CLI (@aws/agentcore) is now the recommended way to create, develop, and deploy AI agents on Amazon Bedrock AgentCore. It supports a broader set of frameworks (Strands, LangGraph, LangChain, Google ADK, OpenAI Agents, and BYO), provides local development with hot reload, built-in evaluations, gateway management, and more.

This folder contains Hands-on tutorials for building, deploying, and managing AI agents with Amazon Bedrock AgentCore.

AgentCore services work independently or together, with any agentic framework (Strands Agents, LangChain, LangGraph, CrewAI, etc.) and any model.

![Amazon Bedrock AgentCore Overview](images/agentcore_overview.png)

## Prerequisites

- An AWS account with Amazon Bedrock access
- Python 3.10+ and Jupyter Notebook (or JupyterLab)
- AWS CLI configured with appropriate credentials
- Basic familiarity with AI agents and AWS services

## Tutorials

### 01 - [Runtime](01-AgentCore-runtime/)

Deploy and scale AI agents on a secure, serverless runtime -- regardless of framework, protocol, or model. Covers hosting agents, MCP servers, A2A, and bi-directional streaming. ([Docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html) · [Deep Dive Video](https://www.youtube.com/live/wizEw5a4gvM?si=7owv5C-kgU8UTzPl))

### 02 - [Gateway](02-AgentCore-gateway/)

Turn APIs, Lambda functions, and existing services into MCP-compatible tools without managing integrations. Includes examples for auth, access control, sensitive data masking, and more. ([Docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) · [Deep Dive Video](https://www.youtube.com/live/atWXM5lziY8?si=qKEzTbU1-15B8pQ0))

### 03 - [Identity](03-AgentCore-identity/)

Manage agent identity and access across AWS services and third-party apps (Slack, Zoom) using standard identity providers (Okta, Entra, Cognito). Covers inbound auth, outbound auth, and 3LO flows. ([Docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html) · [Deep Dive Video](https://www.youtube.com/live/wv2doVDF7KQ?si=sxt2lOufwt7cOeUY))

### 04 - [Memory](04-AgentCore-memory/)

Add fully managed memory to your agents for personalized experiences. Explore short-term memory, long-term memory, branching, and security patterns. ([Docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html) · [Deep Dive Video](https://www.youtube.com/live/-N4v6-kJgwA))

### 05 - [Tools](05-AgentCore-tools/)

Use AgentCore's built-in tools: **Code Interpreter** for secure code execution, and **Browser Tool** for web navigation and form completion. ([Code Interpreter Docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-interpreter-tool.html) · [Browser Tool Docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool.html) · [Deep Dive Video](https://www.youtube.com/live/z3lAJ-Nf_lk?si=Tf45AR3mZVo9rweL))

### 06 - [Observability](06-AgentCore-observability/)

Trace, debug, and monitor agent performance with OpenTelemetry-compatible telemetry. Works for agents hosted on Runtime, self-hosted agents, Lambda-based agents, and EKS-hosted agents. ([Docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability.html) · [Deep Dive Video](https://www.youtube.com/watch?v=wWQgawUPr1k))

### 07 - [Evaluations](07-AgentCore-evaluations/)

Assess agent quality with built-in and custom evaluators across dimensions like correctness, helpfulness, and safety. Includes creating evaluators, running evaluations, and using results. ([Docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations.html) · [Deep Dive Video](https://www.youtube.com/live/i0h7xA8cqYs?si=ZSR_-iQRjju-2H04))

### 08 - [Policy](08-AgentCore-policy/)

Define and enforce security controls using Cedar language policies to prevent data leakage and authority overreach. Covers natural language policy authoring and fine-grained access control. ([Docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html) · [Deep Dive Video](https://www.youtube.com/watch?v=q_9htaugcgI))

### 09 - [End-to-End Workshop](09-AgentCore-E2E/)

Build a complete agent step by step, combining Runtime, Gateway, Identity, Memory, and more into a production-ready solution. ([Deep Dive Video](https://youtu.be/gI_qvheaSoA?si=Pa6VzGXzopuX_koW&t=490))

## Where to Start

- **New to AgentCore?** Start with [01 - Runtime](01-AgentCore-runtime/) and work through the tutorials in order.
- **Looking for a specific capability?** Jump directly to any tutorial -- each one is self-contained.
- **Want the full picture?** The [End-to-End Workshop](09-AgentCore-E2E/) ties all the components together.

## Resources

- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock-agentcore/) -- Official developer guide and API reference
- [AgentCore Deep Dives Playlist](https://www.youtube.com/live/wzIQDPFQx30?si=K4EgotJ6DDj7Ri41) -- Video playlist covering each component in detail
