# Browser Domain Filtering with AWS Network Firewall

## Overview

By default, an AgentCore Browser session can navigate to any URL on the internet. For production agents — especially those handling sensitive data or operating in regulated environments — you need to restrict which domains the browser can reach. This demo shows how to deploy a network firewall allow/deny list and verify it works.

## How It Works

### Network Modes

AgentCore Browser supports two network configurations:

| Mode | Description | Use Case |
|:-----|:------------|:---------|
| `PUBLIC` | Direct internet access (default) | Development, unrestricted tasks |
| `VPC` | Routed through your VPC | Production, domain filtering, private network access |

To enable domain filtering, the browser must run in `VPC` network mode with traffic routed through AWS Network Firewall.

### CloudFormation Architecture

`agentcore-browser-firewall.yaml` deploys:

- **VPC** with public and private subnets in two availability zones
- **Internet gateway** and **NAT gateway** for outbound connectivity
- **AWS Network Firewall** with stateful domain filtering rules:
  - **Allow list**: `example.com`, `github.com`, `wikipedia.org` (and subdomains)
  - **Deny list**: `facebook.com`, `twitter.com`
  - **Default action**: block all other domains
- **Route tables** that route all private subnet traffic through the firewall
- **AgentCore Browser resource** configured with `networkMode: VPC` and the private subnet IDs

```bash
aws cloudformation deploy \
  --template-file agentcore-browser-firewall.yaml \
  --stack-name agentcore-browser-firewall \
  --capabilities CAPABILITY_IAM \
  --region us-west-2
```

Deployment takes 5–10 minutes (Network Firewall provisioning is the slow step).

### How the Verification Script Works

`verify_domain_filtering.py` connects to the browser using raw Playwright over CDP, then tests each domain by navigating to it and checking whether the page loads or times out:

```python
from playwright.async_api import async_playwright

async with async_playwright() as pw:
    browser = await pw.chromium.connect_over_cdp(ws_url, headers=signed_headers)
    page = await browser.new_page()

    # Test an allowed domain
    await page.goto("https://github.com", timeout=10000)   # should succeed

    # Test a blocked domain
    await page.goto("https://facebook.com", timeout=10000)  # should fail
```

SigV4-signed headers are constructed manually using `botocore` so Playwright can authenticate to the AgentCore WebSocket endpoint.

### Getting the Browser ID from CloudFormation

After deploying the stack, retrieve the browser resource ID from the stack outputs:

```bash
export BROWSER_ID=$(aws cloudformation describe-stacks \
  --stack-name agentcore-browser-firewall \
  --query 'Stacks[0].Outputs[?OutputKey==`BrowserToolCustomOutput`].OutputValue' \
  --output text)
```

### Test Results

The script tests five URLs and reports pass/fail for each:

| Domain | Category | Expected |
|:-------|:---------|:---------|
| `example.com` | Allow list | Reachable |
| `github.com` | Allow list | Reachable |
| `wikipedia.org` | Allow list | Reachable |
| `facebook.com` | Deny list | Blocked |
| `twitter.com` | Deny list | Blocked |
| `randomsite12345.com` | Unlisted (default deny) | Blocked |

A navigation timeout on blocked domains is the expected success condition — it means the firewall is correctly dropping traffic. The **unlisted** case confirms the firewall's default-deny posture: any domain not explicitly allowed is also blocked.

## Prerequisites

```bash
pip install -r ../requirements.txt
playwright install chromium
```

AWS credentials with CloudFormation and IAM permissions must be configured. The deployment creates IAM roles with `CAPABILITY_IAM`.

## Deploy the CloudFormation Stack

```bash
aws cloudformation deploy \
  --template-file agentcore-browser-firewall.yaml \
  --stack-name agentcore-browser-firewall \
  --capabilities CAPABILITY_IAM \
  --region us-west-2

# Wait for stack to complete (5-10 minutes)
aws cloudformation wait stack-create-complete \
  --stack-name agentcore-browser-firewall

# Get the browser resource ID
export BROWSER_ID=$(aws cloudformation describe-stacks \
  --stack-name agentcore-browser-firewall \
  --query 'Stacks[0].Outputs[?OutputKey==`BrowserToolCustomOutput`].OutputValue' \
  --output text)

echo "Browser ID: $BROWSER_ID"
```

## Run the Verification Script

```bash
python verify_domain_filtering.py
```

Expected output:

```
Testing domain filtering...
  ✓ ALLOWLIST  example.com    → REACHABLE
  ✓ ALLOWLIST  github.com     → REACHABLE
  ✓ ALLOWLIST  wikipedia.org  → REACHABLE
  ✓ DENYLIST   facebook.com   → BLOCKED
  ✓ DENYLIST   twitter.com    → BLOCKED
All tests passed.
```

## Clean Up

```bash
aws cloudformation delete-stack --stack-name agentcore-browser-firewall

# Wait for deletion to complete
aws cloudformation wait stack-delete-complete \
  --stack-name agentcore-browser-firewall
```

> **Note**: Network Firewall deletion can take several minutes. The stack deletion waits for it automatically.

## IAM Permissions

**Caller (for CloudFormation deployment):**

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:StartBrowserSession",
    "bedrock-agentcore:StopBrowserSession",
    "bedrock-agentcore:ConnectBrowserAutomationStream",
    "cloudformation:*",
    "iam:CreateRole",
    "iam:DeleteRole",
    "iam:PutRolePolicy",
    "iam:DeleteRolePolicy",
    "iam:PassRole",
    "ec2:*",
    "network-firewall:*"
  ],
  "Resource": "*"
}
```

## Files

| File | Description |
|:-----|:------------|
| `verify_domain_filtering.py` | Connects to the custom browser, tests allow/deny list domains, reports results |
| `agentcore-browser-firewall.yaml` | CloudFormation template: VPC + Network Firewall + custom browser resource |
