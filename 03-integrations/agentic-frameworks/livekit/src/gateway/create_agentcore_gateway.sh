#!/bin/bash

# Create gateway with minimal JWT config (using a placeholder)
aws bedrock-agentcore-control create-gateway \
  --name interview-evaluation-gateway \
  --description "Gateway for interview evaluation tools" \
  --role-arn arn:aws:iam::123456789012:role/AgentCoreGatewayRole \
  --protocol-type MCP \
  --authorizer-type CUSTOM_JWT \
  --authorizer-configuration '{
    "customJWTAuthorizer": {
      "discoveryUrl": "https://cognito-idp.us-east-1.amazonaws.com/.well-known/openid-configuration",
      "allowedAudience": ["interview-app"]
    }
  }' \
  --region us-east-1

# Get gateway ID
GATEWAY_ID=$(aws bedrock-agentcore-control list-gateways --region us-east-1 --query 'gateways[?name==`interview-evaluation-gateway`].gatewayId' --output text)

echo "Gateway ID: $GATEWAY_ID"

# Create gateway target for Lambda
aws bedrock-agentcore-control create-gateway-target \
  --gateway-id $GATEWAY_ID \
  --name evaluate_interview \
  --description "Evaluates interview transcripts and generates feedback PDF" \
  --target '{"lambda":{"functionArn":"arn:aws:lambda:us-east-1:123456789012:function:EvaluateInterview"}}' \
  --tool-spec '{"inputSchema":{"json":{"type":"object","properties":{"session_id":{"type":"string","description":"The session ID of the interview to evaluate"}},"required":["session_id"]}}}' \
  --region us-east-1

echo "Gateway and target created successfully!"
