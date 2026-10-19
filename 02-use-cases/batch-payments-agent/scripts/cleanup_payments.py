"""Clean up AgentCore Payments resources.

Deletes all payment resources created by setup_payments.py.

Usage:
    python scripts/cleanup_payments.py
"""

import os
import sys

import boto3
from dotenv import load_dotenv

load_dotenv()

REGION = os.getenv("AWS_REGION", "us-east-1")


def main():
    manager_arn = os.getenv("PAYMENT_MANAGER_ARN", "")
    connector_id = os.getenv("PAYMENT_CONNECTOR_ID", "")
    instrument_id = os.getenv("PAYMENT_INSTRUMENT_ID", "")
    session_id = os.getenv("PAYMENT_SESSION_ID", "")

    if not manager_arn:
        print("Error: PAYMENT_MANAGER_ARN not set in .env")
        sys.exit(1)

    # Extract manager ID from ARN
    manager_id = manager_arn.split("/")[-1]

    print("Cleaning up AgentCore Payments resources...")
    print(f"  Manager: {manager_id}")

    client = boto3.client("bedrock-agentcore", region_name=REGION)

    # Delete in reverse order: session → instrument → connector → manager
    resources = [
        ("PaymentSession", "delete_payment_session", {
            "paymentManagerId": manager_id,
            "paymentSessionId": session_id,
        }),
        ("PaymentInstrument", "delete_payment_instrument", {
            "paymentManagerId": manager_id,
            "paymentInstrumentId": instrument_id,
        }),
        ("PaymentConnector", "delete_payment_connector", {
            "paymentManagerId": manager_id,
            "paymentConnectorId": connector_id,
        }),
        ("PaymentManager", "delete_payment_manager", {
            "paymentManagerId": manager_id,
        }),
    ]

    for name, method, kwargs in resources:
        # Skip if ID is empty
        if any(not v for v in kwargs.values()):
            print(f"  Skipping {name} (ID not set)")
            continue
        try:
            getattr(client, method)(**kwargs)
            print(f"  Deleted {name}: {list(kwargs.values())[-1]}")
        except client.exceptions.ResourceNotFoundException:
            print(f"  {name} not found (already deleted)")
        except Exception as e:
            print(f"  Error deleting {name}: {e}")

    print("\nCleanup complete.")
    print("Remember to also delete:")
    print("  - AgentCore Runtime: agentcore runtime delete --agent-name batch-payments-agent")
    print("  - CloudWatch log groups")


if __name__ == "__main__":
    confirm = input("Delete all payment resources? [y/N]: ").strip().lower()
    if confirm == "y":
        main()
    else:
        print("Aborted.")
