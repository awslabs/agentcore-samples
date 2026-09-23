# Amazon Bedrock AgentCore Browser Tool

## Overview

Amazon Bedrock AgentCore Browser Tool provides AI agents with a secure, fully managed way to interact with websites just like humans do. It allows agents to navigate web pages, fill out forms, and complete complex tasks without requiring developers to write and maintain custom automation scripts.

## How it works

A browser tool sandbox is a secure execution environment that enables AI agents to safely interact with web browsers. When a user makes a request, the Large Language Model (LLM) selects appropriate tools and translates commands. These commands are executed within a controlled sandbox environment containing a headless browser and hosted library server (using tools like Playwright). The sandbox provides isolation and security by containing web interactions within a restricted space, preventing unauthorized system access. The agent receives feedback through screenshots and can perform automated tasks while maintaining system security. This setup enables safe web automation for AI agents.

![architecture local](images/browser-tool.png)

## Key Features

### Secure, Managed Web Interaction

Provides AI agents with a secure, fully managed way to interact with websites just like humans do, allowing navigation, form filling, and complex task completion without requiring custom automation scripts.

### Enterprise Security Features

Provides VM-level isolation with 1:1 mapping between user session and browser session, offering enterprise-grade security. Each browser session runs in an isolated sandbox environment to meet enterprise security needs.

### Model-Agnostic Integration

Supports various AI models and frameworks while providing natural language abstractions for browser actions through tools like interact(), parse(), and discover(), making it particularly suitable for enterprise environments. The tool can execute browser commands from any library and supports various automation frameworks like Playwright and Puppeteer.

### Integration

Amazon Bedrock AgentCore Browser Tool integrates with other Amazon Bedrock AgentCore capabilities through a unified SDK, including:

- Amazon Bedrock AgentCore Runtime
- Amazon Bedrock AgentCore Identity
- Amazon Bedrock AgentCore Memory
- Amazon Bedrock AgentCore Observability

This integration aims to simplify the development process and provide a comprehensive platform for building, deploying, and managing AI agents, with powerful capabilities to perform browser-based tasks.

### Use Cases

The Amazon Bedrock AgentCore Browser Tool is suitable for a wide range of applications, including:

- Web Navigation & Interaction
- Workflow Automation including filling forms

## Tutorials Overview

These tutorials demonstrate AgentCore Browser Tool capabilities across different frameworks and configurations:

### Getting Started

**Browser Use Examples**
- [Getting Started with Bedrock AgentCore Browser Tool and Browser Use](02-browser-with-browserUse/getting_started-agentcore-browser-tool-with-browser-use.ipynb)
- [Amazon Bedrock AgentCore Browser Tool Live View and Browser Use](02-browser-with-browserUse/agentcore-browser-tool-live-view-with-browser-use.ipynb)

**Nova Act Examples**
- [Getting Started with Bedrock AgentCore Browser Tool and Nova Act](01-browser-with-NovaAct/01_getting_started-agentcore-browser-tool-with-nova-act.ipynb)
- [Amazon Bedrock AgentCore Browser Tool Live View and Nova Act](01-browser-with-NovaAct/02_agentcore-browser-tool-live-view-with-nova-act.ipynb)

**Strands Examples**
- [Getting Started with Bedrock AgentCore Browser Tool and Strands](04-browser-with-Strands/01_getting_started-agentcore-browser-tool-with-strands.ipynb)

### Advanced Features

**Observability**
- [Amazon Bedrock AgentCore Browser Tool Observability](03-browser-observability/01_browser_observability.ipynb)

**Live View**
- [Amazon Bedrock AgentCore Browser Tool DCV Live View](05-browser-live-view/01-embed-dcv-live-view-tutorial.ipynb)

**Web Bot Authentication**
- [Amazon Bedrock AgentCore Browser Tool Web Bot Auth](06-Web-Bot-Auth-Signing/01_agentcore-browser-tool-with-web-bot-auth.ipynb)

### VPC Integration

**VPC Configuration**
- [Connecting Public Browser from Private VPC](07-connecting-public-browser-from-private-vpc/01-connecting-public-browser-from-private-vpc-runtime.ipynb)
- [Interacting with VPC-based Browser from VPC](08-Interacting-with-vpc-based-browser-from-vpc/01-Interacting-with-vpc-based-browser-from-vpc.ipynb)

### Security & Configuration

**Domain Filtering**
- [Browser with Domain Filtering using AWS Network Firewall](09-browser-with-domain-filtering/) - Deploy AgentCore Browser in a VPC with Network Firewall for allowlist/denylist domain filtering

**Proxy Routing**
- [Browser with Squid Proxy](11-browser-with-proxy/) - Route browser traffic through an authenticated Squid proxy with access logs shipped to S3

**Browser Profiles**
- [Browser with Persistent Profiles](10-browser-with-profile/browser-profile.ipynb) - Persist and reuse browser session data (cookies, local storage) across multiple sessions
