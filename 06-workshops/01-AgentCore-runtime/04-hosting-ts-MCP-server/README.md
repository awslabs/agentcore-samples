# TypeScript MCP Server on Amazon Bedrock AgentCore

## Overview

This tutorial demonstrates how to host a TypeScript-based MCP (Model Context Protocol) server using the Amazon Bedrock AgentCore runtime environment.


### Tutorial Details

| Information         | Details                                                   |
|:--------------------|:----------------------------------------------------------|
| Tutorial type       | Hosting typescript MCP server                             |
| Tool type           | MCP server                                                |
| Tutorial components | Hosting typescript MCP server on AgentCore Runtime        |
| Tutorial vertical   | Cross-vertical                                            |
| Example complexity  | Easy                                                      |
| SDK used            | Anthropic's typescript SDK for MCP                        |

## Prerequisites

- Node.js v22 or later  
- Docker (for containerization)  
- Amazon ECR (Elastic Container Registry) for storing Docker images  
- AWS account with access to Bedrock AgentCore  

---

## AgentCore Runtime Service Contract

Refer to the [official service contract documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html).

**Runtime configuration:**
- **Host:** `0.0.0.0`  
- **Port:** `8000`  
- **Transport:** Stateless `streamable-http`  
- **Endpoint Path:** `POST /mcp`  

## Local Development

1. Install dependencies

```
npm install
```

2. Set up AWS credentials
```
aws configure
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_REGION=us-east-1
```

3. Start server
```
npm run start
```

4. Test locally using [MCP inspector](https://github.com/modelcontextprotocol/inspector)

```
npx @modelcontextprotocol/inspector
```

## Docker Deployment

1. Create ECR Repository
```
aws ecr create-repository --repository-name mcp-server --region us-east-1
```
2. Build and Push Image to ECR
```
# Get login token
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin [account-id].dkr.ecr.us-east-1.amazonaws.com

docker buildx --platform linux/arm64 \
  -t [account-id].dkr.ecr.us-east-1.amazonaws.com/mcp-server:latest --push .
```

3. Deploy to Bedrock AgentCore

    - Go to AWS Console → Bedrock → AgentCore → Create Agent
    - Choose MCP as the protocol
    - Configure Agent Runtime:
        - Image URI: [account-id].dkr.ecr.us-east-1.amazonaws.com/mcp-server:latest
        - Set IAM Permissions for Bedrock model access
        - Deploy and test in the Agent Sandbox


4. Construct the Encoded ARN MCP URL

```
echo "agent_arn" | sed 's/:/%3A/g; s/\//%2F/g'
```

```
https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT
```

5. Use the MCP url with [MCP inspector](https://github.com/modelcontextprotocol/inspector).

## References
- https://aws.amazon.com/bedrock/agentcore/
- https://github.com/modelcontextprotocol/typescript-sdk
- https://github.com/modelcontextprotocol/inspector


