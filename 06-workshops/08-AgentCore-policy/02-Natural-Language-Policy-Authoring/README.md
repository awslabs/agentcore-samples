# AgentCore Policy - Natural Language Policy Authoring (NL2Cedar)

A hands-on demo of generating Cedar policies from natural language using Amazon Bedrock AgentCore Policy's NL2Cedar capability.

## 🚀 Quick Start

1. **Install dependencies**: `pip install -r requirements.txt`
2. **Open notebook**: `jupyter notebook NL-Authoring-Policy.ipynb`
3. **Follow the steps** in the notebook

> **Note**: This demo builds on the Getting-Started tutorial. If you haven't completed it, the notebook will automatically set up the required infrastructure.

## Overview

This demo showcases how to write authorization requirements in natural language and automatically convert them to Cedar syntax. The NL2Cedar capability helps you:

- Write policies in plain English instead of Cedar syntax
- Generate multiple policies from multi-line statements
- Create principal-based policies with identity attributes
- Verify that generated policies match your requirements

## What You'll Learn

- ✅ Generate Cedar policies from natural language descriptions
- ✅ Create simple single-statement policies
- ✅ Generate multiple policies from multi-line statements
- ✅ Write principal-scoped policies with identity attributes
- ✅ Understand different policy constructions and patterns

## Prerequisites

Before starting, ensure you have:

- AWS CLI configured with appropriate credentials
- Python 3.10+ with boto3 1.42.0+ installed
- `bedrock_agentcore_starter_toolkit` package installed
- Access to AWS Lambda (for target functions)
- Completed the **01-Getting-Started** tutorial (or let the notebook set it up automatically)

## Demo Scenario

This demo uses the **insurance underwriting system** from the Getting-Started tutorial with 3 Lambda tools:

1. **ApplicationTool** - Creates insurance applications
   - Parameters: `applicant_region`, `coverage_amount`

2. **RiskModelTool** - Invokes risk scoring model
   - Parameters: `API_classification`, `data_governance_approval`

3. **ApprovalTool** - Approves underwriting decisions
   - Parameters: `claim_amount`, `risk_level`

## Natural Language Policy Examples

### 1. Simple Single-Statement Policy

**Natural Language:**
```
Allow all users to invoke the application tool when the coverage amount 
is under 1 million and the application region is US or CAN
```

**Generated Cedar Policy:**
```cedar
permit(
  principal,
  action == AgentCore::Action::"ApplicationToolTarget___create_application",
  resource == AgentCore::Gateway::"<gateway-arn>"
) when {
  (context.input.coverage_amount < 1000000) && 
  ((context.input.applicant_region == "US") || 
   (context.input.applicant_region == "CAN"))
};
```

### 2. Multi-Line Statements

**Natural Language:**
```
Allow all users to invoke the risk model tool when data governance approval is true.
Block users from calling the application tool unless coverage amount is present.
```

**Result:** Generates **2 separate policies** - one permit and one forbid policy.

### 3. Principal-Based Policies

**Natural Language:**
```
Allow principals with username "test-user" to invoke the risk model tool
```

**Generated Cedar Policy:**
```cedar
permit(
  principal,
  action == AgentCore::Action::"RiskModelToolTarget___invoke_risk_model",
  resource == AgentCore::Gateway::"<gateway-arn>"
) when {
  (principal.hasTag("username")) && 
  (principal.getTag("username") == "test-user")
};
```

**Natural Language:**
```
Forbid principals to access the approval tool unless they have 
the scope group:Controller
```

**Generated Cedar Policy:**
```cedar
forbid(
  principal,
  action == AgentCore::Action::"ApprovalToolTarget",
  resource == AgentCore::Gateway::"<gateway-arn>"
) when {
  !((principal.hasTag("scope")) && 
    (principal.getTag("scope") like "*group:Controller*"))
};
```

**Natural Language:**
```
Block principals from using risk model tool and approval tool 
unless the principal has role "senior-adjuster"
```

**Generated Cedar Policy:**
```cedar
forbid(
  principal,
  action in [AgentCore::Action::"RiskModelToolTarget",
             AgentCore::Action::"ApprovalToolTarget"],
  resource == AgentCore::Gateway::"<gateway-arn>"
) when {
  !((principal.hasTag("role")) && 
    (principal.getTag("role") == "senior-adjuster"))
};
```

## How NL2Cedar Works

1. **Schema Awareness**: The Gateway target schemas are provided to NL2Cedar to help the foundation model understand tool names and parameters

2. **Natural Language Input**: You provide authorization requirements in plain English

3. **Cedar Generation**: The system generates syntactically correct Cedar policies

4. **Policy Creation**: Generated policies can be directly created in your Policy Engine

## Workflow

The notebook guides you through:

1. **Environment Setup** - Verify credentials and dependencies
2. **Infrastructure Check** - Automatically set up Gateway if needed (from Getting-Started)
3. **Policy Engine Creation** - Create a Policy Engine for NL2Cedar policies
4. **Simple Policy Generation** - Generate a single policy from natural language
5. **Policy Creation** - Create the generated policy in the Policy Engine
6. **Multi-Line Generation** - Generate multiple policies from multi-line statements
7. **Principal-Based Policies** - Create identity-aware policies
8. **Cleanup** - Remove all created resources

## Key Features

### Automatic Infrastructure Setup

If you haven't completed the Getting-Started tutorial, the notebook will:
- Deploy 3 Lambda functions (ApplicationTool, RiskModelTool, ApprovalTool)
- Create AgentCore Gateway with OAuth authentication
- Configure Lambda targets with proper schemas
- Save configuration to `config.json`

### Multi-Policy Generation

When you provide multi-line statements with consistent delimiters (commas, periods, semicolons), NL2Cedar automatically:
- Detects individual policy statements
- Generates separate Cedar policies for each statement
- Returns all policies in the `generatedPolicies` array

### Principal Scope Support

For identity-based policies, you can reference:
- **Username**: `principal.getTag("username")`
- **Role**: `principal.getTag("role")`
- **Scope**: `principal.getTag("scope")`
- **Custom Claims**: Any attribute from your OAuth token

> **💡 Tip**: Providing the exact tag name in your natural language statement helps NL2Cedar create the correct Cedar policy.


## Best Practices

1. **Be Specific**: Clearly state the tool name, parameters, and conditions
2. **Use Exact Parameter Names**: Reference parameters as they appear in the Gateway schema
3. **Specify Principal Attributes**: For identity-based policies, mention the exact tag name
4. **One Concept Per Line**: For multi-line generation, separate distinct policies with consistent delimiters
5. **Test Generated Policies**: Always review generated Cedar syntax before deploying



## Additional Resources

- **Example Policies**: [Supported Cedar Policies](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/example-policies.html)
- **Getting Started Tutorial**: `../01-Getting-Started/README.md`

---

**Happy Building!** 🚀
