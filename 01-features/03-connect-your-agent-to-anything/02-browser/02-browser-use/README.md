# Headless Browser Automation with Browser-Use SDK

## Overview

[Browser-Use](https://github.com/browser-use/browser-use) is a popular open-source browser automation framework that wraps LLMs into agentic browser loops. This demo shows how to connect Browser-Use to an AgentCore Browser session, so the automation runs in a fully managed Chromium sandbox instead of a locally installed browser.

## Architecture

![Browser Tool Architecture](images/browser-tool.png)

A browser tool sandbox is a secure execution environment that enables AI agents to safely interact with web browsers. When a user makes a request, the LLM selects appropriate tools and translates commands. These commands are executed within a controlled sandbox environment containing a headless browser (using Playwright). The sandbox provides isolation and security by containing web interactions within a restricted space, preventing unauthorized system access. The agent receives feedback through screenshots and can perform automated tasks while maintaining system security.

## How It Works

### Authentication Header Forwarding

The AgentCore Browser session requires SigV4-signed HTTP headers on every CDP WebSocket frame. Browser-Use's `BrowserProfile` accepts custom headers which it forwards to the CDP connection:

```python
from browser_use import Agent, Browser, BrowserProfile
from browser_use.llm import ChatAnthropicBedrock
from bedrock_agentcore.tools.browser_client import BrowserClient

client = BrowserClient("us-west-2")
client.start()
ws_url, headers = client.generate_ws_headers()

browser_profile = BrowserProfile(
    headers=headers,      # AgentCore SigV4 auth headers
    timeout=150000,       # 150-second navigation timeout (ms)
)
browser_session = Browser(
    cdp_url=ws_url,
    browser_profile=browser_profile,
    keep_alive=True,
)
await browser_session.start()
```

> **Browser-Use version compatibility**: Browser-Use >= 0.12.x natively forwards `BrowserProfile.headers` to the CDP connection. For older versions, run `patch_browser_use.py` once after installation.

### The Browser-Use Agent Loop

Once the browser is connected, `Agent` handles the full LLM-driven automation loop:

```python
llm = ChatAnthropicBedrock(
    model="global.anthropic.claude-haiku-4-5-20251001-v1:0",
    aws_region="us-west-2",
)

agent = Agent(
    task="Search for a coffee maker on amazon.com and extract details of the first one",
    llm=llm,
    browser_session=browser_session,
)
await agent.run()
```

The agent:
1. Sends the task + current page screenshot to the LLM
2. LLM decides which browser action to take (click, type, scroll, navigate)
3. Agent executes the action via CDP
4. Loop repeats until the task is complete or a step limit is reached

### Using `BrowserClient` vs `browser_session()`

This demo uses `BrowserClient` (explicit lifecycle) rather than `browser_session()` (context manager) because Browser-Use's `Agent.run()` is a long-running async operation that doesn't fit cleanly inside a `with` block:

```python
# Explicit lifecycle — better for async frameworks
client = BrowserClient(region)
client.start()
try:
    ws_url, headers = client.generate_ws_headers()
    # ... async agent.run() ...
finally:
    client.stop()
```

### macOS SSL Certificates

Python 3.13 on macOS uses a framework build that doesn't include system CA certificates by default. WebSocket connections to AWS endpoints fail with `CERTIFICATE_VERIFY_FAILED`. The script auto-wires `certifi` to fix this:

```python
import os, ssl
try:
    import certifi
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    ssl._create_default_https_context = ssl.create_default_context
except ImportError:
    pass
```

If you still see SSL errors, run the CA installer bundled with your Python distribution:
```bash
/Applications/Python\ 3.13/Install\ Certificates.command
```

### The `patch_browser_use.py` Script

For Browser-Use versions older than 0.12.x, the library doesn't read `BrowserProfile.headers` when creating the CDP connection. `patch_browser_use.py` modifies `browser_use/browser/session.py` to:

1. Accept and store `headers` from `BrowserProfile`
2. Forward those headers to `CDPClient` so every WebSocket frame carries the AgentCore SigV4 signature

A backup is saved as `session.py.backup`. If you upgrade browser-use, re-run the patch script. For version 0.12.x and later, the script detects native support and skips patching:

```
This browser_use version already forwards BrowserProfile headers to CDPClient.
No patch needed.
```

## Prerequisites

```bash
pip install -r ../requirements.txt

# Apply auth header patch if needed (checks version and only patches if necessary)
python patch_browser_use.py
```

## Usage

```bash
# Default demo task
python getting_started.py

# Custom task
python getting_started.py \
  --task "Search for a laptop under $500 on amazon.com and list the top 3 results"

# Change region
python getting_started.py \
  --task "Find the latest news on AI" \
  --region us-east-1
```

## IAM Permissions

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock-agentcore:StartBrowserSession",
    "bedrock-agentcore:StopBrowserSession",
    "bedrock-agentcore:ConnectBrowserAutomationStream",
    "bedrock:InvokeModel"
  ],
  "Resource": "*"
}
```

`bedrock:InvokeModel` is required for `ChatAnthropicBedrock` to call Claude through Amazon Bedrock.

## Files

| File | Description |
|:-----|:------------|
| `getting_started.py` | Main Browser-Use demo with AgentCore backend |
| `patch_browser_use.py` | One-time patch to forward auth headers (for browser-use < 0.12.x) |
