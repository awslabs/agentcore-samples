# MCP End-to-End Examples

## Overview

The [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) defines a standard way for AI agents to discover and use tools, access resources, and interact with users through a structured protocol. AgentCore runtime supports hosting MCP servers with full protocol compliance — tools, resources, prompts, sampling, elicitation, and progress notifications.

This folder contains end-to-end tutorials that cover the complete MCP feature set on AgentCore runtime, from server-side capabilities to client-side interactions and utility features.

## MCP Feature Support in AgentCore runtime

<table>
  <thead>
    <tr>
      <th>Category</th>
      <th>Feature</th>
      <th>Spec Methods</th>
      <th align="center">runtime</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td rowspan="4"><strong>MCP Server Features</strong></td>
      <td>Tools</td>
      <td><code>tools/list</code>, <code>tools/call</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Tools (output schema)</td>
      <td><code>output schema</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Resources</td>
      <td><code>resources/list</code>, <code>resources/read</code>, <code>resources/subscribe</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Prompts</td>
      <td><code>prompts/list</code>, <code>prompts/get</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td rowspan="3"><strong>MCP Client Features</strong></td>
      <td>Sampling</td>
      <td><code>sampling/createMessage</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Roots</td>
      <td><code>roots/list</code></td>
      <td align="center">TBD</td>
    </tr>
    <tr>
      <td>Elicitation</td>
      <td><code>elicitation/create</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td rowspan="2"><strong>MCP Base Protocol</strong></td>
      <td>Lifecycle</td>
      <td><code>initialize</code>, <code>initialized</code>, <code>ping</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Transports</td>
      <td><code>response streaming</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td rowspan="4"><strong>MCP Utilities</strong></td>
      <td>Progress</td>
      <td><code>notifications/progress</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Cancellation</td>
      <td><code>notifications/cancel</code></td>
      <td align="center">TBD</td>
    </tr>
    <tr>
      <td>Logging</td>
      <td><code>logs/send</code></td>
      <td align="center">✅</td>
    </tr>
    <tr>
      <td>Tasks</td>
      <td><code>tasks/list</code>, <code>tasks/cancel</code></td>
      <td align="center">✅</td>
    </tr>
  </tbody>
</table>

> ✅ Supported | TBD To Be Determined

## Project Structure

```
08-mcp-e2e/
├── 01-server-e2e/       # MCP Server features (Tools, Resources, Prompts)
│   ├── agents/          # MCP server code (mcp_e2e_stateless_server.py)
│   ├── deploy.py        # Deploy runtime + DynamoDB table
│   ├── invoke.py        # Send JSON-RPC messages to test all features
│   └── cleanup.py       # Delete all resources
├── 02-client-e2e/       # MCP Client features (Elicitation, Sampling)
│   ├── agents/          # MCP server code (mcp_client_features.py)
│   ├── deploy.py        # Deploy runtime + DynamoDB table
│   ├── invoke.py        # Test elicitation and sampling
│   └── cleanup.py       # Delete all resources
├── 03-utilities-e2e/    # MCP Utilities (Progress Notifications)
│   ├── agents/          # MCP server code (mcp_progress_server.py)
│   ├── deploy.py        # Deploy runtime + DynamoDB table
│   ├── invoke.py        # Test progress notifications
│   └── cleanup.py       # Delete all resources
└── helpers/             # Shared utilities (DynamoDB, IAM, Cognito)
```

## What Each Subfolder Demonstrates

### 01 — MCP Server Features

Deploys an MCP server with all three core server capabilities using a finance tracking example:

- **Tools** — `tools/list` and `tools/call` for expense operations (add_expense, add_income, set_budget, get_balance)
- **Resources** — `resources/list` and `resources/read` for monthly summaries and budget status
- **Prompts** — `prompts/list` and `prompts/get` for budget analysis and savings plan templates

The server uses DynamoDB for persistent storage.

```bash
cd 01-server-e2e
python deploy.py    # Creates DynamoDB table, IAM role, uploads code, deploys runtime
python invoke.py    # Sends JSON-RPC messages to test tools, resources, and prompts
python cleanup.py   # Deletes runtime, IAM role, S3 object, and DynamoDB table
```

### 02 — MCP Client Features

Demonstrates client-side MCP capabilities for stateful, interactive sessions:

- **Elicitation** — Server asks the client for user input mid-execution (e.g., guided expense entry with multi-turn prompts)
- **Sampling** — Server delegates LLM call to the client for AI-powered spending analysis

These features enable richer interactions where the server and client collaborate during tool execution, rather than simple request/response patterns.

```bash
cd 02-client-e2e
python deploy.py    # Creates DynamoDB table, IAM role, uploads code, deploys runtime
python invoke.py    # Tests elicitation and sampling features
python cleanup.py   # Deletes runtime, IAM role, S3 object, and DynamoDB table
```

### 03 — MCP Utilities

Covers utility features for long-running operations:

- **Progress Notifications** — One-way updates from server to client during execution. The example generates a 5-step monthly financial report with progress updates at each stage using `ctx.report_progress()`.

Unlike elicitation and sampling (which are request/response), progress notifications are one-way — the server sends updates without waiting for a response.

```bash
cd 03-utilities-e2e
python deploy.py    # Creates DynamoDB table, IAM role, uploads code, deploys runtime
python invoke.py    # Tests progress notifications during report generation
python cleanup.py   # Deletes runtime, IAM role, S3 object, and DynamoDB table
```

### Shared Utilities (`helpers/`)

Common code used across all three examples:

| File | Description |
|:-----|:------------|
| `utils.py` | Cognito user pool setup, IAM role creation, Secrets Manager helpers |
| `dynamo_utils.py` | DynamoDB operations for the finance tracking example |

These helpers are automatically included in the deployment zip by each `deploy.py` script.

## Prerequisites

- AWS CLI configured with appropriate permissions
- Python 3.13+ recommended
- Access to Amazon Bedrock AgentCore runtime
- AWS services used: DynamoDB, IAM, S3

## Quick Start

```bash
# Deploy and test the MCP server features example
cd 01-server-e2e
python deploy.py
python invoke.py
python cleanup.py
```

Follow the examples in order (01 → 02 → 03) since the client and utilities examples build on the same finance tracking pattern.

## Resources

- [MCP Specification](https://modelcontextprotocol.io/specification/2025-11-25/server)
- [Amazon Bedrock AgentCore Documentation](https://docs.aws.amazon.com/bedrock-agentcore/)
