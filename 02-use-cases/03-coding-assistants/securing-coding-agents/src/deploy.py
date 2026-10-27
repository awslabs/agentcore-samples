"""Main orchestrator: provision Gateway, Policy Engine, run scenarios."""

from __future__ import annotations

import sys

from .gateway import setup_gateway
from .policy_engine import setup_policy_engine
from .scenarios import print_summary, run_gateway_scenarios
from .utils import bold, get_account_id, load_config, save_state


def main() -> None:
    """Deploy Gateway + Policy Engine, run Cedar policy scenarios."""
    config = load_config()
    region = config["region"]

    # Validate credentials
    print("Validating AWS credentials...")
    account_id = get_account_id(region)
    print(f"  Account: {account_id}")
    print(f"  Region:  {region}")

    # Step 1: Provision Gateway
    print(f"\n{bold('Setting up MCP Gateway...')}")
    gw = setup_gateway(region)

    state = {
        "region": region,
        "gateway_id": gw["gateway_id"],
        "gateway_url": gw["gateway_url"],
        "client_info": gw["client_info"],
        "lambda_arns": gw["lambda_arns"],
        "iam_role_arn": gw["iam_role_arn"],
    }
    save_state(state)

    # Step 2: Provision Policy Engine + Cedar policies
    print(f"\n{bold('Setting up Policy Engine...')}")
    pe_config = config["policy_engine"]
    pe = setup_policy_engine(
        region=region,
        gateway_client=gw["gateway_client"],
        gateway=gw["gateway"],
        policies_dir=pe_config["policies_dir"],
        enforcement_mode=pe_config["enforcement_mode"],
        gateway_id=gw["gateway_id"],
    )
    state["policy_engine_id"] = pe["policy_engine_id"]
    save_state(state)

    # Step 3: Run scenarios
    print(f"\n{bold('Running Cedar policy scenarios...')}")
    bearer_token = gw["gateway_client"].get_access_token_for_cognito(gw["client_info"])
    results = run_gateway_scenarios(gw["gateway_url"], bearer_token, config["scenarios"])
    passed = print_summary(results)

    # Cleanup instructions
    print(f"\n{bold('Cleanup:')}")
    print("  python -m src.cleanup")

    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
