# Cost of running `agwcd`

`agwcd` itself is free — it's a CLI + CDK app. Cost comes entirely from the **AWS resources the stack deploys**. This page lists every cost driver, whether it's fixed (per month) or usage-based (per request/GB), and what tends to dominate the bill.

> **Prices change and vary by region.** The numbers below are indicative US-East-1 list prices for orientation only — always confirm against the linked AWS pricing pages, and use the [AWS Pricing Calculator](https://calculator.aws/) for an estimate tied to your traffic. There is no AgentCore Gateway charge here; that's billed separately by the gateway you front.

## The short version

For a low-to-moderate traffic deployment, the recurring cost is small and dominated by a few fixed items:

- **~$5.50–6/month baseline** even at near-zero traffic — mostly the **AWS WAF web ACL + rules** (~$5/mo/ACL + ~$1/rule/mo) and the **Route 53 hosted zone** ($0.50/mo).
- **Secrets Manager**: $0.40/mo **per verified gateway** (only if origin verification is on).
- **Everything else** (CloudFront, Lambda, S3 logs, SNS, CloudWatch) scales with request volume and is typically cents-to-a-few-dollars at modest traffic.

Cost grows with **request volume** (CloudFront + WAF + Lambda invocations) and **data transfer out**, which is the usual dominant term at scale.

## Cost drivers by resource

| Resource | Pricing model | Rough US-East-1 list price | Notes |
|----------|---------------|-----------------------------|-------|
| **CloudFront distribution** | Per request + data transfer out | ~$0.085/GB out (first tier); ~$0.01 per 10k HTTPS requests | Usually the largest line item at scale. [Pricing](https://aws.amazon.com/cloudfront/pricing/) |
| **AWS WAF web ACL** | Fixed + per rule + per request | $5.00/mo per web ACL; $1.00/mo per rule; $0.60 per 1M requests | agwcd uses 3 rule groups (Common, Known Bad Inputs, rate-limit) → ~$8/mo fixed before request charges. [Pricing](https://aws.amazon.com/waf/pricing/) |
| **Route 53 hosted zone** | Fixed + per query | $0.50/mo per hosted zone; ~$0.40 per 1M queries | Alias queries to CloudFront are free. [Pricing](https://aws.amazon.com/route53/pricing/) |
| **ACM public certificate** | **Free** | $0 | Public certs used with CloudFront cost nothing. |
| **Regional discovery Lambda** | Per request + GB-second | $0.20 per 1M requests; $0.0000166667/GB-s | Invoked only on discovery-doc fetches (PRM / agent card), not live traffic. Free tier often covers it. [Pricing](https://aws.amazon.com/lambda/pricing/) |
| **Lambda@Edge (`WWW-Authenticate`)** | Per request + GB-second (edge rates) | $0.60 per 1M requests; $0.00000625125/GB-s | Higher per-unit than regional Lambda; runs only on 401 responses from MCP endpoints. |
| **Lambda Function URL / OAC** | Included | $0 | No extra charge beyond the Lambda invocation. |
| **Secrets Manager secret** | Per secret + per 10k API calls | $0.40/mo per secret; $0.05 per 10k calls | **One per verified gateway.** Only created when `origin_verify: true`. [Pricing](https://aws.amazon.com/secrets-manager/pricing/) |
| **Origin-verify interceptor Lambda** | Per request + GB-second | (same as Lambda above) | Runs on the gateway side; one per verified gateway. Only if origin verification is on. |
| **S3 access-logs bucket** | Storage + requests | ~$0.023/GB-mo; PUT/GET request charges | 90-day lifecycle keeps storage bounded. CloudFront log delivery itself is free. [Pricing](https://aws.amazon.com/s3/pricing/) |
| **SNS topic** | Per request/notification | $0.50 per 1M publishes; email notifications free | Only fires on alarm state changes → negligible. [Pricing](https://aws.amazon.com/sns/pricing/) |
| **CloudWatch alarms** | Per alarm | ~$0.10/mo per standard alarm | agwcd creates 2 (5xx > 5%, 4xx > 20%). [Pricing](https://aws.amazon.com/cloudwatch/pricing/) |

## What changes the bill

- **Traffic volume** — CloudFront requests + data-transfer-out and WAF per-request charges scale linearly. At scale, **CloudFront data transfer out** is almost always the dominant cost.
- **Origin verification** — each **verified gateway** adds a Secrets Manager secret ($0.40/mo) + an interceptor Lambda. Gateways left unverified add nothing. See [Origin verification](../README.md#origin-verification).
- **Number of gateways / paths** — more routes mean more CloudFront behaviors, but behaviors themselves aren't billed; the cost is still per-request. (Note the 25-behavior CloudFront soft limit, unrelated to cost.)
- **WAF rules** — adding rule groups is +$1/mo each; the three defaults are already included above.
- **Log retention** — the S3 access-logs bucket has a 90-day lifecycle; longer retention or high traffic grows storage cost.
- **Geo-restriction** — free; it reduces cost by dropping disallowed traffic at the edge.

## Not charged by this stack

- **AgentCore Gateway** usage — billed separately by the gateway you're fronting.
- **ACM public certificates** — free.
- A **gateway-level (regional) WAF** — `agwcd` doesn't create one; if you add it yourself (see [docs/waf-and-edge-protection.md](waf-and-edge-protection.md)) it's a separate WAF bill (another ~$5/mo ACL + rules).

## Estimating your own cost

1. Open the [AWS Pricing Calculator](https://calculator.aws/).
2. Add: CloudFront (your monthly requests + GB out), WAF (1 web ACL + 3 rules + requests), Route 53 (1 hosted zone + queries), Lambda (discovery + edge invocations), S3 (log volume), and Secrets Manager (one secret per verified gateway).
3. Skip ACM (free) and SNS/CloudWatch (typically pennies).

## Reducing cost

- Leave **origin verification off** on gateways that don't need it (saves the per-gateway secret + interceptor) — but weigh that against the CloudFront-bypass risk in [docs/waf-and-edge-protection.md](waf-and-edge-protection.md).
- Tighten the **geo allowlist** to drop unwanted traffic at the edge before it incurs WAF/Lambda/transfer charges.
- Shorten **S3 log retention** if you don't need 90 days.
- Consolidate gateways where possible — reusing one `gateway_url` across paths means one origin (and, when verified, one shared secret) instead of several.
