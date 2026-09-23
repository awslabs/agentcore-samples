# AgentCore Browser — Chrome Enterprise Policies and Custom Root CAs

| Information         | Details                                                                         |
|:--------------------|:--------------------------------------------------------------------------------|
| Tutorial type       | Feature demonstration (2-part)                                                  |
| Agent type          | Direct SDK (Playwright) + optional Strands agent                                |
| Agentic Framework   | Playwright (CDP), optionally Strands Agents                                     |
| LLM model           | None required (Part 1); Anthropic Claude optional                               |
| Tutorial components | AgentCore Browser, Chrome policies, Code Interpreter, Secrets Manager          |
| Example complexity  | Advanced                                                                        |

## Overview

Two complementary browser security features demonstrated together:

**Part 1 — Chrome Enterprise Policies**: Restrict where an AI agent can browse at the *browser
level* — independent of agent prompts or LLM reasoning. Managed policies are cryptographically
enforced by Chrome itself; no prompt injection or jailbreak can bypass them.

**Part 2 — Custom Root CA Certificates**: Enable agents to connect to services that use
non-public certificate authorities (internal portals, corporate proxies).

## Architecture

### Part 1

```
S3 policy JSON (URLBlocklist=*, URLAllowlist=[docs.aws.amazon.com])
       │
       ▼
create_browser(enterprise_policies=[{type: "MANAGED", location: {s3: ...}}])
       │
       ▼
Browser session (Chrome policy active)
  ├─ docs.aws.amazon.com → page loads (allowed)
  └─ wikipedia.org       → ERR_BLOCKED_BY_ADMINISTRATOR (Chrome-level block)
```

### Part 2

```
Secrets Manager secret (BadSSL root CA PEM)
       │
       ▼
create_code_interpreter(certificates=[Certificate.from_secret_arn(secret_arn)])
       │
       ├─ Without cert: SSLCertVerificationError (untrusted CA)
       └─ With cert:    HTTP 200 (CA trusted)
```

## Key Concepts

### Managed vs Recommended policies

| Level | `type` value | Applied at | Can override? |
|:------|:------------|:-----------|:--------------|
| Managed | `"MANAGED"` | `create_browser()` | No — enforced by Chrome |
| Recommended | `"RECOMMENDED"` | `start()` / session level | Yes (overridden by Managed) |

### Root CA pattern

```python
from bedrock_agentcore.tools import Certificate, CodeInterpreter

ci = CodeInterpreter(region)
ci.create_code_interpreter(
    name="my_interpreter",
    execution_role_arn=EXECUTION_ROLE_ARN,
    certificates=[Certificate.from_secret_arn(secret_arn)],
)
```

The same `certificates` parameter works on `BrowserClient.create_browser()` for adding
root CAs to browser sessions.

### Important: DeveloperToolsAvailability

Do NOT set `"DeveloperToolsAvailability": 2` in policies. This disables CDP at the Chrome
level and silently breaks all Playwright automation — the WebSocket connects but Chrome
rejects CDP commands, causing timeouts. Use `0` (allowed) or `1` (extensions only).

## Reviewing the Session Recording (Part 1)

Because session recording is enabled on the custom browser, you can replay the session to observe
policy enforcement in action:

1. Open the [Amazon Bedrock AgentCore console](https://console.aws.amazon.com/bedrock-agentcore/home#)
2. In the navigation pane, choose **Built-in tools**
3. Select your browser tool (**docs_research_browser**)
4. In the **Browser sessions** section, find the completed session with **Terminated** status
5. Choose **View Recording**

The replay shows the allowed URL loading successfully and the blocked URL returning
`ERR_BLOCKED_BY_ADMINISTRATOR` — confirming Chrome-level enforcement, not just agent logic.

## Optional: Run a Strands Agent with the Restricted Browser (Part 1b)

The script includes an optional step that wires a [Strands](https://strandsagents.com/) agent to
the policy-restricted browser. The agent will succeed navigating to `docs.aws.amazon.com` and will
observe that `wikipedia.org` is blocked — demonstrating that policy enforcement is model-agnostic:
no prompt injection or jailbreak can bypass a managed Chrome policy.

```python
from strands import Agent
from strands_tools.browser import AgentCoreBrowser

browser_tool = AgentCoreBrowser(region=REGION, identifier=browser_id)
agent = Agent(tools=[browser_tool.browser], system_prompt="Research AWS docs...")
response = agent("Summarize AgentCore Browser capabilities from docs.aws.amazon.com.")
```

## Sample Scenarios

**Scenario**: Lock a data-entry agent to only access a corporate HR portal.
**Config**: `URLBlocklist: ["*"], URLAllowlist: ["hr.example.com"]`

**Scenario**: Prevent agents from saving passwords or downloading files.
**Config**: `PasswordManagerEnabled: false, DownloadRestrictions: 3`

**Scenario**: Enable agents to access internal microservices with private PKI.
**Config**: Store your org's root CA in Secrets Manager; pass `Certificate.from_secret_arn(...)`

## Applying Root CAs to Your Organization (Part 2)

The `badssl.com` demo mirrors two real-world private-CA scenarios:

| Scenario | What to store in Secrets Manager | Configuration |
|:---------|:---------------------------------|:--------------|
| Internal corporate services (HR portal, Jira, Artifactory) | Your organization's root CA certificate | Reference secret ARN in `certificates` on `create_browser()` or `create_code_interpreter()` |
| SSL-intercepting corporate proxies (Zscaler, Palo Alto Networks) | Your proxy's root CA certificate | Reference secret ARN in `certificates` and set `proxyConfiguration` |

You can combine root CA certificates with Chrome enterprise policies in a single `create_browser()`
call — pass both `enterprise_policies` and `certificates` together.

## Running the Script

```bash
pip install -r ../requirements.txt
playwright install chromium

# Full demo (Part 1 + Part 2)
python chrome_policies.py --region us-west-2

# Part 1 only (skip root CA)
python chrome_policies.py --region us-west-2 --skip-root-ca

# Keep resources after demo for console inspection
python chrome_policies.py --region us-west-2 --skip-cleanup
```

## Troubleshooting

### Browser stuck in CREATE_IN_PROGRESS
**Issue**: IAM role propagation delay or S3 policy file not yet accessible.
**Solution**: The script waits 10 seconds after role creation. If the browser still fails, check IAM permissions include `s3:GetObject` on the policy bucket.

### Playwright times out connecting to the browser
**Issue**: Browser session may not be READY yet.
**Solution**: The script polls session status before connecting. If it still fails, check the CloudWatch log group for the browser.

### Code Interpreter returns SSL error even with root CA
**Issue**: The custom interpreter may take a moment to reach READY; the old CA-free session was used.
**Solution**: Verify the interpreter ID in the script output matches the one with `certificates`. The script polls for READY before invoking.

## Clean Up

```bash
# Automatic (default):
python chrome_policies.py --region us-west-2

# Manual if --skip-cleanup was used:
aws bedrock-agentcore-control delete-browser --browser-id <id>
aws bedrock-agentcore-control delete-code-interpreter --code-interpreter-id <id>
aws secretsmanager delete-secret --secret-id demo-badssl-untrusted-root-ca --force-delete-without-recovery
aws iam delete-role-policy --role-name ac-browser-policy-execution-role --policy-name ac_browser_s3_policy
aws iam delete-role --role-name ac-browser-policy-execution-role
```

## Files

| File | Description |
|:-----|:------------|
| `chrome_policies.py` | Main demo — Part 1 (Chrome policies) + Part 2 (root CA) |

## Further Reading

- [AgentCore Browser documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/browser-tool.html)
- [Chrome Enterprise policy list](https://chromeenterprise.google/policies/)
- [AWS Secrets Manager documentation](https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html)
- [Strands Agents — Model Providers](https://strandsagents.com/latest/user-guide/concepts/model-providers/)
