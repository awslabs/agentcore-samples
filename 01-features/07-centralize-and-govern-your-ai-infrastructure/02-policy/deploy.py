"""
Deploy all resources for the AgentCore Policy demo.

This script creates an end-to-end insurance underwriting demo environment:

  1. Lambda tools  — ApplicationTool, RiskModelTool, ApprovalTool
  2. Cognito OAuth — User Pool + Domain + App Client (M2M client credentials)
  3. Gateway role  — IAM role granting the Gateway permission to invoke Lambdas
  4. Gateway       — AgentCore MCP Gateway with Cognito JWT authorizer
  5. Targets       — Three Lambda targets attached to the Gateway with tool schemas
  6. Policy Engine — Cedar policy engine created and attached to Gateway (ENFORCE mode)
  7. Claims Lambda — Cognito Pre-Token-Generation V3_0 trigger for custom JWT claims

All output is written to policy_config.json for use by policy_demo.py and cleanup.py.

Usage:
    python deploy.py [--region REGION]
"""

import argparse
import io
import json
import logging
import os
import time
import uuid
import zipfile
from pathlib import Path

import boto3
from bedrock_agentcore_starter_toolkit.operations.gateway.client import GatewayClient
from botocore.exceptions import ClientError

# ── Constants ────────────────────────────────────────────────────────────────

GATEWAY_NAME = "PolicyDemo-InsuranceUnderwriting"
LAMBDA_ROLE_NAME = "AgentCorePolicyDemoLambdaRole"
CLAIMS_LAMBDA_NAME = "PolicyDemo_CustomClaimsLambda"

# Default initial claims injected into every JWT token (used for demo scenarios)
DEFAULT_CLAIMS = {
    "department_name": "finance",
    "employee_level": "senior",
    "groups": ["admins", "underwriters"],
    "cost_center": "CC-1001",
}

# Lambda target definitions — name → (JS file path, tool schema)
LAMBDA_TARGETS = {
    "ApplicationTool": {
        "js_file": "utils/application_tool.js",
        "schema": [
            {
                "name": "create_application",
                "description": "Create insurance application with geographic and eligibility validation",
                "inputSchema": {
                    "type": "object",
                    "description": "Input parameters for insurance application creation",
                    "properties": {
                        "applicant_region": {
                            "type": "string",
                            "description": "Customer's geographic region (US, CA, UK, EU, APAC, etc.)",
                        },
                        "coverage_amount": {
                            "type": "integer",
                            "description": "Requested insurance coverage amount in USD",
                        },
                    },
                    "required": ["applicant_region", "coverage_amount"],
                },
            }
        ],
    },
    "RiskModelTool": {
        "js_file": "utils/risk_model_tool.js",
        "schema": [
            {
                "name": "invoke_risk_model",
                "description": "Invoke external risk scoring model with governance controls",
                "inputSchema": {
                    "type": "object",
                    "description": "Input parameters for risk model invocation",
                    "properties": {
                        "API_classification": {
                            "type": "string",
                            "description": "API classification: public, internal, or restricted",
                        },
                        "data_governance_approval": {
                            "type": "boolean",
                            "description": "Whether data governance has approved model usage",
                        },
                    },
                    "required": ["API_classification", "data_governance_approval"],
                },
            }
        ],
    },
    "ApprovalTool": {
        "js_file": "utils/approval_tool.js",
        "schema": [
            {
                "name": "approve_underwriting",
                "description": "Approve high-value or high-risk underwriting decisions",
                "inputSchema": {
                    "type": "object",
                    "description": "Input parameters for underwriting approval",
                    "properties": {
                        "claim_amount": {
                            "type": "integer",
                            "description": "Insurance claim/coverage amount in USD",
                        },
                        "risk_level": {
                            "type": "string",
                            "description": "Risk level assessment: low, medium, high, or critical",
                        },
                    },
                    "required": ["claim_amount", "risk_level"],
                },
            }
        ],
    },
}


# ── AWS Session Setup ─────────────────────────────────────────────────────────


def get_aws_context(region: str | None = None) -> tuple:
    """Return (session, REGION, ACCOUNT_ID) — never hardcodes either."""
    session = boto3.Session()
    resolved_region = region or session.region_name or os.environ.get("AWS_DEFAULT_REGION")
    if not resolved_region:
        raise ValueError("AWS region not configured. Pass --region or run: aws configure")
    account_id = session.client("sts", region_name=resolved_region).get_caller_identity()["Account"]
    return session, resolved_region, account_id


# ── Step 1: Lambda Deployment ─────────────────────────────────────────────────


def get_or_create_lambda_role(iam_client, account_id: str) -> str:
    """Return ARN of the Lambda execution role, creating it if absent."""
    try:
        resp = iam_client.get_role(RoleName=LAMBDA_ROLE_NAME)
        print(f"  IAM role exists: {LAMBDA_ROLE_NAME}")
        return resp["Role"]["Arn"]
    except iam_client.exceptions.NoSuchEntityException:
        pass

    print(f"  Creating IAM role: {LAMBDA_ROLE_NAME}")
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    resp = iam_client.create_role(
        RoleName=LAMBDA_ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(trust),
        Description="Execution role for AgentCore Policy demo Lambda functions",
    )
    iam_client.attach_role_policy(
        RoleName=LAMBDA_ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    print("  Waiting 10s for IAM role propagation...")
    time.sleep(10)
    return resp["Role"]["Arn"]


def deploy_lambda(lambda_client, function_name: str, js_path: str, role_arn: str) -> str:
    """Deploy a Node.js Lambda function from a .js file. Returns the function ARN."""
    print(f"  Deploying Lambda: {function_name}...")
    with open(js_path, "r", encoding="utf-8") as f:
        code = f.read()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("index.mjs", code)
    buf.seek(0)
    zip_bytes = buf.read()

    try:
        resp = lambda_client.create_function(
            FunctionName=function_name,
            Runtime="nodejs20.x",
            Role=role_arn,
            Handler="index.handler",
            Code={"ZipFile": zip_bytes},
            Description=f"AgentCore Policy demo: {function_name}",
            Timeout=30,
            MemorySize=256,
        )
        print(f"    Created: {resp['FunctionArn']}")
        waiter = lambda_client.get_waiter("function_active_v2")
        waiter.wait(FunctionName=function_name)
        return resp["FunctionArn"]
    except lambda_client.exceptions.ResourceConflictException:
        resp = lambda_client.update_function_code(FunctionName=function_name, ZipFile=zip_bytes)
        print(f"    Updated: {resp['FunctionArn']}")
        waiter = lambda_client.get_waiter("function_updated_v2")
        waiter.wait(FunctionName=function_name)
        return resp["FunctionArn"]


def add_lambda_gateway_permission(lambda_client, function_name: str, gateway_arn: str) -> None:
    """Add resource policy allowing bedrock-agentcore.amazonaws.com to invoke the Lambda."""
    statement_id = "AllowAgentCoreGateway"
    try:
        lambda_client.remove_permission(FunctionName=function_name, StatementId=statement_id)
    except ClientError:
        pass
    lambda_client.add_permission(
        FunctionName=function_name,
        StatementId=statement_id,
        Action="lambda:InvokeFunction",
        Principal="bedrock-agentcore.amazonaws.com",
        SourceArn=gateway_arn,
    )
    print(f"    Permission added: {function_name} → bedrock-agentcore (source: gateway)")


def deploy_all_lambdas(lambda_client, iam_client, account_id: str) -> dict:
    """Deploy all three tool Lambda functions. Returns {name: arn}."""
    print("\n[Step 1] Deploying Lambda tool functions...")
    role_arn = get_or_create_lambda_role(iam_client, account_id)
    arns = {}
    for name, cfg in LAMBDA_TARGETS.items():
        arns[name] = deploy_lambda(lambda_client, name, cfg["js_file"], role_arn)
    print(f"  ✓ {len(arns)} Lambda functions ready")
    return arns


# ── Gateway Role Permissions ─────────────────────────────────────────────────


CONFIG_FILE = "policy_config.json"

GATEWAY_ROLE_NAME = "AgentCoreGatewayExecutionRole"


def _build_gateway_trust_policy(region: str, account_id: str) -> dict:
    """Build the trust policy the toolkit expects (must match its Jinja2 template exactly)."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AssumeRolePolicy",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*"},
                },
            }
        ],
    }


def normalize_gateway_role_trust_policy(region: str, account_id: str) -> None:
    """
    If the shared gateway role exists, update its trust policy to match what
    the toolkit expects. This prevents the toolkit from refusing to reuse the
    role when a prior exercise left it with a different trust policy.
    """
    iam_client = boto3.client("iam")
    try:
        iam_client.get_role(RoleName=GATEWAY_ROLE_NAME)
    except iam_client.exceptions.NoSuchEntityException:
        return

    trust_policy = _build_gateway_trust_policy(region, account_id)
    iam_client.update_assume_role_policy(
        RoleName=GATEWAY_ROLE_NAME,
        PolicyDocument=json.dumps(trust_policy),
    )
    print(f"  ✓ Normalized trust policy on existing role: {GATEWAY_ROLE_NAME}")


def ensure_gateway_role_permissions(region: str, account_id: str, gateway_role_arn: str, lambda_arns: dict) -> None:
    """
    Ensure the gateway execution role has the correct trust policy and
    permissions for this demo.

    Fixes three issues that arise when the shared AgentCoreGatewayExecutionRole
    was left by a prior exercise:
      1. Inline policy scoped only to AgentCoreLambdaTestFunction
      2. Trust policy doesn't match what the toolkit expects
      3. Missing bedrock-agentcore:GetPolicyEngine permission
    """
    iam_client = boto3.client("iam")
    role_name = gateway_role_arn.split("/")[-1]

    trust_policy = _build_gateway_trust_policy(region, account_id)
    iam_client.update_assume_role_policy(
        RoleName=role_name,
        PolicyDocument=json.dumps(trust_policy),
    )

    lambda_resources = list(lambda_arns.values())
    policy_document = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokePolicyDemoLambdas",
                "Effect": "Allow",
                "Action": ["lambda:InvokeFunction"],
                "Resource": lambda_resources,
            },
            {
                "Sid": "AgentCorePolicyAccess",
                "Effect": "Allow",
                "Action": [
                    "bedrock-agentcore:GetPolicyEngine",
                    "bedrock-agentcore:GetGateway",
                ],
                "Resource": "*",
            },
        ],
    }
    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName="PolicyDemoPermissions",
        PolicyDocument=json.dumps(policy_document),
    )
    print(f"  ✓ Gateway role permissions ensured: {role_name}")


def save_partial_config(region: str, account_id: str, **kwargs) -> None:
    """Merge kwargs into existing config file (or create it) for incremental resume."""
    path = Path(CONFIG_FILE)
    config = {}
    if path.exists():
        config = json.loads(path.read_text())
    config["region"] = region
    config["account_id"] = account_id
    config.update(kwargs)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


# ── Step 2: Cognito + Gateway Setup ──────────────────────────────────────────


def setup_gateway(region: str, lambda_arns: dict, account_id: str) -> dict:
    """
    Create the Cognito OAuth server, AgentCore MCP Gateway, and Lambda targets.

    Idempotent: if a prior run left a gateway and saved config, resumes from
    that state instead of failing with "already exists".

    Returns a dict with gateway info and client_info for the JWT flow.
    """
    print("\n[Step 2] Setting up Cognito OAuth + AgentCore Gateway...")

    # Resume path: check for saved config with gateway info
    config_path = Path(CONFIG_FILE)
    if config_path.exists():
        existing = json.loads(config_path.read_text())
        if "gateway" in existing and existing["gateway"].get("gateway_id"):
            boto_ctrl = boto3.client("bedrock-agentcore-control", region_name=region)
            try:
                gw = boto_ctrl.get_gateway(gatewayIdentifier=existing["gateway"]["gateway_id"])
                if gw.get("status") in ("READY", "ACTIVE"):
                    print(f"  Resuming with existing gateway: {gw['gatewayId']}")
                    ensure_gateway_role_permissions(region, account_id, gw["roleArn"], lambda_arns)
                    return existing["gateway"]
            except ClientError:
                pass  # gateway gone, fall through to create

    # Check if gateway exists by name but we have no saved credentials
    boto_ctrl = boto3.client("bedrock-agentcore-control", region_name=region)
    resp = boto_ctrl.list_gateways()
    for gw in resp.get("items", []):
        if gw.get("name") == GATEWAY_NAME and gw.get("status") in (
            "READY",
            "ACTIVE",
        ):
            print(f"  Gateway '{GATEWAY_NAME}' exists but no saved credentials.")
            print("  Run cleanup.py first, then re-run deploy.py.")
            raise RuntimeError("Gateway exists without saved credentials. Run cleanup.py first.")

    # Fresh deploy path
    normalize_gateway_role_trust_policy(region, account_id)

    gw_client = GatewayClient(region_name=region)
    gw_client.logger.setLevel(logging.WARNING)

    print("  Creating Cognito User Pool (Essentials tier for V3_0 trigger support)...")
    cognito_resp = gw_client.create_oauth_authorizer_with_cognito("PolicyDemoGateway")
    print("  ✓ OAuth authorizer ready")

    print(f"  Creating MCP Gateway: {GATEWAY_NAME}...")
    gateway = gw_client.create_mcp_gateway(
        name=GATEWAY_NAME,
        role_arn=None,
        authorizer_config=cognito_resp["authorizer_config"],
        enable_semantic_search=True,
    )
    print(f"  ✓ Gateway created: {gateway['gatewayUrl']}")

    gw_client.fix_iam_permissions(gateway)
    ensure_gateway_role_permissions(region, account_id, gateway["roleArn"], lambda_arns)

    print("  Waiting 30s for IAM propagation...")
    time.sleep(30)

    print("  Adding Lambda targets to Gateway...")
    gateway_arn = gateway.get("gatewayArn")
    for name, cfg in LAMBDA_TARGETS.items():
        gw_client.create_mcp_gateway_target(
            gateway=gateway,
            name=f"{name}Target",
            target_type="lambda",
            target_payload={
                "lambdaArn": lambda_arns[name],
                "toolSchema": {"inlinePayload": cfg["schema"]},
            },
            credentials=None,
        )
        print(f"    Added target: {name}Target")

    lambda_client = boto3.client("lambda", region_name=region)
    for name in LAMBDA_TARGETS:
        add_lambda_gateway_permission(lambda_client, name, gateway_arn)

    return {
        "gateway_id": gateway["gatewayId"],
        "gateway_arn": gateway_arn,
        "gateway_url": gateway["gatewayUrl"],
        "client_info": cognito_resp["client_info"],
    }


# ── Step 3: Policy Engine ─────────────────────────────────────────────────────


def create_policy_engine(region: str) -> dict:
    """Find or create a Cedar policy engine. Returns {policyEngineId, policyEngineArn}."""
    print("\n[Step 3] Creating Policy Engine...")
    client = boto3.client("bedrock-agentcore-control", region_name=region)

    # Check for existing ACTIVE engine from a prior run
    try:
        resp = client.list_policy_engines()
        for eng in resp.get("policyEngines", []):
            if eng.get("name", "").startswith("PolicyDemoEngine") and eng.get("status") == "ACTIVE":
                print(f"  Reusing existing Policy Engine: {eng['policyEngineId']}")
                return {
                    "policyEngineId": eng["policyEngineId"],
                    "policyEngineArn": eng["policyEngineArn"],
                }
    except ClientError:
        pass

    engine_name = f"PolicyDemoEngine_{int(time.time()) % 100000}"
    resp = client.create_policy_engine(
        name=engine_name,
        description="Cedar policy engine for insurance underwriting demo",
        clientToken=str(uuid.uuid4()),
    )
    engine_id = resp["policyEngineId"]
    engine_arn = resp["policyEngineArn"]
    print(f"  Policy Engine created: {engine_id}")

    print("  Waiting for ACTIVE status...")
    for _ in range(60):
        status = client.get_policy_engine(policyEngineId=engine_id).get("status")
        if status == "ACTIVE":
            break
        if status in ("CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED"):
            raise RuntimeError(f"Policy Engine failed: {status}")
        print(f"    Status: {status}")
        time.sleep(5)
    print("  ✓ Policy Engine ACTIVE")
    return {"policyEngineId": engine_id, "policyEngineArn": engine_arn}


def attach_policy_engine_to_gateway(region: str, gateway_info: dict, engine_arn: str) -> None:
    """Attach the Policy Engine to the Gateway in ENFORCE mode."""
    print("\n[Step 4] Attaching Policy Engine to Gateway (ENFORCE mode)...")
    client = boto3.client("bedrock-agentcore-control", region_name=region)

    gw = client.get_gateway(gatewayIdentifier=gateway_info["gateway_id"])
    client.update_gateway(
        gatewayIdentifier=gateway_info["gateway_id"],
        name=gw.get("name"),
        roleArn=gw.get("roleArn"),
        protocolType=gw.get("protocolType", "MCP"),
        authorizerType=gw.get("authorizerType", "CUSTOM_JWT"),
        authorizerConfiguration=gw.get("authorizerConfiguration", {}),
        policyEngineConfiguration={"arn": engine_arn, "mode": "ENFORCE"},
    )

    print("  Waiting for Gateway READY...")
    for _ in range(60):
        status = client.get_gateway(gatewayIdentifier=gateway_info["gateway_id"]).get("status")
        if status == "READY":
            break
        if status in ("FAILED", "UPDATE_UNSUCCESSFUL"):
            raise RuntimeError(f"Gateway update failed: {status}")
        print(f"    Status: {status}")
        time.sleep(5)
    print("  ✓ Policy Engine attached to Gateway")


# ── Step 4: Cognito Lambda Trigger (for Custom JWT Claims) ───────────────────


def create_or_update_claims_lambda(lambda_client, iam_client, region: str, account_id: str, claims: dict) -> str:
    """
    Create/update the Cognito Pre-Token-Generation Lambda that injects custom
    claims into every JWT token. Returns the Lambda ARN.

    This Lambda implements the V3_0 trigger required for M2M client_credentials flow.
    V3_0 is only supported on Cognito Essentials or Plus tier.
    """
    claims_json = json.dumps(claims, indent=12)
    lambda_code = f'''import json

def lambda_handler(event, context):
    """
    Pre-Token-Generation V3_0 Lambda trigger for Cognito.
    Adds custom claims to JWT tokens — works for client_credentials (M2M) flow.
    """
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
'''

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("lambda_function.py", lambda_code)
    buf.seek(0)
    zip_bytes = buf.read()

    role_arn = get_or_create_lambda_role(iam_client, account_id)

    try:
        resp = lambda_client.create_function(
            FunctionName=CLAIMS_LAMBDA_NAME,
            Runtime="python3.12",
            Role=role_arn,
            Handler="lambda_function.lambda_handler",
            Code={"ZipFile": zip_bytes},
            Description="Cognito Pre-Token-Generation trigger for AgentCore Policy demo",
            Timeout=30,
            MemorySize=128,
        )
        waiter = lambda_client.get_waiter("function_active_v2")
        waiter.wait(FunctionName=CLAIMS_LAMBDA_NAME)
        return resp["FunctionArn"]
    except lambda_client.exceptions.ResourceConflictException:
        resp = lambda_client.update_function_code(FunctionName=CLAIMS_LAMBDA_NAME, ZipFile=zip_bytes)
        waiter = lambda_client.get_waiter("function_updated_v2")
        waiter.wait(FunctionName=CLAIMS_LAMBDA_NAME)
        return resp["FunctionArn"]


def configure_cognito_trigger(
    cognito_client,
    lambda_client,
    region: str,
    account_id: str,
    user_pool_id: str,
    lambda_arn: str,
) -> None:
    """
    Attach the claims Lambda as a V3_0 Pre-Token-Generation trigger on the User Pool.

    V3_0 is required for M2M (client_credentials) flow — V1_0/V2_0 do not fire
    for client credentials, so custom claims would never be injected.
    """
    cognito_client.update_user_pool(
        UserPoolId=user_pool_id,
        LambdaConfig={
            "PreTokenGenerationConfig": {
                "LambdaVersion": "V3_0",
                "LambdaArn": lambda_arn,
            }
        },
    )

    # Grant Cognito permission to invoke the Lambda
    try:
        lambda_client.add_permission(
            FunctionName=lambda_arn,
            StatementId=f"CognitoTrigger-{user_pool_id.replace('_', '-')}",
            Action="lambda:InvokeFunction",
            Principal="cognito-idp.amazonaws.com",
            SourceArn=f"arn:aws:cognito-idp:{region}:{account_id}:userpool/{user_pool_id}",
        )
    except lambda_client.exceptions.ResourceConflictException:
        pass  # permission already exists


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Deploy AgentCore Policy demo resources")
    parser.add_argument("--region", default=None, help="AWS region (defaults to configured default)")
    args = parser.parse_args()

    _, REGION, ACCOUNT_ID = get_aws_context(args.region)
    print("=" * 65)
    print("AgentCore Policy Demo — Deployment")
    print("=" * 65)
    print(f"  Region:  {REGION}")
    print(f"  Account: {ACCOUNT_ID}")
    print()

    lambda_client = boto3.client("lambda", region_name=REGION)
    iam_client = boto3.client("iam", region_name=REGION)
    cognito_client = boto3.client("cognito-idp", region_name=REGION)

    # Step 1: Deploy Lambda tools
    lambda_arns = deploy_all_lambdas(lambda_client, iam_client, ACCOUNT_ID)
    save_partial_config(REGION, ACCOUNT_ID, lambda_arns=lambda_arns)

    # Step 2: Create Cognito + Gateway + Lambda targets
    gateway_info = setup_gateway(REGION, lambda_arns, ACCOUNT_ID)
    save_partial_config(REGION, ACCOUNT_ID, lambda_arns=lambda_arns, gateway=gateway_info)

    # Step 3: Create Policy Engine
    engine = create_policy_engine(REGION)
    save_partial_config(
        REGION,
        ACCOUNT_ID,
        lambda_arns=lambda_arns,
        gateway=gateway_info,
        policy_engine=engine,
    )

    # Step 4: Attach Policy Engine → Gateway (ENFORCE)
    attach_policy_engine_to_gateway(REGION, gateway_info, engine["policyEngineArn"])

    # Step 5: Create Cognito Lambda trigger for custom claims
    print("\n[Step 5] Configuring Cognito Lambda trigger for custom JWT claims...")
    user_pool_id = gateway_info["client_info"]["user_pool_id"]
    claims_lambda_arn = create_or_update_claims_lambda(lambda_client, iam_client, REGION, ACCOUNT_ID, DEFAULT_CLAIMS)
    configure_cognito_trigger(
        cognito_client,
        lambda_client,
        REGION,
        ACCOUNT_ID,
        user_pool_id,
        claims_lambda_arn,
    )
    print(f"  ✓ Trigger configured (V3_0): {CLAIMS_LAMBDA_NAME}")
    print(f"  Initial claims: {list(DEFAULT_CLAIMS.keys())}")

    # Final save with all configuration
    save_partial_config(
        REGION,
        ACCOUNT_ID,
        lambda_arns=lambda_arns,
        claims_lambda_arn=claims_lambda_arn,
        gateway=gateway_info,
        policy_engine=engine,
    )

    print("\n" + "=" * 65)
    print("✓ Deployment complete!")
    print(f"  Gateway URL:       {gateway_info['gateway_url']}")
    print(f"  Policy Engine ID:  {engine['policyEngineId']}")
    print("  Config saved to:   policy_config.json")
    print()
    print("  Next: python policy_demo.py")
    print("=" * 65)


if __name__ == "__main__":
    main()
