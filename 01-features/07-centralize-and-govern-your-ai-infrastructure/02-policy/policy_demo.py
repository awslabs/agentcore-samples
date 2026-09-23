"""
AgentCore Policy Demo — NL2Cedar, Direct Cedar, and Fine-Grained ABAC.

This script demonstrates the full range of AgentCore Policy capabilities:

  Part A — NL2Cedar: Generate Cedar policies from natural language
    A1. Simple single-statement: coverage + region constraints
    A2. Multi-statement: two separate policies from one paragraph
    A3. Principal-scoped: username, group scope, and role constraints

  Part B — Direct Cedar Policies: Fine-grained attribute-based access control
    B1. Department-based ABAC (JWT claim: department_name)
    B2. Groups-based ABAC with wildcard matching (JWT claim: groups)
    B3. Principal ID-based control (JWT claim: sub == client_id)
    B4. Combined conditions (department + context.input.amount)
    B5. Pattern matching patterns (like operator)

  Part C — End-to-End Agent Demo
    C1. Agent with active Cedar policy — ALLOW scenario
    C2. Agent with active Cedar policy — DENY scenario

Prerequisites:
    python deploy.py   (creates policy_config.json)

Usage:
    python policy_demo.py [--section A|B|C]
    python policy_demo.py                   # runs all sections
"""

import argparse
import base64
import io
import json
import time
import zipfile

import boto3
import requests
from botocore.exceptions import ClientError
from bedrock_agentcore_starter_toolkit.operations.policy.client import PolicyClient

from utils.agent_with_tools import AgentSession


# ── Config ────────────────────────────────────────────────────────────────────


def load_config(path: str = "policy_config.json") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Token Helpers ─────────────────────────────────────────────────────────────


def get_bearer_token(config: dict) -> str:
    """Obtain an OAuth2 client_credentials token from Cognito."""
    ci = config["gateway"]["client_info"]
    resp = requests.post(  # nosec B113
        ci["token_endpoint"],
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": ci["client_id"],
            "client_secret": ci["client_secret"],
        },
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def decode_token(token: str) -> dict:
    """Decode a JWT payload (no signature verification — for demo/inspection only)."""
    parts = token.split(".")
    payload = parts[1]
    payload += "=" * (4 - len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def show_token_claims(config: dict) -> dict:
    """Print the custom claims present in the current JWT token."""
    token = get_bearer_token(config)
    claims = decode_token(token)
    print("  JWT claims in current token:")
    for key in [
        "department_name",
        "employee_level",
        "groups",
        "cost_center",
        "sub",
        "client_id",
    ]:
        if key in claims:
            print(f"    {key}: {claims[key]}")
    return claims


# ── Gateway Request Helper ────────────────────────────────────────────────────


def make_gateway_request(config: dict, token: str, tool_name: str, arguments: dict) -> dict:
    """Send a JSON-RPC tools/call request to the Gateway."""
    resp = requests.post(  # nosec B113
        config["gateway"]["gateway_url"],
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        },
    )
    resp.raise_for_status()
    return resp.json()


def analyze_response(result: dict) -> str:
    """Return 'ALLOWED', 'DENIED', or 'ERROR' based on the Gateway response."""
    if "error" in result:
        msg = result["error"].get("message", "").lower()
        if any(p in msg for p in ["not allowed", "denied", "forbidden", "unauthorized"]):
            return "DENIED"
        return "ERROR"
    if "result" in result:
        if result["result"].get("isError", False):
            text = ""
            for c in result["result"].get("content", []):
                text += (c.get("text", "") if isinstance(c, dict) else str(c)).lower()
            if any(p in text for p in ["not allowed", "denied", "forbidden"]):
                return "DENIED"
            return "DENIED" if result["result"].get("isError") else "ALLOWED"
        return "ALLOWED"
    return "UNKNOWN"


def assert_outcome(expected: str, actual: str, description: str) -> bool:
    ok = expected == actual
    status = "✓ PASS" if ok else "✗ FAIL"
    print(f"  {status}: {description}")
    print(f"    Expected: {expected}  |  Actual: {actual}")
    return ok


# ── Policy CRUD Helpers ───────────────────────────────────────────────────────


def create_cedar_policy(control_client, engine_id: str, name: str, statement: str, description: str = "") -> str | None:
    """Create a Cedar policy. Returns policy ID, or None on failure."""
    print(f"  Creating policy: {name}")
    print("  Cedar statement:")
    print("  " + "-" * 60)
    for line in statement.strip().splitlines():
        print(f"  {line}")
    print("  " + "-" * 60)
    try:
        resp = control_client.create_policy(
            policyEngineId=engine_id,
            name=name,
            description=description or name,
            definition={"cedar": {"statement": statement}},
        )
        policy_id = resp["policyId"]
        # Wait for ACTIVE
        for _ in range(20):
            status = control_client.get_policy(policyEngineId=engine_id, policyId=policy_id).get("status")
            if status == "ACTIVE":
                break
            if status in ("CREATE_FAILED", "UPDATE_FAILED"):
                print(f"  ✗ Policy failed: {status}")
                return None
            time.sleep(3)
        print(f"  ✓ Policy ACTIVE: {policy_id}")
        return policy_id
    except ClientError as e:
        print(f"  ✗ Error: {e}")
        return None


def delete_policy(control_client, engine_id: str, policy_id: str) -> None:
    try:
        control_client.delete_policy(policyEngineId=engine_id, policyId=policy_id)
    except ClientError:
        pass


def delete_all_policies(control_client, engine_id: str) -> None:
    """Delete all policies in the engine (clean slate between scenarios)."""
    try:
        policies = control_client.list_policies(policyEngineId=engine_id).get("policies", [])
        for p in policies:
            delete_policy(control_client, engine_id, p["policyId"])
        if policies:
            print(f"  Deleted {len(policies)} existing policy(ies)")
    except ClientError:
        pass


# ── Cognito Claims Lambda Helper ──────────────────────────────────────────────


def update_jwt_claims(config: dict, new_claims: dict) -> None:
    """
    Update the Cognito Pre-Token-Generation Lambda to inject different claims.

    This simulates different callers (e.g., finance dept vs engineering dept)
    by changing what the Lambda injects into the JWT without creating separate
    Cognito app clients.

    NOTE: After updating, wait ~5s for Lambda changes and then fetch a fresh token.
    """
    region = config["region"]
    account_id = config["account_id"]  # noqa: F841
    user_pool_id = config["gateway"]["client_info"]["user_pool_id"]  # noqa: F841
    claims_lambda_arn = config["claims_lambda_arn"]

    claims_json = json.dumps(new_claims, indent=12)
    lambda_code = f"""import json

def lambda_handler(event, context):
    print(f"Trigger: {{event.get('triggerSource', 'unknown')}}")
    event['response'] = {{
        'claimsAndScopeOverrideDetails': {{
            'accessTokenGeneration': {{
                'claimsToAddOrOverride': {claims_json}
            }},
            'idTokenGeneration': {{
                'claimsToAddOrOverride': {claims_json}
            }}
        }}
    }}
    return event
"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("lambda_function.py", lambda_code)
    buf.seek(0)

    lc = boto3.client("lambda", region_name=region)
    lc.update_function_code(FunctionName=claims_lambda_arn, ZipFile=buf.read())
    waiter = lc.get_waiter("function_updated_v2")
    waiter.wait(FunctionName=claims_lambda_arn)
    print(f"  Claims Lambda updated. New claims: {list(new_claims.keys())}")
    print("  Waiting 5s for propagation...")
    time.sleep(5)


# ── Part A: NL2Cedar ──────────────────────────────────────────────────────────


def part_a_nl2cedar(config: dict):
    """
    Part A: Generate Cedar policies from natural language using NL2Cedar.

    NL2Cedar converts plain English authorization requirements into Cedar policy
    syntax. The Gateway's tool schemas (action/resource names, parameter types)
    are provided to the generation model so it produces accurate Cedar statements.

    Findings handling:
    - INVALID findings may block generation (empty generatedPolicies list)
    - WARNING findings are non-blocking; policy creation may still succeed
    - Pass validation_mode='IGNORE_ALL_FINDINGS' to create despite findings
    """
    print("\n" + "=" * 65)
    print("PART A — NL2Cedar: Generate Cedar Policies from Natural Language")
    print("=" * 65)

    region = config["region"]
    engine_id = config["policy_engine"]["policyEngineId"]
    gateway_arn = config["gateway"]["gateway_arn"]
    control_client = boto3.client("bedrock-agentcore-control", region_name=region)

    # Use PolicyClient (starter toolkit) for the NL2Cedar generate_policy API
    policy_client = PolicyClient(region_name=region)

    # Clean up any leftover policies
    delete_all_policies(control_client, engine_id)

    # ── A1: Simple single-line statement ─────────────────────────────────────
    print("\n[A1] Simple single-line NL statement")
    print("─" * 65)
    nl_input = (
        "Allow all users to invoke the application tool when the coverage amount "
        "is under 1000000 and the applicant region is US or CA"
    )
    print(f"  Natural language: {nl_input}")

    result = policy_client.generate_policy(
        policy_engine_id=engine_id,
        name=f"nl_simple_{int(time.time())}",
        resource={"arn": gateway_arn},
        content={"rawText": nl_input},
        fetch_assets=True,
    )

    _print_and_create_nl_policies(policy_client, control_client, engine_id, result, "nl_simple")

    # ── A2: Multi-statement (generates multiple Cedar policies) ───────────────
    print("\n[A2] Multi-line statement → multiple Cedar policies")
    print("─" * 65)
    nl_multi = (
        "Allow all users to invoke the risk model tool when data governance approval is true.\n"
        "Block users from calling the application tool unless coverage amount is present."
    )
    print(f"  Natural language:\n    {nl_multi.replace(chr(10), chr(10) + '    ')}")

    delete_all_policies(control_client, engine_id)
    result = policy_client.generate_policy(
        policy_engine_id=engine_id,
        name=f"nl_multi_{int(time.time())}",
        resource={"arn": gateway_arn},
        content={"rawText": nl_multi},
        fetch_assets=True,
    )
    _print_and_create_nl_policies(policy_client, control_client, engine_id, result, "nl_multi")

    # ── A3: Principal-scoped statements ──────────────────────────────────────
    print("\n[A3] Principal-scoped statements")
    print("─" * 65)
    print("  These show how NL2Cedar handles JWT claim references.")
    print("  Tip: wrap IdP claim names in <idp_claims>['tag']</idp_claims> for precision.\n")

    principal_statements = [
        (
            'Allow principals with username "test-user" to invoke the risk model tool',
            "principal username tag",
        ),
        (
            "Forbid principals to access the approval tool unless they have the scope "
            'group:Controller <idp_claims>["scope"]</idp_claims>',
            "principal scope tag with idp_claims hint",
        ),
        (
            "Block principals from using risk model tool and approval tool unless the "
            'principal has role "senior-adjuster"',
            "multi-tool role restriction",
        ),
    ]

    for nl_input, description in principal_statements:
        print(f"  [{description}]")
        print(f"  NL: {nl_input}")
        delete_all_policies(control_client, engine_id)
        result = policy_client.generate_policy(
            policy_engine_id=engine_id,
            name=f"nl_principal_{int(time.time())}",
            resource={"arn": gateway_arn},
            content={"rawText": nl_input},
            fetch_assets=True,
        )
        _print_and_create_nl_policies(policy_client, control_client, engine_id, result, "nl_principal")
        print()

    print("\n[A Summary] Key NL2Cedar patterns demonstrated:")
    print("  • context.input.<param> <= value    — numeric constraint on tool parameters")
    print("  • context.input.<param> == 'value'  — exact match on tool parameter")
    print("  • principal.hasTag('claim')          — checks claim presence before getTag")
    print("  • principal.getTag('claim') == '...' — exact JWT claim match")
    print("  • principal.getTag('claim') like '*value*' — wildcard match on claim")
    print("  • action in [AgentCore::Action::...]  — multi-tool restriction")


def _print_and_create_nl_policies(
    policy_client, control_client, engine_id: str, result: dict, name_prefix: str
) -> list:
    """Print generated Cedar policies and create them in the engine."""
    created = []
    if result.get("status") != "GENERATED" or not result.get("generatedPolicies"):
        findings = result.get("findings", [])
        print(f"  ⚠  No policies generated. Status: {result.get('status')}")
        for f in findings:
            print(f"     Finding ({f.get('type')}): {f.get('description', '')}")
        return created

    for i, gen_policy in enumerate(result["generatedPolicies"]):
        findings = gen_policy.get("findings", [])
        cedar_stmt = gen_policy.get("definition", {}).get("cedar", {}).get("statement", "")
        if not cedar_stmt:
            print(f"  ⚠  Policy {i + 1}: no Cedar statement in generated asset")
            continue

        print(f"  Generated Cedar Policy {i + 1}:")
        print("  " + "─" * 60)
        for line in cedar_stmt.strip().splitlines():
            print(f"  {line}")
        print("  " + "─" * 60)

        # Report findings
        invalid = [f for f in findings if f.get("type") == "INVALID"]
        warnings = [f for f in findings if f.get("type") == "WARNING"]
        if invalid:
            print(f"  ⚠  INVALID findings ({len(invalid)}): {[f.get('description') for f in invalid]}")
        if warnings:
            print(f"  ⚠  WARNING findings ({len(warnings)}): {[f.get('description') for f in warnings]}")

        # Create the policy
        policy_name = f"{name_prefix}_{i}_{int(time.time()) % 10000}"
        try:
            policy = policy_client.create_or_get_policy(
                policy_engine_id=engine_id,
                name=policy_name,
                description=f"NL2Cedar generated: {name_prefix}_{i}",
                definition={"cedar": {"statement": cedar_stmt}},
            )
            print(f"  ✓ Policy created: {policy.get('policyId')}")
            created.append(policy.get("policyId"))
        except Exception as exc:
            print(f"  ⚠  Policy creation failed ({exc}). Retrying with IGNORE_ALL_FINDINGS...")
            try:
                policy = policy_client.create_or_get_policy(
                    policy_engine_id=engine_id,
                    name=policy_name,
                    description=f"NL2Cedar generated: {name_prefix}_{i}",
                    definition={"cedar": {"statement": cedar_stmt}},
                    validation_mode="IGNORE_ALL_FINDINGS",
                )
                print(f"  ✓ Policy created (IGNORE_ALL_FINDINGS): {policy.get('policyId')}")
                created.append(policy.get("policyId"))
            except Exception as exc2:
                print(f"  ✗ Could not create policy: {exc2}")
    return created


# ── Part B: Fine-Grained ABAC (Direct Cedar) ─────────────────────────────────


def part_b_fine_grained_abac(config: dict):
    """
    Part B: Direct Cedar policies for attribute-based access control (ABAC).

    JWT claims from Cognito are surfaced as Cedar principal tags:
      principal.hasTag("claim_name")           -- check claim exists
      principal.getTag("claim_name") == "val"  -- exact match
      principal.getTag("claim_name") like "*val*" -- pattern match

    The Cognito Pre-Token-Generation V3_0 Lambda injects custom claims,
    which appear as principal tags in Cedar policy evaluation.
    """
    print("\n" + "=" * 65)
    print("PART B — Fine-Grained ABAC: Direct Cedar Policies")
    print("=" * 65)
    print()
    print("  Cedar principal tag syntax:")
    print("  ┌─────────────────────────────────────────────────────────┐")
    print("  │ Pattern              │ Cedar Syntax                     │")
    print("  ├─────────────────────────────────────────────────────────┤")
    print("  │ Claim exists         │ principal.hasTag('claim_name')   │")
    print("  │ Exact match          │ principal.getTag('claim') == 'v' │")
    print("  │ Contains             │ principal.getTag('claim') like   │")
    print("  │                      │   '*value*'                      │")
    print("  │ Input constraint     │ context.input.field <= 1000      │")
    print("  └─────────────────────────────────────────────────────────┘")

    region = config["region"]
    engine_id = config["policy_engine"]["policyEngineId"]
    gateway_arn = config["gateway"]["gateway_arn"]
    client_id = config["gateway"]["client_info"]["client_id"]
    control_client = boto3.client("bedrock-agentcore-control", region_name=region)

    # ── B1: Department-Based ABAC ─────────────────────────────────────────────
    print("\n[B1] Department-Based ABAC")
    print("─" * 65)
    print("  Policy: only principals with department_name=='finance' can invoke")
    print("  the ApplicationToolTarget___create_application action.\n")

    delete_all_policies(control_client, engine_id)

    department_policy = f'''permit(
  principal,
  action == AgentCore::Action::"ApplicationToolTarget___create_application",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {{
  principal.hasTag("department_name") &&
  principal.getTag("department_name") == "finance"
}};'''

    policy_id = create_cedar_policy(
        control_client,
        engine_id,
        f"dept_finance_{int(time.time()) % 10000}",
        department_policy,
        "Allow ApplicationTool only for finance department",
    )
    if not policy_id:
        print("  ✗ Skipping B1 tests — policy creation failed")
        return

    # Test ALLOW: finance department
    print("\n  Test B1a — finance dept → EXPECTED: ALLOWED")
    update_jwt_claims(
        config,
        {
            "department_name": "finance",
            "employee_level": "senior",
            "cost_center": "CC-1001",
        },
    )
    token = get_bearer_token(config)
    claims = decode_token(token)
    print(f"    Token department_name: {claims.get('department_name', 'NOT PRESENT')}")
    result = make_gateway_request(
        config,
        token,
        "ApplicationToolTarget___create_application",
        {"applicant_region": "US", "coverage_amount": 500000},
    )
    assert_outcome("ALLOWED", analyze_response(result), "Finance dept should be ALLOWED")

    # Test DENY: engineering department
    print("\n  Test B1b — engineering dept → EXPECTED: DENIED")
    update_jwt_claims(
        config,
        {
            "department_name": "engineering",
            "employee_level": "senior",
            "cost_center": "CC-2001",
        },
    )
    token = get_bearer_token(config)
    claims = decode_token(token)
    print(f"    Token department_name: {claims.get('department_name', 'NOT PRESENT')}")
    result = make_gateway_request(
        config,
        token,
        "ApplicationToolTarget___create_application",
        {"applicant_region": "US", "coverage_amount": 500000},
    )
    assert_outcome("DENIED", analyze_response(result), "Engineering dept should be DENIED")

    delete_policy(control_client, engine_id, policy_id)

    # ── B2: Groups-Based ABAC with Pattern Matching ───────────────────────────
    print("\n[B2] Groups-Based ABAC with Wildcard Matching")
    print("─" * 65)
    print("  Cognito serializes list claims as strings in the JWT token.")
    print("  Use like '*value*' (not ==) to check membership in serialized arrays.\n")

    groups_policy = f'''permit(
  principal,
  action == AgentCore::Action::"ApprovalToolTarget___approve_underwriting",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {{
  principal.hasTag("groups") &&
  principal.getTag("groups") like "*admins*"
}};'''

    policy_id = create_cedar_policy(
        control_client,
        engine_id,
        f"groups_admins_{int(time.time()) % 10000}",
        groups_policy,
        "Allow ApprovalTool only for principals in admins group",
    )

    # Test ALLOW: user in admins group
    print("\n  Test B2a — groups=['admins','developers'] → EXPECTED: ALLOWED")
    update_jwt_claims(
        config,
        {
            "groups": ["admins", "developers", "team-alpha"],
            "department_name": "finance",
        },
    )
    token = get_bearer_token(config)
    claims = decode_token(token)
    print(f"    Token groups: {claims.get('groups', 'NOT PRESENT')}")
    result = make_gateway_request(
        config,
        token,
        "ApprovalToolTarget___approve_underwriting",
        {"claim_amount": 50000, "risk_level": "low"},
    )
    assert_outcome(
        "ALLOWED",
        analyze_response(result),
        "User with 'admins' group should be ALLOWED",
    )

    # Test DENY: user without admins
    print("\n  Test B2b — groups=['developers','team-alpha'] (no admins) → EXPECTED: DENIED")
    update_jwt_claims(
        config,
        {
            "groups": ["developers", "team-alpha"],
            "department_name": "finance",
        },
    )
    token = get_bearer_token(config)
    claims = decode_token(token)
    print(f"    Token groups: {claims.get('groups', 'NOT PRESENT')}")
    result = make_gateway_request(
        config,
        token,
        "ApprovalToolTarget___approve_underwriting",
        {"claim_amount": 50000, "risk_level": "low"},
    )
    assert_outcome(
        "DENIED",
        analyze_response(result),
        "User without 'admins' group should be DENIED",
    )

    delete_policy(control_client, engine_id, policy_id)

    # ── B3: Principal ID-Based Control (sub claim) ────────────────────────────
    print("\n[B3] Principal ID-Based Access Control")
    print("─" * 65)
    print("  In M2M client_credentials flow, the JWT 'sub' claim equals the")
    print("  Cognito app client's client_id. This uniquely identifies the caller.\n")

    principal_id_policy = f'''permit(
  principal,
  action == AgentCore::Action::"RiskModelToolTarget___invoke_risk_model",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {{
  principal.hasTag("sub") &&
  principal.getTag("sub") == "{client_id}"
}};'''

    policy_id = create_cedar_policy(
        control_client,
        engine_id,
        f"principal_id_{int(time.time()) % 10000}",
        principal_id_policy,
        f"Allow RiskModelTool only for principal with sub={client_id}",
    )

    print("\n  Test B3a — matching principal (sub == client_id) → EXPECTED: ALLOWED")
    # Restore default claims (sub is always set from Cognito, not from Lambda)
    update_jwt_claims(config, {"department_name": "finance", "groups": ["admins"]})
    token = get_bearer_token(config)
    claims = decode_token(token)
    print(f"    Token sub: {claims.get('sub', 'NOT PRESENT')}")
    print(f"    Policy client_id: {client_id}")
    result = make_gateway_request(
        config,
        token,
        "RiskModelToolTarget___invoke_risk_model",
        {"API_classification": "internal", "data_governance_approval": True},
    )
    assert_outcome("ALLOWED", analyze_response(result), "Matching sub should be ALLOWED")
    print("  Note: To test DENY, use a different Cognito app client with a different client_id.")

    delete_policy(control_client, engine_id, policy_id)

    # ── B4: Combined Conditions (department + context.input) ──────────────────
    print("\n[B4] Combined Conditions — Department + Input Parameter Constraint")
    print("─" * 65)
    print("  Cedar policies can combine principal tag checks with context.input")
    print("  validation. All conditions must be true (implicit AND).\n")

    combined_policy = f'''permit(
  principal,
  action == AgentCore::Action::"ApplicationToolTarget___create_application",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {{
  principal.hasTag("department_name") &&
  principal.getTag("department_name") == "finance" &&
  context.input.coverage_amount <= 1000000
}};'''

    policy_id = create_cedar_policy(
        control_client,
        engine_id,
        f"combined_{int(time.time()) % 10000}",
        combined_policy,
        "Allow ApplicationTool for finance dept with coverage <= $1M",
    )

    print("\n  Test B4a — finance + $500K → EXPECTED: ALLOWED (both conditions met)")
    update_jwt_claims(config, {"department_name": "finance", "employee_level": "senior"})
    token = get_bearer_token(config)
    result = make_gateway_request(
        config,
        token,
        "ApplicationToolTarget___create_application",
        {"applicant_region": "US", "coverage_amount": 500000},
    )
    assert_outcome("ALLOWED", analyze_response(result), "Finance + $500K should be ALLOWED")

    print("\n  Test B4b — finance + $2M → EXPECTED: DENIED (amount exceeds $1M)")
    token = get_bearer_token(config)
    result = make_gateway_request(
        config,
        token,
        "ApplicationToolTarget___create_application",
        {"applicant_region": "US", "coverage_amount": 2000000},
    )
    assert_outcome(
        "DENIED",
        analyze_response(result),
        "Finance + $2M should be DENIED (amount > $1M)",
    )

    print("\n  Test B4c — engineering + $500K → EXPECTED: DENIED (wrong dept)")
    update_jwt_claims(config, {"department_name": "engineering", "employee_level": "senior"})
    token = get_bearer_token(config)
    result = make_gateway_request(
        config,
        token,
        "ApplicationToolTarget___create_application",
        {"applicant_region": "US", "coverage_amount": 500000},
    )
    assert_outcome(
        "DENIED",
        analyze_response(result),
        "Engineering + $500K should be DENIED (wrong dept)",
    )

    delete_policy(control_client, engine_id, policy_id)

    # ── B5: Pattern Matching Reference ───────────────────────────────────────
    print("\n[B5] Pattern Matching with the 'like' Operator (Reference)")
    print("─" * 65)
    print("  The 'like' operator supports wildcards (*) for flexible string matching.\n")

    pattern_examples = [
        (
            "Contains 'admin' anywhere",
            'principal.getTag("groups") like "*admin*"',
            "Matches: ['admin', 'admins', 'team-admin'], '\"admin\"', '[\"admin\",\"dev\"]'",
        ),
        (
            "Starts with 'team-'",
            'principal.getTag("groups") like "team-*"',
            "Matches: 'team-finance', 'team-risk', but NOT '[\"team-risk\"]' (serialized array)",
        ),
        (
            "Specific team group (serialized array)",
            'principal.getTag("groups") like "*team-finance*"',
            'Matches: \'["team-finance"]\', \'["admins","team-finance"]\'',
        ),
        (
            "Exact match when group is scalar",
            'principal.getTag("role") == "senior-adjuster"',
            "For scalar (non-array) JWT claims — prefer == over like",
        ),
    ]

    for title, cedar_expr, notes in pattern_examples:
        print(f"  [{title}]")
        print(f"    Cedar: {cedar_expr}")
        print(f"    Notes: {notes}")
        print()

    # Demonstrate team-based pattern
    team_policy = f'''permit(
  principal,
  action == AgentCore::Action::"ApplicationToolTarget___create_application",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {{
  principal.hasTag("groups") &&
  principal.getTag("groups") like "*team-finance*"
}};'''

    policy_id = create_cedar_policy(
        control_client,
        engine_id,
        f"team_pattern_{int(time.time()) % 10000}",
        team_policy,
        "Allow ApplicationTool for principals in team-finance group",
    )

    print("  Test B5 — groups includes 'team-finance' → EXPECTED: ALLOWED")
    update_jwt_claims(
        config,
        {
            "groups": ["team-finance", "developers"],
            "department_name": "finance",
        },
    )
    token = get_bearer_token(config)
    claims = decode_token(token)
    print(f"    Token groups: {claims.get('groups', 'NOT PRESENT')}")
    result = make_gateway_request(
        config,
        token,
        "ApplicationToolTarget___create_application",
        {"applicant_region": "US", "coverage_amount": 100000},
    )
    assert_outcome("ALLOWED", analyze_response(result), "team-finance member should be ALLOWED")

    delete_policy(control_client, engine_id, policy_id)

    # Restore default claims for Part C
    print("\n  Restoring default claims for Part C...")
    update_jwt_claims(
        config,
        {
            "department_name": "finance",
            "employee_level": "senior",
            "groups": ["admins", "underwriters"],
            "cost_center": "CC-1001",
        },
    )


# ── Part C: End-to-End Agent Demo ─────────────────────────────────────────────


def part_c_agent_demo(config: dict):
    """
    Part C: End-to-end demo with a Strands agent invoking Gateway tools.

    The agent connects via MCP over the Gateway. Cedar policies control which
    tool invocations succeed. This shows the full enforcement path:
      Agent → Gateway → Cedar Policy Check → Lambda Target (or DENIED)
    """
    print("\n" + "=" * 65)
    print("PART C — End-to-End: Strands Agent with Active Cedar Policies")
    print("=" * 65)

    region = config["region"]
    engine_id = config["policy_engine"]["policyEngineId"]
    gateway_arn = config["gateway"]["gateway_arn"]
    control_client = boto3.client("bedrock-agentcore-control", region_name=region)

    delete_all_policies(control_client, engine_id)

    # Create a policy that allows only applications with coverage <= $1M
    allow_policy = f'''permit(
  principal,
  action == AgentCore::Action::"ApplicationToolTarget___create_application",
  resource == AgentCore::Gateway::"{gateway_arn}"
) when {{
  context.input.coverage_amount <= 1000000
}};'''

    policy_id = create_cedar_policy(
        control_client,
        engine_id,
        f"agent_demo_{int(time.time()) % 10000}",
        allow_policy,
        "Allow ApplicationTool for coverage <= $1M — agent demo",
    )

    update_jwt_claims(
        config,
        {
            "department_name": "finance",
            "employee_level": "senior",
            "groups": ["admins", "underwriters"],
        },
    )

    print("\n[C1] Agent with active policy — ALLOW scenario (coverage $750K <= $1M limit)")
    print("─" * 65)
    with AgentSession(verbose=False) as session:
        session.invoke("Create an application for US region with $750,000 coverage")

    print("\n[C2] Agent with active policy — DENY scenario (coverage $2M > $1M limit)")
    print("─" * 65)
    with AgentSession(verbose=False) as session:
        session.invoke("Create an application for US region with $2 million coverage")

    delete_policy(control_client, engine_id, policy_id)
    delete_all_policies(control_client, engine_id)
    print("\n  ✓ Policies cleaned up after demo")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="AgentCore Policy demo")
    parser.add_argument(
        "--section",
        choices=["A", "B", "C"],
        default=None,
        help="Run only one section (A=NL2Cedar, B=Fine-grained ABAC, C=Agent demo)",
    )
    args = parser.parse_args()

    config = load_config()

    print("=" * 65)
    print("AgentCore Policy Demo")
    print("=" * 65)
    print(f"  Region:      {config['region']}")
    print(f"  Gateway ID:  {config['gateway']['gateway_id']}")
    print(f"  Policy Eng:  {config['policy_engine']['policyEngineId']}")
    print()

    if args.section in (None, "A"):
        part_a_nl2cedar(config)

    if args.section in (None, "B"):
        part_b_fine_grained_abac(config)

    if args.section in (None, "C"):
        part_c_agent_demo(config)

    print("\n" + "=" * 65)
    print("Demo complete!")
    print("  Cleanup: python cleanup.py")
    print("=" * 65)


if __name__ == "__main__":
    main()
