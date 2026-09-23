# Set Up AgentCore payments

> **Cost notice:** This tutorial creates IAM roles, CloudWatch log groups, and AgentCore payment resources. These may incur AWS charges. Run the cleanup cell and delete IAM roles and log groups when finished.

## Overview

This tutorial walks you through the complete setup of Amazon Bedrock AgentCore payments using the AWS SDK (boto3). You'll create IAM roles, configure wallet credentials, and provision the payment stack — everything needed before building payment-enabled agents.

AgentCore payments is wallet-provider agnostic. This tutorial covers both Coinbase CDP and Stripe (Privy) providers.

### Resource hierarchy

One PaymentManager per application. Connectors and instruments are child resources:

```
PaymentManager (1 per app — holds auth config + service role)
  ├── Connector: CoinbaseCDP (links to credential provider)
  │    └── Instrument (embedded wallet per user per network)
  ├── Connector: StripePrivy (links to credential provider)
  │    └── Instrument (embedded wallet per user per network)
  └── Session (budget + expiry, works with any instrument)
```

You don't need separate managers per wallet provider. One manager, multiple connectors. The session budget applies regardless of which instrument the agent uses.

### Tutorial Details

| Information         | Details                                                    |
|:--------------------|:-----------------------------------------------------------|
| Tutorial type       | Task-based                                                 |
| Agent type          | N/A (setup only)                                           |
| Agentic Framework   | N/A                                                        |
| LLM model           | N/A                                                        |
| Tutorial components | IAM roles, Payment Manager, Connector, Instrument, Session |
| Tutorial vertical   | Cross-vertical                                             |
| Example complexity  | Easy                                                       |
| SDK used            | boto3 (AWS SDK)                                            |

### Tutorial Key Features

* IAM role separation (4 roles: ControlPlane, Management, ProcessPayment, ResourceRetrieval)
* Control Plane setup: Credential Provider → Payment Manager → Payment Connector
* Data Plane setup: Payment Instrument (wallet) → Payment Session (budget)
* Support for both Coinbase CDP and Stripe (Privy) wallet providers
* Wallet funding instructions (testnet USDC)
* Complete cleanup

## Prerequisites

* Python 3.10+
* AWS credentials configured (`aws sts get-caller-identity` to verify)
* AWS account allowlisted for AgentCore payments preview
* For Coinbase: CDP API keys from https://portal.cdp.coinbase.com/
* For Stripe (Privy): Developer account from https://dashboard.privy.io/

## Manual Steps (actions outside the notebook)

Most of this tutorial is automated (run cells top to bottom). Three steps require action outside the notebook:

| When | What | Where | Time |
|------|------|-------|------|
| **Before running** | Get wallet provider credentials | Run `providers/coinbase_cdp_account_setup.ipynb` or `providers/stripe_privy_account_setup.ipynb` | ~15 min |
| **Step 7b** | Fund wallet — step 1: open faucet | Go to [faucet.circle.com](https://faucet.circle.com/) | ~2 min |
| **Step 7b** | Fund wallet — step 2: paste address | Paste your wallet address into the faucet form | |
| **Step 7b** | Fund wallet — step 3: request USDC | Request 10 USDC and wait for confirmation | |
| **Step 7b** | Delegate signing — Coinbase step 1 | Open the CDP Portal | ~5 min |
| **Step 7b** | Delegate signing — Coinbase step 2 | Navigate to Wallets → Embedded Wallet → Policies | |
| **Step 7b** | Delegate signing — Coinbase step 3 | Enable Delegated Signing | |
| **Step 7b** | Delegate signing — Privy step 1 | Open the Privy reference frontend at localhost:3000 | |
| **Step 7b** | Delegate signing — Privy step 2 | Log in with the end-user email | |
| **Step 7b** | Delegate signing — Privy step 3 | Choose **Connect agent**, then **Give access** | |

Without the funding and delegation steps, `ProcessPayment` will fail in Tutorial 01. The notebook prints a clear ✋ ACTION callout when you reach Step 7b.

## Verification

After completing the notebook, verify the setup succeeded:

1. Confirm `.env` contains `PAYMENT_MANAGER_ARN`, `INSTRUMENT_ID`, and `SESSION_ID`.
2. Run `aws sts get-caller-identity` to verify AWS credentials are active.
3. Confirm the wallet has testnet USDC by checking the instrument balance output in Step 7.

## Cleanup

> **Warning:** Cleanup is irreversible and permanently deletes all payment resources (Manager, Connectors, Instruments) and associated transaction history. Confirm you have completed all downstream tutorials before running cleanup.

When done with all tutorials, clean up resources to avoid charges:

1. Run the cleanup cell at the bottom of `setup_agentcore_payments.ipynb` to delete the Payment Manager and all child resources.
2. Delete the four IAM roles from the IAM console if no longer needed.
3. Delete CloudWatch log groups: `/aws/vendedlogs/bedrock-agentcore/<manager-id>`.

Payment sessions expire automatically after their configured `expiryTimeInMinutes`.

## Conclusion

This tutorial sets up the complete AgentCore payments infrastructure including IAM roles, wallet credentials, and the payment stack. All downstream tutorials (01–07) depend on these resources.
