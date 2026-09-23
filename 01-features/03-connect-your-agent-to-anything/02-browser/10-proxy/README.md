# AgentCore Browser — Squid Proxy Routing

| Information         | Details                                                                   |
|:--------------------|:--------------------------------------------------------------------------|
| Tutorial type       | Feature demonstration                                                     |
| Agent type          | Direct SDK (Playwright) — no LLM agent                                    |
| Agentic Framework   | Playwright (CDP)                                                          |
| LLM model           | None                                                                      |
| Tutorial components | AgentCore Browser, `proxyConfiguration`, Squid EC2, Secrets Manager, S3  |
| Example complexity  | Intermediate                                                              |

## Overview

This sample routes all AgentCore Browser web traffic through an authenticated **Squid forward
proxy** running on EC2. Every URL the browser visits appears in Squid's access log, which is
synced to S3 every 5 minutes — giving you a full, tamper-evident audit trail.

Use this pattern when:
- You need a centralised egress point for compliance / DLP (data loss prevention)
- You want to capture an audit log of every URL an AI agent visits
- You need to restrict the browser to a specific set of approved hosts

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
│  │                     │  │  └─ access logs → S3 │  │
│  └─────────────────────┘  └──────────────────────┘  │
│                                                     │
│  S3 Bucket (squid-logs)    Secrets Manager (creds)  │
└─────────────────────────────────────────────────────┘
```

## Key Concepts

- **`proxyConfiguration`** — passed to `start_browser_session()` to route traffic through the proxy
- **`basicAuth.secretArn`** — references a Secrets Manager secret for proxy credentials; credentials never appear in plaintext
- **Browser egress locked to Squid** — the browser's security group allows only port 3128 outbound; the proxy is the sole internet path
- **S3 audit logs** — Squid syncs access logs every 5 minutes via cron

## Proxy Configuration Structure

```python
proxy_config = {
    "proxies": [{
        "externalProxy": {
            "server": "<squid-private-ip>",
            "port": 3128,
            "credentials": {
                "basicAuth": {"secretArn": "<secret-arn>"}
            }
        }
    }]
}
response = browser_client.start_browser_session(
    browserIdentifier=BROWSER_ID,
    proxyConfiguration=proxy_config,
)
```

## Deployment

```bash
# Deploy the Squid proxy + VPC Browser stack
aws cloudformation deploy \
  --template-file agentcore-browser-proxy.yaml \
  --stack-name agentcore-browser-proxy \
  --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM

# Get stack outputs
aws cloudformation describe-stacks \
  --stack-name agentcore-browser-proxy \
  --query 'Stacks[0].Outputs' --output table
```

## Running the Verification Script

```bash
pip install -r requirements.txt
playwright install chromium

# The script reads Browser ID, Squid IPs, and secret ARN from CloudFormation outputs
python verify_proxy.py
```

The script:
1. Reads the Browser ID, Squid private/public IPs, and Secrets Manager ARN from CloudFormation
2. Starts a browser session with `proxyConfiguration` pointing to Squid
3. Navigates to `icanhazip.com` and compares the observed IP to Squid's public IP
4. Prints **PASS** if they match (traffic routed through proxy)

## Troubleshooting

### IP mismatch (FAIL)
**Issue**: Browser security group may still allow direct egress besides port 3128.
**Solution**: Verify the browser security group has only one outbound rule: TCP 3128 to the Squid instance's security group.

### Connection timeout
**Issue**: Squid may not be running or the EC2 instance is still initialising.
**Solution**: SSH to the Squid EC2 instance and run `systemctl status squid`. Check `/var/log/user-data.log` for setup errors.

### Auth errors (407 Proxy Authentication Required)
**Issue**: Secrets Manager secret doesn't match the Squid htpasswd file.
**Solution**: Check `/var/log/squid/access.log` on the instance for the exact error. Rotate credentials via Secrets Manager and restart Squid.

### No S3 logs appearing
**Issue**: S3 audit logs are empty even after the browser has been used.
**Solution**: Squid syncs access logs every 5 minutes via cron. Wait at least 5 minutes and refresh. If logs are still missing, SSH to the Squid EC2 instance and check `/var/log/user-data.log` for setup errors in the cron configuration.

## Access Logs

Squid access logs sync to S3 every 5 minutes:

```bash
BUCKET=$(aws cloudformation describe-stacks \
  --stack-name agentcore-browser-proxy \
  --query 'Stacks[0].Outputs[?OutputKey==`LogBucketName`].OutputValue' \
  --output text)
aws s3 ls "s3://$BUCKET/squid-logs/" --recursive
```

## Clean Up

```bash
# Empty the log bucket first (required before stack deletion)
BUCKET=$(aws cloudformation describe-stacks --stack-name agentcore-browser-proxy \
  --query 'Stacks[0].Outputs[?OutputKey==`LogBucketName`].OutputValue' --output text)
aws s3 rm "s3://$BUCKET" --recursive

aws cloudformation delete-stack --stack-name agentcore-browser-proxy
```

## Files

| File | Description |
|:-----|:------------|
| `verify_proxy.py` | Proxy verification script — starts a session, navigates to icanhazip.com, validates IP |
| `agentcore-browser-proxy.yaml` | CloudFormation template — VPC, Squid EC2, Browser, S3 logs, Secrets Manager |
| `requirements.txt` | Python dependencies |

## Further Reading

- [AgentCore Browser proxy configuration](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool.html)
- [Squid proxy documentation](http://www.squid-cache.org/Doc/)
