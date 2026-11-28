#!/usr/bin/env python3
"""Pure local, fresh-stack-only configuration. Never infer account from credentials."""

import argparse
import json
import re
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def configure_fresh(root: Path, account: str, region: str, suffix: str) -> dict:
    if not re.fullmatch(r"\d{12}", account):
        raise ValueError("account must contain exactly 12 digits")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-\d+", region):
        raise ValueError("invalid region")
    if region.startswith(("cn-", "us-gov-", "us-iso", "eu-isoe-")):
        raise ValueError("This sample uses commercial-partition Cognito/Identity endpoints only")
    if not re.fullmatch(r"[a-z0-9]{8,12}", suffix):
        raise ValueError("suffix must be 8-12 lowercase letters/digits")
    # Refuse ALL previous target, auth and deployment state, even if apparently empty.
    for relative in (
        "agentcore/aws-targets.json",
        "agentcore/.cli",
        ".cognito-state.json",
        ".claims-v1.json",
        ".env",
        "agentcore/.env.local",
    ):
        if (root / relative).exists():
            raise ValueError(f"Refusing existing state: {relative}. Use a fresh checkout; do not erase old state.")
    spec_path = root / "agentcore/agentcore.json"
    spec = json.loads(spec_path.read_text())
    if spec.get("version") != 2 or spec.get("name") != "ClaimsAgent":
        raise ValueError("Expected the unconfigured v1 ClaimsAgent sample")
    project = f"Claims{suffix}"
    state = {
        "version": 1,
        "owner": str(uuid.uuid4()),
        "project": project,
        "target": "dev",
        "account": account,
        "region": region,
        "notifications": False,
        "provider_name": f"{project}_dev_cognito-gateway-m2m",
        "pool_name": f"{project}-dev-UserPool",
        "domain": f"claims-{suffix}-{account}",
        "phase": "configured",
    }
    # Reserve identity first: partial local configuration also refuses a retry.
    with (root / ".claims-v1.json").open("x") as stream:
        json.dump(state, stream, indent=2)
    with (root / "agentcore/aws-targets.json").open("x") as stream:
        json.dump([{"name": "dev", "account": account, "region": region}], stream, indent=2)
    spec["name"] = project
    spec_path.write_text(json.dumps(spec, indent=2) + "\n")
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--suffix", required=True)
    args = parser.parse_args()
    configure_fresh(PROJECT_ROOT, args.account, args.region, args.suffix)


if __name__ == "__main__":
    main()
