# Wallet Provider Setup

Before running Tutorial 00, choose a wallet provider and run its setup script to save credentials to `.env`.

## Providers

| Provider | Script | Credentials Written to .env |
|----------|--------|------------------------------|
| Coinbase CDP | `coinbase_cdp_account_setup.py` | `COINBASE_API_KEY_ID`, `COINBASE_API_KEY_SECRET`, `COINBASE_WALLET_SECRET` |
| Stripe (Privy) | `stripe_privy_account_setup.py` | `PRIVY_APP_ID`, `PRIVY_APP_SECRET`, `PRIVY_AUTHORIZATION_ID`, `PRIVY_AUTHORIZATION_PRIVATE_KEY` |

Run only one provider setup. If you want both providers (for Tutorial 07 multi-agent), run both.

## Running

```bash
pip install -r providers/requirements.txt

# Option A: Coinbase CDP
python providers/coinbase_cdp_account_setup.py

# Option B: Stripe (Privy)
python providers/stripe_privy_account_setup.py
```

The Coinbase helper opens the exact CDP Portal pages, consumes the two downloaded
credential files without printing their contents, verifies them with Coinbase's
official CDP CLI, and writes the values needed by AgentCore to `.env`.

See the detailed instructions to be followed [here](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-create-manager.html)


## Important Note

To create a Coinbase payment connector, your account must have an active AWS Marketplace subscription to the Coinbase Wallets for AgentCore Payments listing. This is a one-time subscription per account, and the subscribing identity needs the `AWSMarketplaceManageSubscriptions` permission. With this subscription, your Coinbase wallet usage charges are consolidated into your monthly AWS bill based on Coinbase’s pricing on the Coinbase website. There are no additional charges or obligations for the subscription. The requirement applies to Coinbase whether you use Quick create or your own credentials; other providers, such as Stripe (Privy), are not affected. If the subscription is missing, `CreatePaymentConnector` fails with a `SubscriptionRequiredException` (HTTP 403) whose message includes the Marketplace listing URL to subscribe. For more information, see [Subscribe to Coinbase Wallets for AgentCore Payments in AWS Marketplace](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/payments-marketplace-subscription.html).

## Coinbase CDP Setup Summary

If you choose Coinbase, choose how to provide the credentials for the payment auth:

### Quick create (recommended)

Quick create links your Coinbase CDP account and lets AgentCore payments
provision the API key and Wallet Secret for you, so you never generate or paste
any keys. It is available from the AgentCore console and from the AgentCore CLI:

```bash
# No credential flags — you authorize through Coinbase at deploy time
agentcore add payment-connector \
  --manager MyPaymentManager \
  --name MyCoinbaseConnector \
  --provider CoinbaseCDP \
  --provision-mode QUICK_CREATE
```

On `agentcore deploy`, the connector is created in `PENDING_AUTHENTICATION` and the deploy prints an `authorizationUrl`. Open it, sign in to Coinbase, and grant access — the connector then moves to `READY` with a service-provisioned credential provider. Quick create does not support linking to an existing project with a Wallet Secret. See Step 2 of the [Tutorial 00 setup guide](../README.md) for the full command sequence.

Watch the detailed walkthrough in the [GA announcement blog](https://aws.amazon.com/blogs/machine-learning/amazon-bedrock-agentcore-payments-is-now-generally-available-enabling-agents-to-transact-safely-and-autonomously-at-scale/).

### Use an existing Coinbase CDP configuration

Coinbase does not expose a supported CLI bootstrap that can mint an existing
project's root API key and Wallet Secret. The project owner creates those
credentials in the portal; the helper automates the safe handoff from there.

1. Install the official CDP CLI: `npm install -g @coinbase/cdp-cli`
2. Run `python providers/coinbase_cdp_account_setup.py --open-portal`
3. Sign in to the CDP Portal in the browser tab that opens
4. At [Secret API Keys](https://portal.cdp.coinbase.com/api-keys/secret), select the demo project,
   choose **Create API Key**, and use these settings:
   **Opt-out of IP allowlisting** on; **View (read-only)** only; Trade, Transfer, Receive, Export,
   and Manage off; **Ed25519 (Recommended)** selected. Download the JSON key file. Do not create a
   Client API Key.
5. At
   [Wallets → Non-custodial Wallet → Security](https://portal.cdp.coinbase.com/wallets/non-custodial/security),
   choose **Generate Wallet Secret**, download the Wallet Secret file, and turn
   **Delegated signing** on. Do not create a project or account policy for this demo.
6. Return to the terminal and provide the two downloaded file paths

The project toggle enables delegated signing but does not grant per-wallet
consent. After AgentCore creates the embedded wallet, open the returned
WalletHub `redirectUrl`, sign in as the linked user, and grant signing
permission for that wallet.

![Coinbase Secret API Key settings for the demo](../images/coinbase-secret-api-key-settings.png)

Use **Opt-out of IP allowlisting**, View (read-only) only, no Export or Manage
permissions, and Ed25519.

![Coinbase Wallet Security settings for the demo](../images/coinbase-wallet-security-settings.png)

Generate the Wallet Secret, then turn **Delegated signing on**. The screenshot
shows the switch before it is enabled; no project or account policy is needed.

The Wallet Secret is shown **only once**. Store both downloads in a secure
location and delete unnecessary copies after AgentCore has ingested them. Never
paste either secret into documentation, source control, chat, or shell history.

For a non-interactive import after downloading the files:

```bash
python providers/coinbase_cdp_account_setup.py \
  --api-key-file ~/Downloads/cdp_api_key.json \
  --wallet-secret-file ~/Downloads/cdp_wallet_secret.txt
```

The embedded wallet created by AgentCore must hold enough testnet USDC to pay
the x402 endpoint. For Base Sepolia, request free testnet USDC from
[Circle's faucet](https://faucet.circle.com/). Testnet USDC has no monetary
value, so no real Coinbase balance or purchase is required.

## Stripe (Privy) Setup Summary

1. Create a Privy app at [dashboard.privy.io](https://dashboard.privy.io)
2. Enable Email + EVM wallets + SVM (Solana) wallets in app settings
3. Generate an authorization key under Wallet Infrastructure → Authorization
4. Clone and run the Privy reference frontend (`git clone https://github.com/privy-io/aws-agentcore-sdk`)
5. Run `stripe_privy_account_setup.py` and follow the prompts

The Privy reference frontend must be running at `http://localhost:3000` for the
end-user consent step in Tutorial 00 Step 7b (after the wallet is created).

## After Provider Setup

Return to `setup_agentcore_payments.py` — it reads `CREDENTIAL_PROVIDER_TYPE` from `.env`
automatically and uses the correct provider.
