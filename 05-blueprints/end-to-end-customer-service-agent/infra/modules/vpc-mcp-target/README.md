# Private MCP Target — Entra ID Delegated Authentication

Deploys a customer service MCP server on AgentCore Runtime, secured by Microsoft Entra ID delegated token exchange through an AgentCore Gateway.

## How It Works

1. User authenticates with their corporate Entra ID credentials
2. Gateway validates the user's token and exchanges it for a downstream-scoped token
3. MCP Runtime validates the delegated token and serves customer service tools
4. All traffic stays on the AWS backbone — no public internet exposure

## Components

| Component | Purpose |
|-----------|---------|
| `mcp-server/server.py` | FastMCP server with customer service tools |
| `mcp-server/provision_runtime.py` | Deploys the server to AgentCore Runtime |
| `setup/register_identity_provider.py` | Registers Entra ID credentials with AgentCore |
| `setup/wire_gateway_to_runtime.py` | Creates the gateway target with delegated auth |
| `setup/validate_end_to_end.py` | End-to-end test with real user authentication |
| `main.tf` | Gateway infrastructure (Terraform) |
| `VPC_EGRESS_OBO_README.md` | Detailed configuration guide and troubleshooting |

## Quick Start

```bash
# Prerequisites: pip install bedrock-agentcore-starter-toolkit msal boto3

# Step 1 — Deploy MCP server
cd mcp-server && python provision_runtime.py && cd ..

# Step 2 — Register identity provider
python setup/register_identity_provider.py --client-secret <your-entra-secret>

# Step 3 — Deploy gateway
terraform init && terraform apply

# Step 4 — Connect gateway to runtime
python setup/wire_gateway_to_runtime.py \
  --gateway-id $(terraform output -raw gateway_id) \
  --runtime-id <runtime-id-from-step-1> \
  --provider-arn <arn-from-step-2>

# Step 5 — Validate
python setup/validate_end_to_end.py --interactive
```

## Configuration Reference

See `VPC_EGRESS_OBO_README.md` for:
- Complete Azure portal configuration steps
- AWS console settings
- IAM role permissions
- Troubleshooting guide
