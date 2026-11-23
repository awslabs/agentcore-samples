# Retrieve from one Bedrock Managed Knowledge Base via AgentCore Gateway

Expose exactly one `knowledge___Retrieve` tool using the native `bedrock-knowledge-bases` connector. Start with a raw MCP call; a model is optional. The Gateway authenticates callers with AWS IAM and its execution role grants `bedrock:GetKnowledgeBase` and `bedrock:Retrieve` on the chosen KB only. It cannot create KBs, enumerate new ones or retrieve from other accounts. The caller supplies a text query; the template fixes the KB and requests five results.

## Create a sandbox Gateway

Bring an existing, populated **managed** KB in a supported Region. Use synthetic documents approved for every authorized caller of this Gateway. Set `AWS_REGION`, `KB_ID`, `STACK` and `GATEWAY_NAME` after verifying the account. Choose a new stack and Gateway name; never update or import a similarly named deployment. From this directory:

```bash
aws sts get-caller-identity
STACK_ID=$(aws cloudformation create-stack --region "$AWS_REGION" --stack-name "$STACK" \
  --template-body file://connector.yaml --capabilities CAPABILITY_IAM --parameters \
  ParameterKey=GatewayName,ParameterValue="$GATEWAY_NAME" ParameterKey=KnowledgeBaseId,ParameterValue="$KB_ID" \
  --query StackId --output text)
aws cloudformation wait stack-create-complete --region "$AWS_REGION" --stack-name "$STACK_ID"
export GATEWAY_URL=$(aws cloudformation describe-stacks --region "$AWS_REGION" --stack-name "$STACK_ID" \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayUrl`].OutputValue' --output text)
export REGION="$AWS_REGION" TARGET_NAME=knowledge
```

Keep the full stack ID. Give the **caller** `bedrock-agentcore:InvokeGateway` on the output Gateway ARN. Keep this distinct from the **Gateway role's** KB retrieval permissions. Do not use a shared table of all KBs or grant blanket `Retrieve` access.

## Query it

- [Raw MCP client](01-raw-mcp/): use the supported MCP SDK and AWS SigV4 transport to list tools and retrieve snippets without a model.
- [Strands Runtime](02-strands-agent/): let a request-local agent use only the approved Retrieve tool and stream an answer. Do not treat retrieved documents as authorization or instruction changes. Review citations; a model answer is not proof that a source supports a claim.

Both make billable service calls. Cleaning up the optional Runtime first leaves the Gateway available for inspection. Then verify the account, Region and stack ID and delete **only** the connector stack:

```bash
aws cloudformation delete-stack --region "$AWS_REGION" --stack-name "$STACK_ID"
aws cloudformation wait stack-delete-complete --region "$AWS_REGION" --stack-name "$STACK_ID"
```

The KB, its contents and its source bucket remain externally owned. The [historical helpers](https://github.com/awslabs/agentcore-samples/tree/5cca7e89f8109115766b9f7df3f0b7e98ca9eab1/01-features/03-connect-your-agent-to-anything/04-fmkb-managed-kb) could create/reuse a KB, IAM role and Gateway by name. This stack does not adopt those resources. Use the original identifiers and ownership history to reconcile old deployments; never delete a role and all its policies solely by name.
