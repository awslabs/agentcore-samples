# AgentCore gateway MCP Inspector

A developer tool for testing and debugging AgentCore gateway interactions. Use it to inspect tools, prompts, and resources exposed through your AgentCore gateway via the Streamable HTTP transport. AgentCore gateway MCP Inspector is meant for learning and testing.

> This project is a fork of [MCP Inspector](https://github.com/modelcontextprotocol/inspector) by the Model Context Protocol community. All credit for the original inspector goes to its [contributors](https://github.com/modelcontextprotocol/inspector/graphs/contributors).

## Getting Started

### Requirements

- Node.js >= 22.7.5
- AWS CLI configured with credentials (`aws configure`)

### Quick Start

```bash
npm install
npm run build
npm start
```

The UI opens at `http://localhost:6274`. The proxy server runs on port `6277`.

### Development Mode

```bash
npm install
npm run dev
```

## Features

### gateway Selector

On launch, the inspector lists all AgentCore Gateways in your AWS account (using `ListGateways`). Select a gateway to auto-populate the URL field, or enter a custom URL manually.

### Authentication Modes

The inspector supports four inbound authentication modes for connecting to your AgentCore gateway:

| Mode                         | Description                                                                                                                                                              |
| :--------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Manual Token**             | Provide a Bearer token directly in the Authorization header                                                                                                              |
| **OAuth 2.0 Flow**           | Pre-registered client with client ID, secret, and scopes. Opens the Auth settings panel for the full OAuth flow. Dynamic Client Registration (DCR) and CIMD are planned. |
| **AgentCore identity (M2M)** | Create or reuse a workload identity, select a credential provider from your account, and obtain an access token via `GetWorkloadAccessToken` + `GetResourceOauth2Token`  |
| **IAM (SigV4)**              | Signs outbound requests with AWS SigV4 using the server's configured AWS credentials. Service name: `bedrock-agentcore`.                                                 |

All modes support additional custom headers alongside the primary authentication method.

### AgentCore identity 3LO

The inspector includes a `/complete-token-auth` endpoint for completing AgentCore identity three-legged OAuth token binding via `CompleteResourceTokenAuthCommand`. The client detects 3LO callbacks and completes the flow automatically.

## Configuration

| Setting       | Description                                                      | Default              |
| :------------ | :--------------------------------------------------------------- | :------------------- |
| `CLIENT_PORT` | Port for the web UI                                              | 6274                 |
| `SERVER_PORT` | Port for the proxy server                                        | 6277                 |
| `HOST`        | Bind address                                                     | localhost            |
| `AWS_REGION`  | AWS region for gateway listing, identity APIs, and SigV4 signing | from `aws configure` |

## Architecture

The inspector consists of two components:

-**Inspector Client**: React web UI (Vite, Tailwind, Radix UI) -**MCP Proxy**: Node.js Express server that bridges the web UI to your AgentCore gateway via Streamable HTTP

The proxy is not a network proxy. It functions as both an MCP client (connecting to your AgentCore gateway) and an HTTP server (serving the web UI), enabling browser-based inspection of MCP servers.

### Proxy Endpoints

| Endpoint                             | Purpose                                                    |
| :----------------------------------- | :--------------------------------------------------------- |
| `GET/POST/DELETE /mcp`               | Streamable HTTP transport proxy                            |
| `POST /complete-token-auth`          | AgentCore identity 3LO token binding                       |
| `GET /gateways`                      | Lists AgentCore Gateways via `ListGateways` + `GetGateway` |
| `GET /identity/credential-providers` | Lists OAuth2 credential providers                          |
| `POST /identity/ensure-workload`     | Creates or reuses a workload identity                      |
| `POST /identity/m2m-token`           | Obtains M2M access token via AgentCore identity            |
| `GET /config`                        | Returns default configuration and AWS region               |
| `GET /health`                        | Health check                                               |

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [MCP Inspector (upstream)](https://github.com/modelcontextprotocol/inspector)
