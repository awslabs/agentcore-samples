# Device Management Module

## Architecture & Overview

### What is the Device Management Module?

The Device Management Module is the core backend component that implements all device management operations. It consists of an AWS Lambda function that serves as the execution engine for MCP (Model Context Protocol) tools, backed by Amazon DynamoDB for data persistence.

### Key Responsibilities
- **Device Operations**: List, query, and manage IoT device inventory
- **Configuration Management**: Handle device settings and WiFi network configurations
- **User Management**: Manage user accounts and access permissions
- **Activity Tracking**: Log and query user interactions with devices
- **Data Persistence**: Store and retrieve data from Amazon DynamoDB tables

### Architecture Components
- **AWS Lambda Function**: Serverless compute executing device management logic
- **Amazon DynamoDB Tables**: NoSQL database storing device, user, and activity data
- **MCP Tools**: Standardized interfaces for device management operations
- **IAM Roles**: Security policies for AWS service access

## Prerequisites

### Required Software
- **Python 3.10+**
- **AWS CLI** (configured with appropriate permissions)
- **Boto3** (AWS SDK for Python)

### AWS Services Access
- **AWS Lambda** service permissions
- **Amazon DynamoDB** read/write access
- **IAM** role creation and management permissions
- **Amazon CloudWatch Logs** for function logging

### Required IAM Permissions
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "lambda:*",
        "dynamodb:*",
        "iam:CreateRole",
        "iam:AttachRolePolicy",
        "iam:PassRole",
        "logs:*"
      ],
      "Resource": "*"
    }
  ]
}
```

## Deployment Steps

### Option 1: Automated Deployment (Recommended)

```bash
# From the device-management directory
chmod +x deploy.sh
./deploy.sh
```

### Option 2: Manual Deployment

#### Step 1: Environment Configuration
```bash
# Create .env file
cp .env.example .env
# Edit with your values:
# - AWS_REGION
# - LAMBDA_FUNCTION_NAME
# - LAMBDA_ROLE_NAME
```

#### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

#### Step 3: Create Amazon DynamoDB Tables
```bash
python dynamodb_models.py
```

#### Step 4: Deploy AWS Lambda Function
```bash
# Package and deploy
zip -r function.zip . -x "*.env" "__pycache__/*" "*.pyc"
aws lambda create-function \
  --function-name DeviceManagementLambda \
  --runtime python3.10 \
  --role arn:aws:iam::ACCOUNT:role/DeviceManagementLambdaRole \
  --handler lambda_function.lambda_handler \
  --zip-file fileb://function.zip
```

#### Step 5: Generate Test Data (Optional)
```bash
python synthetic_data.py
```

### Deployment Verification

```bash
# Test AWS Lambda function locally
python test_lambda.py

# Test deployed function
aws lambda invoke \
  --function-name DeviceManagementLambda \
  --payload '{"tool_name": "list_devices"}' \
  response.json

# Check Amazon DynamoDB tables
aws dynamodb list-tables --region us-west-2
```

## Sample Queries

The AWS Lambda function supports these MCP tool operations:

### Device Management Operations
```python
# List all devices
{
  "tool_name": "list_devices"
}

# Get specific device settings
{
  "tool_name": "get_device_settings",
  "device_id": "DG-10016"
}
```

### WiFi Network Operations
```python
# List WiFi networks for a device
{
  "tool_name": "list_wifi_networks",
  "device_id": "DG-10005"
}

# Update WiFi SSID
{
  "tool_name": "update_wifi_ssid",
  "device_id": "DG-10016",
  "network_id": "WN-1016-1",
  "ssid": "NewNetworkName"
}

# Update WiFi security
{
  "tool_name": "update_wifi_security",
  "device_id": "DG-10005",
  "network_id": "WN-1005-1",
  "security_type": "WPA3"
}
```

### User and Activity Operations
```python
# List all users
{
  "tool_name": "list_users"
}

# Query user activity
{
  "tool_name": "query_user_activity",
  "start_date": "2023-06-20",
  "end_date": "2023-06-25",
  "user_id": "user123"  # optional
}
```

### Expected Response Format
```json
{
  "statusCode": 200,
  "body": [
    {
      "device_id": "DG-10016",
      "name": "Factory Sensor A3",
      "model": "Sensor-X",
      "connection_status": "Connected",
      "ip_address": "192.168.1.16",
      "last_connected": "2023-06-26T18:26:46"
    }
  ]
}
```

## Cleanup Instructions

### Remove AWS Lambda Function

```bash
# Delete function
aws lambda delete-function --function-name DeviceManagementLambda

# Delete function logs
aws logs delete-log-group --log-group-name "/aws/lambda/DeviceManagementLambda"
```

### Remove Amazon DynamoDB Tables

```bash
# Delete all tables
aws dynamodb delete-table --table-name Devices
aws dynamodb delete-table --table-name DeviceSettings
aws dynamodb delete-table --table-name WifiNetworks
aws dynamodb delete-table --table-name Users
aws dynamodb delete-table --table-name UserActivities
```

### Remove IAM Roles (if created by deployment)

```bash
# Detach policies and delete role
aws iam detach-role-policy \
  --role-name DeviceManagementLambdaRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

aws iam delete-role --role-name DeviceManagementLambdaRole
```

### Clean Up Local Files

```bash
# Remove deployment artifacts
rm -f function.zip
rm -f response.json
rm -rf __pycache__/
rm -f .env  # Contains sensitive data
```

## Configuration

### Amazon DynamoDB Tables Schema

#### Devices Table
- **Primary Key**: `device_id` (String)
- **Attributes**: `name`, `model`, `connection_status`, `ip_address`, `mac_address`, `firmware_version`, `last_connected`

#### DeviceSettings Table
- **Primary Key**: `device_id` (String)
- **Attributes**: `settings` (Map), `last_updated`

#### WifiNetworks Table
- **Primary Key**: `device_id` (String), `network_id` (String)
- **Attributes**: `ssid`, `security_type`, `enabled`, `channel`, `signal_strength`

#### Users Table
- **Primary Key**: `user_id` (String)
- **Attributes**: `username`, `email`, `role`, `created_at`, `last_login`

#### UserActivities Table
- **Primary Key**: `user_id` (String), `timestamp` (String)
- **Global Secondary Index**: `ActivityTypeIndex` on `activity_type`
- **Attributes**: `activity_type`, `description`, `ip_address`, `device_id`

### Environment Variables

```bash
# AWS Configuration
AWS_REGION=us-west-2

# AWS Lambda Configuration
LAMBDA_FUNCTION_NAME=DeviceManagementLambda
LAMBDA_ROLE_NAME=DeviceManagementLambdaRole

# IAM Configuration
AGENT_GATEWAY_POLICY_NAME=AgentGatewayAccess
AGENT_GATEWAY_ROLE_NAME=AgentGatewayAccessRole
```

## Troubleshooting

### Common Issues

**AWS Lambda deployment failures**:
- Check IAM permissions for Lambda service
- Verify Python dependencies are compatible
- Ensure deployment package size is under AWS limits

**Amazon DynamoDB access errors**:
- Verify IAM role has DynamoDB permissions
- Check table names match configuration
- Ensure tables exist in the correct region

**Function timeout errors**:
- Increase AWS Lambda timeout setting
- Optimize DynamoDB queries with indexes
- Check for infinite loops in code

### Debug Commands

```bash
# Test function locally
python test_lambda.py

# Check function logs
aws logs tail /aws/lambda/DeviceManagementLambda --follow

# Test DynamoDB connectivity
aws dynamodb scan --table-name Devices --max-items 5

# Validate function configuration
aws lambda get-function --function-name DeviceManagementLambda
```

## Integration with Other Modules

- **Gateway Module**: Exposes this AWS Lambda function as MCP tools through Gateway Target
- **Agent Runtime Module**: Invokes these tools through the Gateway to execute device operations
- **Frontend Module**: Indirectly uses these operations through the Agent Runtime for user interactions