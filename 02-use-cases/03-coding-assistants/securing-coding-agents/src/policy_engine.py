"""Policy Engine provisioning and Cedar policy deployment."""

from __future__ import annotations

import re
import time
from pathlib import Path

import boto3

from .utils import resolve_gateway_arn

_STATEMENT_RE = re.compile(r"^(?:permit|forbid)\s*\(", re.MULTILINE)
_PROPAGATION_DELAY = 15


def setup_policy_engine(
    region: str,
    gateway_client,
    gateway: dict,
    policies_dir: str,
    enforcement_mode: str,
    gateway_id: str,
) -> dict:
    """Create Policy Engine, load Cedar policies, attach to Gateway.

    Returns dict with policy_engine_id and policy_engine_arn.
    """
    from bedrock_agentcore_starter_toolkit.operations.policy.client import PolicyClient

    policy_client = PolicyClient(region_name=region)

    # Create or get Policy Engine
    engine = policy_client.create_or_get_policy_engine(
        name="coding_agent_policy_engine",
        description="Cedar policies for coding agent tool access control",
    )
    pe_id = engine["policyEngineId"]

    # Attach to Gateway
    gateway_client.update_gateway_policy_engine(
        gateway_identifier=gateway_id,
        policy_engine_arn=engine["policyEngineArn"],
        mode=enforcement_mode,
    )
    print(f"  Policy Engine attached (mode: {enforcement_mode})")
    print(f"  Waiting {_PROPAGATION_DELAY}s for propagation...")
    time.sleep(_PROPAGATION_DELAY)

    # Load and submit Cedar policies
    ac_client = boto3.client("bedrock-agentcore-control", region_name=region)
    gateway_arn = resolve_gateway_arn(region, gateway_id)

    for policy_file in sorted(Path(policies_dir).glob("*.cedar")):
        cedar_text = policy_file.read_text()
        cedar_text = cedar_text.replace("<gateway_arn>", gateway_arn)

        statements = _split_statements(cedar_text)
        for idx, stmt in enumerate(statements):
            if not stmt.strip():
                continue
            name = f"{policy_file.stem}_{idx}" if len(statements) > 1 else policy_file.stem
            name = name.replace("-", "_")
            _create_policy(ac_client, pe_id, name, stmt, policy_file.name)

    print(f"  Waiting {_PROPAGATION_DELAY}s for policy propagation...")
    time.sleep(_PROPAGATION_DELAY)

    return {
        "policy_engine_id": pe_id,
        "policy_engine_arn": engine["policyEngineArn"],
    }


def _split_statements(text: str) -> list[str]:
    """Split a Cedar file into individual statements."""
    matches = list(_STATEMENT_RE.finditer(text))
    if len(matches) <= 1:
        return [text.strip()]
    statements = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        statements.append(text[start:end].strip())
    return [s for s in statements if s]


def _create_policy(ac_client, pe_id: str, name: str, statement: str, source: str) -> None:
    """Create a single Cedar policy with retry logic."""
    print(f"  Submitting: {name}")
    try:
        resp = ac_client.create_policy(
            policyEngineId=pe_id,
            name=name,
            description=f"From {source}",
            definition={"cedar": {"statement": statement}},
            validationMode="IGNORE_ALL_FINDINGS",
        )
        policy_id = resp["policyId"]

        # Poll for ACTIVE
        for _ in range(20):
            time.sleep(2)
            status = ac_client.get_policy(policyEngineId=pe_id, policyId=policy_id)
            if status.get("status") == "ACTIVE":
                print(f"    ✓ {name} ACTIVE")
                return
            if status.get("status") == "CREATE_FAILED":
                reasons = status.get("statusReasons", [])
                print(f"    ✗ {name} FAILED: {reasons}")
                return
        print(f"    ⚠ {name} timeout")
    except Exception as e:
        if "ConflictException" in type(e).__name__:
            print(f"    ✓ {name} already exists")
        else:
            raise
