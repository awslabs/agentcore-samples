#!/bin/bash
set -euo pipefail

echo "=========================================="
echo "Cleaning up: Keycloak + private CA + AgentCore"
echo "=========================================="
echo ""
echo "This deletes the resources created by the sample, in this order:"
echo "  1. Agent runtime stack (AgentCore-PrivateIdpKeycloakAgent-default)"
echo "  2. AgentCore gateway, gateway target and credential provider"
echo "  3. PrivateIdpKeycloakGatewayInfraStack"
echo "  4. PrivateIdpKeycloakStack (Keycloak, ALB, private CA, secrets)"
echo "  5. PrivateIdpKeycloakVpcStack (may need a retry while Lattice ENIs are released)"
echo ""
if [ "${1:-}" != "-y" ]; then
    read -p "Are you sure? (y/N) " -n 1 -r
    echo ""
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_REGION=${AWS_REGION:-$(aws configure get region 2>/dev/null || echo "us-east-1")}
export CDK_DEFAULT_ACCOUNT=$ACCOUNT_ID

# Step 1: Agent runtime stack
AGENT_STACK="AgentCore-PrivateIdpKeycloakAgent-default"
if aws cloudformation describe-stacks --stack-name "$AGENT_STACK" &>/dev/null; then
    echo "Deleting agent runtime stack ($AGENT_STACK)..."
    aws cloudformation delete-stack --stack-name "$AGENT_STACK"
    aws cloudformation wait stack-delete-complete --stack-name "$AGENT_STACK"
    echo "  Done."
else
    echo "Agent runtime stack not found (skipping)."
fi
echo ""

# Step 2: Gateway, target and credential provider
uv run python setup_agentcore.py --cleanup
echo ""

# Steps 3-4: Gateway infrastructure and Keycloak
uv run cdk destroy PrivateIdpKeycloakGatewayInfraStack --force
uv run cdk destroy PrivateIdpKeycloakStack --force

# Step 5: VPC (AgentCore-managed Lattice ENIs can take time to be released)
if uv run cdk destroy PrivateIdpKeycloakVpcStack --force; then
    echo ""
    echo "=========================================="
    echo "Cleanup complete!"
    echo "=========================================="
else
    echo ""
    echo "PrivateIdpKeycloakVpcStack deletion failed, most likely because VPC Lattice ENIs are still"
    echo "being released (this can take up to 8 hours). Retry later with:"
    echo "  uv run cdk destroy PrivateIdpKeycloakVpcStack --force"
    exit 1
fi
