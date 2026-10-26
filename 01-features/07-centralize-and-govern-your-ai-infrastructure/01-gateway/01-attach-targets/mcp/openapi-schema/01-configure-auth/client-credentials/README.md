# OpenAPI with OAuth Outbound Auth using Bedrock AgentCore gateway

## Overview

Bedrock AgentCore gateway provides customers a way to turn their existing APIs into fully-managed MCP servers without needing to manage infra or hosting. Customers can bring OpenAPI spec in JSON or YAML. We will demonstrate a customer service agent using enterprise support apis secured by OAuth2.

![architecture](./images/openapis-oauth-gateway.png)

### Tutorial Architecture

In this tutorial we will transform operations defined in OpenAPI yaml/json file into MCP tools and host it in Bedrock AgentCore gateway.
For demonstration purposes, we will build a Customer support agent that answers queries related to support tickets. The agent uses OpenAPIs of Zendesk support apis. The solution uses Langchain Agent using Amazon Bedrock models

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- Node.js >= 22.7.5
- [AgentCore CLI](https://www.npmjs.com/package/@aws/agentcore): `npm install -g @aws/agentcore@0.30.0`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- [IAM permissions](https://github.com/aws/agentcore-cli/blob/main/docs/PERMISSIONS.md)
- Okta account with:
  - client_id
  - client_secret
  - Your Okta domain (e.g., dev-123456.okta.com)
  - An OAuth2 authorization server ID (often default)
- Zendesk integrated with Okta

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses Okta for inbound auth. If you prefer Cognito, see the [Optional Setup guide](../../../../../00-optional-setup/) for full details. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta).

If you are using Cognito instead of Okta, deploy the Cognito stack from [00-optional-setup](../../../../../00-optional-setup/) and capture the outputs:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"

export DISCOVERY_URL=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`DiscoveryUrl`].OutputValue' --output text)

export GATEWAY_CLIENT_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`GatewayClientId`].OutputValue' --output text)

export USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name $COGNITO_STACK_NAME \
  --query 'Stacks[0].Outputs[?OutputKey==`UserPoolId`].OutputValue' --output text)

export GATEWAY_CLIENT_SECRET=$(aws cognito-idp describe-user-pool-client \
  --user-pool-id $USER_POOL_ID \
  --client-id $GATEWAY_CLIENT_ID \
  --query 'UserPoolClient.ClientSecret' --output text)
```

### Configuring Okta for Inbound Authorization

Follow these steps to create an OAuth authorizer in Okta:

- If you do not have an Okta subscription, please sign up for a free trial [Okta documentation](https://www.okta.com/free-trial/).
- Sign in to the Okta Admin console
- Follow the instructions [Okta documentation](https://developer.okta.com/docs/guides/implement-grant-type/clientcreds/main/) to create an Application with the **Client Credentials** grant type.
- After creating the application, go to the Applications page and select the application you just created. Save the **Client ID** and **Client Secret** in a text editor.
- Disable **Require Demonstrating Proof of Possession (DPoP)** under General Settings.
- Open the left navigation bar and select Security -> API. Select the Default Authorization Server that was created for you.
- Save the **Audience** value to a text editor.
- Save the **Issuer** value (It should look similar to `https://trial-xxxxx.okta.com/oauth2/default`) to a text editor.
- Define a Custom Scope. Go to the Scopes tab, Click "Add Scope". Add a scope called InvokeGateway.
- Select **Access Policies**. Create a new Access policy. Give it a name and Assign to All Clients. After the policy has been created, select **Add Rule**, leave all values as default, then select **Create Rule**.

Set your Okta values as environment variables:

```bash
export OKTA_DISCOVERY_URL="<Your Okta Issuer value>/.well-known/openid-configuration"
export OKTA_AUDIENCE="<The audience value you saved earlier>"
export OKTA_CLIENT_ID="<Okta client credentials client id>"
export OKTA_CLIENT_SECRET="<Okta client credentials secret>"
export OKTA_SCOPE="<Okta scope for gateway invocation>"
```

### Step 2: Create AgentCore gateway (AgentCore CLI)

All tutorials share a single AgentCore CLI project at [`gatewaylabproject/`](../../../../../gatewaylabproject/). Navigate to that directory and run all subsequent CLI commands from there.

```bash
agentcore add gateway \
  --name openapi-oauth-gateway \
  --authorizer-type CUSTOM_JWT \
  --discovery-url $OKTA_DISCOVERY_URL \
  --allowed-audience $OKTA_AUDIENCE \
  --client-id $OKTA_CLIENT_ID \
  --client-secret $OKTA_CLIENT_SECRET \
  --exception-level DEBUG

agentcore deploy --yes
```

Capture the gateway ID and URL:

```bash
export GATEWAY_ID=$(agentcore status --json | python3 -c "
import sys, json
data, _ = json.JSONDecoder().raw_decode(sys.stdin.read().lstrip())
print(next(r['identifier'] for r in data['resources'] if r['name'] == 'openapi-oauth-gateway'))
")

export GATEWAY_URL=$(aws bedrock-agentcore-control get-gateway \
  --gateway-identifier $GATEWAY_ID \
  --query 'gatewayUrl' --output text)/mcp

echo "gateway ID:  $GATEWAY_ID"
echo "gateway URL: $GATEWAY_URL"
```

### Step 3: Create Zendesk Credential and OpenAPI Target (boto3)

Zendesk does not provide an OIDC discovery endpoint, so the credential provider and target are created via boto3.

Set up your [Zendesk OAuth Application](https://support.zendesk.com/hc/en-us/articles/4408845965210-Using-OAuth-authentication-with-your-application) using the [client credentials](https://support.zendesk.com/hc/en-us/articles/8983332483226-Announcing-support-for-OAuth-2-0-Client-Credentials-grant-type) grant type, then set the values:

```bash
export ZENDESK_DOMAIN="https://<your-subdomain>.zendesk.com"
export ZENDESK_TOKEN_ENDPOINT="https://<your-subdomain>.zendesk.com/oauth/tokens"
export ZENDESK_CLIENT_ID="<Your Zendesk OAuth client id>"
export ZENDESK_SECRET="<Your Zendesk OAuth client secret>"
```

The OpenAPI spec for Zendesk support APIs is at [`openapi-specs/Zendesk-support-apis.yaml`](../../../../../gatewaylabproject/openapi-specs/Zendesk-support-apis.yaml). Make sure the server URL in the OpenAPI file points to your own Zendesk endpoint URL before proceeding.

This script creates the credential provider and target:

```bash
uv run python scripts/openapi-oauth/deploy_target.py
```

Ensure all targets are in `READY` state.

## Demo

> [!TIP]
> Instead of running these scripts, you can use the [AgentCore gateway MCP Inspector](../../../../../05-community/gateway-mcp-inspector/) to explore Zendesk tools interactively.

![demo](./images/demo.gif)

### Option 1: AgentCore gateway MCP Client

```bash
uv sync
uv run python scripts/openapi-oauth/invoke.py
```

This lists all Zendesk tools exposed through the gateway and calls `ListTickets`.

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

From the [`gatewaylabproject/`](../../../../../gatewaylabproject/) directory, delete the target and credential provider (created via boto3):

```bash
uv run python scripts/openapi-oauth/cleanup.py
```

Then remove the gateway (created via CLI):

```bash
agentcore remove gateway --name openapi-oauth-gateway -y
agentcore deploy --yes
```

> [!NOTE]
> The auto-created credentials are CLI-managed. Remove them by running `agentcore remove all --yes` or manually from `agentcore/agentcore.json`, then run `agentcore deploy --yes`.

Delete the Cognito stack (if you deployed it and no longer need it for other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Documentation

- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [OpenAPI Target](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-target-openapi.html)
- [AgentCore identity](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity-authentication.html)
