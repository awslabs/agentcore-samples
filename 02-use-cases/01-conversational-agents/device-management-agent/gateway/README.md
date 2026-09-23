# Gateway Module

## Architecture & Overview

### What is the Gateway Module?

The Gateway Module creates and configures the Amazon Bedrock AgentCore Gateway, which serves as the secure entry point for all device management operations. It handles authentication via Amazon Cognito and routes requests to the appropriate AWS Lambda function targets.

### Key Responsibilities
- **Gateway Creation**: Set up Amazon Bedrock AgentCore Gateway with proper authentication
- **Target Configuration**: Connect the Gateway to AWS Lambda functions via Gateway Targets
- **Authentication Management**: Configure Amazon Cognito OAuth for secure access
- **Observability Setup**: Enable Amazon CloudWatch logging and monitoring
- **Security Enforcement**: Implement JWT token validation and access control

### Architecture Components
- **Amazon Bedrock Gateway**: Main entry point for MCP protocol requests
- **Gateway Target**: Connection between Gateway and AWS Lambda function
- **Amazon Cognito Integration**: OAuth authentication and authorization
- **Amazon CloudWatch Logs**: Centralized logging for gateway operations

## Prerequisites

### Required Software
- **Python 3.10+**
- **AWS CLI** (configured with appropriate permissions)
- **Boto3** (AWS SDK for Python)

### AWS Services Access
- **Amazon Bedrock AgentCore** service permissions
- **Amazon Cognito** User Pool management
- **IAM** role creation and management
- **Amazon CloudWatch Logs** for observability

### Required AWS Resources
- **Amazon Cognito User Pool**: Must be created before gateway setup
- **Amazon Cognito App Client**: With appropriate OAuth scopes
- **IAM Role**: For gateway execution with bedrock-agentcore permissions
- **AWS Lambda Function**: Deployed from device-management module

### Required IAM Permissions
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "iam:PassRole",
                "bedrock-agentcore:*",
                "cognito-idp:*",
                "logs:*"
            ],
            "Resource": "*"
        }
    ]
}
```

## Deployment Steps

### Option 1: Automated Setup (Recommended)

```bash
# From the gateway directory
python cognito_oauth_setup.py    # Sets up Amazon Cognito OAuth
python create_gateway.py         # Creates the Gateway
python device-management-target.py  # Creates Gateway Target
python gateway_observability.py  # Enables logging
```

### Option 2: Manual Step-by-Step Deployment

#### Step 1: Environment Configuration
```bash
# Create .env file
cp .env.example .env
# Edit with your values:
# - AWS_REGION
# - COGNITO_USERPOOL_ID
# - COGNITO_APP_CLIENT_ID
# - LAMBDA_ARN (from device-management module)
```

#### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

#### Step 3: Setup Amazon Cognito OAuth
```bash
python cognito_oauth_setup.py
# This will automatically update your .env file with:
# - COGNITO_CLIENT_SECRET
# - COGNITO_DOMAIN
```

#### Step 4: Create Amazon Bedrock Gateway
```bash
python create_gateway.py
# Note the Gateway ID from output and update .env:
# GATEWAY_IDENTIFIER=your-gateway-id
```

#### Step 5: Create Gateway Target
```bash
python device-management-target.py
# This connects the Gateway to your AWS Lambda function
```

#### Step 6: Enable Observability
```bash
python gateway_observability.py
# This sets up Amazon CloudWatch logging
```

### Deployment Verification

```bash
# Test Gateway connectivity
curl -H "Authorization: Bearer <token>" \
     https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp

# Verify Gateway exists
aws bedrock-agentcore get-gateway --gateway-identifier <gateway-id>

# Check Gateway Target
aws bedrock-agentcore list-gateway-targets --gateway-identifier <gateway-id>

# Verify Amazon CloudWatch log group
aws logs describe-log-groups --log-group-name-prefix "/aws/bedrock-agentcore"
```

## Sample Queries

Once the Gateway is deployed, you can test it with these operations:

### Authentication Token Generation
```bash
# Get OAuth token from Amazon Cognito
curl --http1.1 -X POST https://<cognito-domain>.auth.<region>.amazoncognito.com/oauth2/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=client_credentials&client_id=<client-id>&client_secret=<client-secret>"
```

### MCP Protocol Requests
```bash
# List available tools
curl -X POST https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tools/list",
    "params": {}
  }'

# Execute device list tool
curl -X POST https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "2",
    "method": "tools/call",
    "params": {
      "name": "list_devices",
      "arguments": {}
    }
  }'
```

### Expected Response Format
```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "result": {
    "tools": [
      {
        "name": "list_devices",
        "description": "List all devices in the system"
      },
      {
        "name": "get_device_settings",
        "description": "Get settings for a specific device"
      }
    ]
  }
}
```

## Cleanup Instructions

### Remove Gateway Components

```bash
# Delete Gateway Target
aws bedrock-agentcore delete-gateway-target \
  --gateway-identifier <gateway-identifier> \
  --target-name device-management-target

# Delete Gateway
aws bedrock-agentcore delete-gateway \
  --gateway-identifier <gateway-identifier>
```

### Remove Amazon CloudWatch Resources

```bash
# Delete log groups
aws logs delete-log-group --log-group-name "/aws/bedrock-agentcore/gateway"
aws logs delete-log-group --log-group-name "/aws/bedrock-agentcore/device-management"
```

### Clean Up Local Files

```bash
# Remove environment file (contains sensitive data)
rm .env

# Remove any temporary files
rm -rf __pycache__/
```

### Optional: Clean Up Amazon Cognito Resources

```bash
# Only if you created these specifically for this project
# aws cognito-idp delete-user-pool-client --user-pool-id <pool-id> --client-id <client-id>
# aws cognito-idp delete-user-pool --user-pool-id <pool-id>
```

## Configuration

### Environment Variables

```bash
# AWS Configuration
AWS_REGION=us-west-2
ENDPOINT_URL=https://bedrock-agentcore-control.us-west-2.amazonaws.com

# AWS Lambda Configuration (from device-management module)
LAMBDA_ARN=arn:aws:lambda:us-west-2:account:function:DeviceManagementLambda

# Gateway Configuration
GATEWAY_IDENTIFIER=your-gateway-identifier
GATEWAY_NAME=Device-Management-Gateway
GATEWAY_DESCRIPTION=Device Management Gateway
ROLE_ARN=arn:aws:iam::account:role/YourGatewayRole

# Target Configuration
TARGET_NAME=device-management-target
TARGET_DESCRIPTION=List, Update device management activities

# Amazon Cognito Configuration
COGNITO_USERPOOL_ID=your-cognito-userpool-id
COGNITO_APP_CLIENT_ID=your-cognito-app-client-id
COGNITO_CLIENT_SECRET=your-cognito-client-secret
COGNITO_DOMAIN=your-domain.auth.us-west-2.amazoncognito.com

# Auto-populated by scripts
GATEWAY_ARN=arn:aws:bedrock-agentcore:us-west-2:account:gateway/gateway-id
GATEWAY_ID=your-gateway-id
```

### Amazon Cognito OAuth Configuration

The Gateway uses Amazon Cognito for authentication with these settings:
- **Grant Type**: `client_credentials`
- **Scopes**: `cognito-device-gateway/invoke`
- **Token Endpoint**: `https://<domain>.auth.<region>.amazoncognito.com/oauth2/token`
- **Discovery URL**: `https://cognito-idp.<region>.amazonaws.com/<pool-id>/.well-known/openid-configuration`

## Troubleshooting

### Common Issues

**Gateway creation failures**:
- Verify Amazon Cognito User Pool ID and App Client ID are correct
- Check IAM role ARN has proper permissions
- Ensure AWS region is consistent across all resources

**Authentication failures**:
- Regenerate Amazon Cognito client secret
- Verify OAuth scopes are properly configured
- Check token expiration and refresh as needed

**Gateway Target connection failures**:
- Verify AWS Lambda function exists and is accessible
- Check Gateway identifier matches created Gateway
- Ensure AWS Lambda function has proper permissions

### Debug Commands

```bash
# Test Amazon Cognito token generation
python -c "
import requests
response = requests.post('https://<domain>.auth.<region>.amazoncognito.com/oauth2/token',
  headers={'Content-Type': 'application/x-www-form-urlencoded'},
  data='grant_type=client_credentials&client_id=<id>&client_secret=<secret>')
print(response.json())
"

# Check Gateway status
aws bedrock-agentcore get-gateway --gateway-identifier <gateway-id>

# List Gateway Targets
aws bedrock-agentcore list-gateway-targets --gateway-identifier <gateway-id>

# Check Amazon CloudWatch logs
aws logs describe-log-groups --log-group-name-prefix "/aws/bedrock-agentcore"
```

## Integration with Other Modules

- **Device Management Module**: Gateway Target connects to the AWS Lambda function from this module
- **Agent Runtime Module**: Uses the Gateway endpoint to access MCP tools
- **Frontend Module**: Indirectly uses Gateway through Agent Runtime for device operations