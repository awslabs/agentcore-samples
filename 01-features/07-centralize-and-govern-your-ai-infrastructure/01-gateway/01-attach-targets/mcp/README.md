# Attach MCP Targets

MCP targets operate in aggregation mode, the gateway acts as an MCP server whose capabilities combine those of all its MCP targets. Clients see a single consolidated tools/list response that includes tools from all attached targets.

![aggregation](../../images/aggregation.png)

## Tutorials

| Section                                   | Description                                                |
| :---------------------------------------- | :--------------------------------------------------------- |
| [smithy-schema](smithy-schema/)           | Define MCP tools using Smithy schema                       |
| [openapi-schema](openapi-schema/)         | Define MCP tools using OpenAPI schema                      |
| [aws-lambda](aws-lambda/)                 | Attach an AWS Lambda function as an MCP target             |
| [amazon-api-gateway](amazon-api-gateway/) | Attach an Amazon API Gateway endpoint as an MCP target     |
| [mcp-servers](mcp-servers/)               | Attach existing MCP servers as targets                     |
| [integrations](integrations/)             | Use built-in AgentCore gateway integrations as MCP targets |
| [connectors](connectors/)                 | Attach built-in managed connectors such as Web Search Tool |

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
