# Device Management System

## Architecture & Overview

### What is the Device Management System?

This use case implements a comprehensive Device Management System using Amazon Bedrock AgentCore. It provides a unified interface for managing IoT devices, WiFi networks, users, and activities through natural language interactions, eliminating the need to navigate multiple device-specific applications.

| Information | Details |
|-------------|---------|
| Use case type | Conversational AI |
| Agent type | Single agent |
| Use case components | Tools, Gateway, Runtime, Frontend |
| Use case vertical | IoT/Smart Home |
| Example complexity | Intermediate |
| SDK used | Amazon Bedrock AgentCore SDK, boto3 |

### System Architecture

![Device Management Architecture](./images/device-management-architecture.png)

The Device Management System follows a modular, cloud-native architecture:

#### Core Components:
1. **User Interface**: Web application with chat interface for natural language interactions
2. **Amazon Bedrock AgentCore Runtime**: Processes natural language requests and manages conversation context
3. **Amazon Bedrock Gateway**: Authenticates requests and routes them to appropriate targets
4. **AWS Lambda Function**: Executes device management operations and business logic
5. **Amazon DynamoDB**: Stores device data, user information, and activity logs
6. **Amazon Cognito**: Handles user authentication and authorization

#### Data Flow:
1. User submits natural language query through web interface
2. Request is authenticated via Amazon Cognito and processed by AgentCore Runtime
3. Runtime determines appropriate tool and sends request through Gateway
4. Gateway routes to AWS Lambda function which queries/updates Amazon DynamoDB
5. Results flow back through the same path with natural language response

#### Observability:
- **Amazon CloudWatch Logs**: Centralized logging for all components
- **AWS X-Ray**: Distributed tracing for request flows
- **Amazon CloudWatch Metrics**: Performance and usage metrics

### Key Features

- **Device Management**: List devices, retrieve settings, monitor status
- **WiFi Network Management**: View and update network configurations (SSID, security)
- **User Management**: Manage user accounts and permissions
- **Activity Tracking**: Query user interactions and system activities
- **Natural Language Interface**: Conversational interactions instead of complex UIs

## Prerequisites

### Required Software
- **Python 3.10+**
- **AWS CLI** (configured with appropriate permissions)
- **Git** (for cloning the repository)

### AWS Account Requirements
- **AWS Account** with administrative permissions
- **AWS Region**: Recommended us-west-2 (configurable)

### Required AWS Services
- **Amazon Bedrock AgentCore** access
- **AWS Lambda** service access
- **Amazon DynamoDB** service access
- **Amazon Cognito** service access
- **Amazon CloudWatch** service access

### IAM Permissions
Your AWS user/role needs permissions for:
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "iam:PassRole"
            ],
            "Resource": "arn:aws:iam::*:role/DeviceManagement*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:*",
                "lambda:*",
                "dynamodb:*",
                "cognito-idp:*",
                "logs:*"
            ],
            "Resource": [
                "arn:aws:bedrock-agentcore:*:*:*",
                "arn:aws:lambda:*:*:function:device-management-*",
                "arn:aws:dynamodb:*:*:table/Devices*",
                "arn:aws:cognito-idp:*:*:userpool/*",
                "arn:aws:logs:*:*:log-group:/aws/lambda/device-management-*"
            ]
        }
    ]
}
```

### Environment Setup
1. **Clone the repository**:
   ```bash
   git clone <repository-url>
   cd device-management-system
   ```

2. **Install Python dependencies**:
   ```bash
   # Option 1: Install all dependencies (recommended for full setup)
   pip install -r requirements.txt
   
   # Option 2: Install component-specific dependencies only
   pip install -r device-management/requirements.txt  # Core AWS Lambda functionality
   pip install -r gateway/requirements.txt           # Gateway creation only
   pip install -r agent-runtime/requirements.txt     # Agent runtime only
   pip install -r frontend/requirements.txt          # Web interface only
   ```

3. **Configure environment variables**:
   ```bash
   cp .env.example .env
   # Edit .env with your specific values
   ```

## Deployment Steps

### Option 1: Automated Deployment (Recommended)

Deploy all components with a single script:

```bash
chmod +x deploy_all.sh
./deploy_all.sh
```

This script will:
1. Deploy AWS Lambda function with dependencies
2. Create and configure Amazon Bedrock Gateway
3. Set up gateway targets and observability
4. Configure agent runtime
5. Set up frontend application

### Option 2: Manual Step-by-Step Deployment

#### Step 1: Deploy AWS Lambda Function
```bash
cd device-management
chmod +x deploy.sh
./deploy.sh
cd ..
```

#### Step 2: Create Amazon Bedrock Gateway
```bash
cd gateway
python create_gateway.py
python device-management-target.py
python gateway_observability.py
cd ..
```

#### Step 3: Setup Agent Runtime
```bash
cd agent-runtime
chmod +x setup.sh
./setup.sh
cd ..
```

#### Step 4: Setup Frontend (Optional)
```bash
cd frontend
python main.py
# Access at http://localhost:8000
```

#### Step 5: Generate Test Data
```bash
cd device-management
python synthetic_data.py
cd ..
```

### Deployment Verification

1. **Test AWS Lambda function**:
   ```bash
   cd device-management
   python test_lambda.py
   ```

2. **Verify Gateway connectivity**:
   ```bash
   curl -H "Authorization: Bearer <token>" \
        https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
   ```

3. **Check Amazon DynamoDB tables**:
   ```bash
   aws dynamodb list-tables --region <your-region>
   ```

## Sample Queries

Once deployed, you can interact with the system using natural language. Here are example queries:

### Device Management Queries
```
"Can you list all devices in the system?"
"Show me all dormant devices"
"What devices are currently online?"
"List devices that haven't connected in the last 24 hours"
```

### Device Configuration Queries
```
"Can you show me the device settings for device ID DG-10016?"
"What's the current firmware version for device DG-10005?"
"Show me the configuration for all WR64 model devices"
```

### WiFi Network Management
```
"Can you show me the WiFi settings for device ID DB-10005?"
"List all WiFi networks for device DG-10016"
"Update the SSID for device DG-10016 to 'HomeNetwork-5G'"
"Change the security type for device DB-10005 network WN-1005-1 to WPA3"
```

### User and Activity Queries
```
"List all users in the system"
"Show me login activities from the last 24 hours"
"Who accessed device DG-10016 yesterday?"
"Query user activity for john.smith between 2023-06-20 and 2023-06-25"
```

### System Information Queries
```
"What tools are available?"
"How many devices are connected to the guest network?"
"Show me all maintenance activities this week"
```

### Expected Response Format
The system returns formatted, human-readable responses:

```
Devices in Device Remote Management System

Name                  | Device ID  | Model     | Status     | IP Address      | Last Connected
----------------------|------------|-----------|------------|-----------------|---------------
Factory Sensor A3     | DG-10016   | Sensor-X  | Connected  | 192.168.1.16    | 2023-06-26 18:26
Warehouse Camera      | DG-10022   | Cam-Pro   | Dormant    | 192.168.1.22    | 2023-06-10 14:45
```

## Configuration

### Environment Variables

The system uses environment variables for configuration. Key variables include:

```bash
# AWS Configuration
AWS_REGION=us-west-2
ENDPOINT_URL=https://bedrock-agentcore-control.us-west-2.amazonaws.com

# AWS Lambda Configuration
LAMBDA_ARN=arn:aws:lambda:us-west-2:account:function:DeviceManagementLambda

# Gateway Configuration
GATEWAY_IDENTIFIER=your-gateway-identifier
MCP_SERVER_URL=https://gateway-id.gateway.bedrock-agentcore.us-west-2.amazonaws.com

# Amazon Cognito Configuration
COGNITO_USERPOOL_ID=your-cognito-userpool-id
COGNITO_APP_CLIENT_ID=your-cognito-app-client-id
COGNITO_DOMAIN=your-domain.auth.us-west-2.amazoncognito.com
```

### MCP Client Configuration

For Amazon Q CLI integration:

```json
{
  "mcpServers": {
    "device-management": {
      "command": "npx",
      "timeout": 60000,
      "args": [
        "mcp-remote@latest",
        "https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp",
        "--header",
        "Authorization: Bearer <bearer-token>"
      ]
    }
  }
}
```

## Troubleshooting

### Common Issues

**AWS Lambda deployment failures**:
- Check AWS IAM permissions and AWS Lambda service quotas
- Verify Python dependencies in requirements.txt

**Gateway creation failures**:
- Verify Amazon Cognito User Pool ID and App Client ID
- Check IAM role ARN permissions

**Amazon DynamoDB access issues**:
- Confirm AWS Lambda execution role has necessary permissions
- Verify table names and regions match configuration

**Authentication issues**:
- Check Amazon Cognito configuration and token validity
- Verify bearer token generation process

### Debug Commands

```bash
# Test AWS Lambda function locally
cd device-management && python test_lambda.py

# Check Amazon DynamoDB tables
aws dynamodb list-tables --region us-west-2

# Verify Gateway status
aws bedrock-agentcore get-gateway --gateway-identifier <gateway-id>

# Check logs
aws logs describe-log-groups --log-group-name-prefix "/aws/bedrock-agentcore"
```

## Cleanup Instructions

### Automated Cleanup

```bash
chmod +x cleanup_all.sh
./cleanup_all.sh
```

### Manual Cleanup

#### 1. Delete AWS Lambda Function
```bash
aws lambda delete-function --function-name DeviceManagementLambda
```

#### 2. Delete Gateway Components
```bash
# Delete Gateway Target
aws bedrock-agentcore delete-gateway-target \
  --gateway-identifier <gateway-identifier> \
  --target-name device-management-target

# Delete Gateway
aws bedrock-agentcore delete-gateway \
  --gateway-identifier <gateway-identifier>
```

#### 3. Delete Amazon DynamoDB Tables
```bash
aws dynamodb delete-table --table-name Devices
aws dynamodb delete-table --table-name DeviceSettings
aws dynamodb delete-table --table-name WifiNetworks
aws dynamodb delete-table --table-name Users
aws dynamodb delete-table --table-name UserActivities
```

#### 4. Delete Amazon CloudWatch Log Groups
```bash
aws logs delete-log-group --log-group-name /aws/bedrock-agentcore/device-management-agent
aws logs delete-log-group --log-group-name /aws/lambda/DeviceManagementLambda
```

#### 5. Delete IAM Roles (if created by deployment)
```bash
aws iam delete-role --role-name DeviceManagementLambdaRole
aws iam delete-role --role-name AgentGatewayAccessRole
```

## Project Structure

```
device-management-system/
├── agent-runtime/          # Agent runtime components
│   ├── requirements.txt    # Agent runtime dependencies
│   └── requirements-runtime.txt # Runtime-specific dependencies
├── device-management/      # AWS Lambda function and Amazon DynamoDB setup
│   └── requirements.txt    # Lambda function dependencies
├── frontend/              # Web interface application
│   └── requirements.txt    # Frontend web app dependencies
├── gateway/               # Gateway creation and configuration
│   └── requirements.txt    # Gateway setup dependencies
├── images/                # Architecture diagrams
├── .env.example          # Environment variables template
├── requirements.txt      # Consolidated dependencies (all components)
├── deploy_all.sh         # Automated deployment script
├── cleanup_all.sh        # Automated cleanup script
└── README.md             # This file
```

## Additional Resources

- [Amazon Bedrock Documentation](https://docs.aws.amazon.com/bedrock/)
- [Amazon Bedrock AgentCore Developer Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/agents.html)
- [AWS Lambda Documentation](https://docs.aws.amazon.com/lambda/)
- [Amazon DynamoDB Documentation](https://docs.aws.amazon.com/dynamodb/)
- [Amazon Cognito Documentation](https://docs.aws.amazon.com/cognito/)

## Disclaimer

The examples provided in this repository are for experimental and educational purposes only. They demonstrate concepts and techniques but are not intended for direct use in production environments. Make sure to have Amazon Bedrock Guardrails in place to protect against prompt injection.