# User Onboarding and Backend Wallet Operations

> See `user_onboarding.ipynb` for the complete step-by-step tutorial.

## Overview

Tutorial 00 creates the infrastructure and gets your first wallet funded. This tutorial covers the full wallet lifecycle in depth, split into two parts:

- **Part 1 — Onboarding (per end user):** create the wallet, fund it, and delegate signing so the agent can spend on the user's behalf.
- **Part 2 — Backend operations:** balance checks, multi-network wallets, session budgets, instrument listing, and remaining-budget queries. These run from your application backend, not by the end user — included here so you see the full lifecycle in one place.

### What you learn

| Topic | Part | Details |
|-------|------|---------|
| Create an embedded wallet | 1 | `CreatePaymentInstrument` per end user |
| Crypto-to-crypto funding | 1 | Testnet faucet, direct USDC transfers |
| Fiat-to-crypto onramp | 1 | Coinbase Onramp URL, Stripe Onramp (credit card, bank, Apple Pay) |
| Delegation | 1 | Coinbase project-level signing vs Privy key quorum consent |
| Balance check | 2 | `GetPaymentInstrumentBalance` before creating a session |
| Multi-network wallets | 2 | Same user with Ethereum + Solana wallets |
| Session patterns | 2 | Different budgets for quick lookup vs deep research |
| Instrument listing | 2 | `ListPaymentInstruments` for ops and wallet selectors |
| Remaining-budget checks | 2 | `GetPaymentSession` during a task |

## Prerequisites

* Tutorial 00 completed (`.env` exists)
* Wallet funded with testnet USDC from https://faucet.circle.com/

This tutorial works with either wallet provider you configured in Tutorial 00 (Coinbase CDP or Stripe/Privy).

## Cleanup

Payment instruments persist until explicitly deleted. The three payment sessions created in this tutorial (quick lookup, research task, deep analysis) expire automatically after their configured `expiryTimeInMinutes`. To delete all payment resources (Manager, Connectors, Instruments), run the cleanup cell in Tutorial 00.

## Conclusion

This tutorial covers the complete wallet lifecycle: onboarding (create, fund, delegate), and backend operations (balance checks, multi-network wallets, session budgets). It demonstrates how to manage embedded wallets for end users and implement common backend wallet operations.
