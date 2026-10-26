# Tutorial 05 — Agent with Browser Tool Pays for Content

| Information         | Details                                                                 |
|:--------------------|:------------------------------------------------------------------------|
| Tutorial type       | Task-based                                                              |
| Agent type          | Single, payment-enabled                                                 |
| Agentic Framework   | Strands Agents                                                          |
| LLM model           | Anthropic Claude Sonnet 4.6                                             |
| Components          | AgentCore Browser (`BrowserClient`), Playwright, `PaymentManager`, x402 |
| Example complexity  | Intermediate                                                            |

> **Reads** the shared stack from [Tutorial 00](../00-setup-agentcore-payments/) — `PAYMENT_MANAGER_ARN`,
> `USER_ID`, `INSTRUMENT_ID`, `AWS_REGION`. **Does:** runs one SDK script that drives a managed AgentCore
> Browser session, creates a per-user payment session, and pays x402 paywalls inside the browser.
> Provisioning: **SDK**. → [How the pieces fit together](../README.md#cli-vs-sdk)

## Overview

In this tutorial your agent drives a managed cloud browser and pays x402 paywalls **inside the browser
session**. You build a custom Strands tool, `browse_with_payment`, that reuses the **PaymentManager** and
**payment instrument (wallet)** you provisioned in Tutorial 00. When `page.goto(url)` returns HTTP 402,
the tool reads the x402 requirements, calls `PaymentManager.generate_payment_header()` for a signed proof,
injects that header on the retry navigation, and returns the unlocked content — all without leaving the
same Playwright session, so cookies, auth tokens, and DOM context are preserved.

The resources involved: the **PaymentManager** and **payment instrument (wallet)** from Tutorial 00, a
short-lived **payment session** the script creates at runtime (`$1.00` budget, 60-minute expiry), and
an **AgentCore Browser** session for managed Chromium. A spending session is per-user, so the SDK
(`PaymentManager`) mints one here in your application code, scoped to the user you serve — the wallet
itself is the one you provisioned in Tutorial 00.

> **Billable resources.** Running the script starts an AgentCore Browser session and can settle a
> real (testnet) x402 payment. See [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/).

> **Testnet only.** Wallets use Base Sepolia (Ethereum) or Solana Devnet with free USDC from
> [faucet.circle.com](https://faucet.circle.com/). Testnet USDC has no monetary value.

> **Supported regions:** `us-east-1`, `us-west-2`, `eu-central-1`, `ap-southeast-2`. Set `AWS_REGION`
> in the shared `.env` to one of these.

## Architecture

![Architecture](images/architecture.png)

```
Strands Agent
  └── browse_with_payment tool
        │
        ├── 1. BrowserClient.start() → managed cloud Chromium
        ├── 2. Playwright connects to AgentCore Browser (WebSocket)
        ├── 3. page.goto(url) → response interceptor detects 402
        ├── 4. Extract x402 requirements from response
        ├── 5. PaymentManager.generate_payment_header() → signed proof
        ├── 6. page.route() injects proof header on the navigation request
        ├── 7. page.goto(url) retries → 200 + content
        └── 8. Return content to agent
```

### Two payment patterns compared

| Pattern | Tool | Payment handling | Best for |
|---------|------|------------------|----------|
| **Plugin (Tutorial 01)** | `http_request` | Plugin intercepts tool output, retries externally | API endpoints, MCP tools |
| **Browser (this tutorial)** | Custom `browse_with_payment` | Tool handles 402 internally, retries in the same session | Browser-rendered content, paywalls |

Use the plugin pattern for API calls. Use the browser pattern when you must keep session state
(cookies, auth tokens, DOM context) across the payment retry.

## Prerequisites

- **Tutorial 00 completed** — the shared `.env` (one directory up, at `00-getting-started/.env`) is
  populated with `PAYMENT_MANAGER_ARN`, `USER_ID`, `INSTRUMENT_ID`, and `AWS_REGION`. This tutorial reads
  them via `utils.load_tutorial_env()`.
- **Wallet funded + delegated signing granted** — the instrument must be `ACTIVE` (the script asserts
  this before starting the browser). Fund with testnet USDC and grant delegated signing in Tutorial 00
  (or Tutorial 03).
- **Python 3.10+** and AWS CLI configured (`aws sts get-caller-identity`).
- **Python deps + Playwright Chromium:**
  ```bash
  pip install -r requirements.txt
  python -m playwright install chromium
  ```

No AgentCore CLI install is required to run this tutorial — the SDK does the work here. The CLI is used
only in the optional inspect step below, to confirm the shared stack from Tutorial 00 is live.

## Walkthrough

### Step 1 — Confirm Tutorial 00 is done

The shared `.env` at `00-getting-started/.env` must already contain `PAYMENT_MANAGER_ARN`, `USER_ID`,
`INSTRUMENT_ID`, and `AWS_REGION`, and the instrument must be `ACTIVE` (funded + delegated). If you have
not done this, run [Tutorial 00](../00-setup-agentcore-payments/) first.

### Step 2 — Install dependencies and the browser binary

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

`python -m playwright install chromium` downloads the Chromium build Playwright uses to connect to the
managed AgentCore Browser session.

### Step 3 — Run the script

```bash
python browser_paywall_payments.py
```

That single command runs the whole flow. In order, the script:

1. Loads config from the shared `.env` via `load_tutorial_env()` (`PAYMENT_MANAGER_ARN`, `USER_ID`,
   `INSTRUMENT_ID`, `AWS_REGION`).
2. Verifies the instrument is `ACTIVE`, then mints a per-user payment session in-code via
   `manager.create_payment_session()` (`$1.00` budget, 60-minute expiry).
3. Builds the `browse_with_payment` tool and a Strands agent around it.
4. Asks the agent to browse the target x402 endpoint; the tool navigates, pays on 402, and retries in the
   same session.
5. Prints remaining session spend and a CloudWatch traces link.

Default target URL (a Coinbase CDP x402 discovery endpoint used as a demonstration):
`https://api.cdp.coinbase.com/platform/v2/x402/discovery/search?query=technology+trends&limit=3`

## How the tool pays inside the browser session

![Payment Flow Sequence](images/payment_flow_sequence.png)

The shared payment stack (manager + connector) comes from Tutorial 00, and the `SESSION_ID` is created
in-code via `manager.create_payment_session()` (Step 3, above). The one thing that must happen *inside*
the browser tool is signing the 402 proof for the current navigation — the agent needs the signed header
in-process to inject it on the retry. The tool does that with `PaymentManager.generate_payment_header()`
(repo import convention shown):

```python
from bedrock_agentcore.payments import PaymentManager

manager = PaymentManager(payment_manager_arn=PAYMENT_MANAGER_ARN, region_name=REGION)

# Sign the 402 for the SESSION_ID created in-code via manager.create_payment_session — returns just
# the proof header; the tool injects it and retries the navigation.
payment_header = manager.generate_payment_header(
    user_id=USER_ID,
    payment_instrument_id=INSTRUMENT_ID,
    payment_session_id=SESSION_ID,   # created in-code via manager.create_payment_session
    payment_required_request={"statusCode": 402, "headers": resp_headers, "body": body},
)
```

`generate_payment_header()` signs **just the proof header** — it does not perform the retry itself. The
tool then uses Playwright's `page.route()` to inject that header on the **navigation request only**;
sub-resources (images, CSS, analytics) never receive the payment header, so the proof is not leaked to
third-party origins. This is what makes the browser pattern well suited to paywalled, browser-rendered
content: the retry happens in the same session with all its state intact.

## What the script does

`browser_paywall_payments.py` connects Playwright to the managed AgentCore Browser over CDP
(`connect_over_cdp`), signs and retries the 402 in that same session, and always calls
`browser_client.stop()` in a `finally` block.

## Inspect / verify

The script prints remaining budget after the run. To check the same session's remaining spend
yourself, pass the session id the script printed (`Created payment session <SESSION_ID>`) to the SDK:

```python
from bedrock_agentcore.payments import PaymentManager

manager = PaymentManager(payment_manager_arn=PAYMENT_MANAGER_ARN, region_name=REGION)

session_info = manager.get_payment_session(user_id=USER_ID, payment_session_id=SESSION_ID)
print(session_info["availableLimits"]["availableSpendAmount"], "of", session_info["limits"]["maxSpendAmount"])
```

To check the wallet's on-chain balance directly, use `PaymentManager.get_payment_instrument_balance()`
with the instrument id and connector id from the shared `.env`:

```python
from bedrock_agentcore.payments import PaymentManager

manager = PaymentManager(payment_manager_arn=PAYMENT_MANAGER_ARN, region_name=REGION)

chain = "BASE_SEPOLIA" if NETWORK == "ETHEREUM" else "SOLANA_DEVNET"
balance = manager.get_payment_instrument_balance(
    payment_connector_id=PAYMENT_CONNECTOR_ID,
    payment_instrument_id=INSTRUMENT_ID,
    chain=chain,
    token="USDC",
    user_id=USER_ID,
)
print(balance["tokenBalance"]["amount"] / 1_000_000, "USDC")  # amount is micro-USDC
```

To confirm the shared payment manager/connector from Tutorial 00 are live, use the AgentCore CLI.
`agentcore status` is project-scoped, so run it from Tutorial 00's scaffolded project directory:

```bash
cd ../00-setup-agentcore-payments/PaymentSetup && agentcore status --type payment
```

Confirm the shared `.env` (`00-getting-started/.env`) still has `PAYMENT_MANAGER_ARN`, `USER_ID`,
`INSTRUMENT_ID`, and `AWS_REGION`. `load_tutorial_env()` raises `FileNotFoundError` only if the `.env`
file itself is missing; individual missing keys come back as `None`. The hard stop at startup is the
instrument-`ACTIVE` assertion — the script will not start a browser session against a non-active wallet.

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `Instrument is <status> — fund and delegate…` | Instrument not `ACTIVE` | Fund the wallet + grant delegated signing in Tutorial 00 or 03 |
| Playwright Chromium not found | Browser binary not installed | `python -m playwright install chromium` |
| `BrowserClient` connection timeout | AgentCore Browser unavailable in region | Set `AWS_REGION` to a supported region (`us-east-1`, `us-west-2`, `eu-central-1`, `ap-southeast-2`) |
| Endpoint returns 200 without payment (`Paid: False`) | Target didn't return HTTP 402 | Expected for the demo discovery endpoint; use an x402 endpoint that returns 402 to exercise the pay path |
| Payment proof rejected (402 on retry) | Wallet unfunded or delegation not active | Verify USDC balance and delegated signing (Tutorial 00 / 03) |
| `FileNotFoundError` for `.env` at startup | Tutorial 00 not run / no shared `.env` | Run Tutorial 00 first to create and populate the shared `.env` |

## Clean Up

No teardown is required for this tutorial: the AgentCore Browser session is closed by
`browser_client.stop()` on every run, and the payment session expires automatically after its 60-minute
`expiry_time_in_minutes`. Nothing here provisions new shared infrastructure.

To tear down the shared stack (manager, connector, IAM roles) and delete the instrument, run the
**Clean Up** section in [Tutorial 00](../00-setup-agentcore-payments/).

## Next steps

- **Tutorial 06** — [`../06-research-agent-with-payment-memory/`](../06-research-agent-with-payment-memory/) — add AgentCore Memory to recall past data and skip redundant paid calls.
- **Tutorial 07** — [`../07-multi-agent-payment-orchestrator/`](../07-multi-agent-payment-orchestrator/) — multiple agents, separate wallets, per-agent budgets.
