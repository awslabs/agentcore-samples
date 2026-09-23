#!/bin/bash
set -e

echo "=========================================="
echo "Cleaning up: PingFederate + VPC Lattice + AgentCore Identity"
echo "=========================================="
echo ""
echo "This will destroy ALL resources created by the sample."
echo ""
echo "Cleanup order:"
echo "  1. AgentCore Gateway (if exists)"
echo "  2. AgentCore credential provider (if exists)"
echo "  3. Agent runtime stack (if deployed)"
echo "  4. PrivateIdpLatticeStack (if deployed)"
echo "  5. PrivateIdpGatewayInfraStack"
echo "  6. PrivateIdpPingFederateStack"
echo "  7. PrivateIdpVpcStack (may require retry if Lattice ENIs not yet released)"
echo ""
read -p "Are you sure? (y/N) " -n 1 -r
echo ""

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

echo ""

# Step 1: Delete gateway (best-effort)
echo "Deleting AgentCore Gateway..."
GATEWAY_ID=$(aws bedrock-agentcore-control list-gateways \
    --query 'items[?name==`PingGateway`].gatewayId' --output text 2>/dev/null || echo "")
if [ -n "$GATEWAY_ID" ] && [ "$GATEWAY_ID" != "None" ]; then
    aws bedrock-agentcore-control delete-gateway --gateway-identifier "$GATEWAY_ID" 2>/dev/null
    echo "  Gateway 'PingGateway' ($GATEWAY_ID) deleted."
else
    echo "  Gateway 'PingGateway' not found (skipping)."
fi
echo ""

# Step 2: Delete credential provider (best-effort)
echo "Deleting AgentCore credential provider..."
if aws bedrock-agentcore-control delete-oauth2-credential-provider \
    --name "ping-private-idp" 2>/dev/null; then
    echo "  Credential provider 'ping-private-idp' deleted."
else
    echo "  Credential provider 'ping-private-idp' not found or already deleted (skipping)."
fi
echo ""

# Step 3: Delete agent runtime stack (best-effort)
AGENT_STACK="AgentCore-PrivateIdpPingAgent-default"
if aws cloudformation describe-stacks --stack-name "$AGENT_STACK" &>/dev/null; then
    echo "Deleting agent runtime stack ($AGENT_STACK)..."
    aws cloudformation delete-stack --stack-name "$AGENT_STACK"
    echo "  Waiting for stack deletion..."
    aws cloudformation wait stack-delete-complete --stack-name "$AGENT_STACK"
    echo "  Agent runtime stack deleted."
else
    echo "Agent runtime stack ($AGENT_STACK) not found (skipping)."
fi
echo ""

# Step 4: Delete PrivateIdpLatticeStack (if it exists)
if aws cloudformation describe-stacks --stack-name PrivateIdpLatticeStack &>/dev/null; then
    echo "Destroying PrivateIdpLatticeStack..."
    uv run cdk destroy PrivateIdpLatticeStack --force
fi

# Step 5: Delete PrivateIdpGatewayInfraStack
if aws cloudformation describe-stacks --stack-name PrivateIdpGatewayInfraStack &>/dev/null; then
    echo "Destroying PrivateIdpGatewayInfraStack..."
    uv run cdk destroy PrivateIdpGatewayInfraStack --force
fi

# Step 6: Delete PrivateIdpPingFederateStack
echo "Destroying PrivateIdpPingFederateStack..."
uv run cdk destroy PrivateIdpPingFederateStack --force

# Step 7: Try to delete PrivateIdpVpcStack — may fail if Lattice ENIs not yet released
echo "Destroying PrivateIdpVpcStack..."
if uv run cdk destroy PrivateIdpVpcStack --force; then
    echo ""
    echo "=========================================="
    echo "Cleanup complete!"
    echo "=========================================="
else
    echo ""
    echo "=========================================="
    echo "PrivateIdpVpcStack deletion failed"
    echo "=========================================="
    echo ""
    echo "VPC Lattice ENIs can take up to 8 hours to be released by AWS."
    echo "Wait and retry with: uv run cdk destroy PrivateIdpVpcStack --force"
    echo ""
    echo "To check ENI status:"
    echo "  VPC_ID=\$(aws cloudformation describe-stacks --stack-name PrivateIdpVpcStack \\"
    echo "      --query 'Stacks[0].Outputs[?OutputKey==\`VpcId\`].OutputValue' --output text)"
    echo "  aws ec2 describe-network-interfaces --filters Name=vpc-id,Values=\$VPC_ID"
    exit 1
fi
