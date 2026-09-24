# VS Code AgentCore Confluence Serverless - CDK Deployment Guide

This CDK stack deploys a serverless OAuth proxy for VS Code + AgentCore Gateway integration, eliminating the need for local proxy servers.

## Architecture

The stack creates:

1. **Cognito User Pool** - JWT authentication for inbound auth
2. **Lambda Functions** - MCP proxy and OAuth callback handlers
3. **API Gateway** - Public HTTP API endpoint
4. **IAM Roles** - Necessary permissions for Lambda and AgentCore

## Prerequisites

- AWS CLI configured with appropriate credentials
- Node.js 18+ and npm installed
- AWS CDK CLI installed: `npm install -g aws-cdk`
- Docker installed (required for Lambda bundling)

## Installation

1. Navigate to the CDK directory:

```bash
cd 01-tutorials/02-AgentCore-gateway/04-integration/03-ide-gateway-tool/cdk
```

2. Install dependencies:

```bash
npm install
```

3. Bootstrap CDK (first time only):

```bash
cdk bootstrap
```

## Deployment

### Deploy the Stack

```bash
cdk deploy
```

The deployment will create all resources and output important values including:

- API Gateway endpoint
- Cognito User Pool ID
- Client IDs
- VS Code MCP configuration

### Post-Deployment Steps

#### 1. Create a Test User

After deployment, create a test user in the Cognito User Pool:

```bash
# Get the User Pool ID from CDK outputs
USER_POOL_ID="<from-cdk-output>"

# Create user
aws cognito-idp admin-create-user \
  --user-pool-id $USER_POOL_ID \
  --username vscode-user \
  --temporary-password "TempPassword123!" \
  --user-attributes Name=email,Value=vscode-user@example.com Name=email_verified,Value=true \
  --message-action SUPPRESS

# Set permanent password
aws cognito-idp admin-set-user-password \
  --user-pool-id $USER_POOL_ID \
  --username vscode-user \
  --password "TempPassword123!" \
  --permanent
```

#### 2. Configure Atlassian OAuth (Optional)

If you want to connect to Confluence:

1. Create an OAuth app in Atlassian Developer Console
2. Use the AgentCore Gateway callback URL (see notebook for details)
3. Create AgentCore Gateway and Confluence target using the notebook or AWS Console

#### 3. Update Lambda Environment Variables

If you create an AgentCore Gateway, update the Lambda environment variable:

```bash
LAMBDA_NAME="<proxy-lambda-name-from-output>"
GATEWAY_URL="<your-gateway-url>"

aws lambda update-function-configuration \
  --function-name $LAMBDA_NAME \
  --environment "Variables={GATEWAY_URL=$GATEWAY_URL,COGNITO_DOMAIN=<cognito-domain>,CLIENT_ID=<client-id>,CALLBACK_LAMBDA_URL=<api-endpoint>}"
```

#### 4. Configure VS Code

Add the MCP configuration to your VS Code settings (the exact JSON is provided in CDK outputs):

Create or update `.vscode/mcp.json`:

```json
{
  "servers": {
    "agentcore-confluence-<timestamp>": {
      "type": "http",
      "url": "<api-gateway-endpoint>",
      "headers": {
        "MCP-Protocol-Version": "2025-11-25"
      }
    }
  }
}
```

## Stack Outputs

After deployment, the stack provides these outputs:

- **ApiEndpoint** - API Gateway URL for VS Code to connect to
- **UserPoolId** - Cognito User Pool ID
- **CognitoDomain** - Cognito domain name
- **VSCodeClientId** - Client ID for VS Code OAuth
- **M2MClientId** - Client ID for machine-to-machine testing
- **ProxyLambdaName** - Name of the MCP proxy Lambda function
- **CallbackLambdaName** - Name of the OAuth callback Lambda function
- **VSCodeMcpConfig** - Ready-to-use MCP configuration JSON

## Testing

### Test the API Gateway

```bash
API_ENDPOINT="<from-cdk-output>"

# Test OAuth metadata endpoint
curl $API_ENDPOINT/.well-known/oauth-authorization-server

# Test ping endpoint
curl $API_ENDPOINT/ping
```

### Test with VS Code

1. Open VS Code with the MCP extension
2. The extension should prompt for authentication
3. Use credentials: `vscode-user` / `TempPassword123!`
4. After authentication, MCP tools should be available

## Cleanup

To delete all resources:

```bash
cdk destroy
```

Note: You may need to manually delete:

- CloudWatch Log Groups (not auto-deleted by default)
- Any AgentCore Gateways created separately

## Troubleshooting

### Lambda Function Errors

Check Lambda logs:

```bash
aws logs tail /aws/lambda/<lambda-function-name> --follow
```

### Cognito Authentication Issues

Verify Cognito configuration:

```bash
aws cognito-idp describe-user-pool --user-pool-id <user-pool-id>
aws cognito-idp describe-user-pool-client --user-pool-id <user-pool-id> --client-id <client-id>
```

### API Gateway Issues

Test API Gateway endpoints:

```bash
# Test OAuth metadata
curl -v <api-endpoint>/.well-known/oauth-authorization-server

# Test with authentication
TOKEN="<your-jwt-token>"
curl -H "Authorization: Bearer $TOKEN" <api-endpoint>/
```

## Architecture Details

### Lambda Functions

1. **MCP Proxy Lambda** (`mcp_proxy_lambda.py`)

   - Handles OAuth metadata endpoints
   - Proxies MCP requests to AgentCore Gateway
   - Manages token exchange with Cognito
   - Intercepts OAuth callbacks

2. **Callback Lambda** (`callback_lambda.py`)
   - Handles 3LO OAuth callbacks
   - Calls CompleteResourceTokenAuth API
   - Stores user tokens for session binding

### API Gateway Routes

| Method | Path                                      | Handler  | Description                 |
| ------ | ----------------------------------------- | -------- | --------------------------- |
| GET    | `/.well-known/oauth-authorization-server` | Proxy    | OAuth server metadata       |
| GET    | `/.well-known/oauth-protected-resource`   | Proxy    | Resource metadata           |
| GET    | `/authorize`                              | Proxy    | OAuth authorization         |
| GET    | `/callback`                               | Proxy    | OAuth callback intercept    |
| POST   | `/token`                                  | Proxy    | Token exchange              |
| POST   | `/register`                               | Proxy    | Dynamic client registration |
| GET    | `/ping`                                   | Callback | Health check                |
| POST   | `/userIdentifier/token`                   | Callback | Store user token            |
| GET    | `/oauth2/callback`                        | Callback | 3LO callback                |
| ANY    | `/{proxy+}`                               | Proxy    | MCP proxy (default)         |

### Security

- Cognito User Pool with password policy
- JWT token authentication
- IAM roles with least privilege
- CORS enabled for VS Code
- Resource server scopes for fine-grained access

## Additional Resources

- [Original Jupyter Notebook](../01_vscode_agentcore_confluence_serverless.ipynb)
- [AgentCore Documentation](https://docs.aws.amazon.com/bedrock/)
- [MCP Protocol Specification](https://modelcontextprotocol.io/)

## Support

For issues or questions:

1. Check CloudWatch Logs for Lambda errors
2. Review API Gateway execution logs
3. Verify Cognito configuration
4. Refer to the original notebook for detailed explanations
