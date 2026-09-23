# AgentCore Gateway OpenAPI Specification

This directory contains the modified OpenAPI specification for use with Amazon Bedrock AgentCore Gateway.

## Files

- `visa-b2b-stub-openapi.json` - Modified OpenAPI spec pointing to stub API Gateway

## Key Modifications

The original Visa B2B OpenAPI spec (`../api_reference.json`) has been modified for gateway use:

### Original Server URL:
```json
"servers": [
  {
    "url": "https://sandbox.api.visa.com",
    "description": "Sandbox server"
  }
]
```

### Modified Server URL (Stub API):
```json
"servers": [
  {
    "url": "https://bb5q1syz89.execute-api.us-east-1.amazonaws.com/v1",
    "description": "Visa B2B Stub API Gateway"
  }
]
```

## Usage

This modified spec is uploaded to S3 and used by AgentCore Gateway to:
1. Generate MCP tools from OpenAPI operations
2. Route tool invocations to the stub API Gateway
3. Enable Payment Agent to call Visa B2B APIs through MCP protocol

## Deployment

The spec is automatically uploaded to S3 when running:
```bash
cd infrastructure
./deploy-agentcore-gateway.sh
```

## Generated MCP Tools

The gateway auto-generates these MCP tools from the OpenAPI spec:

1. **VirtualCardRequisition** - Create virtual payment card
   - Operation: `POST /vpa/v1/accountManagement/VirtualCardRequisition`
   
2. **ProcessPayments** - Process payment transaction
   - Operation: `POST /vpa/v1/payment/ProcessPayments`
   
3. **GetPaymentDetails** - Get payment status
   - Operation: `POST /vpa/v1/payment/GetPaymentDetails`

## Production Migration

To switch from stub API to real Visa API:
1. Update the `servers.url` to point to production Visa API
2. Configure API key authentication in gateway target
3. Re-upload spec to S3
4. Update gateway target configuration

No changes needed to Payment Agent code!
