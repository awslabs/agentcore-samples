#!/bin/bash

################################################################################
# Device Management Lambda Function - Deployment Script
#
# This script deploys the Device Management Lambda function along with all
# required IAM roles, policies, and permissions. It handles both initial
# deployment and updates to existing Lambda functions.
#
# COMPONENTS DEPLOYED:
#   1. Lambda Function: Device management backend with MCP tools
#   2. Lambda IAM Role: Execution role with DynamoDB permissions
#   3. Agent Gateway IAM Role: Role for gateway to invoke Lambda
#   4. Agent Gateway IAM Policy: Permissions for gateway operations
#   5. Lambda Package: Zipped function code with dependencies
#
# IAM RESOURCES CREATED:
#   - DeviceManagementLambdaRole: Lambda execution role
#     * AWSLambdaBasicExecutionRole (managed policy)
#     * DeviceManagementDynamoDBAccess (inline policy)
#   
#   - AgentGatewayAccessRole: Gateway access role
#     * Assumed by bedrock-agentcore.amazonaws.com
#     * AgentGatewayAccess policy attached
#   
#   - AgentGatewayAccess: Policy for gateway operations
#     * bedrock-agentcore:*Gateway*
#     * bedrock-agentcore:*WorkloadIdentity
#     * bedrock-agentcore:*CredentialProvider
#
# DYNAMODB PERMISSIONS:
#   - GetItem, Query, Scan, UpdateItem on tables:
#     * Devices
#     * DeviceSettings
#     * WifiNetworks
#     * Users
#     * UserActivities (including ActivityTypeIndex GSI)
#
# CONFIGURATION:
#   Environment variables (with defaults):
#   - LAMBDA_FUNCTION_NAME: DeviceManagementLambda
#   - LAMBDA_ROLE_NAME: DeviceManagementLambdaRole
#   - AGENT_GATEWAY_POLICY_NAME: AgentGatewayAccess
#   - AGENT_GATEWAY_ROLE_NAME: AgentGatewayAccessRole
#   - AWS_REGION: us-west-2
#
# DEPLOYMENT PROCESS:
#   1. Load environment variables from .env (if exists)
#   2. Create Agent Gateway IAM policy and role
#   3. Package Lambda function with dependencies
#   4. Create or update Lambda function
#   5. Export Lambda ARN to lambda_arn.txt
#   6. Update gateway/.env with Lambda ARN and Role ARN
#
# USAGE:
#   ./deploy.sh
#
# EXIT CODES:
#   0 - Deployment successful
#   Non-zero - Deployment failed (AWS CLI error)
#
# OUTPUTS:
#   - lambda_arn.txt: Contains Lambda ARN for gateway configuration
#   - gateway/.env: Updated with LAMBDA_ARN and ROLE_ARN
#   - lambda_package.zip: Temporary package file (cleaned up)
#
# ENVIRONMENT FILES UPDATED:
#   ../gateway/.env:
#   - LAMBDA_ARN: ARN of deployed Lambda function
#   - ROLE_ARN: ARN of Agent Gateway access role
#
# NOTES:
#   - Creates IAM resources if they don't exist
#   - Updates existing Lambda function code if already deployed
#   - Waits 10 seconds for IAM role propagation
#   - Supports both macOS (BSD sed) and Linux (GNU sed)
#   - Cleans up temporary package directory and zip file
#   - Requires AWS credentials with appropriate permissions
#
# PREREQUISITES:
#   - AWS CLI installed and configured
#   - Python 3.12 runtime available in AWS Lambda
#   - requirements.txt with Lambda dependencies
#   - lambda_function.py with handler implementation
#
################################################################################

# Load environment variables from .env file if it exists
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

# Configuration
LAMBDA_FUNCTION_NAME=${LAMBDA_FUNCTION_NAME:-"DeviceManagementLambda"}
LAMBDA_ROLE_NAME=${LAMBDA_ROLE_NAME:-"DeviceManagementLambdaRole"}
AGENT_GATEWAY_POLICY_NAME=${AGENT_GATEWAY_POLICY_NAME:-"AgentGatewayAccess"}
AGENT_GATEWAY_ROLE_NAME=${AGENT_GATEWAY_ROLE_NAME:-"AgentGatewayAccessRole"}
REGION=${AWS_REGION:-"us-west-2"}
ZIP_FILE="lambda_package.zip"

# Get AWS account ID
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
echo "AWS Account ID: $ACCOUNT_ID"

echo "Packaging Lambda function..."

# Create a temporary directory for packaging
mkdir -p package

# Install dependencies to the package directory
pip install -r requirements.txt --target ./package

# Copy Lambda function files to the package directory
cp lambda_function.py ./package/

# Create the ZIP file
cd package
zip -r ../$ZIP_FILE .
cd ..

echo "Lambda package created: $ZIP_FILE"

# Function to create Agent Gateway IAM policy and role
create_agent_gateway_iam() {
    echo "Creating Agent Gateway IAM policy and role..."
    
    # Check if the policy already exists
    POLICY_EXISTS=$(aws iam list-policies --query "Policies[?PolicyName=='$AGENT_GATEWAY_POLICY_NAME'].PolicyName" --output text)
    
    if [ -z "$POLICY_EXISTS" ]; then
        echo "Creating AgentGatewayAccess policy..."
        POLICY_ARN=$(aws iam create-policy \
            --policy-name $AGENT_GATEWAY_POLICY_NAME \
            --policy-document '{
                "Version": "2012-10-17",
                "Statement": [
                    {   
                        "Effect": "Allow",
                        "Action": [
                            "bedrock-agentcore:*Gateway*",
                            "bedrock-agentcore:*WorkloadIdentity",
                            "bedrock-agentcore:*CredentialProvider",
                            "bedrock-agentcore:*Token*",
                            "bedrock-agentcore:*Access*"
                        ],
                        "Resource": "arn:aws:bedrock-agentcore:*:*:*gateway*"
                    }
                ]
            }' \
            --query 'Policy.Arn' \
            --output text)
        echo "Created policy with ARN: $POLICY_ARN"
    else
        POLICY_ARN="arn:aws:iam::$ACCOUNT_ID:policy/$AGENT_GATEWAY_POLICY_NAME"
        echo "Policy $AGENT_GATEWAY_POLICY_NAME already exists with ARN: $POLICY_ARN"
    fi
    
    # Check if the role already exists
    ROLE_EXISTS=$(aws iam list-roles --query "Roles[?RoleName=='$AGENT_GATEWAY_ROLE_NAME'].RoleName" --output text)
    
    if [ -z "$ROLE_EXISTS" ]; then
        echo "Creating AgentGatewayAccessRole..."
        GATEWAY_ROLE_ARN=$(aws iam create-role \
            --role-name $AGENT_GATEWAY_ROLE_NAME \
            --assume-role-policy-document "{
                \"Version\": \"2012-10-17\",
                \"Statement\": [
                    {
                        \"Sid\": \"GatewayAssumeRolePolicy\",
                        \"Effect\": \"Allow\",
                        \"Principal\": {
                            \"Service\": \"bedrock-agentcore.amazonaws.com\"
                        },
                        \"Action\": \"sts:AssumeRole\"
                            }
                        }
                    }
                ]
            }" \
            --query 'Role.Arn' \
            --output text)
        
        echo "Created role with ARN: $GATEWAY_ROLE_ARN"
        
        # Attach the policy to the role
        echo "Attaching policy to role..."
        aws iam attach-role-policy \
            --role-name $AGENT_GATEWAY_ROLE_NAME \
            --policy-arn $POLICY_ARN
        
        echo "Policy attached to role successfully"
    else
        GATEWAY_ROLE_ARN="arn:aws:iam::$ACCOUNT_ID:role/$AGENT_GATEWAY_ROLE_NAME"
        echo "Role $AGENT_GATEWAY_ROLE_NAME already exists with ARN: $GATEWAY_ROLE_ARN"
    fi
    
    # Update the gateway .env file with the role ARN
    GATEWAY_ENV_FILE="../gateway/.env"
    if [ -f "$GATEWAY_ENV_FILE" ]; then
        echo "Updating gateway .env file with Agent Gateway Role ARN..."
        
        # Check if ROLE_ARN already exists in the file
        if grep -q "^ROLE_ARN=" "$GATEWAY_ENV_FILE"; then
            # Update existing ROLE_ARN line
            if [[ "$OSTYPE" == "darwin"* ]]; then
                # macOS
                sed -i '' "s|^ROLE_ARN=.*|ROLE_ARN=$GATEWAY_ROLE_ARN|g" "$GATEWAY_ENV_FILE"
            else
                # Linux
                sed -i "s|^ROLE_ARN=.*|ROLE_ARN=$GATEWAY_ROLE_ARN|g" "$GATEWAY_ENV_FILE"
            fi
            echo "Updated existing ROLE_ARN in gateway .env file"
        else
            # Add ROLE_ARN to the file
            echo "" >> "$GATEWAY_ENV_FILE"
            echo "# Agent Gateway Role ARN (auto-updated by device-management deploy.sh)" >> "$GATEWAY_ENV_FILE"
            echo "ROLE_ARN=$GATEWAY_ROLE_ARN" >> "$GATEWAY_ENV_FILE"
            echo "Added ROLE_ARN to gateway .env file"
        fi
    else
        echo "Warning: Gateway .env file not found at $GATEWAY_ENV_FILE"
        echo "You will need to manually add ROLE_ARN=$GATEWAY_ROLE_ARN to the gateway .env file"
    fi
}

# Create Agent Gateway IAM resources
create_agent_gateway_iam

# Check if the Lambda function already exists
FUNCTION_EXISTS=$(aws lambda list-functions --region $REGION --query "Functions[?FunctionName=='$LAMBDA_FUNCTION_NAME'].FunctionName" --output text)

if [ -z "$FUNCTION_EXISTS" ]; then
    echo "Creating IAM role for Lambda function..."
    
    # Create IAM role
    ROLE_ARN=$(aws iam create-role \
        --role-name $LAMBDA_ROLE_NAME \
        --assume-role-policy-document '{
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole"
            }]
        }' \
        --query 'Role.Arn' \
        --output text)
    
    # Attach policies to the role
    aws iam attach-role-policy \
        --role-name $LAMBDA_ROLE_NAME \
        --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
    
    # Create custom policy for DynamoDB access
    aws iam put-role-policy \
        --role-name $LAMBDA_ROLE_NAME \
        --policy-name DeviceManagementDynamoDBAccess \
        --policy-document '{
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": [
                    "dynamodb:GetItem",
                    "dynamodb:Query",
                    "dynamodb:Scan",
                    "dynamodb:UpdateItem"
                ],
                "Resource": [
                    "arn:aws:dynamodb:us-west-2:*:table/Devices",
                    "arn:aws:dynamodb:us-west-2:*:table/DeviceSettings",
                    "arn:aws:dynamodb:us-west-2:*:table/WifiNetworks",
                    "arn:aws:dynamodb:us-west-2:*:table/Users",
                    "arn:aws:dynamodb:us-west-2:*:table/UserActivities",
                    "arn:aws:dynamodb:us-west-2:*:table/UserActivities/index/ActivityTypeIndex"
                ]
            }]
        }'
    
    echo "Waiting for role to propagate..."
    sleep 10
    
    echo "Creating Lambda function..."
    aws lambda create-function \
        --function-name $LAMBDA_FUNCTION_NAME \
        --runtime python3.12 \
        --handler lambda_function.lambda_handler \
        --role $ROLE_ARN \
        --zip-file fileb://$ZIP_FILE \
        --timeout 30 \
        --memory-size 256 \
        --region $REGION
else
    echo "Updating existing Lambda function..."
    aws lambda update-function-code \
        --function-name $LAMBDA_FUNCTION_NAME \
        --zip-file fileb://$ZIP_FILE \
        --region $REGION
fi

# Clean up
rm -rf package
rm -f $ZIP_FILE

echo "Deployment completed successfully!"
echo "Lambda function: $LAMBDA_FUNCTION_NAME"
echo "Lambda ARN: $(aws lambda get-function --function-name $LAMBDA_FUNCTION_NAME --region $REGION --query 'Configuration.FunctionArn' --output text)"

# Export the Lambda ARN to a file for use by the gateway module
LAMBDA_ARN=$(aws lambda get-function --function-name $LAMBDA_FUNCTION_NAME --region $REGION --query 'Configuration.FunctionArn' --output text)
echo "LAMBDA_ARN=$LAMBDA_ARN" > lambda_arn.txt
echo "Lambda ARN saved to lambda_arn.txt"

# Update the gateway .env file with the Lambda ARN
GATEWAY_ENV_FILE="../gateway/.env"
if [ -f "$GATEWAY_ENV_FILE" ]; then
    echo "Updating gateway .env file with Lambda ARN..."
    
    # Check if LAMBDA_ARN already exists in the file
    if grep -q "^LAMBDA_ARN=" "$GATEWAY_ENV_FILE"; then
        # Update existing LAMBDA_ARN line
        if [[ "$OSTYPE" == "darwin"* ]]; then
            # macOS
            sed -i '' "s|^LAMBDA_ARN=.*|LAMBDA_ARN=$LAMBDA_ARN|g" "$GATEWAY_ENV_FILE"
        else
            # Linux
            sed -i "s|^LAMBDA_ARN=.*|LAMBDA_ARN=$LAMBDA_ARN|g" "$GATEWAY_ENV_FILE"
        fi
        echo "Updated existing LAMBDA_ARN in gateway .env file"
    else
        # Add LAMBDA_ARN to the file
        echo "" >> "$GATEWAY_ENV_FILE"
        echo "# Lambda configuration (auto-updated by device-management deploy.sh)" >> "$GATEWAY_ENV_FILE"
        echo "LAMBDA_ARN=$LAMBDA_ARN" >> "$GATEWAY_ENV_FILE"
        echo "Added LAMBDA_ARN to gateway .env file"
    fi
else
    echo "Warning: Gateway .env file not found at $GATEWAY_ENV_FILE"
    echo "You will need to manually add LAMBDA_ARN=$LAMBDA_ARN to the gateway .env file"
fi
