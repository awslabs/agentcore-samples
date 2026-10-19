"""
Cedar policy generation utilities for Automated Policy Management.

Provides:
- rbac_to_natural_language: converts a tool's RBAC manifest entry into
  a natural language policy statement suitable for NL2Cedar.
- generate_cedar_from_nl: calls the AgentCore NL2Cedar API and returns
  the raw Cedar statement string.
- generate_policies_for_all_tools: orchestrates NL -> Cedar for every
  tool in the RBAC manifest.
"""

import time
import logging
from typing import Optional

from bedrock_agentcore_starter_toolkit.operations.policy.client import PolicyClient

logger = logging.getLogger(__name__)


def rbac_to_natural_language(action_key: str, manifest_entry: dict) -> str:
    """
    Convert one RBAC manifest entry into a natural language policy statement
    that NL2Cedar can process.

    Args:
        action_key: The full action key, e.g.
                    "FinancialReportTarget___get_financial_report"
        manifest_entry: Dict from the RBAC manifest for this action.

    Returns:
        A natural language string describing the access control rule.
    """
    rbac = manifest_entry.get("rbac", {})
    function_name = manifest_entry.get("function_name", action_key)
    target_name = manifest_entry.get("target_name", "")

    # The Gateway evaluates context.input (request parameters) to enforce
    # access control. Principal role claims require custom IdP configuration
    # beyond this demo's scope, so we generate Cedar policies that enforce
    # the parameter-level constraints from the RBAC manifest. The allowed_roles
    # field is preserved in the manifest as documentation for the human reviewer.
    principal_clause = "Allow all principals"

    # Build action clause
    action_clause = f"to invoke the {function_name} tool on the {target_name} target"

    # Build condition clauses from parameter constraints only
    conditions = []
    for param_name, constraint in rbac.get("constraints", {}).items():
        c_type = constraint.get("type", "string")
        if c_type == "string" and "enum" in constraint:
            vals = " or ".join(f'"{v}"' for v in constraint["enum"])
            conditions.append(f"the {param_name} is {vals}")
        elif c_type == "integer" and "max" in constraint:
            conditions.append(f"the {param_name} does not exceed {constraint['max']}")

    # Assemble
    parts = [principal_clause, action_clause]
    if conditions:
        parts.append("when " + " and ".join(conditions))

    return " ".join(parts)


def generate_cedar_from_nl(
    nl_statement: str,
    policy_engine_id: str,
    gateway_arn: str,
    policy_client: PolicyClient,
    policy_name: Optional[str] = None,
) -> Optional[str]:
    """
    Call the AgentCore NL2Cedar API to generate a Cedar statement from
    a natural language description.

    Args:
        nl_statement: Natural language policy description.
        policy_engine_id: ID of the target policy engine.
        gateway_arn: ARN of the Gateway resource.
        policy_client: An initialised PolicyClient instance.
        policy_name: Optional name for the transient generation request.

    Returns:
        Cedar policy statement string, or None if generation failed.
    """
    name = policy_name or f"auto_gen_{int(time.time())}"
    logger.info("Calling NL2Cedar for: %s", nl_statement[:80])

    result = policy_client.generate_policy(
        policy_engine_id=policy_engine_id,
        name=name,
        resource={"arn": gateway_arn},
        content={"rawText": nl_statement},
        fetch_assets=True,
    )

    if result.get("status") != "GENERATED":
        logger.warning(
            "NL2Cedar returned status=%s for statement: %s",
            result.get("status"),
            nl_statement,
        )
        return None

    generated = result.get("generatedPolicies", [])
    if not generated:
        logger.warning("NL2Cedar returned no policies for: %s", nl_statement)
        return None

    # Return the first generated Cedar statement
    cedar_stmt = generated[0].get("definition", {}).get("cedar", {}).get("statement")
    return cedar_stmt


def generate_policies_for_all_tools(
    rbac_manifest: dict,
    policy_engine_id: str,
    gateway_arn: str,
    region: str,
) -> dict:
    """
    For every tool in the RBAC manifest, generate a natural language statement
    and convert it to Cedar via NL2Cedar.

    Args:
        rbac_manifest: The full rbac_manifest dict from config.json.
        policy_engine_id: Target policy engine ID.
        gateway_arn: Gateway ARN used as Cedar resource.
        region: AWS region.

    Returns:
        Dict mapping action_key -> {nl_statement, cedar_statement} for each tool.
        Tools that failed generation are excluded.
    """
    policy_client = PolicyClient(region_name=region)
    results = {}

    for action_key, manifest_entry in rbac_manifest.items():
        print(f"\n  Processing: {action_key}")

        # Step 1: RBAC -> NL
        nl_statement = rbac_to_natural_language(action_key, manifest_entry)
        print(f"  NL statement: {nl_statement}")

        # Step 2: NL -> Cedar
        cedar_stmt = generate_cedar_from_nl(
            nl_statement=nl_statement,
            policy_engine_id=policy_engine_id,
            gateway_arn=gateway_arn,
            policy_client=policy_client,
            policy_name=f"gen_{int(time.time())}",
        )

        if cedar_stmt:
            results[action_key] = {
                "nl_statement": nl_statement,
                "cedar_statement": cedar_stmt,
                "manifest_entry": manifest_entry,
            }
            print("  Cedar generated successfully")
        else:
            print(f"  WARNING: Cedar generation failed for {action_key}")

    return results
