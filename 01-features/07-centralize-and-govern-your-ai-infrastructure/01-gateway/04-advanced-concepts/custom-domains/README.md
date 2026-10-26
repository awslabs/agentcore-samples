# agwcd — Custom Domains for Amazon Bedrock AgentCore Gateway

As organizations deploy AI agents at scale with Amazon Bedrock AgentCore Gateway, a common requirement emerges: expose gateway endpoints through custom domain names that align with corporate branding and security standards. AgentCore Gateway provides a fully managed MCP endpoint, but production deployments often need a custom domain (for example, `mcp.example.com`), the ability to front **multiple gateways** under one domain, and enterprise edge controls — WAF, geo-restriction, access logging, error alarms — plus **correct OAuth / A2A discovery per path**.

`agwcd` (AgentCore Gateway Custom Domain) is a CLI that manages a config file describing your domain, its routes (path → gateway), and each route's endpoints, then deploys a single CloudFront distribution (via AWS CDK) that reverse-proxies everything and rewrites discovery documents so they resolve against your custom domain.

![arch](./architecture.png)

## Prerequisites

- An Amazon Bedrock AgentCore Gateway — see the [getting started guide](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-quick-start.html).
- A registered domain and a **Route 53 public hosted zone** for it (see DNS delegation below).
- [AWS CDK](https://docs.aws.amazon.com/cdk/v2/guide/getting-started.html) CLI and [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-getting-started.html) configured with permissions for CloudFront, Route 53, ACM, WAF, S3, SNS, Secrets Manager, and Lambda.
- [Python 3.12](https://www.python.org/downloads/)+ and [uv](https://docs.astral.sh/uv/).

## Install

```bash
uv sync                  # creates .venv, installs deps + the `agwcd` command (editable)
source .venv/bin/activate
```

`uv sync` reads `pyproject.toml` (and pins to `uv.lock`). Add the test dependency with `uv sync --extra dev`, or run one-off commands without activating via `uv run` (for example `uv run agwcd list`, `uv run pytest`).

## Usage

### 1. DNS delegation

If your domain is registered elsewhere, delegate a subdomain to Route 53: create a public hosted zone (for example `mcp.example.com`) and add its four NS records at your registrar. Verify:

```bash
dig mcp.example.com NS +short
```

See [docs/hosted-zone-setup.md](docs/hosted-zone-setup.md) for step-by-step setup covering both cases — domain registered **through AWS (Route 53)** and **outside AWS** (external registrar).

### 2. Build the config with the CLI

Each command takes flags so the whole config can be built non-interactively (handy for CI or when you already know the layout). Say your gateway `https://<gw>.gateway.bedrock-agentcore.us-east-1.amazonaws.com` exposes four things you want on the custom domain: the aggregated **MCP** endpoint, an HTTP-passthrough MCP target `github`, an A2A agent `monitor-agent`, and the aggregated **inference** endpoint. Attach the gateway at the root (`/`) with origin verification on, then add the four endpoints:

```bash
agwcd setup --domain mcp.example.com
agwcd add path / \
  -g https://<gw>.gateway.bedrock-agentcore.us-east-1.amazonaws.com \
  --origin-verify
agwcd add endpoint / -t mcp
agwcd add endpoint / -t http_mcp -n github
agwcd add endpoint / -t http_a2a -n monitor-agent
agwcd add endpoint / -t inference
agwcd list                        # review the resulting URLs (and per-gateway verification)
```

`agwcd list` then prints:

```text
Domain: mcp.example.com
Geo allowlist: US, CA, AT, BE, BG, HR, CY, CZ, DK, EE, FI, FR, DE, GR, HU, IE, IT, LV, LT, LU, MT, NL, PL, PT, RO, SK, SI, ES, SE

/  →  https://<gw>.gateway.bedrock-agentcore.us-east-1.amazonaws.com  [verified]
  - mcp: https://mcp.example.com/mcp
  - http_mcp/github: https://mcp.example.com/github/mcp
  - http_a2a/monitor-agent: https://mcp.example.com/monitor-agent
  - inference: https://mcp.example.com/inference
```

To front **multiple gateways** instead, give each its own path — `agwcd add path /hr -g <gwA>` and `agwcd add path /sales -g <gwB>` (root and path gateways are mutually exclusive; see [Concepts](#concepts)).

Prefer prompts? `agwcd init` runs a guided walkthrough (domain → gateway(s) → endpoints → deploy), and `add path` / `add endpoint` fall back to interactive prompts when flags are omitted. All commands accept `--config <file>` (default `agwcd.json`). `add path` enables [origin verification](#origin-verification) with `--origin-verify` / `--no-origin-verify`.

### 3. Deploy

```bash
agwcd deploy                      # = cdk deploy -c agwcd_config=agwcd.json
```

`agwcd synth`, `agwcd diff`, and `agwcd destroy` wrap the corresponding `cdk` commands the same way. Extra `cdk` flags pass through: `agwcd deploy --require-approval never`.

> **Note:** the stack deploys to `us-east-1` — CloudFront requires ACM certificates and WAF Web ACLs there, and Lambda@Edge functions live there and are replicated globally. Set `CDK_DEFAULT_ACCOUNT` (or configure the CDK environment) for the target account.

## What gets deployed

| Resource | Purpose |
|----------|---------|
| Route 53 A record | Alias record pointing the custom domain to CloudFront |
| ACM certificate | TLS certificate with DNS validation (`RemovalPolicy.RETAIN`) |
| CloudFront distribution | Reverse proxy — per-endpoint live + discovery behaviors |
| CloudFront Function (viewer-request) | Strips the route prefix, injects the resource-metadata header, denies (403) unmatched URIs |
| AWS WAF Web ACL | Common rules, known bad inputs, rate limiting (2,000 req/5 min per IP) |
| Regional discovery Lambda | Fetches downstream PRM / Agent Card and overrides only the domain-facing field(s); Function URL origin behind Origin Access Control |
| Lambda@Edge (`WWW-Authenticate` rewrite) | Rewrites the `resource_metadata` param on 401 responses per RFC 9728 (added only when an MCP endpoint exists) |
| Lambda (origin-verify interceptor) | Sample gateway REQUEST interceptor — **one per verified gateway** (only for gateways with `origin_verify: true`) |
| Secrets Manager secret | Auto-generated origin-verification header value — **one per verified gateway** |
| S3 bucket | CloudFront access logs (90-day lifecycle, encrypted, SSL enforced) |
| CloudWatch log groups | WAF logs (`aws-waf-logs-agwcd`, `authorization` redacted) + discovery/interceptor Lambda logs, 3-month retention |
| KMS key | Customer-managed key (rotation on) encrypting the alarm SNS topic at rest |
| SNS topic + CloudWatch alarms | 5xx (>5%) and 4xx (>20%) error-rate alerting (KMS-encrypted) |


## Concepts

One custom domain fronts **either**:

- a single gateway at the **root** (`/`), **or**
- one-or-more gateways under distinct **paths** (`/hr`, `/sales`, …) — the same gateway URL may be reused across paths.

Root and path gateways are **mutually exclusive**: once a gateway is attached at `/`, the CLI won't let you add path gateways, and vice versa. (This avoids collisions between a root gateway's targets and a same-named path.)

Each route (path) exposes one or more **endpoints**:

| Endpoint type | Live URL | Discovery document | What gets overridden |
|---|---|---|---|
| `mcp` | `https://<domain>/<path>/mcp` | PRM at `https://<domain>/.well-known/oauth-protected-resource/<path>/mcp` | `resource` |
| `http_mcp` (HTTP-passthrough MCP target) | `https://<domain>/<path>/<targetName>/mcp` | PRM at `…/.well-known/oauth-protected-resource/<path>/<targetName>/mcp` | `resource` |
| `http_a2a` (A2A agent) | `https://<domain>/<path>/<targetName>` | Agent Card at `https://<domain>/<path>/<targetName>/.well-known/agent-card.json` (+ PRM) | `url`, `additionalInterfaces[].url` |
| `inference` (gateway inference endpoint) | `https://<domain>/<path>/inference` | PRM at `…/.well-known/oauth-protected-resource/<path>/inference` | `resource` |
| `http_agent` (agent-as-tool, no card) | `https://<domain>/<path>/<targetName>` | PRM at `…/.well-known/oauth-protected-resource/<path>/<targetName>` | `resource` |

Discovery documents are **never built from scratch** — `agwcd` fetches the downstream document from the gateway and overrides only the field(s) above, so every other field (`authorization_servers`, scopes, skills, …) is preserved. This follows the MCP authorization spec (RFC 9728 path-insertion) and the A2A Agent Card spec.

New target types can be added by dropping a `TargetType` subclass into `custom_domains/targets/` and registering it — see `CLAUDE.md`. `TargetType.plan()` already builds live passthrough + a path-inserted PRM + the `WWW-Authenticate` rewrite for every type, so a subclass usually only sets the two path hooks. For example, `http_agent` (a normal agent-as-tool with no agent card) is:

```python
class HttpAgentTarget(TargetType):
    type_key = "http_agent"
    requires_target_name = True
    description = "HTTP agent-as-tool, no card (/<targetName>)"

    def _live_base(self, route_prefix, target_name):
        return f"{route_prefix}/{target_name}"

    def _origin_prefix(self, target_name):
        return f"/{target_name}"
```

Add one line to `custom_domains/targets/registry.py` and it's live — it gets a PRM at `…/.well-known/oauth-protected-resource/<path>/<targetName>` automatically. Override `_extra_discovery_routes` only when the type also serves a card (like `http_a2a`). The bundled `inference` and `http_agent` types are exactly this pattern.

## Config file — `agwcd.json`

The CLI reads/writes this; you can also hand-edit it. See `agwcd.example.json`.

```json
{
  "domain_name": "mcp.example.com",
  "geo_allowlist": ["US", "CA"],
  "routes": [
    { "path": "/hr", "gateway_url": "https://gwhr.gateway.bedrock-agentcore.us-east-1.amazonaws.com",
      "origin_verify": true,
      "endpoints": [ { "type": "mcp" } ] },
    { "path": "/sales", "gateway_url": "https://gwsales.gateway.bedrock-agentcore.us-east-1.amazonaws.com",
      "origin_verify": false,
      "endpoints": [
        { "type": "mcp" },
        { "type": "http_mcp", "target_name": "catalog" },
        { "type": "http_a2a", "target_name": "planner" }
      ] }
  ]
}
```

`geo_allowlist` defaults to US + Canada + EU member states if omitted. `origin_verify` is **optional and per-gateway** (default `false`) — see [Origin verification](#origin-verification). Routes that share a `gateway_url` must use the same `origin_verify` value.

## Origin verification

Origin verification prevents callers from bypassing CloudFront (and therefore WAF, geo-restriction, and logging) by hitting the AgentCore Gateway endpoint directly. When enabled for a gateway, the stack generates a secret and injects it as the `X-AgentCore-Origin-Verify` header on the CloudFront → gateway origin (and on the discovery Lambda's downstream fetches to that gateway); a sample REQUEST interceptor Lambda rejects any request missing the header.

> **WAF placement:** `agwcd` attaches the web ACL to **CloudFront** (the custom domain), not to the gateway — the two use incompatible scopes (CLOUDFRONT vs. REGIONAL). Keeping the WAF at the edge + turning on origin verification makes the WAF-protected edge the only usable path in. A separate regional WAF on the gateway is only worth adding when origin verification is off, or for deliberate defense-in-depth. See [docs/waf-and-edge-protection.md](docs/waf-and-edge-protection.md).

It is **optional and configured per gateway**:

- `agwcd add path` asks whether to enable it (default yes) — or pass `--origin-verify` / `--no-origin-verify`. In the config it is the route's `origin_verify` boolean.
- Because the header is injected at the shared gateway origin, it is a property of the **gateway**, not the path: routes sharing a `gateway_url` must agree, and the CLI inherits the setting when you reuse a gateway.
- Each verified gateway gets its **own** secret and interceptor. Gateways left off get no secret, no header, and no interceptor.
- Enabling it requires a [post-deploy step](#configure-the-origin-verify-interceptor-only-for-verified-gateways): attach the interceptor to the gateway.

## PRM (protected-resource metadata)

The PRM served for an MCP endpoint is proxied from the **gateway's** own discovery document, with only the domain-facing `resource` field overridden to `https://<domain>/<path>/<target>/mcp`. Every other field (`authorization_servers`, `scopes_supported`, …) is preserved verbatim — the document is never built from scratch.

An AgentCore Gateway has a **single inbound authorizer** for the whole gateway, so its own PRM already advertises the correct authorization server for every path and target. Overriding the advertised auth server would only make discovery lie — a client that followed it would obtain a token the gateway rejects. `agwcd` therefore overrides `resource` only.

> Third-party discovery hosts never receive a gateway's origin-verify secret (the header is only injected on fetches to the verified gateway itself).

## A2A agent cards (behind gateway auth)

The gateway's single inbound authorizer protects **everything**, including each agent's `.well-known/agent-card.json`. `agwcd` makes an authenticated agent card resolve on the custom domain like this:

- **Discovery.** Each A2A endpoint gets its own path-inserted PRM, exactly like MCP — `https://<domain>/.well-known/oauth-protected-resource/<path>/<agent>`, proxied from the gateway's root PRM with `resource` overridden to the bare agent URL `https://<domain>/<path>/<agent>` (no `/mcp`). A `401` on either the live endpoint or the card carries `WWW-Authenticate` pointing at that custom-domain PRM, so a client can find the auth server (Entra, …) and get a token.
- **Fetching the card.** The card body is served by the discovery Lambda (only it can rewrite the card's `url`). It **forwards the caller's bearer token** to the gateway on the downstream fetch — no stored service credential — so the card stays exactly as protected as the gateway. Because the discovery origin is OAC-signed (SigV4 occupies `Authorization`), a viewer-request CloudFront function copies the caller's token into a side header (`x-agwcd-authorization`) for the Lambda to forward.

So the client flow is the standard OAuth challenge: `GET card` → `401` → custom-domain PRM → token → retry `GET card` with `Authorization: Bearer <token>` → `200` with the `url` rewritten to the custom domain. This needs an A2A client that sends its gateway token to the card URL (or follows the 401 challenge).

## Stack outputs

| Output | Description |
|--------|-------------|
| `DistributionDomain` | CloudFront distribution domain name |
| `CustomDomain` | `https://<domain>` |
| `AlarmTopicArn` | SNS topic ARN for error-rate alarms |
| `OriginVerifyHeader` | Custom origin header name (`X-AgentCore-Origin-Verify`) — only if any gateway is verified |
| `OriginVerifySecret<N>Arn` | Secrets Manager ARN for verified gateway *N* (output description names the gateway) |
| `OriginVerifyInterceptor<N>Arn` | REQUEST interceptor Lambda ARN for verified gateway *N* |
| `Endpoint<N>` | The live URL of each configured endpoint |

## Post-deployment

### Configure the origin-verify interceptor (only for verified gateways)

If you enabled `origin_verify` for a gateway, finish wiring it up post-deploy. Attach that gateway's `OriginVerifyInterceptor<N>` Lambda (or your own with the logic from `lambda/origin_verify/index.py`) as a REQUEST interceptor on **that gateway** with `passRequestHeaders` enabled — the Lambda's `ORIGIN_VERIFY_HEADER` and `ORIGIN_VERIFY_SECRET_ARN` are already set, and it fetches the secret value from Secrets Manager at runtime (the plaintext never lands in an environment variable). The interceptor validates the header for **every target type on the gateway** — MCP targets (the `mcp` payload) and HTTP-passthrough / inference targets (the shared `http` payload, base64 body) — so a single attachment covers all endpoints. Repeat per verified gateway (each has its own secret + interceptor). Retrieve a value if needed:

```bash
aws secretsmanager get-secret-value \
  --secret-id <OriginVerifySecret0Arn> --query SecretString --output text
```

Gateways left at `origin_verify: false` skip all of this — no secret, no header, no interceptor.

### Subscribe to alarms (optional)

```bash
aws sns subscribe --topic-arn <AlarmTopicArn> \
  --protocol email --notification-endpoint your-team@example.com
```

### Point clients at the custom domain

```json
{ "mcpServers": { "sales": { "url": "https://mcp.example.com/sales/mcp" } } }
```

## Verify a deployment

```bash
dig mcp.example.com +short                                   # CloudFront IPs
curl -v https://mcp.example.com 2>&1 | grep "subject:"       # cert = your domain

# PRM resolves to the per-path custom-domain URL (not <gw>/mcp), other fields preserved
curl https://mcp.example.com/.well-known/oauth-protected-resource/sales/mcp

# 401 on the live endpoint carries the correct resource_metadata
curl -i -X POST https://mcp.example.com/sales/mcp -d '{}'
#   WWW-Authenticate: Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/sales/mcp"

# A2A agent card `url` points at the custom-domain agent path
curl https://mcp.example.com/sales/planner/.well-known/agent-card.json
```

## Cleanup

```bash
agwcd destroy          # = cdk destroy
```

- The **ACM certificate** has `RemovalPolicy.RETAIN`; delete it manually in the ACM console (`us-east-1`) if no longer needed.
- **Lambda@Edge replicas** take 30–60 min to clean up after the distribution is deleted; if `destroy` fails on them, wait and retry.
- The **Secrets Manager secret** is scheduled for deletion with a 30-day recovery window (`--force-delete-without-recovery` to remove immediately).
- Empty the **S3 access-logs bucket** before it can be deleted.
- Remove the **gateway REQUEST interceptor** configuration to avoid rejected requests after the secret is gone.

## Cost

`agwcd` is free; you pay for the AWS resources the stack deploys (CloudFront, WAF, Route 53, Lambda, S3 logs, and — per verified gateway — Secrets Manager). The recurring baseline is small and dominated by the WAF web ACL + rules (~$8/mo) and the Route 53 hosted zone ($0.50/mo); most other costs scale with request volume and data transfer. See [docs/pricing.md](docs/pricing.md) for the full breakdown and how to estimate your own bill.

## Development

```bash
uv sync --extra dev
uv run pytest          # pure-Python unit tests (no AWS creds needed)
```

Architecture and constraints are documented in `CLAUDE.md`.
