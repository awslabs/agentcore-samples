# Protecting your gateway with AWS WAF

Use AWS WAF with Amazon Bedrock AgentCore gateway to protect your gateway from web exploits, bot traffic, and volumetric attacks. AWS WAF provides an inline security layer that evaluates every inbound request before it reaches your targets.

## Overview

When you associate an AWS WAF web access control list (web ACL) with your gateway, AWS WAF inspects every inbound request and applies the rules you configure. Requests that match a block rule are rejected before they reach any target. You associate one web ACL per gateway, at the gateway level.

This tutorial attaches a no-auth MCP server target (the public [Exa MCP server](https://docs.exa.ai)) to a gateway, associates a regional web ACL with AWS Managed Rules plus a rate-based rule, and shows an allowed request reaching the target and a blocked request being rejected by AWS WAF.

![arch](./images/architecture.png)

### Tutorial Details

| Information          | Details                                  |
| :------------------- | :--------------------------------------- |
| Tutorial type        | Interactive                              |
| AgentCore components | AgentCore gateway, AWS WAF               |
| gateway Target type  | MCP server (no auth)                     |
| Inbound Auth         | Amazon Cognito (CUSTOM_JWT)              |
| Example complexity   | Intermediate                             |
| SDK used             | boto3 (bedrock-agentcore-control, wafv2) |

### How it works

When you associate a web ACL with your gateway, the request flow is:

1. A client sends a request to your gateway endpoint.
2. AWS WAF evaluates the request against the rules in the associated web ACL.
3. If the request is allowed, the gateway routes it to the appropriate target.
4. If the request is blocked, the gateway returns an error to the client without forwarding the request.

AWS WAF evaluates every inbound request inline. When no web ACL is associated with your gateway, there is zero overhead and no AWS WAF evaluation occurs.

**Failure mode.** If AWS WAF is unreachable or times out during evaluation, the gateway uses its configured failure mode. The default is `FAIL_CLOSE` (block the request, security first). `FAIL_OPEN` forwards the request to the target without evaluation. Set it with the `UpdateGateway` API (see Step 4).

**Blocked-request responses.** For MCP targets, a blocked request returns a JSON-RPC error with code `-32002` and the message `"Authorization error - Request forbidden"`. For HTTP and passthrough targets, a blocked request returns HTTP 403.

## Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/getting-started/installation/) installed
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configured with credentials (`aws configure`)
- A regional AWS WAF web ACL is created in the same Region as your gateway (this tutorial creates one). CloudFront (global) web ACLs are not supported.
- Your IAM identity has these permissions:
  - `wafv2:AssociateWebACL`, `wafv2:DisassociateWebACL`, `wafv2:GetWebACLForResource`, `wafv2:ListResourcesForWebACL`
  - `wafv2:CreateWebACL`, `wafv2:GetWebACL`, `wafv2:DeleteWebACL`
  - `bedrock-agentcore:GatewayAssociateWebACL`, `bedrock-agentcore:GatewayDisassociateWebACL`, `bedrock-agentcore:GatewayGetWebACLForResource`, `bedrock-agentcore:GatewayListResourcesForWebACL`

## Deployment Steps

> [!IMPORTANT]
> All commands in this tutorial run from the [`gatewaylabproject/`](../../gatewaylabproject/) directory. Navigate there before proceeding.

### Step 1 (optional): Deploy Amazon Cognito

> [!NOTE]
> Amazon Cognito is **not required** for AgentCore gateway. This tutorial uses it to keep the focus on AWS WAF. For your enterprise workloads, you can configure any OAuth 2.0 compliant identity provider (e.g., Entra ID, Auth0, Okta). See the [Optional Setup guide](../../00-optional-setup/) for full details.

If you haven't deployed the Cognito stack yet, follow the instructions in [00-optional-setup](../../00-optional-setup/). Once deployed, capture the stack name:

```bash
export COGNITO_STACK_NAME="agentcore-gateway-lab"
```

### Step 2: Create the gateway (boto3)

The gateway must be in `READY` state before you associate a web ACL. The shared `deploy_gateway.py` script reads the Cognito outputs and writes `GATEWAY_ID` / `GATEWAY_URL` to the tutorial's `.env`:

```bash
uv run python scripts/deploy_gateway.py \
  --name waf-gateway \
  --env-file scripts/waf/.env
```

### Step 3: Attach a target and associate the web ACL (boto3)

```bash
uv run python scripts/waf/deploy.py
```

This script attaches a no-auth MCP server target, creates the web ACL, and associates it with the gateway.

The MCP target points at the public Exa MCP server. Because Exa's endpoint is public, the gateway forwards requests with no outbound credential (no credential provider):

```python
gateway_client.create_gateway_target(
    name="exa-mcp",
    gatewayIdentifier="<GATEWAY_ID>",
    targetConfiguration={
        "mcp": {"mcpServer": {"endpoint": "https://mcp.exa.ai/mcp"}}
    },
)
```

The web ACL is regional, with AWS Managed Rules (started in `COUNT` mode so you can observe matches before enforcing) and a rate-based rule. Associate it with the gateway by its resource ARN. Using the AWS CLI:

```bash
aws wafv2 create-web-acl \
  --name waf-gateway-acl \
  --scope REGIONAL \
  --default-action Allow={} \
  --visibility-config SampledRequestsEnabled=true,CloudWatchMetricsEnabled=true,MetricName=waf-gateway-acl \
  --rules '[
    {"Name":"common-rule-set","Priority":0,"OverrideAction":{"Count":{}},
     "Statement":{"ManagedRuleGroupStatement":{"VendorName":"AWS","Name":"AWSManagedRulesCommonRuleSet"}},
     "VisibilityConfig":{"SampledRequestsEnabled":true,"CloudWatchMetricsEnabled":true,"MetricName":"common-rule-set"}},
    {"Name":"rate-limit","Priority":1,"Action":{"Block":{}},
     "Statement":{"RateBasedStatement":{"Limit":100,"AggregateKeyType":"IP"}},
     "VisibilityConfig":{"SampledRequestsEnabled":true,"CloudWatchMetricsEnabled":true,"MetricName":"rate-limit"}}
  ]'

aws wafv2 associate-web-acl \
  --web-acl-arn <web-acl-arn> \
  --resource-arn arn:aws:bedrock-agentcore:<region>:<account>:gateway/<gateway-id>
```

The `--resource-arn` is the gateway ARN. One gateway has at most one web ACL; re-associating replaces the existing one.

### Step 4 (optional): Configure the failure mode

The default failure mode is `FAIL_CLOSE`. To allow requests through when AWS WAF is unreachable (availability over security), set `FAIL_OPEN` with `UpdateGateway`:

```bash
aws bedrock-agentcore-control update-gateway \
  --gateway-identifier <gateway-id> \
  --name waf-gateway \
  --role-arn <gateway-service-role-arn> \
  --authorizer-type CUSTOM_JWT \
  --authorizer-configuration '{"customJWTAuthorizer": {"discoveryUrl": "<discovery-url>", "allowedClients": ["<client-id>"]}}' \
  --waf-configuration '{"failureMode": "FAIL_OPEN"}'
```

### Step 5: Enforce the managed rules

After observing matches in `COUNT` mode, switch the managed rule group to `BLOCK` to enforce it:

```bash
uv run python scripts/waf/deploy.py --mode block
```

## Demo

> [!TIP]
> You can also explore the gateway with the [AgentCore gateway MCP Inspector](../../05-community/gateway-mcp-inspector/).

```bash
uv sync
uv run python scripts/waf/invoke.py
```

The script lists the Exa tools through the gateway (an allowed request reaching the target), then sends a burst of calls to trip the rate-based rule. Once the limit is exceeded, AWS WAF blocks the request and the gateway returns:

```text
Allowed request: tools/list (reaches the Exa MCP target)
  web_search_exa
  ...

Blocked request: sending a burst of 150 calls to trip the rate rule
  Request 101: BLOCKED by AWS WAF (HTTP 403)
    {"jsonrpc": "2.0", "error": {"code": -32002, "message": "Authorization error - Request forbidden"}, "id": "tools-list-raw"}
```

> [!NOTE]
> AWS WAF rate-based rules use a roughly 5-minute window with sampling, so the block may not trip on the first run. Re-run the demo, or check the `WafBlocks` metric (below).

## Monitoring

AWS WAF activity for your gateway is available in Amazon CloudWatch under the `AWS/Bedrock-AgentCore` namespace:

| Metric          | Description                                                                                                         |
| :-------------- | :------------------------------------------------------------------------------------------------------------------ |
| `WafBlocks`     | Count of requests blocked by AWS WAF.                                                                               |
| `WafFailOpens`  | Count of requests forwarded without evaluation because AWS WAF was unreachable and the failure mode is `FAIL_OPEN`. |
| `WafFailCloses` | Count of requests rejected because AWS WAF was unreachable and the failure mode is `FAIL_CLOSE`.                    |

For rule-level detail about blocked requests, enable AWS WAF logging and correlate the request ID in your gateway logs with the AWS WAF logs.

## Best practices

Consider building potential [Security Automations for AWS WAF](https://docs.aws.amazon.com/solutions/latest/security-automations-for-aws-waf/architecture-overview.html), as follows:

![auto](./images/automation.png)

- Use AWS Managed Rules rule groups for common protections against known threats.
- Implement rate-based rules to protect against volumetric attacks.
- Use IP-based rules to allowlist or denylist known sources.
- Test AWS WAF rules in `COUNT` mode before switching to `BLOCK` to understand the impact on your traffic.
- Monitor the `WafBlocks`, `WafFailOpens`, and `WafFailCloses` metrics to tune your rules.
- Use the default `FAIL_CLOSE` mode for security-sensitive workloads. Use `FAIL_OPEN` only when availability is critical and you have other security controls in place.

## Cleanup

> [!IMPORTANT]
> Clean up this tutorial before starting another. Leftover resources can cause conflicts with other tutorials.

You must disassociate the web ACL from the gateway before you can delete the gateway. The cleanup script disassociates the web ACL, deletes it, then deletes the gateway (and its target), the gateway IAM role, and the tutorial's `.env` file:

```bash
uv run python scripts/waf/cleanup.py
```

Delete the Cognito stack (if no longer needed by other tutorials):

```bash
aws cloudformation delete-stack --stack-name $COGNITO_STACK_NAME
```

## Documentation

- [Protecting your gateway with AWS WAF](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-waf.html)
- [AgentCore gateway Developer Guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html)
- [AWS WAF Developer Guide](https://docs.aws.amazon.com/waf/latest/developerguide/)
