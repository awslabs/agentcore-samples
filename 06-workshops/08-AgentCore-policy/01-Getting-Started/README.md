# AgentCore Policy - Getting Started Demo

A complete, hands-on demo of implementing policy-based controls for AI agents using Amazon Bedrock AgentCore Policy.

## 🚀 Quick Start

1. **Install dependencies**: `pip install -r requirements.txt`
2. **Open notebook**: `jupyter notebook AgentCore-Policy-Demo.ipynb`
3. **Follow the steps** in the notebook

> **Note**: Requires boto3 version 1.42.0 or higher for native policy-registry API support.

## Overview

This demo provides a complete walkthrough of implementing policy-based controls for AI agent interactions with tools through AgentCore Gateway.

## What You'll Learn

- ✅ Deploy Lambda functions as agent tools
- ✅ Setup AgentCore Gateway with multiple Lambda targets
- ✅ Create and configure Policy Engines
- ✅ Write Cedar policies for fine-grained access control
- ✅ Test policy enforcement with real AI agent requests
- ✅ Understand ALLOW and DENY scenarios

## Demo Scenario

We'll build an **insurance underwriting processing system** with policy controls:

- **Tools**: 
  - **ApplicationTool** - Creates insurance applications with geographic and eligibility validation
    - Parameters: `applicant_region` (string), `coverage_amount` (integer)
  - **RiskModelTool** - Invokes external risk scoring model with governance controls
    - Parameters: `API_classification` (string), `data_governance_approval` (boolean)
  - **ApprovalTool** - Approves high-value or high-risk underwriting decisions
    - Parameters: `claim_amount` (integer), `risk_level` (string)

- **Policy Rule**: Only allow insurance applications with coverage under $1M
- **Test Cases**: 
  - ✅ $750K application (ALLOWED)
  - ❌ $1.5M application (DENIED)

> **Important**: Policies can only reference parameters defined in the Gateway target schema. Each tool has its own schema with specific parameters that can be used in policy conditions.

## Prerequisites

Before starting, ensure you have:

- AWS CLI configured with appropriate credentials
- Python 3.10+ with boto3 1.42.0+ installed
- `bedrock_agentcore_starter_toolkit` package installed
- `strands` package installed (for AI agent functionality)
- Access to AWS Lambda (for creating target functions)
- Access to Amazon Bedrock (for AI agent model)
- Working in **us-east-1 (N.Virginia)** region

> **Note**: The gateway setup script will automatically create the necessary IAM roles with proper trust policies for AgentCore service.

## Setup Instructions

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

**Important**: Ensure boto3 version 1.42.0 or higher is installed:

```bash
pip install --upgrade boto3
```

### 2. Open the Demo Notebook

```bash
jupyter notebook AgentCore-Policy-Demo.ipynb
```

### 3. Follow the Notebook

The notebook guides you through:

1. **Environment Setup** - Verify credentials and dependencies
2. **Lambda Deployment** - Deploy 3 Lambda functions (ApplicationTool, RiskModelTool, ApprovalTool)
3. **Gateway Setup** - Configure AgentCore Gateway with OAuth and attach Lambda targets
4. **Agent Testing** - Test the AI agent with access to all tools (no policies yet)
5. **Policy Engine** - Create policy engine and attach to gateway
6. **Cedar Policies** - Write and deploy Cedar policies for access control
7. **Policy Testing** - Test ALLOW and DENY scenarios with real AI agent requests
8. **Cleanup** - Remove all created resources

> **Note**: The demo uses boto3's native policy-registry client (available in boto3 1.42.0+) and the Strands framework for AI agent functionality.

## Project Structure

```
Getting-Started/
├── AgentCore-Policy-Demo.ipynb    # Main demo notebook
├── README.md                       # This file
├── requirements.txt                # Python dependencies
├── config.json                     # Generated configuration file
└── scripts/                        # Supporting scripts
    ├── setup_gateway.py            # Gateway setup with auto IAM role creation
    ├── agent_with_tools.py         # AI agent session manager
    ├── get_client_secret.py        # Retrieve Cognito client secret
    ├── policy_generator.py         # NL to Cedar generation
    └── lambda-target-setup/        # Lambda deployment scripts
        ├── deploy_lambdas.py       # Deploy all 3 Lambda functions
        ├── application_tool.js     # ApplicationTool Lambda code
        ├── risk_model_tool.js      # RiskModelTool Lambda code
        └── approval_tool.js        # ApprovalTool Lambda code
```

## Key Concepts

### AgentCore Gateway

A MCP like client that allows agents to access tools.

### Policy Engine

A collection of Cedar policies that evaluates requests against defined rules in real-time.

### Cedar Policy Language

A declarative policy language with this structure:

```cedar
permit(
  principal,              // Who can access
  action,                 // What action they can perform  
  resource                // What resource they can access
) when {
  conditions              // Under what conditions
};
```

### Policy Modes

- **LOG_ONLY**: Evaluates policies but doesn't block requests (for testing)
- **ENFORCE**: Actively blocks requests that violate policies (for production)

## Example Policy

```cedar
permit(
  principal,
  action == AgentCore::Action::"ApplicationToolTarget___create_application",
  resource == AgentCore::Gateway::"<gateway-arn>"
) when {
  context.input.coverage_amount <= 1000000
};
```

This policy:
- Allows insurance application creation with coverage under $1M
- Denies applications with coverage of $1M or more
- Applies to the ApplicationTool target
- Evaluates the `coverage_amount` parameter in real-time

> **Key Insight**: When a Policy Engine is attached to a Gateway in ENFORCE mode, the default action is DENY. You must explicitly create permit policies for each tool you want to allow access to.

## Architecture

```
┌─────────────┐
│   AI Agent  │
└──────┬──────┘
       │ Tool Call Request
       ▼
┌─────────────────────┐
│  AgentCore Gateway  │
│  + OAuth Auth       │
└──────┬──────────────┘
       │ Policy Check
       ▼
┌─────────────────────┐
│   Policy Engine     │
│   (Cedar Policies)  │
└──────┬──────────────┘
       │ ALLOW / DENY
       ▼
┌─────────────────────┐
│   Lambda Target     │
│   (RefundTool)      │
└─────────────────────┘
```

## Testing

The demo includes comprehensive testing with a real AI agent:

### Before Policy Engine Attachment
- Agent can list all 3 tools
- Agent can invoke all tools without restrictions
- No policy enforcement

### After Policy Engine Attachment (Empty)
- Agent cannot list any tools (default DENY)
- Agent cannot invoke any tools
- All requests blocked

### After Adding Application Policy
- Agent can list ApplicationTool only
- Agent can create applications under $1M ✅
- Agent cannot create applications over $1M ❌
- Other tools remain blocked

### Test 1: ALLOW Scenario ✅
- Request: Create application with $750K coverage
- Expected: ALLOWED
- Reason: $750K <= $1M
- Result: Lambda executes, application created

### Test 2: DENY Scenario ❌
- Request: Create application with $1.5M coverage
- Expected: DENIED
- Reason: $1.5M > $1M
- Result: Policy blocks request, Lambda never executes

## Advanced Features

### Multiple Conditions

```cedar
permit(...) when {
  context.input.coverage_amount <= 1000000 &&
  has(context.input.applicant_region) &&
  context.input.applicant_region == "US"
};
```

### Region-Based Conditions

```cedar
permit(...) when {
  context.input.applicant_region in ["US", "CA", "UK"]
};
```

### Risk Model Governance

```cedar
permit(
  principal,
  action == AgentCore::Action::"RiskModelToolTarget___invoke_risk_model",
  resource == AgentCore::Gateway::"<gateway-arn>"
) when {
  context.input.API_classification == "public" &&
  context.input.data_governance_approval == true
};
```

### Approval Thresholds

```cedar
permit(
  principal,
  action == AgentCore::Action::"ApprovalToolTarget___approve_underwriting",
  resource == AgentCore::Gateway::"<gateway-arn>"
) when {
  context.input.claim_amount <= 100000 &&
  context.input.risk_level in ["low", "medium"]
};
```

### Deny Policies

```cedar
forbid(...) when {
  context.input.coverage_amount > 10000000
};
```

## Monitoring and Debugging

### CloudWatch Logs

Policy decisions are logged to CloudWatch:

- **Gateway Logs**: Request/response details
- **Policy Engine Logs**: Policy evaluation results
- **Lambda Logs**: Tool execution details

### Common Issues

1. **Policy Not Enforcing**
   - Verify ENFORCE mode (not LOG_ONLY)
   - Check policy status is ACTIVE
   - Confirm gateway attachment

2. **All Requests Denied**
   - Review policy conditions
   - Verify action name matches target
   - Check resource ARN matches gateway

3. **Authentication Failures**
   - Verify OAuth credentials
   - Check token endpoint accessibility
   - Ensure client_id and client_secret are correct

4. **Module Import Errors**
   - Ensure boto3 1.42.0+ is installed: `pip install --upgrade boto3`
   - Ensure strands is installed: `pip install strands`
   - Restart Jupyter kernel after updating dependencies
   - Clear Python cache: `rm -rf scripts/__pycache__`

5. **Agent Session Errors**
   - If you see `MCPClientInitializationError`, restart the notebook kernel
   - Ensure config.json has the client_secret field populated
   - Run `scripts/get_client_secret.py` to retrieve the secret if missing

6. **AWS Token Expired**
   - Refresh AWS credentials: `aws sso login` or `aws configure`
   - Restart notebook kernel to pick up new credentials
   - Re-run cells from the beginning


## Additional Resources

- **Cedar Policy Language**: [Cedar Documentation](https://docs.cedarpolicy.com/)
- **Amazon Bedrock AgentCore Policy**: [AWS AgentCore Documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy.html)

---

**Happy Building!** 🚀
