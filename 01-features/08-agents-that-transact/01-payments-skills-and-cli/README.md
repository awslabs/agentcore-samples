# AgentCore Payments via the `aws-agents` Plugin

Add AgentCore x402 payment capability to any AI agent using the
[`aws-agents`](https://github.com/aws/agent-toolkit-for-aws/tree/main/plugins/aws-agents)
plugin. For the two coding-assistant paths below, point your coding assistant
at this folder, give it a prompt, and the plugin's payments skill handles
provisioning, wiring, and verification end-to-end. The third path skips the
coding assistant entirely -- OpenClaw installs the plugin and reads its config
directly.

**Preview** -- AgentCore payments is currently in preview. Features and APIs may change.

**Testnet only.** This guide uses Base Sepolia with free USDC from [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no real-world value.

## How it works

```
Agent calls a paid URL
  -> 402 Payment Required            (server returns the price)
  -> AgentCore settles USDC          (within a spending limit you set)
  -> request retried -> 200 OK       (agent receives the content)
```

The skill drives the entire setup: CLI commands, SDK scripts, framework wiring
(Strands native plugin, LangGraph middleware, or any framework via a generic tool),
and debugging. You only intervene for credentials and wallet funding.

## Prerequisites

These can be completed by your coding agent (Kiro, Claude Code, Cursor) or
manually in your terminal. If your coding agent has unrestricted terminal and
network access, you can ask it to handle these installs for you. If using a
sandboxed environment with restricted network access (e.g. Codex with default
permissions), complete these in a normal terminal first.

### AWS and system tools (one-time, global)

| Requirement | Notes |
|-------------|-------|
| AWS account with AgentCore payments preview access | `aws sts get-caller-identity` must succeed |
| Supported region | See [AgentCore supported regions](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html) (payments row) |
| Node.js 20+ | `node --version` |
| AgentCore CLI >= 0.20 | `npm install -g @aws/agentcore@0.30.0` (npm, **not** pip) |
| Python 3.10+ | `python3 --version` (macOS/Linux) or `python --version` (Windows) |

**Verify the CLI.** `agentcore --version` must print a version (e.g. `0.22.0`).
If it prints a `Usage: agentcore [OPTIONS] COMMAND ...` help screen, the older
Python toolkit is shadowing the npm CLI. Fix your `PATH`.

### Coding agent plugin (one-time, per tool)

Your coding assistant will install the `aws-agents` plugin automatically on first
use if it has network access to GitHub. No manual steps needed in most cases.

If the coding assistant cannot install the plugin (network restricted, timeout,
or unsupported), install it manually from a normal terminal:

| Tool | Manual install command |
|------|----------------------|
| **Claude Code** | `/plugin install aws-agents@claude-plugins-official` then `/reload-plugins` |
| **Codex** | `codex plugin marketplace add aws/agent-toolkit-for-aws` then install `aws-agents` |
| **Cursor** | Import `aws/agent-toolkit-for-aws` as a team marketplace, install `aws-agents` |
| **Kiro** | `git clone https://github.com/aws/agent-toolkit-for-aws.git` (no plugin support -- uses local clone) |

Start a fresh coding agent session after installing.

### Python dependencies (per project, in your agent's venv)

Install in the same Python environment where your agent runs:

```bash
pip install bedrock-agentcore httpx
```

### Payment provider credentials (have ready)

Obtain credentials from one provider before starting. The assistant will pause and
ask you to enter them via the CLI wizard.

- **Coinbase CDP** ([portal.cdp.coinbase.com](https://portal.cdp.coinbase.com/)):
  API Key ID, API Key Secret, Wallet Secret. Enable **Delegated signing** under
  Project -> Wallet -> Embedded Wallets -> Policies.
- **Stripe Privy** ([dashboard.privy.io](https://dashboard.privy.io/)): App ID, App
  Secret, P-256 Authorization key pair (strip the `wallet-auth:` prefix), and
  Authorization ID.

## Choose your path

| Starting point | Folder | Minimal prompt |
|----------------|--------|----------------|
| Existing agent | [`add-to-existing-agent/`](add-to-existing-agent/) | *"Use this folder to add AgentCore payments to my existing agent."* |
| New agent | [`build-new-agent-that-can-transact/`](build-new-agent-that-can-transact/) | *"Use this folder to create a new AgentCore payments agent."* |
| Conversational agent, no coding assistant | [`converse-with-openclaw-agent/`](converse-with-openclaw-agent/) | Install the plugin and configure `openclaw.json` directly. |

For the first two paths, point your coding assistant at the chosen folder. It
reads [`AGENTS.md`](AGENTS.md) and drives the plugin's skill from there. The
third path has no coding-assistant handoff -- see that folder's own README.

## What you will do manually

The assistant handles everything except two steps that involve your secrets or browser:

1. **Provider credentials.** You run `agentcore add payment-connector` (no flags ->
   interactive wizard). Secrets stay in your terminal, never in chat.
2. **Delegation and funding.** You visit a URL to grant signing permission and fund
   the wallet with testnet USDC at [faucet.circle.com](https://faucet.circle.com/).

## Verify

After setup, test with:

```
Fetch https://sandbox.node4all.com/v1/x402-test and tell me what you find.
```

Success = `402` -> payment -> `200 OK` (~$0.002 testnet USDC).

## Cleanup

```bash
agentcore remove payment-connector --manager <ManagerName> --name <ConnectorName> -y
agentcore remove payment-manager --name <ManagerName> -y
agentcore deploy -y
```

## Resources

- Plugin: [`aws-agents`](https://github.com/aws/agent-toolkit-for-aws/tree/main/plugins/aws-agents)
- Payments skill for the coding-assistant paths (source of truth): [`references/payments.md`](https://github.com/aws/agent-toolkit-for-aws/blob/main/plugins/aws-agents/skills/agents-build/references/payments.md)
- Payments skill for the OpenClaw path (source of truth): [`agents-pay`](https://github.com/aws/agent-toolkit-for-aws/tree/main/plugins/aws-agents/skills/agents-pay) (SKILL.md + references/)
- [AgentCore payments documentation](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments.html)
