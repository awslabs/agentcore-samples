# Preserve Entra user identity across a Runtime and an MCP tool

This example has two Runtime containers. **Two tokens serve different purposes.** The HTTP agent obtains a machine-to-machine token for the MCP application and sends it as the transport bearer. It separately exchanges the Runtime-provided workload token on behalf of the user for Microsoft Graph `User.Read`. Only the dedicated MCP Runtime receives that delegated token, in its explicitly allowlisted custom header. It calls a fixed Graph `/me` endpoint and returns display name and job title. Neither token is a tool argument or prompt. The profile result itself is visible to Bedrock; use a dedicated test identity and account.

## Prepare resources

Review the [Entra setup](ENTRA_SETUP.md) with your identity administrator. Use two distinct application registrations in the same tenant. The MCP audience is `api://<McpClientId>`; grant the agent application's service principal the `mcp_invoke` application role. Grant delegated `User.Read` for Graph and consent as required. Confirm the agent token's `aud` equals the configured agent client ID and the issuer matches the tenant-specific discovery document in the template. These are not interchangeable tokens.

Bring an existing, owner-approved **Microsoft OAuth credential provider** configured for those app registrations, a private ECR repository (or two) and two execution roles. The workflow never creates OAuth providers, writes app secrets or modifies IAM roles. Prepare the roles following the [Runtime permissions guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html). Limit runtime trust to the account and intended resources. Both roles need only their image/log access and required baseline Runtime permissions. The agent additionally needs invocation of the approved Bedrock model/profile and Identity token retrieval scoped to its workload, provider and associated vault/secret as applicable. The MCP role does **not** need model or token-vending permissions. Allow HTTPS egress to the corresponding AWS and Graph endpoints.

## Build and deploy

From this directory, use a sandbox, temporary AWS credentials and an ARM64-capable Docker builder. Review `runtimes.yaml`. Set `AWS_REGION`, `REPO`, `TENANT_ID`, `AGENT_CLIENT_ID`, `MCP_CLIENT_ID`, `AGENT_NAME`, `MCP_NAME`, `AGENT_ROLE`, `MCP_ROLE`, `PROVIDER`, `MODEL_ID` and a **new** `STACK` name. Role parameters are paths/names, not secrets or ARNs. Use a versioned/tag-immutable repository with fresh tags. Example for commercial AWS:

```bash
set -euo pipefail
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY="$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$REGISTRY"
TAG=$(python3 -c 'import uuid; print(uuid.uuid4().hex)')
for service in mcp agent; do
  docker buildx build --platform linux/arm64 --provenance=false --push -t "$REGISTRY/$REPO:$service-$TAG" "$service/"
done
digest() { aws ecr describe-images --region "$AWS_REGION" --repository-name "$REPO" --image-ids "imageTag=$1-$TAG" --query 'imageDetails[0].imageDigest' --output text; }
STACK_ID=$(aws cloudformation create-stack --region "$AWS_REGION" --stack-name "$STACK" --template-body file://runtimes.yaml --parameters \
  ParameterKey=TenantId,ParameterValue="$TENANT_ID" ParameterKey=AgentClientId,ParameterValue="$AGENT_CLIENT_ID" \
  ParameterKey=McpClientId,ParameterValue="$MCP_CLIENT_ID" ParameterKey=AgentName,ParameterValue="$AGENT_NAME" \
  ParameterKey=McpName,ParameterValue="$MCP_NAME" ParameterKey=AgentImage,ParameterValue="$REPO@$(digest agent)" \
  ParameterKey=McpImage,ParameterValue="$REPO@$(digest mcp)" ParameterKey=AgentRole,ParameterValue="$AGENT_ROLE" \
  ParameterKey=McpRole,ParameterValue="$MCP_ROLE" ParameterKey=ProviderName,ParameterValue="$PROVIDER" \
  ParameterKey=ModelId,ParameterValue="$MODEL_ID" --query StackId --output text)
aws cloudformation wait stack-create-complete --region "$AWS_REGION" --stack-name "$STACK_ID"
aws cloudformation describe-stacks --region "$AWS_REGION" --stack-name "$STACK_ID" --query 'Stacks[0].Outputs'
```

Keep the full stack ID. The template derives the agent's MCP destination from the stack-created Runtime ARN, not a caller-supplied URL. Runtime validates the MCP audience **and** `roles` claim before the tool runs. Only the Graph header is forwarded to the MCP container; the application doesn't need the M2M bearer. Use an approved client with the end user's access token for the agent audience to send `{"prompt":"What is my display name?"}` to the **Agent** Runtime invocation endpoint. Do not put any token on the command line or log the headers. A token for the wrong audience or missing the MCP role must not grant access. AWS, inference and identity-provider calls may incur costs.

## Cleanup

After verifying account, Region and the recorded stack ID, run `aws cloudformation delete-stack --region "$AWS_REGION" --stack-name "$STACK_ID"` and wait for `stack-delete-complete`. The two runtimes are stack-owned; the provider, app registrations, roles, images and log retention are not. Remove only dedicated resources you own after checking other consumers. The [historical script](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/01-features/05-authenticate-and-authorize/04-entra-obo-mcp-runtime) reused named roles and later enumerated role policies and repositories; do not adopt or bulk-delete historical resources by name.
