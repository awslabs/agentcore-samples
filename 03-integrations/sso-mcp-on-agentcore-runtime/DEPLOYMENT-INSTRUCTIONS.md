# Enterprise CloudWatch MCP Server V2 - Deployment Instructions for Fabio

## 🎯 Quick Overview

This package contains everything you need to deploy the Enterprise CloudWatch MCP Server V2 with AWS Identity Center integration. The server addresses all your feedback requirements:

✅ **Official AWS MCP Integration** - Uses CloudWatch APIs following awslabs patterns  
✅ **Identity Center Authentication** - Replaces Cognito with enterprise SSO  
✅ **Multi-Tenant Support** - User isolation and permission propagation  
✅ **Cross-Account Access** - Secure access across multiple AWS accounts  
✅ **Production Ready** - Complete automation and enterprise security  

## 🚀 30-Second Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure your Identity Center (interactive)
python setup-identity-center.py

# 3. Deploy to AWS
python deploy-server.py

# 4. Configure Kiro IDE
python setup-kiro.py

# 5. Test everything
python test-connection.py
```

## 📋 What You Need to Provide

### Identity Center Information
The setup script will automatically discover most details, but you'll need:

1. **Your Identity Center Email** - Your user email in Identity Center
2. **Target Account IDs** - AWS accounts you want cross-account access to
3. **AWS Region** - Where your Identity Center is located (auto-detected)

### AWS Permissions
Your AWS user/role needs these permissions:
- Identity Center access (`sso:*`, `sso-admin:*`)
- Lambda deployment (`lambda:*`)
- API Gateway management (`apigateway:*`)
- CloudWatch access (`logs:*`, `cloudwatch:*`)

## 🔧 Your Identity Center Configuration

The setup script will discover your Identity Center instance automatically. Here's what it finds:

```bash
python setup-identity-center.py
```

**Example Output:**
```
🔍 Discovering Identity Center configuration...
✅ Current AWS Account: 123456789012
✅ Identity Center Instance Found:
   ARN: arn:aws:sso:::instance/ssoins-7223216897069a26
   Identity Store ID: d-1234567890

📝 Please provide the following information:
Your Identity Center email address: fabio@yourcompany.com
Target account #1 (or Enter to finish): 987654321098
Target account #2 (or Enter to finish): 555666777888

✅ Configuration saved to config.json
```

## 🎭 Demo Commands for Your Team

Once deployed, you can use these natural language commands in Kiro:

### Health & Authentication
```
"Check server health"
"What's my user info?"
```

### CloudWatch Logs
```
"List my log groups"
"Search logs in /aws/lambda/my-function for ERROR"
"Show recent logs from /aws/apigateway/my-api"
```

### Cross-Account Access
```
"List log groups in account 987654321098"
"Search logs in account 555666777888 for timeout"
```

### CloudWatch Metrics & Alarms
```
"List CloudWatch metrics for AWS/Lambda"
"Show alarms in ALARM state"
"List metrics in namespace AWS/EC2"
```

## 🔐 Security Features You Requested

### Identity Center Integration
- ✅ Replaces Cognito with AWS Identity Center
- ✅ Uses your existing enterprise SSO
- ✅ Respects Identity Center permission sets
- ✅ Full user context and audit trails

### Multi-Tenant Architecture
- ✅ User isolation per request
- ✅ Permission propagation from Identity Center
- ✅ Audit context with user attribution
- ✅ Secure credential handling

### Cross-Account Security
- ✅ IAM role assumption with external IDs
- ✅ Prevents confused deputy attacks
- ✅ Temporary session credentials
- ✅ CloudTrail audit for all operations

## 📊 Architecture Overview

```
Kiro IDE → MCP Server (Lambda) → Identity Center → CloudWatch APIs
    ↓              ↓                    ↓              ↓
Natural Lang.  Authentication    User Context    Cross-Account
Commands       & Authorization   Propagation     Resource Access
```

## 🧪 Testing Your Deployment

### Automated Testing
```bash
python test-connection.py
```

**Expected Results:**
- ✅ AWS Credentials: PASS
- ✅ Identity Center Access: PASS  
- ✅ CloudWatch Access: PASS
- ✅ Cross-Account Access: PASS (if configured)
- ✅ Kiro Configuration: PASS

### Manual Testing in Kiro
1. Restart Kiro IDE
2. Open chat and try: `"Check server health"`
3. Try: `"List my log groups"`
4. Try: `"What's my user info?"`

## 🔧 Customization Options

### Configuration File (config.json)
After running setup, you can customize:

```json
{
  "identity_center": {
    "instance_arn": "arn:aws:sso:::instance/ssoins-YOUR-INSTANCE",
    "region": "us-east-1",
    "account_id": "123456789012"
  },
  "user_config": {
    "default_user_email": "fabio@yourcompany.com",
    "session_duration": 3600
  },
  "cross_account": {
    "target_accounts": ["987654321098", "555666777888"],
    "role_name": "CrossAccountMCPRole",
    "external_id": "mcp-cross-account-access"
  }
}
```

### Cross-Account Role Setup
For each target account, create this IAM role:

**Role Name:** `CrossAccountMCPRole`
**Trust Policy:**
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "AWS": "arn:aws:iam::YOUR-SOURCE-ACCOUNT:root"
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

**Permissions Policy:**
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
        "cloudwatch:DescribeAlarms"
      ],
      "Resource": "*"
    }
  ]
}
```

## 🚨 Troubleshooting

### Common Issues
1. **"Identity Center not found"** → Ensure Identity Center is enabled in your account
2. **"Permission denied"** → Check IAM permissions listed above
3. **"MCP not connecting"** → Restart Kiro IDE completely
4. **"Cross-account denied"** → Verify target account roles exist

### Debug Commands
```bash
# Check AWS access
aws sts get-caller-identity

# Test Identity Center
aws sso-admin list-instances

# Test CloudWatch
aws logs describe-log-groups --limit 1

# Full diagnostic
python test-connection.py
```

## 📞 Support & Next Steps

### If You Need Help
1. Run `python test-connection.py` for diagnostics
2. Check `TROUBLESHOOTING.md` for common issues
3. Review CloudWatch logs for the Lambda function
4. Contact the team with specific error messages

### After Successful Deployment
1. **Train your team** on the available commands
2. **Set up cross-account roles** in target accounts
3. **Configure Identity Center permissions** as needed
4. **Monitor usage** through CloudWatch and CloudTrail

## 🎉 Success Indicators

You'll know everything is working when:
- ✅ `python test-connection.py` shows all tests passing
- ✅ Kiro MCP view shows "enterprise-cloudwatch-v2" as connected
- ✅ `"Check server health"` returns your user info
- ✅ `"List my log groups"` shows your CloudWatch log groups
- ✅ Cross-account commands work (if configured)

## 📦 Package Contents Summary

- **enterprise-cloudwatch-mcp-server.py** - Main MCP server
- **setup-identity-center.py** - Interactive Identity Center setup
- **deploy-server.py** - Automated AWS deployment
- **setup-kiro.py** - Kiro IDE configuration
- **test-connection.py** - End-to-end testing
- **Complete documentation** - Setup, demo, troubleshooting guides

**Ready to deploy! This addresses all your feedback and provides enterprise-grade CloudWatch access through natural language in Kiro IDE.**