"""One-time setup for AgentCore Payments resources.

Creates the payment infrastructure needed for the DeFi Payments Agent:
1. PaymentCredentialProvider (stores Coinbase CDP API keys in Identity)
2. PaymentManager (top-level payment coordination)
3. PaymentConnector (links manager to Coinbase CDP wallet)
4. PaymentInstrument (the agent's wallet)
5. PaymentSession (spending limits and expiry)

Prerequisites:
- AWS credentials configured (aws configure)
- Coinbase CDP API key and wallet secret from portal.cdp.coinbase.com
- IAM role with BedrockAgentCoreFullAccess policy

Usage:
    python scripts/setup_payments.py
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from getpass import getpass

import boto3
from dotenv import load_dotenv

load_dotenv()

REGION = os.getenv("AWS_REGION", "us-east-1")


def wait_for_ready(client, get_fn, id_key, id_value, resource_name, timeout=120):
    """Poll a resource until it reaches READY state."""
    print(f"  Waiting for {resource_name} to be ready...", end="", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = get_fn(**{id_key: id_value})
            status = resp.get("status", resp.get("state", "UNKNOWN"))
            if status == "READY":
                print(" READY")
                return resp
            if "FAILED" in status:
                print(f" FAILED: {status}")
                sys.exit(1)
        except Exception:
            pass
        print(".", end="", flush=True)
        time.sleep(5)
    print(f" TIMEOUT after {timeout}s")
    sys.exit(1)


def main():
    print("=" * 60)
    print("  AgentCore Payments Setup — Batch Payments Agent")
    print("=" * 60)
    print(f"\n  Region: {REGION}\n")

    # Collect Coinbase CDP credentials
    print("Step 0: Coinbase CDP Credentials")
    print("  Get these from https://portal.cdp.coinbase.com/\n")
    cdp_api_key = input("  CDP API Key ID: ").strip()
    cdp_api_secret = getpass("  CDP API Key Secret: ").strip()

    if not cdp_api_key or not cdp_api_secret:
        print("Error: CDP credentials are required.")
        sys.exit(1)

    # Create clients
    identity_client = boto3.client("bedrock-agentcore-identity", region_name=REGION)
    payments_client = boto3.client("bedrock-agentcore", region_name=REGION)

    # Step 1: Create credential provider
    print("\nStep 1: Creating PaymentCredentialProvider...")
    try:
        cred_resp = identity_client.create_payment_credential_provider(
            name="spraay-batch-agent-coinbase",
            providerType="COINBASE_CDP",
            credentials={
                "coinbaseCdp": {
                    "apiKeyId": cdp_api_key,
                    "apiKeySecret": cdp_api_secret,
                }
            },
        )
        cred_provider_id = cred_resp["providerIdentifier"]
        print(f"  Created: {cred_provider_id}")
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)

    # Step 2: Create payment manager
    print("\nStep 2: Creating PaymentManager...")
    try:
        manager_resp = payments_client.create_payment_manager(
            name="spraay-batch-payments",
            authorizerType="IAM",
        )
        manager_id = manager_resp["paymentManagerId"]
        manager_arn = manager_resp["paymentManagerArn"]
        print(f"  Created: {manager_id}")
        print(f"  ARN: {manager_arn}")
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)

    wait_for_ready(
        payments_client,
        payments_client.get_payment_manager,
        "paymentManagerId",
        manager_id,
        "PaymentManager",
    )

    # Step 3: Create payment connector
    print("\nStep 3: Creating PaymentConnector (Coinbase CDP)...")
    try:
        connector_resp = payments_client.create_payment_connector(
            paymentManagerId=manager_id,
            name="coinbase-cdp-connector",
            connectorType="COINBASE_CDP",
            credentialProviderIdentifier=cred_provider_id,
        )
        connector_id = connector_resp["paymentConnectorId"]
        print(f"  Created: {connector_id}")
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)

    # Step 4: Create payment instrument (agent wallet)
    print("\nStep 4: Creating PaymentInstrument (agent wallet)...")
    try:
        instrument_resp = payments_client.create_payment_instrument(
            paymentManagerId=manager_id,
            paymentConnectorId=connector_id,
            userId="batch-agent-user",
        )
        instrument_id = instrument_resp["paymentInstrumentId"]
        redirect_url = instrument_resp.get("paymentInstrumentDetails", {}).get(
            "redirectUrl", "N/A"
        )
        print(f"  Created: {instrument_id}")
        print(f"  Wallet Hub URL: {redirect_url}")
        print("  → Open this URL to fund the wallet with USDC on Base Sepolia")
        print("  → Use https://faucet.circle.com/ for testnet USDC")
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)

    # Step 5: Create payment session
    print("\nStep 5: Creating PaymentSession...")
    max_spend = os.getenv("MAX_SPEND_AMOUNT", "1.00")
    expiry = datetime.now(timezone.utc) + timedelta(hours=24)

    try:
        session_resp = payments_client.create_payment_session(
            paymentManagerId=manager_id,
            paymentInstrumentId=instrument_id,
            maxSpendAmount=max_spend,
            currency="USDC",
            expiresAt=expiry.isoformat(),
        )
        session_id = session_resp["paymentSessionId"]
        print(f"  Created: {session_id}")
        print(f"  Budget: {max_spend} USDC")
        print(f"  Expires: {expiry.isoformat()}")
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)

    # Output .env values
    print("\n" + "=" * 60)
    print("  Setup complete! Add these to your .env file:")
    print("=" * 60)
    env_values = f"""
PAYMENT_MANAGER_ARN={manager_arn}
PAYMENT_CONNECTOR_ID={connector_id}
PAYMENT_INSTRUMENT_ID={instrument_id}
PAYMENT_SESSION_ID={session_id}
PAYMENT_USER_ID=batch-agent-user
"""
    print(env_values)

    # Write to .env if it exists
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        update = input("Update .env file with these values? [y/N]: ").strip().lower()
        if update == "y":
            with open(env_path, "a") as f:
                f.write("\n# AgentCore Payments — auto-generated by setup_payments.py\n")
                f.write(env_values)
            print("  .env updated!")

    print("\nNext steps:")
    print("  1. Fund the agent wallet via the Wallet Hub URL above")
    print("  2. Run: python -m agent.main")
    print("  3. Try: 'What DeFi services are available through Spraay?'")


if __name__ == "__main__":
    main()
