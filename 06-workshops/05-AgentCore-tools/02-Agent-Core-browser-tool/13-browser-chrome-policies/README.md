# AgentCore Browser with Chrome Enterprise Policies and Custom Root CAs

This example demonstrates how to use [Chrome enterprise policies](https://chromeenterprise.google/policies/) and custom root CA certificates with Amazon Bedrock AgentCore Browser and Code Interpreter.

## Overview

Chrome enterprise policies let you:
- **Restrict agent navigation**: Define URL allow lists and block lists that limit where agents can browse
- **Disable risky features**: Turn off the password manager, block downloads, disable DevTools
- **Enforce compliance**: Apply managed policies at the browser level that cannot be overridden by sessions

Custom root CA certificates let you:
- **Connect to internal services**: Trust certificates signed by your organization's private CA (Jira, Artifactory, HR portals)
- **Work with corporate proxies**: Trust SSL-intercepting proxy root CAs (Zscaler, Palo Alto Networks)

## Use Cases

- Lock down a data-entry agent to only access a specific corporate portal
- Prevent agents from storing credentials or downloading files
- Enable agents to connect to internal infrastructure that uses private PKI
- Route agent traffic through SSL-intercepting corporate proxies

## Getting Started

### Prerequisites

- Python 3.10 or later
- An AWS account with Amazon Bedrock AgentCore access enabled
- AWS credentials configured (`aws sts get-caller-identity`)
- An AWS Region where Amazon Bedrock AgentCore is available

> **Note:** The notebook creates all required resources (S3 bucket, IAM role, AgentCore Browser, Code Interpreter) automatically. You do not need to pre-create any resources.

### Installation

```bash
pip install -r requirements.txt
```

### Run

```bash
jupyter notebook browser-chrome-policies.ipynb
```

Run the cells sequentially. Part 1 covers Chrome enterprise policies, Part 2 covers custom root CA certificates.

## Notebook Walkthrough

The [browser-chrome-policies.ipynb](browser-chrome-policies.ipynb) notebook demonstrates:

### Setup

- Creates an S3 bucket for policy files and session recordings
- Creates an IAM execution role with a trust policy for `bedrock-agentcore.amazonaws.com` and S3 permissions

### Part 1: Chrome Enterprise Policies

1. **Create Chrome policy** — Define a policy JSON that blocks all URLs except AWS documentation and disables risky features, then upload it to S3
2. **Create browser with managed policies** — Create a custom AgentCore Browser with the policy enforced via `enterprise_policies` with `type: "MANAGED"` and session recording enabled
3. **Demonstrate with Playwright** — Navigate to an allowed URL (page loads) and a blocked URL (Chrome displays an error page), showing browser-level enforcement independent of any agent logic
4. **Review session recording** — Replay the session in the AgentCore console to observe the policy enforcement
5. **(Optional) Run a Strands agent** — Use the restricted browser with an AI agent framework to show end-to-end agent behavior under policy restrictions

### Part 2: Custom Root CA Certificates

6. **Store root CA in Secrets Manager** — Store the [BadSSL](https://badssl.com) untrusted root CA certificate (a public test certificate) in AWS Secrets Manager
7. **Code Interpreter WITHOUT root CA** — Show the `SSLCertVerificationError` when connecting to a site with an untrusted certificate
8. **Code Interpreter WITH root CA** — Create a custom Code Interpreter with `Certificate.from_secret_arn()` and show a successful HTTP 200 connection

### Cleanup

Deletes all resources: custom browser, Code Interpreter, IAM role, Secrets Manager secret, and S3 policy file.

## Key SDK Patterns

### Managed Chrome policies (browser level)

```python
from bedrock_agentcore.tools import BrowserClient

client = BrowserClient(REGION)

response = client.create_browser(
    name="my_browser",
    execution_role_arn=EXECUTION_ROLE_ARN,
    network_configuration={"networkMode": "PUBLIC"},
    enterprise_policies=[
        {
            "location": {
                "s3": {
                    "bucket": POLICY_BUCKET,
                    "prefix": POLICY_KEY,
                }
            },
            "type": "MANAGED",
        }
    ],
)
```

### Custom root CA certificates

```python
from bedrock_agentcore.tools import CodeInterpreter, Certificate

ci_client = CodeInterpreter(REGION)

response = ci_client.create_code_interpreter(
    name="my_interpreter",
    execution_role_arn=EXECUTION_ROLE_ARN,
    network_configuration={"networkMode": "PUBLIC"},
    certificates=[
        Certificate.from_secret_arn(SECRET_ARN)
    ],
)
```

### Policy enforcement levels

| Level | Parameter | When set | Chrome directory | Can override? |
|-------|-----------|----------|------------------|---------------|
| Managed | `type: "MANAGED"` | `create_browser()` | `/etc/chromium/policies/managed/` | No |
| Recommended | `type: "RECOMMENDED"` | `start()` / `browser_session()` | `/etc/chromium/policies/recommended/` | Yes (by managed) |

## What to Observe

- **In your terminal**: Playwright output showing the allowed page title and the blocked URL error
- **In the AgentCore console**: Navigate to **Built-in tools** → your browser → active session → **View live session** to watch in real time
- **Session replay**: After the session ends, choose **View Recording** on the terminated session to see the timeline with the blocked URL attempt
- **Root CA demo**: Terminal output shows the SSL error (without cert) and successful 200 response (with cert)

## Files

| File | Description |
|------|-------------|
| `browser-chrome-policies.ipynb` | Complete tutorial notebook with setup, Chrome policies, root CA demo, and cleanup |
| `requirements.txt` | Python dependencies |
| `README.md` | This file |

## Security Considerations

- Chrome policies enforce restrictions at the browser level, independent of agent prompts
- Managed policies cannot be overridden by session-level recommended policies
- Root CA certificates should be rotated before expiration
- Use IAM least-privilege policies for S3 and Secrets Manager access
- Session recordings may contain sensitive page content — apply appropriate S3 access controls

## Additional Resources

- [Amazon Bedrock AgentCore Browser documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool.html)
- [Chrome Enterprise policy list](https://chromeenterprise.google/policies/)
- [AWS Secrets Manager documentation](https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html)
- [Strands Agents — Model Providers](https://strandsagents.com/latest/user-guide/concepts/model-providers/)
- [Amazon Bedrock AgentCore Python SDK](https://github.com/aws/bedrock-agentcore-sdk-python)
