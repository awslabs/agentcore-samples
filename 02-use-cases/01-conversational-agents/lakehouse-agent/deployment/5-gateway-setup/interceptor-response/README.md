o/)
ons
2. Environment variables are set correctly
3. Cognito configuration is valid
4. CloudWatch logs for detailed error messages

## References

- [AWS AgentCore Gateway Interceptors](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors-types.html)
- [Fine-Grained Access Control Tutorial](https://github.com/awslabs/amazon-bedrock-agentcore-samples/tree/main/06-workshops/02-AgentCore-gateway/09-fine-grained-access-control)
- [MCP Protocol Specification](https://spec.modelcontextprotocol.i5. **Logging**: Logs authorization decisions for audit trail

## Troubleshooting

### Tools not being filtered

Check:
1. `passRequestHeaders: true` in gateway configuration
2. Authorization header is present in request
3. JWT token is valid
4. DynamoDB table has correct mappings

### Empty tool list returned

Check:
1. DynamoDB table has entry for user's group
2. `allowed_tools` attribute is populated
3. Tool names match exactly (case-sensitive)

### Lambda errors

Check:
1. Lambda has DynamoDB read permissiextraction
- DynamoDB lookups
- Tool filtering decisions
- Error details with stack traces

View logs in CloudWatch:
```bash
aws logs tail /aws/lambda/lakehouse-gateway-response-interceptor --follow
```

## Security Considerations

1. **JWT Validation**: Always validates JWT signature and claims
2. **Least Privilege**: Only returns tools user is authorized to see
3. **System Tool Filtering**: Always removes internal system tools
4. **Error Handling**: Fails closed (returns empty list on authorization errors)
ng

The interceptor handles errors gracefully:

1. **No JWT token**: Returns all tools minus system tools
2. **Invalid JWT**: Returns all tools minus system tools
3. **No claim found**: Returns all tools minus system tools
4. **No DynamoDB mapping**: Returns empty tool list
5. **DynamoDB error**: Returns empty tool list
6. **Any exception**: Returns original response unchanged

## Logging

The interceptor logs:
- JWT validation results
- Claim   }
          ]
        }
      }
    }
  }
}
```

### Expected Behavior

For administrators group:
- Input: 5 tools (including x_amz_bedrock_agentcore_search)
- Output: 2 tools (query_login_audit, text_to_sql)
- Filtered: x_amz_bedrock_agentcore_search + unauthorized tools

For adjusters group:
- Input: 5 tools
- Output: 3 tools (get_claims_summary, get_claim_details, query_claims)
- Filtered: x_amz_bedrock_agentcore_search + unauthorized tools

## Error Handli   "id": 1,
        "method": "tools/list"
      }
    },
    "gatewayResponse": {
      "statusCode": 200,
      "headers": {},
      "body": {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
          "tools": [
            {
              "name": "lakehouse-mcp-target___query_claims",
              "description": "Query claims data"
            },
            {
              "name": "lakehouse-mcp-target___x_amz_bedrock_agentcore_search",
              "description": "Semantic search"
          mbda': {
                'arn': '<response-interceptor-lambda-arn>'
            }
        },
        'interceptionPoints': ['RESPONSE'],
        'inputConfiguration': {
            'passRequestHeaders': True  # Required to access Authorization header
        }
    }
]
```

## Testing

### Test Event Structure

```json
{
  "interceptorInputVersion": "1.0",
  "mcp": {
    "gatewayRequest": {
      "headers": {
        "Authorization": "Bearer <jwt-token>"
      },
      "body": {
        "jsonrpc": "2.0",
     ndler lambda_function.lambda_handler \
  --zip-file fileb://response-interceptor-lambda.zip \
  --timeout 30 \
  --memory-size 256 \
  --environment "Variables={COGNITO_REGION=us-west-2,COGNITO_USER_POOL_ID=<pool-id>,COGNITO_APP_CLIENT_ID=<client-id>,TENANT_ROLE_MAPPING_TABLE=lakehouse_tenant_role_map}"
```

## Gateway Configuration

Add the response interceptor to your gateway:

```python
interceptor_configurations=[
    {
        'interceptor': {
            'lada function
4. Stores Lambda ARN in SSM Parameter Store

### Manual Deployment

```bash
# Install dependencies
pip install -r requirements.txt -t dist/ --platform manylinux2014_x86_64 --only-binary=:all:

# Copy Lambda code
cp lambda_function.py dist/

# Create deployment package
cd dist && zip -r ../response-interceptor-lambda.zip . && cd ..

# Deploy to Lambda
aws lambda create-function \
  --function-name lakehouse-gateway-response-interceptor \
  --runtime python3.11 \
  --role <LAMBDA_ROLE_ARN> \
  --halakehouse-agent/cognito-app-client-id`
   - `/app/lakehouse-agent/tenant-role-mapping-table`

## Deployment

### Prerequisites

1. Request interceptor must be deployed first (creates shared IAM role)
2. DynamoDB table `lakehouse_tenant_role_map` must exist
3. Cognito User Pool must be configured

### Deploy

```bash
cd interceptor-response
./deploy.sh
```

The deployment script:
1. Installs Python dependencies (python-jose, cryptography)
2. Packages Lambda function with dependencies
3. Creates or updates Lambuse-agent/cognito-user-pool-id`
   - `/app/ - `/app/lakehoITO_APP_CLIENT_ID`
   - `TENANT_ROLE_MAPPING_TABLE`

2. SSM Parameter Store:
  cognito:groups",
  "claim_value": "[\"administrators\"]",
  "role_type": "iam_role",
  "role_value": "arn:aws:iam::123456789012:role/lakehouse-administrators-role",
  "allowed_tools": [
    "query_login_audit",
    "text_to_sql"
  ]
}
```

## Configuration

The interceptor uses the following configuration sources (in order):

1. Environment variables:
   - `COGNITO_REGION`
   - `COGNITO_USER_POOL_ID`
   - `COGNaim_name": "
{
  "clude allowed tools
- Always removes system tools (e.g., `x_amz_bedrock_agentcore_search`)

## DynamoDB Table Structure

Table: `lakehouse_tenant_role_map`

```jsonority: cognito:groups > email > username)
- Queries DynamoDB table `lakehouse_tenant_role_map` using composite key:
  - Partition key: `claim_name` (e.g., "cognito:groups")
  - Sort key: `claim_value` (e.g., '["administrators"]')

### 3. Tool Filtering
- Gets `allowed_tools` list from DynamoDB
- Filters MCP tool list to only inclts Bearer token from the Authorization header
- Validates JWT signature using Cognito public keys
- Decodes JWT claims to get user identity

### 2. Authorization Lookup
- Extracts claim for authorization (pri    ↓
                        DynamoDB
                  (tenant_role_map)
```

## How It Works

### 1. JWT Validation
- Extrac# Architecture

```
MCP Server → Gateway → Response Interceptor → Agent/Client
                        lters the tool list to only include tools the user is authorized to see
4. Always removes system tools like `x_amz_bedrock_agentcore_search`

# the request
2. Looks up allowed tools for the user's group in DynamoDB
3. FiThe response interceptor:
1. Extracts JWT claims from the Authorization header int returned by the MCP server based on user group permissions.

## Overview

 Interceptor to filter the tool lisfunction acts as an AgentCore Gateway Response# Gateway Response Interceptor

This Lambda 