# AgentCore Browser with Squid Proxy

This example deploys an Amazon Bedrock AgentCore Browser that routes all web traffic through a Squid forward proxy running on EC2. The proxy authenticates requests via Secrets Manager and ships access logs to S3, giving you a full audit trail of every URL the browser visits.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  VPC (10.0.0.0/16)                                  │
│                                                     │
│  ┌─────────────────────┐  ┌──────────────────────┐  │
│  │  Private Subnet     │  │  Public Subnet       │  │
│  │                     │  │                      │  │
│  │  AgentCore Browser  │──│  Squid EC2 (:3128)   │──── Internet
│  │  (VPC mode)         │  │  ├─ basic auth       │  │
│  │                     │  │  ├─ access logs → S3 │  │
│  └─────────────────────┘  │  └─ creds ← Secrets Mgr │
│                           └──────────────────────┘  │
│                                                     │
│  S3 Bucket (squid-logs)    Secrets Manager (creds)  │
└─────────────────────────────────────────────────────┘
```

The browser's security group only allows egress to Squid on port 3128 — there is no NAT Gateway, so the proxy is the sole path to the internet.

## Quick Deploy

To create the stack using CloudFormation, use following script:

[![Launch Stack](https://s3.amazonaws.com/cloudformation-examples/cloudformation-launch-stack.png)](agentcore-browser-proxy.yaml)

### Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| VpcCidr | `10.0.0.0/16` | CIDR block for the VPC |
| AvailabilityZone | — | AZ for all subnets |
| BrowserName | `proxy_browser` | AgentCore Browser name |
| SquidInstanceType | `t3.micro` | EC2 instance type for Squid |

Proxy credentials (username + random password) are auto-generated in Secrets Manager.

## What Gets Deployed

| Resource | Purpose |
|----------|---------|
| VPC + 2 subnets | Network isolation |
| EC2 (Squid) | Forward proxy with basic auth |
| Secrets Manager secret | Proxy credentials (auto-generated) |
| S3 bucket | Squid access logs (90-day lifecycle) |
| AgentCore Browser | VPC mode, egress locked to proxy |
| IAM roles | Least-privilege for browser + EC2 |

## Verify the Proxy

After deployment, get the stack outputs:

```bash
aws cloudformation describe-stacks \
  --stack-name agentcore-browser-proxy \
  --query 'Stacks[0].Outputs' --output table
```

### Option A: Notebook

```bash
pip install -r requirements.txt
```

Load `verify_proxy.ipynb` in Kiro IDE or your favorite IDE.

### Option B: Script

```bash
pip install -r requirements.txt
python verify_proxy.py
```

Both will:
1. Read the Browser ID, Squid IPs, and secret ARN from CloudFormation
2. Start a browser session with `proxyConfiguration` pointing to Squid
3. Navigate to `icanhazip.com` and compare the observed IP to Squid's public IP
4. Print PASS if they match

### Proxy Configuration Structure

The `proxyConfiguration` passed to `start_browser_session()`:

```json
{
  "proxies": [{
    "externalProxy": {
      "server": "<squid-private-ip>",
      "port": 3128,
      "credentials": {
        "basicAuth": {
          "secretArn": "arn:aws:secretsmanager:..."
        }
      }
    }
  }]
}
```

You can also add `domainPatterns` to route only specific domains through the proxy, and use `bypass.domainPatterns` to skip the proxy for certain domains.

## Access Logs

Squid access logs are synced to S3 every 5 minutes:

```
s3://<stack>-squid-logs-<account>/squid-logs/YYYY/MM/DD/HH/<instance>-access.log.<n>
```

List recent logs:

```bash
BUCKET=$(aws cloudformation describe-stacks --stack-name agentcore-browser-proxy \
  --query 'Stacks[0].Outputs[?OutputKey==`LogBucketName`].OutputValue' --output text)
aws s3 ls "s3://$BUCKET/squid-logs/" --recursive
```

## Files

| File | Description |
|------|-------------|
| `agentcore-browser-proxy.yaml` | CloudFormation template |
| `verify_proxy.py` | CLI verification script |
| `verify_proxy.ipynb` | Notebook version with S3 log check |
| `requirements.txt` | Python dependencies |

## Cleanup

```bash
# Empty the log bucket first (required before stack deletion)
BUCKET=$(aws cloudformation describe-stacks --stack-name agentcore-browser-proxy \
  --query 'Stacks[0].Outputs[?OutputKey==`LogBucketName`].OutputValue' --output text)
aws s3 rm "s3://$BUCKET" --recursive

aws cloudformation delete-stack --stack-name agentcore-browser-proxy
```

## Security Considerations

- Browser runs in a private subnet with no direct internet access
- All web traffic is forced through the authenticated Squid proxy
- Proxy credentials are stored in Secrets Manager (never in plaintext)
- S3 log bucket has public access blocked and server-side encryption
- Squid security group only accepts connections from the browser security group
