# Enterprise MCP Gateway – CDK Infrastructure

This CDK stack deploys an enterprise-grade MCP (Model Context Protocol) gateway backed by Amazon Bedrock AgentCore, using an Application Load Balancer (ALB) with full security hardening.

## Architecture Overview

The stack provisions:

- **Cognito User Pool** with OAuth 2.0 Authorization Code Grant, custom scopes (`mcp.read` / `mcp.write`), and a Pre-Token Generation Lambda for audience/role claim injection.
- **AgentCore Gateway** with Cognito authorizer, Cedar policy engine (ENFORCE mode), and Bedrock Guardrails (PII masking/blocking).
- **Lambda functions**: MCP proxy, weather tool, inventory tool, user details tool, interceptor, and pre-token generation.
- **VPC** with private subnets, NAT gateway, and VPC Interface Endpoint for AgentCore.
- **Internet-facing ALB** with TLS termination, WAF WebACL, and access logging.

## Prerequisites

- AWS CDK v2 installed (`npm install -g aws-cdk`)
- Node.js 18+
- An ACM certificate and Route 53 hosted zone for the custom domain
- Python 3.12 (for Lambda bundling)

## Configuration

Set CDK context variables in `cdk.context.json` or via `-c` flags:

| Variable | Description | Default |
|---|---|---|
| `domainName` | Custom domain name (e.g. `enterprise-mcp`) | `""` |
| `hostedZoneName` | Route 53 hosted zone name | `""` |
| `hostedZoneId` | Route 53 hosted zone ID | `""` |
| `certificateArn` | ACM certificate ARN | `""` |

## Deployment

```bash
# From the cdk/ directory
npm install
npx cdk synth
npx cdk deploy
```

> **Note:** The stack is pinned to `us-east-1` in `bin/enterprise-mcp-infra.ts`. Update the `region` value there if you need a different region.

## Useful Commands

| Command | Description |
|---|---|
| `npm run build` | Compile TypeScript to JS |
| `npm run watch` | Watch for changes and compile |
| `npm run test` | Run Jest unit tests |
| `npx cdk synth` | Emit the synthesized CloudFormation template |
| `npx cdk diff` | Compare deployed stack with current state |
| `npx cdk deploy` | Deploy this stack |
| `npx cdk destroy` | Tear down the stack |

## Security Posture

### Implemented

| Feature | Details |
|---|---|
| Cognito User Pool | Admin-only sign-up, strong password policy, Pre-Token Generation Lambda for audience/role claims |
| OAuth 2.0 | Authorization Code Grant with custom scopes (`mcp.read`, `mcp.write`) |
| JWT audience validation | Proxy Lambda validates `aud` claim before forwarding to AgentCore |
| AgentCore Cognito authorizer | Token verified a second time by AWS at the gateway level |
| Cedar policy engine | Fine-grained per-user tool access in ENFORCE mode |
| Bedrock Guardrails | PII masking (address, name, email) and blocking (credit card numbers) via interceptor |
| Lambda-in-VPC proxy | Private subnet, NAT egress only |
| VPC Interface Endpoint | AgentCore traffic stays on AWS private network, never crosses public internet |
| ALB TLS termination | TLS 1.2+ on custom domain via ACM certificate |
| ALB `dropInvalidHeaderFields` | Rejects malformed headers (request-smuggling mitigation) |
| ALB Host-header gating | Every forwarding rule requires Host header match; raw `*.elb` DNS returns 404 |
| HTTP → HTTPS redirect | Permanent redirect on port 80 |
| WAF WebACL | IP rate limit (1,000 req/5 min), AWS IP Reputation list, Core Rule Set (OWASP Top 10), Known Bad Inputs |
| WAF Bot Control | COMMON level in COUNT mode (switch to BLOCK after traffic validation) |
| Reserved Lambda concurrency | Caps on all functions to limit DoS blast radius |
| Gateway resource policy | Restricts `InvokeGateway` to the VPC |
| Shield Standard | Automatic L3/L4 DDoS protection on public ALBs |
| ALB access logging | S3 bucket with SSE, public access blocked, SSL enforced, 90-day lifecycle expiration |
| Redirect URI allowlist | `handle_callback` validates `redirect_uri` against registered Cognito callback URLs before issuing 302 redirects (prevents open-redirect / auth code theft) |
| Per-Lambda IAM roles | Four dedicated least-privilege roles: `preTokenLambdaRole` (Cognito trigger), `proxyLambdaRole` (VPC + AgentCore invoke), `interceptorLambdaRole` (Bedrock Guardrails only), `toolLambdaRole` (CloudWatch Logs only) |

### Not Implemented – Consider Before Production

| Feature | Details |
|---|---|
| Shield Advanced | L7 DDoS protection, SRT access, cost protection (subscription required) |
| Bot Control TARGETED | Higher inspection level for WAF Bot Control (additional cost) |
| CloudTrail / Security Hub | Centralized audit and security findings |
| ALB access-log Athena workgroup | Query access logs via Athena for forensic analysis |
| GuardDuty findings | Threat detection integration |
| MFA enforcement | Cognito User Pool is MFA-ready but not enforced (`mfa: cognito.Mfa.REQUIRED`) |
| Scoped IAM resources | Several policies use `Resource: "*"` — scope to specific ARNs |
| PKCE enforcement | Verify PKCE is enforced on the Cognito public client (no client secret) |
| Log encryption | Lambda CloudWatch logs use default settings (no KMS CMK encryption) |
| Log retention policy | Lambda CloudWatch log retention is indefinite by default |

## Project Structure

```
cdk/
├── bin/
│   └── enterprise-mcp-infra.ts          # CDK app entry point (region pinned to us-east-1)
├── lib/
│   ├── enterprise-mcp-infra-stack.ts     # Main infrastructure stack
│   └── agentcore-policy-engine.ts        # Cedar policy engine construct
├── lambda/
│   ├── mcp_proxy_lambda.py              # MCP OAuth proxy Lambda
│   ├── pre_token_generation_lambda.py   # Cognito pre-token generation trigger
│   ├── interceptor/
│   │   └── interceptor.py               # Guardrails interceptor Lambda
│   ├── mcp-servers/
│   │   ├── weather/                     # Weather tool Lambda
│   │   ├── inventory/                   # Inventory tool Lambda
│   │   └── user_details/               # User details tool Lambda
│   └── agentcore-policy-engine/         # Policy engine custom resource Lambda
├── test/
│   └── enterprise-mcp-infra.test.ts     # Jest tests
├── cdk.json
├── cdk.context.json
├── tsconfig.json
└── package.json
```
