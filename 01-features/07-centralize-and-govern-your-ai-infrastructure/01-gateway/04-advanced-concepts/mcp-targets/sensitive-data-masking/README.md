# Masking Sensitive Data in Amazon Bedrock AgentCore gateway Tool Responses

## Overview

This notebook demonstrates how to **automatically anonymize Personally Identifiable Information (PII)** in tool responses using **Amazon Bedrock AgentCore gateway interceptors** integrated with **Amazon Bedrock Guardrails**. The interceptor inspects tool responses in real-time and anonymizes sensitive data using Bedrock's built-in PII detection and anonymization capabilities before returning results to clients, ensuring compliance with data privacy regulations.

### Why Mask Sensitive Data at the gateway

When building AI applications that access customer data, you need to protect sensitive information:

- **Compliance**: Meet GDPR, HIPAA, PCI-DSS, and other regulatory requirements
- **Data Minimization**: Only expose necessary information to clients
- **Centralized Protection**: Apply anonymization rules consistently across all tools
- **Zero Trust**: Don't rely on downstream systems to protect sensitive data
- **AI-Powered Detection**: Leverage Bedrock Guardrails' advanced PII detection across 31+ PII types

The gateway interceptor provides a **centralized enforcement point** that anonymizes PII regardless of which tool returns it, without modifying individual tool implementations.

---

## What This Tutorial Covers

This tutorial implements PII anonymization using a **RESPONSE interceptor** with **Amazon Bedrock Guardrails**:

**PII Anonymization (RESPONSE interceptor + Bedrock Guardrails)**

- Creates a Bedrock Guardrail configured with 31+ PII types for comprehensive detection
- Intercepts tool responses from the gateway
- Applies Bedrock Guardrails to detect and anonymize sensitive data (emails, phone numbers, SSNs, credit cards, addresses, and more)
- Replaces detected PII with anonymized placeholders (e.g., `[EMAIL]`, `[PHONE]`)
- Returns the sanitized response to the client

![PII Masking Architecture](images/PII-mask.png)

---

## Why Use gateway Interceptors

Gateway Interceptors allow you to:

- **Data Protection**: Automatically anonymize sensitive information from responses using AI-powered detection
- **Compliance Enforcement**: Apply consistent data protection policies across all tools
- **Comprehensive Coverage**: Detect 31+ types of PII including names, addresses, financial data, health information, and more
- **Audit & Governance**: Log data access and anonymization events
- **Response Transformation**: Modify data in transit without changing tools
- **Managed Service**: Leverage Bedrock Guardrails' continuously updated PII detection models

Because interceptors are attached at the **gateway layer**, they protect data from **any** underlying MCP server or tool without modifying application code.

---

## Tutorial Details

| Information              | Details                                                                    |
| ------------------------ | -------------------------------------------------------------------------- |
| **Tutorial type**        | Interactive                                                                |
| **AgentCore components** | Amazon Bedrock AgentCore gateway, gateway Interceptors, Bedrock Guardrails |
| **gateway Target type**  | MCP Server (Lambda-based tool)                                             |
| **Interceptor types**    | AWS Lambda (RESPONSE)                                                      |
| **Inbound Auth IdP**     | Amazon Cognito (CUSTOM_JWT authorizer)                                     |
| **Data Protection**      | PII anonymization using Amazon Bedrock Guardrails (31+ PII types)          |
| **Tutorial components**  | gateway, Lambda Interceptor, Bedrock Guardrails, Amazon Cognito, MCP tools |
| **Tutorial vertical**    | Cross-vertical (applicable to any industry with PII)                       |
| **Example complexity**   | Intermediate                                                               |
| **SDK used**             | boto3                                                                      |

---

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md) for Lambda, IAM, Cognito, Bedrock AgentCore, and Bedrock Guardrails

> **Note:** The Cleanup section at the end deletes the AWS resources created by this tutorial (gateway, Lambdas, IAM roles, Guardrails, etc.). Only run it when you're ready to tear everything down.

---

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on gateway patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../../00-optional-setup/).

Once deployed, capture the outputs into environment variables:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

export GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayClientId`].OutputValue' --output text)

export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

export GATEWAY_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)
```

### Step 2: Deploy Lambda Functions + Bedrock Guardrail (CloudFormation)

Deploy the interceptor Lambda, tool Lambda, Bedrock Guardrail (31+ PII types), and IAM roles.

From the [`gatewaylabproject/`](../../gatewaylabproject/) directory:

```bash
export LAMBDA_STACK_NAME="agentcore-data-masking-lambdas"
export CFN_BUCKET="agentcore-cfn-$(aws sts get-caller-identity --query Account --output text)-$(aws configure get region)"

aws s3 mb "s3://$CFN_BUCKET" 2>/dev/null || true

aws cloudformation package \
  --template-file cloudformation/data-masking/data-masking-stack.yaml \
  --s3-bucket $CFN_BUCKET \
  --output-template-file /tmp/data-masking-packaged.yaml

aws cloudformation deploy \
  --template-file /tmp/data-masking-packaged.yaml \
  --stack-name $LAMBDA_STACK_NAME \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset
```

> [!NOTE]
> `aws cloudformation package` uploads the Lambda code from `app/data-masking/` to S3 (required because the code exceeds CloudFormation's inline size limit). The commands above create an S3 bucket automatically if it doesn't exist.

Capture the Lambda ARNs:

```bash
export INTERCEPTOR_ARN=$(aws cloudformation describe-stacks \
  --stack-name $LAMBDA_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`InterceptorFunctionArn`].OutputValue' --output text)

export TOOL_ARN=$(aws cloudformation describe-stacks \
  --stack-name $LAMBDA_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`ToolFunctionArn`].OutputValue' --output text)

echo "Interceptor ARN: $INTERCEPTOR_ARN"
echo "Tool ARN:        $TOOL_ARN"
```

### Step 3: Create AgentCore gateway with RESPONSE Interceptor (boto3)

> [!NOTE]
> The AgentCore CLI does not yet support `interceptorConfigurations`. This step uses a boto3 script.

From the [`gatewaylabproject/`](../../gatewaylabproject/) directory:

```bash
uv run python scripts/deploy_gateway.py \
  --name data-masking-gateway \
  --lambda-targets \
  --interceptor-arn $INTERCEPTOR_ARN \
  --interceptor-point RESPONSE \
  --env-file scripts/data-masking/.env
```

Export the gateway ID and URL (saved by the deploy script):

```bash
source scripts/data-masking/.env
export GATEWAY_ID GATEWAY_URL

echo "gateway ID:  $GATEWAY_ID"
echo "gateway URL: $GATEWAY_URL"
```

### Step 4: Create gateway Target (boto3)

This script registers the employee-data-tool Lambda as a gateway target:

```bash
uv run python scripts/data-masking/deploy.py
```

---

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../05-community/gateway-mcp-inspector/) to explore the data-masking tools interactively.

![demo](./images/demo.gif)

From the [`gatewaylabproject/`](../../gatewaylabproject/) directory:

```bash
uv sync
uv run python scripts/data-masking/invoke.py
```

This lists all tools exposed through the gateway, then invokes the employee-data-tool. The response interceptor applies Bedrock Guardrails to anonymize PII before returning results.

### What to Expect

The employee data tool returns realistic employee information containing various types of PII. The Lambda interceptor will:

1. **Intercept the response** after the tool executes
2. **Apply Bedrock Guardrails** to detect PII across 31+ entity types
3. **Anonymize detected PII** by replacing it with placeholder tokens
4. **Return the sanitized response** to the client

### Bedrock Guardrails Anonymization Format

Bedrock Guardrails replaces detected PII with anonymized placeholders:

- **Emails**: `john.doe@example.com` -> `[EMAIL]`
- **Phone Numbers**: `+1-555-123-4567` -> `[PHONE]`
- **Names**: `John Doe` -> `[NAME]`
- **Addresses**: `123 Main St, Springfield, IL 62701` -> `[ADDRESS]`
- **SSN**: `123-45-6789` -> `[US_SOCIAL_SECURITY_NUMBER]`
- **Credit Cards**: `4532-1234-5678-9010` -> `[CREDIT_DEBIT_CARD_NUMBER]`
- **IP Addresses**: `192.168.1.1` -> `[IP_ADDRESS]`
- **URLs**: `https://example.com` -> `[URL]`

### Example Output

**Before anonymization (raw tool response):**

```json
{
  "employee_id": "EMP-98765",
  "department": "Engineering",
  "contact_info": "alice.smith@company.com",
  "mailing_info": "456 Oak Avenue, Boston, MA 02101",
  "status": "Active",
  "financial_info": {
    "bank_account": "123456789",
    "routing_number": "987654321",
    "credit_card": "4532-1234-5678-9010",
    "cvv": "123",
    "card_expiry": "12/28",
    "pin": "1234",
    "tax_id": "987-65-4321",
    "account_balance": 25000.5,
    "credit_score": 750
  }
}
```

**After anonymization (intercepted response):**

```json
{
  "employee_id": "EMP-98765",
  "department": "Engineering",
  "contact_info": "[EMAIL]",
  "mailing_info": "[ADDRESS]",
  "status": "Active",
  "financial_info": {
    "bank_account": "[US_BANK_ACCOUNT_NUMBER]",
    "routing_number": "[US_BANK_ROUTING_NUMBER]",
    "credit_card": "[CREDIT_DEBIT_CARD_NUMBER]",
    "cvv": "[CREDIT_DEBIT_CARD_CVV]",
    "card_expiry": "[CREDIT_DEBIT_CARD_EXPIRY]",
    "pin": "[PIN]",
    "tax_id": "[US_INDIVIDUAL_TAX_IDENTIFICATION_NUMBER]",
    "account_balance": 25000.5,
    "credit_score": 750
  }
}
```

**Key Observations:**

- **Content-Based Detection**: Field names like `contact_info` and `mailing_info` don't explicitly say "email" or "address", but Bedrock Guardrails detects and anonymizes the content based on pattern recognition
- **Comprehensive Financial PII Protection**: All sensitive financial data (bank accounts, credit cards, tax IDs) are automatically detected and anonymized
- **Selective Anonymization**: Non-sensitive financial data like account balances and credit scores remain unchanged
- **31+ PII Types**: Bedrock Guardrails protects against a wide range of PII types without requiring explicit field name matching

---

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../gatewaylabproject/) directory, remove resources in reverse order:

```bash
uv run python scripts/data-masking/cleanup.py
```

Delete the gateway and its IAM role:

```bash
uv run python scripts/cleanup_gateway.py \
  --name data-masking-gateway \
  --env-file scripts/data-masking/.env
```

Delete the Lambda + Guardrail stack and the S3 bucket used for packaging:

```bash
aws cloudformation delete-stack --stack-name $LAMBDA_STACK_NAME
aws s3 rb "s3://$CFN_BUCKET" --force
```

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

---

## Summary

This tutorial demonstrates PII masking using Lambda interceptors with Bedrock Guardrails:

1. **Setup** - Created Bedrock Guardrail, Lambda interceptor, tool Lambda, and gateway target
2. **Test** - Verified PII masking through gateway responses (31+ PII types anonymized)
3. **Cleanup** - Deleted all resources

## What We Demonstrated

- **Lambda RESPONSE interceptor** that masks sensitive data in tool responses
- **Bedrock Guardrails integration** for AI-powered PII detection and anonymization
- **Content-based detection** that works regardless of field names
- **gateway integration** with custom interceptors
- **Complete resource lifecycle** management

## Next Steps

- Customize masking patterns for your use case
- Add more sophisticated PII detection rules via Guardrails
- Integrate with compliance logging
- Monitor CloudWatch logs for debugging

