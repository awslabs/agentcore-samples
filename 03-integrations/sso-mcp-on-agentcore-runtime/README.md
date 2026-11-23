# Enterprise CloudWatch MCP Server V2 - Deployment Package

## 🎯 Overview
This package contains the Enterprise CloudWatch MCP Server V2 that integrates with AWS Identity Center for secure cross-account CloudWatch access through Kiro IDE using natural language commands.

## 📦 Package Contents

### Core Files
- `enterprise-cloudwatch-mcp-server.py` - Main MCP server with CloudWatch integration
- `config-template.json` - Configuration template (needs customization)
- `setup-identity-center.py` - Identity Center discovery and setup script
- `deploy-server.py` - Automated deployment script
- `test-connection.py` - Connection and functionality testing

### Documentation
- `SETUP-GUIDE.md` - Complete setup instructions
- `IDENTITY-CENTER-GUIDE.md` - Identity Center configuration guide
- `DEMO-COMMANDS.md` - Available commands and demo script
- `TROUBLESHOOTING.md` - Common issues and solutions

### Kiro Integration
- `kiro-mcp-config.json` - Kiro MCP configuration template
- `setup-kiro.py` - Automated Kiro configuration script

## 🚀 Quick Start

### Step 1: Configure Identity Center
```bash
# 1. Run the discovery script to find your Identity Center details
python setup-identity-center.py

# 2. Follow the prompts to configure your Identity Center instance
# This will update config-template.json with your specific settings
```

### Step 2: Install Dependencies
```bash
# Install required Python packages
pip install -r requirements.txt
```

### Step 3: Configure Kiro
```bash
# Set up Kiro MCP integration
python setup-kiro.py
```

### Step 4: Test the Setup
```bash
# Test the MCP server
python test-connection.py
```

## 🔧 Configuration Requirements

You will need to provide:
- **Identity Center Instance ARN** (discovered automatically)
- **AWS Account ID** (your primary account)
- **User Email** (your Identity Center user)
- **Target Account IDs** (accounts you want to access)

## 📋 Prerequisites

- AWS CLI configured with appropriate permissions
- Python 3.8+ with boto3, requests
- Kiro IDE installed
- AWS Identity Center access

## 🎭 Demo Commands

Once configured, you can use these natural language commands in Kiro:

### Health & Status
- "Check server health"
- "What's my user info?"

### CloudWatch Logs
- "List my log groups"
- "Search logs in [log-group-name] for errors"
- "Show recent logs from [log-group-name]"

### Cross-Account Access
- "List log groups in account [account-id]"
- "Search logs in account [account-id] for [pattern]"

### CloudWatch Metrics & Alarms
- "List CloudWatch metrics"
- "Show alarms in ALARM state"
- "List metrics for AWS/Lambda"

## 🔐 Security Features

- ✅ AWS Identity Center integration
- ✅ Multi-tenant support with user isolation
- ✅ Audit trails for all operations
- ✅ Permission set propagation
- ✅ Secure credential handling

## 📞 Support

For issues or questions:
1. Check `TROUBLESHOOTING.md`
2. Run `python test-connection.py` for diagnostics
3. Review CloudTrail logs for audit information

## 🎉 Ready to Deploy

This package is production-ready and includes:
- Complete automation scripts
- Comprehensive documentation
- Testing and validation tools
- Security best practices
- Multi-account support

**Start with the SETUP-GUIDE.md for detailed instructions.**