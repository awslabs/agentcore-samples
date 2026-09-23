#!/bin/bash
# Generate cdk.json context from SSM Parameter Store (populated by Phase 1 deployment).
#
# Usage:
#   cd 02-use-cases/lakehouse-agent/deployment/advanced-agentcore-policy-gateway-interceptor
#   AWS_REGION=us-east-1 bash scripts/generate-cdk-context.sh

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"

echo "Fetching configuration from SSM Parameter Store (region: $REGION)..."

get_param() {
    aws ssm get-parameter --name "$1" --region "$REGION" --query "Parameter.Value" --output text 2>/dev/null || echo ""
}

ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text 2>/dev/null)
GATEWAY_ID=$(get_param "/app/lakehouse-agent/gateway-id")
GATEWAY_ARN=$(get_param "/app/lakehouse-agent/gateway-arn")
GATEWAY_ROLE_ARN=$(aws bedrock-agentcore-control get-gateway \
    --gateway-identifier "$GATEWAY_ID" --region "$REGION" \
    --query "roleArn" --output text 2>/dev/null)
DISCOVERY_URL="https://cognito-idp.${REGION}.amazonaws.com/$(get_param '/app/lakehouse-agent/cognito-user-pool-id')/.well-known/openid-configuration"
CLIENT_ID=$(get_param "/app/lakehouse-agent/cognito-app-client-id")
REQUEST_INTERCEPTOR_ARN=$(get_param "/app/lakehouse-agent/interceptor-lambda-arn")
RESPONSE_INTERCEPTOR_ARN=$(get_param "/app/lakehouse-agent/response-interceptor-lambda-arn")
TARGET_ID=$(aws bedrock-agentcore-control list-gateway-targets \
    --gateway-identifier "$GATEWAY_ID" --region "$REGION" \
    --query "items[0].targetId" --output text 2>/dev/null)

# Validate
if [ -z "$ACCOUNT_ID" ] || [ "$ACCOUNT_ID" = "None" ]; then
    echo "Error: Could not determine AWS account ID. Is aws CLI configured?"
    exit 1
fi
if [ -z "$GATEWAY_ID" ]; then
    echo "Error: Could not fetch gateway-id from SSM. Is the lakehouse-agent deployed?"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CDK_JSON="$SCRIPT_DIR/../cdk.json"

cat > "$CDK_JSON" <<EOF
{
	"app": "npx ts-node bin/app.ts",
	"context": {
		"account": "$ACCOUNT_ID",
		"region": "$REGION",
		"gatewayId": "$GATEWAY_ID",
		"gatewayName": "lakehouse-gateway",
		"gatewayArn": "$GATEWAY_ARN",
		"gatewayRoleArn": "$GATEWAY_ROLE_ARN",
		"discoveryUrl": "$DISCOVERY_URL",
		"allowedClientId": "$CLIENT_ID",
		"requestInterceptorArn": "$REQUEST_INTERCEPTOR_ARN",
		"responseInterceptorArn": "$RESPONSE_INTERCEPTOR_ARN",
		"targetId": "$TARGET_ID"
	}
}
EOF

echo ""
echo "Generated $CDK_JSON:"
cat "$CDK_JSON"
echo ""
echo "Done."
