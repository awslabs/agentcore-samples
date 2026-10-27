# Enterprise CloudWatch MCP Server V2 - Complete Setup Guide

## 🎯 Overview
This guide will walk you through setting up the Enterprise CloudWatch MCP Server V2 with AWS Identity Center integration for secure cross-account CloudWatch access through Kiro IDE.

## 📋 Prerequisites

### Required Software
- **AWS CLI** - Configured with appropriate permissions
- **Python 3.8+** - With pip package manager
- **Kiro IDE** - Latest version installed
- **Git** (optional) - For cloning and version control

### Required AWS Permissions
Your AWS user/role needs these permissions:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sso:ListInstances",
        "sso-admin:ListInstances",
        "sso-admin:ListPermissionSets",
        "identitystore:ListUsers",
        "identitystore:DescribeUser",
        "lambda:CreateFunction",
        "lambda:UpdateFunctionCode",
        "lambda:AddPermission",
        "apigateway:*",
        "iam:PassRole",
        "logs:DescribeLogGroups",
        "cloudwatch:ListMetrics",
        "cloudwatch:DescribeAlarms"
      ],
      "Resource": "*"
    }
  ]
}
```

### AWS Identity Center Requirements
- AWS Identity Center must be enabled in your account
- You must have a user account in Identity Center
- Access to manage permission sets (for cross-account setup)

## 🚀 Step-by-Step Setup

### Step 1: Prepare Your Environment

1. **Install Python Dependencies**
   ```bash
   pip install boto3 requests
   ```

2. **Configure AWS CLI**
   ```bash
   aws configure
   # Enter your AWS Access Key ID, Secret Access Key, Region, and Output format
   ```

3. **Verify AWS Access**
   ```bash
   aws sts get-caller-identity
   ```

### Step 2: Configure Identity Center

1. **Run the Identity Center Discovery Script**
   ```bash
   python setup-identity-center.py
   ```

2. **Provide Required Information**
   - Your Identity Center email address
   - Target AWS account IDs for cross-account access
   - Confirm discovered Identity Center instance details

3. **Verify Configuration**
   - The script will create `config.json` with your settings
   - Review the file to ensure all details are correct

### Step 3: Deploy the MCP Server

1. **Run the Deployment Script**
   ```bash
   python deploy-server.py
   ```

2. **Monitor Deployment Progress**
   - Lambda function creation/update
   - API Gateway setup
   - Kiro configuration generation

3. **Note the Deployment Details**
   - Lambda Function ARN
   - API Gateway URL
   - Generated Kiro configuration

### Step 4: Configure Kiro IDE

1. **Run the Kiro Setup Script**
   ```bash
   python setup-kiro.py
   ```

2. **Restart Kiro IDE**
   - Close Kiro completely
   - Restart to load the new MCP server configuration

3. **Verify MCP Server Connection**
   - Open Kiro's MCP Server view in the feature panel
   - Look for "enterprise-cloudwatch-v2" server
   - Status should show as "Connected"

### Step 5: Test the Setup

1. **Run the Connection Test**
   ```bash
   python test-connection.py
   ```

2. **Test in Kiro IDE**
   - Open a chat in Kiro
   - Try: "Check server health"
   - Try: "List my log groups"
   - Try: "What's my user info?"

## 🔧 Configuration Details

### Identity Center Configuration
The `config.json` file contains your Identity Center settings:
```json
{
  "identity_center": {
    "instance_arn": "arn:aws:sso:::instance/ssoins-xxxxxxxxx",
    "region": "us-east-1",
    "account_id": "123456789012"
  },
  "user_config": {
    "default_user_email": "your-email@company.com",
    "session_duration": 3600
  }
}
```

### Cross-Account Setup
For cross-account access, you need to set up IAM roles in target accounts:

1. **Create IAM Role in Target Account**
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Principal": {
           "AWS": "arn:aws:iam::SOURCE-ACCOUNT:root"
         },
         "Action": "sts:AssumeRole",
         "Condition": {
           "StringEquals": {
             "sts:ExternalId": "mcp-cross-account-access"
           }
         }
       }
     ]
   }
   ```

2. **Attach CloudWatch Permissions**
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Action": [
           "logs:DescribeLogGroups",
           "logs:DescribeLogStreams",
           "logs:FilterLogEvents",
           "cloudwatch:ListMetrics",
           "cloudwatch:DescribeAlarms",
           "cloudwatch:GetMetricStatistics"
         ],
         "Resource": "*"
       }
     ]
   }
   ```

## 🎭 Usage Examples

### Basic Commands
```
"Check server health"
"What's my user info?"
"List my log groups"
```

### CloudWatch Logs
```
"Search logs in /aws/lambda/my-function for ERROR"
"Show recent logs from /aws/apigateway/my-api"
"List log groups with prefix /aws/lambda"
```

### Cross-Account Access
```
"List log groups in account 123456789012"
"Search logs in account 123456789012 for timeout"
```

### CloudWatch Metrics and Alarms
```
"List CloudWatch metrics for AWS/Lambda"
"Show alarms in ALARM state"
"List metrics in namespace AWS/EC2"
```

## 🔍 Troubleshooting

### Common Issues

1. **"Identity Center not found"**
   - Ensure Identity Center is enabled in your account
   - Check AWS CLI region matches Identity Center region
   - Verify you have sso:ListInstances permission

2. **"Access denied to Identity Center"**
   - Add required Identity Center permissions to your user/role
   - Check if you're using the correct AWS credentials

3. **"Lambda deployment failed"**
   - Ensure you have lambda:CreateFunction permission
   - Check if IAM role for Lambda execution exists
   - Verify region settings in configuration

4. **"MCP server not connecting in Kiro"**
   - Restart Kiro IDE completely
   - Check MCP Server view for error messages
   - Verify kiro-mcp-config.json was created correctly

5. **"Cross-account access denied"**
   - Verify IAM role exists in target account
   - Check external ID matches configuration
   - Ensure trust relationship allows your source account

### Debug Commands

```bash
# Check AWS credentials
aws sts get-caller-identity

# Test Identity Center access
aws sso-admin list-instances

# Check Lambda function
aws lambda list-functions --query 'Functions[?contains(FunctionName, `enterprise-cloudwatch-mcp`)]'

# Test cross-account role
aws sts assume-role --role-arn arn:aws:iam::TARGET-ACCOUNT:role/CrossAccountMCPRole --role-session-name test --external-id mcp-cross-account-access
```

## 📞 Support

If you encounter issues:

1. **Run the test script**: `python test-connection.py`
2. **Check the logs**: Look at CloudWatch logs for the Lambda function
3. **Verify configuration**: Ensure all settings in `config.json` are correct
4. **Review permissions**: Confirm all required AWS permissions are granted

## 🎉 Success Indicators

You'll know the setup is successful when:
- ✅ All tests in `test-connection.py` pass
- ✅ MCP server shows "Connected" in Kiro
- ✅ "Check server health" command works in Kiro
- ✅ You can list CloudWatch log groups
- ✅ Cross-account commands work (if configured)

**Congratulations! Your Enterprise CloudWatch MCP Server V2 is ready to use.**