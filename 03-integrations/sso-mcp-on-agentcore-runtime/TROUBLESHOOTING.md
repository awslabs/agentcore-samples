# Enterprise CloudWatch MCP Server V2 - Troubleshooting Guide

## 🔍 Common Issues and Solutions

### 1. Identity Center Discovery Issues

#### Problem: "Identity Center not found"
**Symptoms:**
- `setup-identity-center.py` reports no instances found
- Error: "No Identity Center instances found in this account"

**Solutions:**
```bash
# Check if Identity Center is enabled
aws sso-admin list-instances

# Verify you're in the correct region
aws configure get region

# Check your permissions
aws iam get-user
aws sts get-caller-identity
```

**Required Permissions:**
- `sso:ListInstances`
- `sso-admin:ListInstances`

#### Problem: "Access denied to Identity Center"
**Symptoms:**
- Permission errors when running discovery script
- "AccessDenied" in error messages

**Solutions:**
1. Add required permissions to your IAM user/role
2. Ensure you're using the correct AWS credentials
3. Check if MFA is required for your account

### 2. AWS Credentials Issues

#### Problem: "AWS credentials not configured"
**Symptoms:**
- `NoCredentialsError` when running scripts
- "Unable to locate credentials" errors

**Solutions:**
```bash
# Configure AWS CLI
aws configure

# Or use environment variables
export AWS_ACCESS_KEY_ID=your-key
export AWS_SECRET_ACCESS_KEY=your-secret
export AWS_DEFAULT_REGION=us-east-1

# Or use AWS profiles
aws configure --profile your-profile
export AWS_PROFILE=your-profile
```

#### Problem: "Invalid credentials"
**Symptoms:**
- "SignatureDoesNotMatch" errors
- "InvalidAccessKeyId" errors

**Solutions:**
1. Verify your access key and secret key are correct
2. Check if credentials have expired
3. Ensure no extra spaces in credential values
4. Try generating new access keys

### 3. Deployment Issues

#### Problem: "Lambda deployment failed"
**Symptoms:**
- Errors during `deploy-server.py` execution
- "AccessDenied" for Lambda operations

**Solutions:**
```bash
# Check Lambda permissions
aws iam list-attached-user-policies --user-name your-username
aws iam list-attached-role-policies --role-name your-role

# Test Lambda access
aws lambda list-functions

# Required permissions:
# - lambda:CreateFunction
# - lambda:UpdateFunctionCode
# - lambda:AddPermission
# - iam:PassRole
```

#### Problem: "API Gateway creation failed"
**Symptoms:**
- Lambda deploys but API Gateway fails
- "InsufficientPermissions" for API Gateway

**Solutions:**
1. Add API Gateway permissions:
   - `apigateway:*`
   - `execute-api:*`
2. Check region consistency
3. Verify account limits for API Gateway

### 4. Kiro Integration Issues

#### Problem: "MCP server not connecting"
**Symptoms:**
- Server shows "Disconnected" in Kiro MCP view
- No response to MCP commands in Kiro

**Solutions:**
1. **Restart Kiro completely**
   ```bash
   # Close Kiro entirely, then restart
   ```

2. **Check MCP configuration**
   ```bash
   # Verify config file exists
   cat ~/.kiro/settings/mcp.json
   # or
   cat .kiro/settings/mcp.json
   ```

3. **Regenerate configuration**
   ```bash
   python setup-kiro.py
   # Then restart Kiro
   ```

#### Problem: "MCP commands not working"
**Symptoms:**
- Server connected but commands fail
- Error responses in Kiro chat

**Solutions:**
1. Test the underlying API:
   ```bash
   python test-connection.py
   ```

2. Check Lambda function logs:
   ```bash
   aws logs describe-log-groups --log-group-name-prefix /aws/lambda/enterprise-cloudwatch-mcp
   ```

3. Verify API Gateway endpoint:
   ```bash
   curl -X POST https://your-api-id.execute-api.region.amazonaws.com/prod/health
   ```

### 5. Cross-Account Access Issues

#### Problem: "Cross-account access denied"
**Symptoms:**
- Current account commands work
- Cross-account commands fail with permission errors

**Solutions:**
1. **Verify target account role exists**
   ```bash
   # In target account
   aws iam get-role --role-name CrossAccountMCPRole
   ```

2. **Check trust relationship**
   ```json
   {
     "Version": "2012-10-17",
     "Statement": [
       {
         "Effect": "Allow",
         "Principal": {
           "AWS": "arn:aws:iam::SOURCE-ACCOUNT-ID:root"
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

3. **Test role assumption manually**
   ```bash
   aws sts assume-role \
     --role-arn arn:aws:iam::TARGET-ACCOUNT:role/CrossAccountMCPRole \
     --role-session-name test \
     --external-id mcp-cross-account-access
   ```

### 6. CloudWatch Access Issues

#### Problem: "No log groups found"
**Symptoms:**
- Commands execute but return empty results
- "Access denied" for CloudWatch operations

**Solutions:**
1. **Check CloudWatch permissions**
   ```bash
   # Test basic access
   aws logs describe-log-groups --limit 1
   ```

2. **Required CloudWatch permissions:**
   - `logs:DescribeLogGroups`
   - `logs:DescribeLogStreams`
   - `logs:FilterLogEvents`
   - `cloudwatch:ListMetrics`
   - `cloudwatch:DescribeAlarms`

3. **Verify region settings**
   ```bash
   # Check if log groups exist in your region
   aws logs describe-log-groups --region us-east-1
   ```

### 7. Configuration Issues

#### Problem: "Invalid configuration"
**Symptoms:**
- Scripts fail to load config.json
- JSON parsing errors

**Solutions:**
1. **Validate JSON syntax**
   ```bash
   python -m json.tool config.json
   ```

2. **Regenerate configuration**
   ```bash
   rm config.json
   python setup-identity-center.py
   ```

3. **Check file permissions**
   ```bash
   ls -la config.json
   chmod 644 config.json
   ```

## 🔧 Debug Commands

### General Debugging
```bash
# Check AWS identity
aws sts get-caller-identity

# List available regions
aws ec2 describe-regions --query 'Regions[].RegionName'

# Check service availability
aws logs describe-log-groups --limit 1
aws sso-admin list-instances
```

### MCP Server Debugging
```bash
# Test connection
python test-connection.py

# Check Lambda function
aws lambda list-functions --query 'Functions[?contains(FunctionName, `enterprise-cloudwatch-mcp`)]'

# View Lambda logs
aws logs tail /aws/lambda/enterprise-cloudwatch-mcp-ACCOUNT-ID --follow
```

### Kiro Debugging
```bash
# Check MCP configuration
cat ~/.kiro/settings/mcp.json | jq '.mcpServers."enterprise-cloudwatch-v2"'

# Regenerate Kiro config
python setup-kiro.py
```

## 📞 Getting Help

### Step-by-Step Diagnosis
1. **Run the test script**
   ```bash
   python test-connection.py
   ```

2. **Check each component**
   - AWS credentials: `aws sts get-caller-identity`
   - Identity Center: `aws sso-admin list-instances`
   - CloudWatch: `aws logs describe-log-groups --limit 1`
   - Lambda: `aws lambda list-functions`

3. **Review logs**
   - CloudWatch logs for Lambda function
   - Kiro IDE console/logs
   - AWS CloudTrail for API calls

### Common Resolution Steps
1. **Restart everything**
   - Close Kiro completely
   - Re-run setup scripts
   - Restart Kiro

2. **Regenerate configuration**
   ```bash
   rm config.json kiro-mcp-config.json
   python setup-identity-center.py
   python deploy-server.py
   python setup-kiro.py
   ```

3. **Check permissions systematically**
   - AWS IAM permissions
   - Identity Center access
   - Cross-account role setup

## ✅ Success Verification

After resolving issues, verify everything works:

```bash
# 1. Test connection
python test-connection.py

# 2. Check Kiro MCP view
# - Open Kiro
# - Go to MCP Server view
# - Verify "enterprise-cloudwatch-v2" is connected

# 3. Test commands in Kiro
# - "Check server health"
# - "List my log groups"
# - "What's my user info?"
```

## 🎯 Prevention Tips

1. **Use consistent regions** across all AWS services
2. **Test permissions** before deployment
3. **Keep credentials secure** and rotate regularly
4. **Document custom configurations** for your environment
5. **Test cross-account setup** in a development environment first

**Remember: Most issues are related to AWS permissions or configuration. Start with the test script to identify the specific problem area.**