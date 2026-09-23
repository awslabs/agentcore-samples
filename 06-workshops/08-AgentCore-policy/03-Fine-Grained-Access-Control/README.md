# Policy for Amazon Bedrock AgentCore

## Overview

Policy for Amazon Bedrock AgentCore enables fine-grained access control for AI agents using Cedar policies. It evaluates JWT token claims to determine whether tool invocations should be allowed or denied.

### Architecture

```
                                ┌───────────────────────┐
                                │  Policy for AgentCore │
                                │  (Cedar Policies)     │
                                │                       │
                                │  Evaluates:           │
                                │  - principal tags     │
                                │  - context.input      │
                                │  - resource           │
                                └───────────┬───────────┘
                                            │ attached
                                            ▼
┌─────────────────┐             ┌───────────────────────┐             ┌─────────────┐
│   Amazon        │  JWT Token  │  Amazon Bedrock       │             │   Lambda    │
│   Cognito       │────────────▶│  AgentCore Gateway    │────────────▶│   Target    │
│   + AWS Lambda  │  with       │                       │  if ALLOWED │   (Tool)    │
└─────────────────┘  claims     └───────────────────────┘             └─────────────┘
```

### Tutorial Details

| Information          | Details                                                 |
|:---------------------|:--------------------------------------------------------|
| AgentCore components | Gateway, Identity, Policy                               |
| Example complexity   | Intermediate                                            |
| SDK used             | boto3, requests                                         |

## Prerequisites

- AWS account with appropriate IAM permissions
- Amazon Bedrock AgentCore Gateway with OAuth authorizer
- Amazon Cognito User Pool (M2M client, **Essentials** or **Plus** tier)
- Python 3.8+

## Getting Started

### Option 1: Setup Script (New Resources)

```bash
pip install bedrock-agentcore-starter-toolkit
python setup-gateway.py
```

### Option 2: Existing Resources

Create `gateway_config.json` with your Gateway and Cognito details (see notebook for template).

### Run the Tutorial

Open [policy_for_agentcore_tutorial.ipynb](policy_for_agentcore_tutorial.ipynb)

## Cedar Policy Syntax

| Pattern | Cedar Syntax |
|---------|-------------|
| Check claim exists | `principal.hasTag("claim_name")` |
| Exact match | `principal.getTag("claim_name") == "value"` |
| Pattern match | `principal.getTag("claim_name") like "*value*"` |
| Input validation | `context.input.field <= value` |

## Test Scenarios

1. **Department-Based** - Allow only users from specific departments
2. **Groups-Based** - Allow users in specific groups (pattern matching)
3. **Principal ID-Based** - Allow specific client applications
4. **Combined Conditions** - Multiple conditions with input validation

## Best Practices

- Use `hasTag()` before `getTag()` to avoid errors
- Use pattern matching carefully - `like "*value*"` can match unintended strings
- Test both ALLOW and DENY scenarios
- Use V3_0 Lambda trigger for M2M client credentials flow
