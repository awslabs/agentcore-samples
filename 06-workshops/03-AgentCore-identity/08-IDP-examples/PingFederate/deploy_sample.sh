#!/bin/bash
set -e

# Parse flags
DEPLOY_LATTICE=false
for arg in "$@"; do
    case $arg in
        --self-managed-lattice)
            DEPLOY_LATTICE=true
            shift
            ;;
        *)
            echo "Unknown option: $arg"
            echo "Usage: ./deploy_sample.sh [--self-managed-lattice]"
            exit 1
            ;;
    esac
done
export DEPLOY_LATTICE

echo "=========================================="
echo "AgentCore Private IdP: PingFederate + VPC Lattice"
echo "=========================================="
echo ""

# Check prerequisites
echo "Checking prerequisites..."

if ! command -v uv &> /dev/null; then
    echo "Error: uv is not installed"
    echo "   Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
echo "  uv installed"

if ! command -v docker &> /dev/null; then
    echo "Error: docker is not installed"
    echo "   Install: https://docs.docker.com/get-docker/"
    exit 1
fi
echo "  docker installed"

if ! command -v cdk &> /dev/null; then
    echo "Error: AWS CDK is not installed"
    echo "   Install: npm install -g aws-cdk"
    exit 1
fi
echo "  AWS CDK installed"

if ! command -v agentcore &> /dev/null; then
    echo "Error: agentcore CLI is not installed"
    echo "   Install: npm install -g @aws/agentcore@0.30.0"
    exit 1
fi
# Checked after the not-installed branch on purpose: a user with the WRONG CLI has
# the binary on PATH, so a check nested inside that branch can never reach them.
node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "ERROR: Node.js 20+ required by the AgentCore CLI (found $(node -v))"; exit 1; }
agentcore --version | grep -q '^0\.' \
  || { echo "ERROR: these samples need AgentCore CLI v0. Run: npm install -g @aws/agentcore@0.30.0"; exit 1; }
echo "  agentcore CLI installed ($(agentcore --version))"

if ! command -v aws &> /dev/null; then
    echo "Error: AWS CLI is not installed"
    echo "   Install: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi

AWS_CLI_VERSION=$(aws --version 2>&1 | sed -n 's/.*aws-cli\/\([0-9]*\.[0-9]*\).*/\1/p')
MIN_VERSION="2.27"
if [ "$(printf '%s\n' "$MIN_VERSION" "$AWS_CLI_VERSION" | sort -V | head -n1)" != "$MIN_VERSION" ]; then
    echo "Error: AWS CLI version $AWS_CLI_VERSION is too old (requires >= $MIN_VERSION)"
    echo "   Update: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi
echo "  AWS CLI installed (v$AWS_CLI_VERSION)"

echo ""
echo "All prerequisites met!"
echo ""

# Check for PingFederate DevOps credentials
if [ -f .env ]; then
    echo "Loading configuration from .env..."
    set -a
    source .env
    set +a
fi

if [ -z "$PING_IDENTITY_DEVOPS_USER" ] || [ -z "$PING_IDENTITY_DEVOPS_KEY" ]; then
    echo "Error: PingFederate DevOps credentials not set."
    echo "   Set PING_IDENTITY_DEVOPS_USER and PING_IDENTITY_DEVOPS_KEY"
    echo "   in your .env file or as environment variables."
    echo ""
    echo "   Sign up at: https://devops.pingidentity.com/get-started/devopsRegistration/"
    exit 1
fi
echo "  PingFederate DevOps credentials found"

if [ -z "$CERTIFICATE_ARN" ]; then
    echo "Error: CERTIFICATE_ARN not set."
    echo "   Provide the ARN of a publicly trusted ACM certificate."
    echo "   AgentCore Identity requires a publicly trusted TLS certificate"
    echo "   to connect to the private IdP via VPC Lattice."
    echo ""
    echo "   Create one with: aws acm request-certificate --domain-name ping.example.com --validation-method DNS"
    exit 1
fi
echo "  CERTIFICATE_ARN found"

if [ -z "$PING_DOMAIN" ]; then
    echo "Error: PING_DOMAIN not set."
    echo "   Set the domain name matching your ACM certificate (e.g., ping.example.com)."
    exit 1
fi
echo "  PING_DOMAIN found ($PING_DOMAIN)"
echo ""

# Set up virtual environment
echo "Setting up Python environment..."
uv sync
echo "  Dependencies installed"
echo ""

# Deploy with CDK
echo "Deploying with CDK..."
uv run cdk bootstrap --qualifier pingidp --toolkit-stack-name CDKToolkit-pingidp

uv run cdk synth --quiet

uv run cdk deploy --all --require-approval never

echo ""
echo "=========================================="
echo "Deployment complete!"
echo "=========================================="
echo ""
echo "PingFederate was configured automatically via a Lambda custom resource"
echo "running inside the VPC (no public network access required)."
echo ""

# Get stack outputs
VPC_ID=$(aws cloudformation describe-stacks --stack-name PrivateIdpVpcStack \
    --query 'Stacks[0].Outputs[?OutputKey==`VpcId`].OutputValue' --output text 2>/dev/null || echo "N/A")
SUBNET_IDS=$(aws cloudformation describe-stacks --stack-name PrivateIdpVpcStack \
    --query 'Stacks[0].Outputs[?OutputKey==`PrivateSubnetIds`].OutputValue' --output text 2>/dev/null || echo "N/A")
DISCOVERY_URL=$(aws cloudformation describe-stacks --stack-name PrivateIdpPingFederateStack \
    --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text 2>/dev/null || echo "N/A")
ALB_DNS=$(aws cloudformation describe-stacks --stack-name PrivateIdpPingFederateStack \
    --query 'Stacks[0].Outputs[?OutputKey==`AlbDnsName`].OutputValue' --output text 2>/dev/null || echo "N/A")
GATEWAY_ROLE_ARN=$(aws cloudformation describe-stacks --stack-name PrivateIdpGatewayInfraStack \
    --query 'Stacks[0].Outputs[?OutputKey==`GatewayRoleArn`].OutputValue' --output text 2>/dev/null || echo "N/A")
MCP_ECHO_LAMBDA_ARN=$(aws cloudformation describe-stacks --stack-name PrivateIdpGatewayInfraStack \
    --query 'Stacks[0].Outputs[?OutputKey==`McpEchoLambdaArn`].OutputValue' --output text 2>/dev/null || echo "N/A")

echo "Discovery URL:       $DISCOVERY_URL"
echo "VPC ID:              $VPC_ID"
echo "Private Subnet IDs:  $SUBNET_IDS"
echo "ALB DNS Name:        $ALB_DNS"
echo "Gateway Role ARN:    $GATEWAY_ROLE_ARN"
echo "MCP Echo Lambda ARN: $MCP_ECHO_LAMBDA_ARN"
echo ""

# Check if Lattice stack was deployed (--self-managed-lattice)
RESOURCE_CONFIG_ID=$(aws cloudformation describe-stacks --stack-name PrivateIdpLatticeStack \
    --query 'Stacks[0].Outputs[?OutputKey==`ResourceConfigurationId`].OutputValue' --output text 2>/dev/null || echo "")

# Convert comma-separated subnet IDs to JSON array
SUBNET_JSON=$(echo "$SUBNET_IDS" | sed 's/,/","/g' | sed 's/^/["/' | sed 's/$/"]/')

echo "=========================================="
echo "Step A: Create the credential provider"
echo "=========================================="
echo ""

if [ -n "$RESOURCE_CONFIG_ID" ]; then
    echo "Resource Configuration ID: $RESOURCE_CONFIG_ID"
    echo ""
    echo "Mode: Self-managed VPC Lattice (you deployed the Lattice resources)"
    echo ""
    echo "  aws bedrock-agentcore-control create-oauth2-credential-provider \\"
    echo "      --name \"ping-private-idp\" \\"
    echo "      --credential-provider-vendor \"CustomOauth2\" \\"
    echo "      --oauth2-provider-config-input '{"
    echo "          \"customOauth2ProviderConfig\": {"
    echo "              \"oauthDiscovery\": { \"discoveryUrl\": \"$DISCOVERY_URL\" },"
    echo "              \"clientId\": \"agentcore-client\","
    echo "              \"clientSecret\": \"agentcore-test-secret-12345\","
    echo "              \"privateEndpoint\": {"
    echo "                  \"selfManagedLatticeResource\": {"
    echo "                      \"resourceConfigurationIdentifier\": \"$RESOURCE_CONFIG_ID\""
    echo "                  }"
    echo "              }"
    echo "          }"
    echo "      }'"
else
    echo "Mode: AgentCore-managed VPC Lattice"
    echo ""
    echo "  aws bedrock-agentcore-control create-oauth2-credential-provider \\"
    echo "      --name \"ping-private-idp\" \\"
    echo "      --credential-provider-vendor \"CustomOauth2\" \\"
    echo "      --oauth2-provider-config-input '{"
    echo "          \"customOauth2ProviderConfig\": {"
    echo "              \"oauthDiscovery\": { \"discoveryUrl\": \"$DISCOVERY_URL\" },"
    echo "              \"clientId\": \"agentcore-client\","
    echo "              \"clientSecret\": \"agentcore-test-secret-12345\","
    echo "              \"privateEndpoint\": {"
    echo "                  \"managedVpcResource\": {"
    echo "                      \"vpcIdentifier\": \"$VPC_ID\","
    echo "                      \"subnetIds\": $SUBNET_JSON,"
    echo "                      \"endpointIpAddressType\": \"IPV4\""
    echo "                  }"
    echo "              }"
    echo "          }"
    echo "      }'"
fi
echo ""

echo "=========================================="
echo "Step B: Create the AgentCore Gateway"
echo "=========================================="
echo ""
echo "The gateway uses CUSTOM_JWT inbound auth with PingFederate as the token"
echo "issuer. The privateEndpoint tells the gateway to validate JWTs by reaching"
echo "PingFederate's JWKS endpoint via VPC Lattice (private connectivity)."
echo ""
echo "  aws bedrock-agentcore-control create-gateway \\"
echo "      --name \"PingGateway\" \\"
echo "      --protocol-type \"MCP\" \\"
echo "      --role-arn \"$GATEWAY_ROLE_ARN\" \\"
echo "      --authorizer-type \"CUSTOM_JWT\" \\"
echo "      --authorizer-configuration '{"
echo "          \"customJWTAuthorizer\": {"
echo "              \"discoveryUrl\": \"$DISCOVERY_URL\","
echo "              \"allowedClients\": [\"agentcore-client\"],"
echo "              \"privateEndpoint\": {"
echo "                  \"managedVpcResource\": {"
echo "                      \"vpcIdentifier\": \"$VPC_ID\","
echo "                      \"subnetIds\": $SUBNET_JSON,"
echo "                      \"endpointIpAddressType\": \"IPV4\""
echo "                  }"
echo "              }"
echo "          }"
echo "      }' \\"
echo "      --exception-level \"DEBUG\""
echo ""
echo "Wait for the gateway to become READY (~2-3 minutes):"
echo ""
echo "  aws bedrock-agentcore-control list-gateways \\"
echo "      --query 'items[?name==\`PingGateway\`].{id:gatewayId,status:status}'"
echo ""

echo "=========================================="
echo "Step C: Add the MCP Echo Lambda target"
echo "=========================================="
echo ""
echo "Once the gateway is READY, add the Lambda target. Replace GATEWAY_ID with"
echo "the gatewayId from the previous step:"
echo ""
echo "  aws bedrock-agentcore-control create-gateway-target \\"
echo "      --gateway-identifier GATEWAY_ID \\"
echo "      --name \"McpEchoTarget\" \\"
echo "      --target-configuration '{"
echo "          \"mcp\": {"
echo "              \"lambda\": {"
echo "                  \"lambdaArn\": \"$MCP_ECHO_LAMBDA_ARN\","
echo "                  \"toolSchema\": {"
echo "                      \"inlinePayload\": ["
echo "                          {"
echo "                              \"name\": \"get_time\","
echo "                              \"description\": \"Get the current UTC time\","
echo "                              \"inputSchema\": { \"type\": \"object\", \"properties\": {}, \"required\": [] }"
echo "                          },"
echo "                          {"
echo "                              \"name\": \"echo\","
echo "                              \"description\": \"Echo a message back\","
echo "                              \"inputSchema\": {"
echo "                                  \"type\": \"object\","
echo "                                  \"properties\": { \"message\": { \"type\": \"string\", \"description\": \"Message to echo\" } },"
echo "                                  \"required\": [\"message\"]"
echo "                              }"
echo "                          }"
echo "                      ]"
echo "                  }"
echo "              }"
echo "          }"
echo "      }' \\"
echo "      --credential-provider-configurations '[{\"credentialProviderType\": \"GATEWAY_IAM_ROLE\"}]'"
echo ""

# Configure agent deployment target
ACCOUNT_ID=$(aws sts get-caller-identity --query 'Account' --output text 2>/dev/null || echo "")
DEPLOY_REGION=${AWS_REGION:-$(aws configure get region 2>/dev/null || echo "us-east-1")}

if [ -n "$ACCOUNT_ID" ]; then
    echo "Configuring agent deployment target..."
    cat > agent/private-idp-ping-agent/agentcore/aws-targets.json <<TARGETS
[{"name": "default", "account": "$ACCOUNT_ID", "region": "$DEPLOY_REGION"}]
TARGETS
    echo "  aws-targets.json updated (account: $ACCOUNT_ID, region: $DEPLOY_REGION)"
    echo ""
fi

# Install agent CDK dependencies
echo "Installing agent CDK dependencies..."
(cd agent/private-idp-ping-agent/agentcore/cdk && npm install --silent)
echo "  Done"
echo ""

echo "=========================================="
echo "Step D: Deploy the runtime"
echo "=========================================="
echo ""
echo "Set GATEWAY_URL to the gateway's MCP endpoint, then deploy:"
echo ""
echo "  cd agent/private-idp-ping-agent"
echo "  agentcore deploy -y"
echo ""
echo "Then test with:"
echo "  agentcore invoke --prompt \"test\""
echo ""
echo "See README.md for full instructions."
