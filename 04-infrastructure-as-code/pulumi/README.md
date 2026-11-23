# Pulumi Samples

Deploy Amazon Bedrock AgentCore resources using Pulumi in TypeScript or Python.

## Choose Your Language

| Language                        | Description                                                              | Samples   |
| ------------------------------- | ------------------------------------------------------------------------ | --------- |
| **[TypeScript](./typescript/)** | Strong typing, rich npm ecosystem, compile-time checks                   | 4 samples |
| **[Python](./python/)**         | Familiar syntax for Python developers, uv for fast dependency management | 4 samples |

## Prerequisites

### Common

- AWS CLI configured
- [Pulumi CLI](https://www.pulumi.com/docs/install/) installed
- Access to Amazon Bedrock AgentCore
- [Pulumi Account](https://app.pulumi.com/signup) (or another [state backend](https://www.pulumi.com/docs/iac/concepts/state-and-backends/))

### TypeScript Samples

- Node.js 18+ and npm

### Python Samples

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) for dependency management

### Authentication

Pulumi supports multiple AWS authentication methods. The recommended approach is [Pulumi ESC with AWS OIDC](https://www.pulumi.com/docs/esc/environments/configuring-oidc/aws/) for short-lived credentials instead of long-lived local credentials.

See [AWS provider configuration](https://www.pulumi.com/registry/packages/aws/installation-configuration/) for all options.

## Samples Overview

### TypeScript Samples

| Sample                                                                     | Description                                          |
| -------------------------------------------------------------------------- | ---------------------------------------------------- |
| [basic-runtime](./typescript/basic-runtime/)                               | Simple agent deployment                              |
| [multi-agent-runtime](./typescript/multi-agent-runtime/)                   | Multi-agent system with agent-to-agent communication |
| [mcp-server-agentcore-runtime](./typescript/mcp-server-agentcore-runtime/) | MCP server with JWT authentication                   |
| [end-to-end-weather-agent](./typescript/end-to-end-weather-agent/)         | Weather agent with tools and memory                  |

### Python Samples

| Sample                                                                 | Description                                          |
| ---------------------------------------------------------------------- | ---------------------------------------------------- |
| [basic-runtime](./python/basic-runtime/)                               | Simple agent deployment                              |
| [multi-agent-runtime](./python/multi-agent-runtime/)                   | Multi-agent system with agent-to-agent communication |
| [mcp-server-agentcore-runtime](./python/mcp-server-agentcore-runtime/) | MCP server with JWT authentication                   |
| [end-to-end-weather-agent](./python/end-to-end-weather-agent/)         | Weather agent with tools and memory                  |

## Quick Start

### TypeScript

```bash
cd typescript/<sample-name>
npm install
pulumi login
pulumi stack init dev
pulumi config set aws:region us-east-1 -s dev
pulumi up -s dev
```

### Python

```bash
cd python/<sample-name>
pulumi login
pulumi stack init dev
pulumi config set aws:region us-east-1 -s dev
pulumi up -s dev
```

See each sample's README for configuration options, outputs, and testing instructions.

## Pulumi Advantages

- **Multi-language support** - Use TypeScript, Python, Go, C#, Java, or YAML
- **Built-in secrets** - Encrypted secrets management without external tools
- **Pulumi ESC** - Centralized environments, secrets, and configuration with OIDC support
- **State management** - Built-in state backend with no extra setup required
- **Type safety** - IDE support and compile-time error checking in TypeScript
- **Testing** - Unit and integration test support using standard test frameworks
