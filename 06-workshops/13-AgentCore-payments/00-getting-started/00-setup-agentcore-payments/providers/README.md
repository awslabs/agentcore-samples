# Provider Account Setup Guides

Before running Tutorial 00, you need credentials from a supported wallet provider.
These guides walk you through creating an account and obtaining the credentials that
Tutorial 00 expects in your `.env` file.

---

## Which guide should I follow?

| If you want to use... | Follow this guide |
|:----------------------|:-----------------|
| **Coinbase Developer Platform (CDP)** | [Coinbase CDP Account Setup](coinbase_cdp_account_setup.ipynb) |
| **Stripe via Privy** | [Stripe / Privy Account Setup](stripe_privy_account_setup.ipynb) |

After completing the relevant guide, return to [Tutorial 00](../setup_agentcore_payments.ipynb).

---

## What credentials do I need?

The required credentials depend on your chosen provider:

### Coinbase CDP

| Variable | Description |
|:---------|:------------|
| `COINBASE_API_KEY_ID` | Your Coinbase CDP API key ID |
| `COINBASE_API_KEY_SECRET` | Your Coinbase CDP API key secret |
| `COINBASE_WALLET_SECRET` | Your Coinbase CDP wallet secret |

Set `CREDENTIAL_PROVIDER_TYPE=CoinbaseCDP` in your `.env`.

### Stripe / Privy

The vendor is called `StripePrivy` but configuration is 100% Privy credentials. There are no Stripe-side fields.

| Variable | Description |
|:---------|:------------|
| `PRIVY_APP_ID` | App ID from Privy dashboard |
| `PRIVY_APP_SECRET` | App secret from Privy dashboard → API keys |
| `PRIVY_AUTHORIZATION_ID` | Authorization ID of your P-256 authorization key (Wallet infrastructure → Authorization keys) |
| `PRIVY_AUTHORIZATION_PRIVATE_KEY` | P-256 private key (raw base64, strip `wallet-auth:` prefix) |

Set `CREDENTIAL_PROVIDER_TYPE=StripePrivy` in your `.env`.

> **Important:** `PRIVY_AUTHORIZATION_ID` is the ID of your P-256 authorization key, not an API key. The `authorizationPrivateKey` must have the `wallet-auth:` prefix stripped — Bedrock AgentCore validation rejects the prefixed form.

## Cleanup

Credentials stored in `.env` are only used during the tutorial. For deployed workloads, store credentials in [AWS Secrets Manager](https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html) instead of `.env` files.

> **Note:** AWS resources created using these credentials (IAM roles, Payment Manager, Connectors, Instruments) persist until explicitly deleted. Run the cleanup cell in Tutorial 00 to remove them when no longer needed.

## Conclusion

After completing the relevant provider setup guide, the required credentials are stored in the `.env` file. Return to [Tutorial 00](../setup_agentcore_payments.ipynb) to provision the AgentCore payments stack using these credentials.
