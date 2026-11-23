# Connect a Databricks SQL MCP target using a service principal

This example attaches **one** managed MCP server to an existing AgentCore Gateway. Gateway exchanges the configured OAuth client credentials with the selected Databricks workspace. The inbound user's token authenticates the Gateway request; it is not a Databricks user token. Every permitted caller of this target acts with the **same service principal's** Databricks privileges. If Unity Catalog must enforce each human user's own grants, use [the native per-user token exchange path](../databricks-dbsql-native-obo/) instead.

## Before adding a target

- Obtain approval from the owner of a sandbox Gateway, its configured JWT authorizer and its role. Give the role only the token-vault/provider/secret access required for this existing provider; do not change a shared Gateway's authorizer or add a broad new IAM policy for this sample.
- Use an AWS Databricks workspace you administer with the managed DBSQL MCP endpoint available. Confirm the **exact** workspace hostname out-of-band; an organizational DNS suffix is not evidence that you own a workspace. Create a separate, restricted service principal with read-only access to synthetic tables and the necessary warehouse entitlement. `all-apis` is the Databricks OAuth scope used here; actual permissions come from workspace/Unity Catalog grants. Do not grant admin or unrestricted write permissions.
- Ask the provider owner for an existing AgentCore OAuth credential provider ARN in the Gateway's account/Region, configured with the matching Databricks workspace and `client_credentials` grant. The secret belongs in the provider, not a notebook, template parameter or CLI argument. Do not use a provider intended for another origin.
- **Adding the target changes what callers of the Gateway can discover/invoke.** Use a dedicated Gateway or an approved restrictive policy. A caller's access to this test Gateway must not imply access to production data. Listing and SQL calls may contact Databricks and incur charges.

## Create the target

Set `AWS_REGION`, `STACK`, `GATEWAY_ID`, `TARGET_NAME`, `WORKSPACE_HOST` and `PROVIDER_ARN` after verifying the account. Review `target.yaml`: there is no Runtime, IAM role, Cognito resource, Gateway or provider creation. Choose fresh stack and target names. Stop on a name collision rather than replacing a target.

```bash
aws sts get-caller-identity
STACK_ID=$(aws cloudformation create-stack --region "$AWS_REGION" --stack-name "$STACK" --template-body file://target.yaml \
  --parameters ParameterKey=GatewayId,ParameterValue="$GATEWAY_ID" ParameterKey=TargetName,ParameterValue="$TARGET_NAME" \
  ParameterKey=WorkspaceHost,ParameterValue="$WORKSPACE_HOST" ParameterKey=ProviderArn,ParameterValue="$PROVIDER_ARN" \
  --query StackId --output text)
aws cloudformation wait stack-create-complete --stack-name "$STACK_ID" --region "$AWS_REGION"
```

Keep the full stack ID. From an MCP client already authorized for the gateway, run `tools/list` and inspect the **actual** target-prefixed tool names and schemas. If authorized to test SQL, start with a read-only identity query such as `SELECT current_user()` using the returned schema; expect the configured service principal, not the interactive user. Successful discovery alone does not prove warehouse permissions. Never print tokens, raw credentials or sensitive query results.

## Cleanup

Verify account, Region and full stack ID. Run `aws cloudformation delete-stack --stack-name "$STACK_ID" --region "$AWS_REGION"` and wait for `stack-delete-complete`. The provider, inbound client, Gateway, Databricks principal, warehouse and tables remain externally owned. Do not delete a shared Gateway because one target used it.

The [historical notebook](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/03-integrations/data-platforms/databricks-dbsql-agentcore-gateway) also generated a Strands consumer, deployed it with Toolkit and created identity infrastructure. This focused target replaces neither the hosted model nor that provisioning. Reconcile historical resources by their original IDs and owners.
