# Optional Setup

## Step 1 (optional): Amazon Cognito Setup

> [!NOTE]
> In these labs, AgentCore gateway is configured with Amazon Cognito for inbound authentication. This is done to keep the focus on AgentCore gateway patterns. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider for inbound authentication (e.g., Entra ID, Auth0, Okta): see [identity provider setup](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-idps.html). For outbound authorization between AgentCore gateway and your targets, we recommend setting up [AgentCore gateway identity credential management](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html).

Deploy the Amazon Cognito User Pool stack:

> [!IMPORTANT]
> This Amazon Cognito stack is designed for**tutorial and testing purposes only**. MFA is disabled, the password policy is relaxed, and advanced security features are not enabled.**Do not deploy this stack to production environments without a thorough security review.**For production workloads, enable MFA, enforce a strong password policy, and configure advanced security features per your organization's requirements.

| Region    | Launch                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| :-------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| us-east-1 | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-east-1#/stacks/new?stackName=agentcore-gateway-lab&templateURL=https://ws-assets-prod-iad-r-iad-ed304a55c2ca1aee.s3.us-east-1.amazonaws.com/015a2de4-9522-4532-b2eb-639280dc31d8/cognito-dev-stack-no-prod.yaml) |
| us-west-2 | [![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](https://console.aws.amazon.com/cloudformation/home?region=us-west-2#/stacks/new?stackName=agentcore-gateway-lab&templateURL=https://ws-assets-prod-iad-r-pdx-f3b3f9f1a7d6a3d0.s3.us-west-2.amazonaws.com/015a2de4-9522-4532-b2eb-639280dc31d8/cognito-dev-stack-no-prod.yaml) |

Or deploy via the CLI from the [`gatewaylabproject/`](../gatewaylabproject/) directory:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

aws cloudformation deploy \
  --template-file cloudformation/cognito/cognito-signup-stack.yaml \
  --stack-name $COGNITO_STACK_NAME \
  --no-fail-on-empty-changeset
```

Once the stack is deployed, retrieve the outputs you will need for the later tutorials:

```bash
aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs' --output table
```

### What the stack creates

| Resource             | Purpose                                                                              |
| :------------------- | :----------------------------------------------------------------------------------- |
| **User Pool**        | Self-service signup with email verification — users sign up and verify themselves    |
| **Hosted UI Domain** | Amazon Cognito-hosted login and signup pages                                         |
| **Web Client**       | Public client (no secret) for browser-based auth code + PKCE flows                   |
| **MCP Client**       | Confidential M2M client (`client_credentials` grant) for gateway-to-MCP-server auth  |
| **gateway Client**   | Confidential M2M client (`client_credentials` grant) for inbound auth to the gateway |
| **Resource Server**  | Defines `api/mcp` and `api/gateway` custom scopes                                    |

## Cleanup

> [!IMPORTANT]
> The Amazon Cognito stack is shared across all gateway tutorials. Only delete it when you are done with all labs.

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
