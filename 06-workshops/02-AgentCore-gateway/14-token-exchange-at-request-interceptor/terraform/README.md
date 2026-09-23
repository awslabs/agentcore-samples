# AgentCore Gateway with Token Exchange at Request Interceptor - Terraform

This Terraform configuration provisions the same infrastructure as the companion Jupyter notebook (`token-exchange-at-request-interceptor.ipynb`), enabling secure token exchange and identity propagation in multi-hop agent workflows using AgentCore Gateway.

## Architecture

1. **Client** initiates requests with Cognito OAuth2 tokens (client credentials flow)
2. **AgentCore Gateway** routes requests through an interceptor for token exchange
3. **Gateway Interceptor Lambda** validates the inbound token and exchanges it for a scoped downstream token via Cognito
4. **API Gateway (OpenAPI Target)** receives the processed request with the exchanged token
5. **Strands Agent** (not provisioned by Terraform) can connect to the gateway via streamable HTTP transport

## Prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.0
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/install-cliv2.html) configured with credentials
- AWS provider >= 5.0 (with `aws_bedrockagentcore_*` resource support)

## File Structure

```
terraform/
├── providers.tf          # AWS, archive, null, random providers
├── variables.tf          # region, name_prefix
├── data.tf               # Account ID, region, random suffix for unique names
├── cognito.tf            # User Pool, Domain, Resource Server, App Client
├── lambda.tf             # Pre Token Generation + Gateway Interceptor Lambdas
├── apigateway.tf         # REST API (OpenAPI), Authorizer, API Key, Usage Plan
├── agentcore.tf          # Credential Provider, Gateway IAM Role, Gateway, Target
├── outputs.tf            # Key outputs (IDs, ARNs, URLs)
└── lambda_src/
    ├── pre_token_generation/
    │   └── lambda_function.py
    └── gateway_interceptor/
        └── lambda_function.py
```

## Resources Created

| Resource | Terraform Resource |
|---|---|
| Cognito User Pool (Essentials tier) | `aws_cognito_user_pool.this` + `null_resource.configure_user_pool` |
| Cognito Resource Server (read/write scopes) | `aws_cognito_resource_server.this` |
| Cognito App Client (client_credentials) | `aws_cognito_user_pool_client.this` |
| Cognito User Pool Domain | `aws_cognito_user_pool_domain.this` |
| Pre Token Generation Lambda + IAM Role | `aws_lambda_function.pre_token_generation` |
| Gateway Interceptor Lambda + IAM Role | `aws_lambda_function.gateway_interceptor` |
| API Gateway REST API (OpenAPI import) | `aws_api_gateway_rest_api.this` |
| Cognito Authorizer | `aws_api_gateway_authorizer.cognito` |
| API Key + Usage Plan | `aws_api_gateway_api_key.this` + `aws_api_gateway_usage_plan.this` |
| AgentCore API Key Credential Provider | `aws_bedrockagentcore_api_key_credential_provider.this` |
| AgentCore Gateway (Custom JWT + Interceptor) | `aws_bedrockagentcore_gateway.this` |
| AgentCore Gateway Target (OpenAPI) | `aws_bedrockagentcore_gateway_target.this` |

## Usage

```bash
cd terraform
terraform init
terraform apply
```

To customize the deployment:

```bash
terraform apply -var="region=us-west-2" -var="name_prefix=myproject"
```

## Variables

| Name | Description | Default |
|---|---|---|
| `region` | AWS region | `us-east-1` |
| `name_prefix` | Prefix for resource names | `agentcore` |

## Outputs

| Name | Description |
|---|---|
| `cognito_user_pool_id` | Cognito User Pool ID |
| `cognito_client_id` | Cognito App Client ID |
| `cognito_client_secret` | Cognito App Client Secret (sensitive) |
| `cognito_token_endpoint` | Cognito OAuth2 token endpoint |
| `api_gateway_url` | API Gateway invoke URL |
| `gateway_id` | AgentCore Gateway ID |
| `gateway_url` | AgentCore Gateway URL |
| `gateway_target_id` | AgentCore Gateway Target ID |

## Testing with Strands Agent

After deploying, use the outputs to connect a Strands agent:

```python
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamablehttp_client

# Use terraform output values
gateway_url = "<gateway_url from terraform output>"
access_token = "<obtain via cognito token endpoint>"

client = MCPClient(lambda: streamablehttp_client(
    gateway_url,
    headers={"Authorization": f"Bearer {access_token}"}
))

model = BedrockModel(model_id="us.amazon.nova-pro-v1:0")

with client:
    tools = client.list_tools_sync()
    agent = Agent(model=model, tools=tools)
    response = agent("List all tools available to you")
```

## Cleanup

```bash
terraform destroy
```

## Design Notes

- A `random_id` suffix is used instead of timestamps to avoid resource recreation on every plan/apply.
- A `null_resource` with AWS CLI is used to upgrade the Cognito User Pool to Essentials tier and attach the V3_0 Pre Token Generation trigger, since the Terraform AWS provider does not natively support `UserPoolTier`.
- Native `aws_bedrockagentcore_*` Terraform resources are used for the gateway, target, and credential provider.
