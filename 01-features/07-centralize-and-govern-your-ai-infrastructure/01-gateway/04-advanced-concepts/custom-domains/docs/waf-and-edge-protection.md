# WAF and edge protection: where does the firewall go?

Short answer: **`agwcd` puts AWS WAF on CloudFront (the custom domain), not on the gateway.** Combined with **origin verification**, that makes the WAF-protected edge the only usable way in. This guide explains why, and when you might also want a WAF on the gateway itself.

## The two places a WAF can live

There are two independent WAF attachment points in this architecture, and they use **different, incompatible web ACL scopes**:

| Where | Web ACL scope | Attaches to | Provisioned by |
|-------|---------------|-------------|----------------|
| **CloudFront (the custom domain)** | `CLOUDFRONT` (global, us-east-1) | The CloudFront distribution | **`agwcd` — automatically** |
| **The AgentCore Gateway** | `REGIONAL` (gateway's region) | The gateway resource ARN | You, manually ([AWS docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-waf.html)) |

A single web ACL cannot cover both — a `CLOUDFRONT`-scope ACL can't attach to a gateway, and a `REGIONAL` ACL can't attach to CloudFront. They are separate resources.

## Why `agwcd` protects CloudFront

The whole point of `agwcd` is to make one custom domain the **single front door**: clients hit `https://mcp.example.com/...`, CloudFront reverse-proxies to the gateway. So the edge is where you want to inspect traffic:

- It filters **every** request — live endpoints *and* the discovery documents (PRM / agent card) served by the edge functions and the discovery Lambda — before anything reaches a gateway.
- It runs at the edge, closest to the caller, so bad traffic is dropped before it enters your account.
- It's the layer that already carries your other edge controls (geo-restriction, access logging, rate limiting).

The web ACL `agwcd` deploys includes AWS Managed Rules (Common + Known Bad Inputs) and a rate-based rule (2,000 requests / 5 min per IP).

## The gap: bypassing CloudFront

CloudFront-level WAF only inspects traffic that **goes through CloudFront**. The AgentCore Gateway endpoint is itself publicly reachable, so a caller who knows the gateway URL could hit it directly and skip your WAF, geo-restriction, and logging entirely.

`agwcd` closes this gap with **origin verification** (per gateway, opt-in):

- The stack generates a secret and injects it as the `X-AgentCore-Origin-Verify` header on the CloudFront → gateway origin.
- You attach the sample REQUEST interceptor to the gateway; it rejects any request missing that header.
- Net effect: the gateway only accepts requests that came through CloudFront — i.e. through the WAF.

See the [Origin verification](../README.md#origin-verification) section for how to enable it.

## Recommendation

**Turn on origin verification and keep the WAF on CloudFront.** Do not put a second WAF on the gateway unless you have a specific reason:

- ✅ **Origin verification ON + CloudFront WAF (agwcd default path):** the gateway is effectively reachable only via the WAF-protected edge. No gateway-level WAF needed.
- ⚠️ **Origin verification OFF:** direct-to-gateway traffic skips your WAF completely. If you can't enforce origin verification, add a **regional web ACL on the gateway** as defense-in-depth. This is the only case where the gateway WAF is really warranted.
- 🛡️ **Belt-and-suspenders:** highly regulated workloads may want WAF in *both* places. That's fine — just remember they're separate web ACLs you manage independently, with separate rules and metrics.

## Adding a WAF to the gateway (only if you need it)

This is **not** done by `agwcd` — the CLI never touches gateway configuration. Do it manually with a **regional** web ACL in the gateway's region:

```bash
# Create/choose a REGIONAL web ACL, then associate it with the gateway:
aws wafv2 associate-web-acl \
  --web-acl-arn arn:aws:wafv2:us-east-1:123456789012:regional/webacl/my-gateway-acl/<id> \
  --resource-arn arn:aws:bedrock-agentcore:us-east-1:123456789012:gateway/<gateway-id>
```

Notes from the AWS docs:
- The web ACL must be **REGIONAL** and in the **same region as the gateway**. CloudFront (global) web ACLs are rejected.
- One web ACL per gateway; the association replaces any existing one. You must disassociate before deleting the gateway.
- Failure mode is configurable via `UpdateGateway --waf-configuration '{"failureMode": "FAIL_CLOSE"}'` (default) or `FAIL_OPEN`.
- Blocked MCP requests return JSON-RPC error `-32002`; HTTP/passthrough targets return HTTP 403.
- Monitor `WafBlocks`, `WafFailOpens`, `WafFailCloses` in the `AWS/Bedrock-AgentCore` CloudWatch namespace.

## Summary

- `agwcd` gives you WAF **at the edge (CloudFront)** out of the box.
- Enable **origin verification** so nobody can bypass it by calling the gateway directly.
- Add a **regional gateway WAF** only when origin verification isn't in place, or for deliberate defense-in-depth — and manage it yourself, separately.
