<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Amazon Bedrock AgentCore Gateway - Authorization Code Flow Examples

This repository contains step-by-step Jupyter notebooks demonstrating how to connect remote MCP servers to [Amazon Bedrock AgentCore Gateway](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html) using the OAuth 2.0 Authorization Code Grant flow.

## Available Examples

| MCP Server | Notebook |
|---|---|
| [GitHub](https://github.com/github/github-mcp-server) | [github-mcp-server.ipynb](github-mcp-server.ipynb) |
| [Atlassian (Jira/Confluence)](https://github.com/atlassian/atlassian-mcp-server) | Coming soon |
| [Salesforce](https://help.salesforce.com/s/articleView?id=platform.hosted_mcp_servers.htm&type=5) | Coming soon |
| [Snowflake](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-agents-mcp) | Coming soon |


## Concepts

### Authorization Code Flow

The OAuth 2.0 Authorization Code Grant (sometimes called "three-legged OAuth") allows an application to access resources on behalf of a user without ever seeing their credentials. The flow works as follows:

1. The application redirects the user to the authorization server's login page
2. The user authenticates and grants consent
3. The authorization server redirects back with an authorization code
4. The application exchanges the code for an access token
5. The access token is used to call the protected API

In the context of AgentCore Gateway, this flow is used to obtain OAuth tokens for calling MCP servers that require user-level authorization (e.g., accessing a user's GitHub repos, Jira issues, or Salesforce records).

### Two Methods for Target Creation

Each notebook demonstrates two ways to create MCP server targets on AgentCore Gateway:

**Method 1 — Implicit Sync:** The admin completes the authorization code flow during target creation. AgentCore Gateway uses the resulting token to connect to the MCP server, discover its tools, and cache the tool definitions. This requires human interaction during setup.

**Method 2 — Schema Upfront:** The admin provides the tool schema directly (from a JSON file) during target creation. No OAuth flow is needed at creation time. This is ideal for Infrastructure-as-Code pipelines where human interaction is not possible.

In both methods, gateway users can call `tools/list` without authenticating to the MCP server (cached tools are returned). The authorization code flow is only triggered when a user calls `tools/call`.

### URL Session Binding

[URL Session Binding](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/oauth2-authorization-url-session-binding.html) is a security mechanism that ensures the user who started the OAuth flow is the same user who completed consent. When AgentCore Identity generates an authorization URL, it also returns a session URI. After the user completes consent, the application calls `CompleteResourceTokenAuth` with the session URI and the user's identity. AgentCore Identity validates the match before issuing an access token.

This prevents a scenario where a user accidentally shares the authorization URL and someone else completes consent on their behalf. The authorization URL and session URI expire after 10 minutes.

### Credential Providers

AgentCore Gateway uses [credential providers](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-credential-providers.html) to manage OAuth tokens for MCP server targets. Each notebook creates a credential provider configured for the specific MCP server's OAuth setup:

- **GitHub** — uses the built-in `GithubOauth2` vendor
- **Atlassian** — uses `CustomOauth2` with manual authorization server metadata
- **Salesforce** — uses `CustomOauth2` with OpenID Connect discovery URL

### Inbound vs Outbound Authentication

- **Inbound auth** controls who can invoke the AgentCore Gateway. These notebooks use Amazon Cognito with a machine-to-machine (M2M) client credentials flow.
- **Outbound auth** controls how the gateway authenticates to the MCP server target. This is the authorization code flow demonstrated in each notebook.

## Prerequisites

- An AWS account with access to Amazon Bedrock AgentCore
- Python 3.11+
- An OAuth app registered with the target MCP server provider (GitHub, Atlassian, or Salesforce)
- AWS CLI configured with appropriate credentials

## Getting Started

1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

2. Open a notebook and follow the steps sequentially:
   ```bash
   jupyter notebook github-mcp-server.ipynb
   ```

3. Each notebook will prompt you for your OAuth app credentials and guide you through the full setup, invocation, and cleanup process.

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE.txt) file for details.
