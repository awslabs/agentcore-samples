# Integrate Claude Code with MCP Server using AgentCore Gateway

> [!IMPORTANT]
> The examples provided in this repository are for experimental and educational purposes only. They demonstrate concepts and techniques but are not intended for direct use in production environments.

[Claude Code](https://docs.anthropic.com/en/docs/claude-code) is Anthropic's agentic coding tool that lives in your terminal. It connects to external capabilities through MCP servers, but as the number of connected servers grows, two problems emerge:

- **Context window overhead**: Every MCP server adds tool definitions to the context window, leaving less room for code reasoning.
- **Configuration sprawl**: In enterprise environments, each developer must individually configure and authenticate against every server.

AgentCore Gateway solves both by acting as a single, central MCP server that aggregates multiple backend MCP servers behind one endpoint with dynamic tool loading via semantic search.

## Architecture

![Solution Architecture](images/claude_code_agentcore_gateway_architecture_new.png)

In this architecture, the [AWS Knowledge MCP Server](https://aws.amazon.com/about-aws/whats-new/2025/10/aws-knowledge-mcp-server-generally-available/) is exposed as an MCP target through AgentCore Gateway. Claude Code connects to the gateway using OAuth, enabling developers to securely access and retrieve knowledge in real time, with only the relevant tools loaded into context per request.

| Information          | Details                                                   |
|:---------------------|:----------------------------------------------------------|
| Tutorial type        | Interactive (Jupyter Notebook)                            |
| AgentCore components | AgentCore Gateway, AgentCore Identity                     |
| Gateway Target type  | MCP server                                                |
| Inbound Auth IdP     | Amazon Cognito (can use others)                           |
| Tutorial vertical    | Cross-vertical                                            |
| Complexity           | Easy                                                      |
| SDK used             | boto3                                                     |

## Prerequisites

### AWS Account & Permissions

1. **AWS Account** with credentials configured (via environment variables, AWS CLI profile, or SageMaker notebook role)
2. **IAM permissions** to:
   - Create and manage IAM roles and policies (`iam:CreateRole`, `iam:PutRolePolicy`, `iam:PassRole`)
   - Create and manage Amazon Cognito user pools, resource servers, and app clients (`cognito-idp:*`)
   - Create and manage AgentCore Gateways and targets (`bedrock-agentcore:*`)

### Environment

- Python 3.10+
- Jupyter Notebook (Python kernel)
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI installed and authenticated (`claude` available in your terminal)

### AWS Services Used

- **Amazon Cognito** — OAuth 2.0 client credentials flow for inbound gateway authorization
- **Amazon Bedrock AgentCore Gateway** — central MCP endpoint that Claude Code connects to
- **AWS IAM** — role assumed by the gateway to access backend targets

## Usage

1. **Install dependencies**

   ```bash
   pip install -U -r requirements.txt
   ```

2. **Open the notebook**

   ```bash
   jupyter notebook claude-code-gateway-mcp-server.ipynb
   ```

3. **Follow the notebook steps** which walk through:
   - Creating an IAM role for the gateway
   - Setting up a Cognito user pool with OAuth 2.0 client credentials
   - Creating the AgentCore Gateway with semantic search and Cognito authorization
   - Adding the AWS Knowledge MCP Server as a gateway target
   - Obtaining an access token and registering the gateway in Claude Code via `claude mcp add`

## Sample Prompts

Once the gateway is registered in Claude Code, try:

- `/mcp` → select `my-tools-gw` → View tools to see available tools
- Ask Claude Code questions that leverage the AWS Knowledge MCP Server tools exposed through the gateway

## Clean Up

The notebook includes cleanup cells to:

1. Delete the AgentCore Gateway and its targets
2. Remove the MCP server from Claude Code (`claude mcp remove my-tools-gw`)

Additional resources you may need to manually delete:
- IAM role and policies (`sample-claude-code-mcp-gateway`)
- Cognito user pool (`sample-agentcore-gateway-pool`)

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guidelines](../../../CONTRIBUTING.md) for details.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](../../../LICENSE) file for details.

## 🆘 Support

- **Issues**: Report bugs or request features via [GitHub Issues](https://github.com/awslabs/amazon-bedrock-agentcore-samples/issues)

## 🔄 Updates

This repository is actively maintained and updated with new capabilities and examples. Watch the repository to stay updated with the latest additions.
