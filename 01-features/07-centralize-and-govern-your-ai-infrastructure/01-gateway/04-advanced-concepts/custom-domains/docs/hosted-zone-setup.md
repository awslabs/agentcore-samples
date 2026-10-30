# Setting up the Route 53 hosted zone

`agwcd` requires a **Route 53 public hosted zone** for your domain (or subdomain) in the **same AWS account** you deploy to. The stack looks the zone up at synth time (`PublicHostedZone.from_lookup`) and uses it for two things:

- **ACM certificate validation** — ACM writes a `_<token>` CNAME into the zone and issues the cert once it sees it (DNS validation, fully automatic).
- **The alias record** — an A/alias record pointing your domain at the CloudFront distribution.

No zone in the account → `agwcd deploy` / `cdk synth` fails. This guide covers both ownership cases.

> Throughout, replace `mcp.example.com` with your domain. Using a **subdomain** (like `mcp.example.com`) is recommended so you don't have to move your apex domain's DNS.

---

## Case A — domain registered through AWS (Route 53)

If you registered the domain in Route 53, a **public hosted zone was created for you automatically** at registration, and its name servers are already wired to the registered domain. There is usually **nothing to set up**.

### 1. Confirm the hosted zone exists

```bash
aws route53 list-hosted-zones-by-name --dns-name example.com \
  --query "HostedZones[?Name=='example.com.']"
```

You should see one entry with a `Id` like `/hostedzone/Z0123456789ABCDEFGHIJ`.

### 2. Deciding apex vs. subdomain

- **Serving the apex** (`example.com`) or a subdomain that lives in the **existing** zone (`mcp.example.com` with no separate zone): use the existing zone as-is. Skip to *Verify* below.
- **Serving a subdomain in its own zone** (optional, for delegation/isolation): create a dedicated hosted zone for the subdomain and delegate to it — follow *Case B, steps 1–3*, except the NS records go into your **Route 53 apex zone** instead of an external registrar:

  ```bash
  # after creating the mcp.example.com zone (Case B step 1), copy its 4 NS values, then:
  aws route53 change-resource-record-sets --hosted-zone-id <APEX_ZONE_ID> \
    --change-batch '{
      "Changes": [{
        "Action": "UPSERT",
        "ResourceRecordSet": {
          "Name": "mcp.example.com",
          "Type": "NS",
          "TTL": 172800,
          "ResourceRecords": [
            {"Value": "ns-1.awsdns-00.org"},
            {"Value": "ns-2.awsdns-00.co.uk"},
            {"Value": "ns-3.awsdns-00.com"},
            {"Value": "ns-4.awsdns-00.net"}
          ]
        }
      }]
    }'
  ```

### Verify

```bash
dig mcp.example.com NS +short     # returns the Route 53 name servers
```

You're done — the domain resolves through Route 53 and ACM DNS validation will work.

---

## Case B — domain registered outside AWS (external registrar)

Your domain's DNS is at another registrar (GoDaddy, Namecheap, Cloudflare, etc.). You **delegate a subdomain** to Route 53 so `agwcd` can manage its records, without moving your whole domain.

### 1. Create a public hosted zone for the subdomain

```bash
aws route53 create-hosted-zone \
  --name mcp.example.com \
  --caller-reference "agwcd-$(date +%s)"
```

Note the returned `HostedZone.Id`.

### 2. Get the zone's four name servers

```bash
aws route53 get-hosted-zone --id <HOSTED_ZONE_ID> \
  --query "DelegationSet.NameServers" --output text
```

Returns four values, e.g. `ns-123.awsdns-45.com  ns-678.awsdns-90.net  ns-234.awsdns-56.org  ns-789.awsdns-12.co.uk`.

### 3. Add NS records at your registrar

In your registrar's DNS control panel, create an **NS record** for the subdomain host (`mcp`) with those four name servers as values:

| Type | Host / Name | Value |
|------|-------------|-------|
| NS | `mcp` | `ns-123.awsdns-45.com` |
| NS | `mcp` | `ns-678.awsdns-90.net` |
| NS | `mcp` | `ns-234.awsdns-56.org` |
| NS | `mcp` | `ns-789.awsdns-12.co.uk` |

(Exact UI varies by registrar. Some want a trailing dot on the values; some want the host as `mcp` and others as `mcp.example.com`.)

### 4. Wait for delegation to propagate, then verify

```bash
dig mcp.example.com NS +short
```

When this returns the **AWS** name servers (not your registrar's), delegation is live. This can take from a few minutes up to the parent zone's TTL (often ~30–60 min).

---

## After the zone is ready (both cases)

1. Point `agwcd` at the domain and deploy:

   ```bash
   agwcd setup --domain mcp.example.com
   agwcd add path ...        # attach gateways
   agwcd deploy              # ACM validates via DNS + creates the alias record
   ```

   ACM DNS validation and the CloudFront alias record are created **by the stack** — you do not add them manually.

2. Confirm the domain serves the distribution once deployed:

   ```bash
   dig mcp.example.com +short                              # CloudFront IPs
   curl -v https://mcp.example.com 2>&1 | grep "subject:"  # cert = your domain
   ```

## Troubleshooting

- **`Cannot retrieve value from context provider hostedZone`** at synth/deploy — the account has no public hosted zone matching `domain_name`. Create/verify it (above), and make sure your credentials target the **same account** as the zone.
- **ACM certificate stuck in `PENDING_VALIDATION`** — DNS isn't resolving to the zone yet. For Case B, confirm `dig <domain> NS +short` returns the AWS name servers; for a private-only zone, ACM DNS validation cannot complete (the zone must be **public**).
- **Wrong account** — `from_lookup` only sees zones in the account/region the CDK environment resolves to. Set `CDK_DEFAULT_ACCOUNT` accordingly.
- **Apex-domain limits** — some external registrars can't delegate the apex (`example.com`) via NS; delegate a subdomain instead, which is the recommended pattern here anyway.
