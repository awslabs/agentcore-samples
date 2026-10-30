"""
Deploy all resources for the AgentCore Guardrails-as-Policies demo.

Creates an insurance underwriting environment with content safety guardrails:

  1. Lambda tools   — ApplicationTool (with message), RiskModelTool, ApprovalTool
  2. IAM role       — Lambda execution role
  3. Gateway        — AgentCore MCP Gateway (IAM auth)
  4. Targets        — Three Lambda targets with tool schemas
  5. Policy Engine  — Cedar policy engine
  6. Base permit    — Cedar PERMIT allowing all traffic (guardrail FORBIDs override)
  7. Guardrail policies:
       - block_violence       : content filter on message (VIOLENCE >= 0.5)
       - block_jailbreak      : prompt attack on message (JAILBREAK >= 0.7)
       - block_ssn            : sensitive info on message (SSN >= 0.5)
       - block_credit_cards   : sensitive info on message (CREDIT_CARD >= 0.5)
  8. Attach engine   — ENFORCE mode on the gateway

All output is written to guardrail_config.json.

Usage:
    python deploy.py [--region REGION] [--profile PROFILE]
"""

import argparse
import io
import json
import os
import time
import uuid
import zipfile

import boto3
from botocore.exceptions import ClientError

# ── Constants ─────────────────────────────────────────────────────────────────

GATEWAY_NAME = "GuardrailDemo-InsuranceUnderwriting"
LAMBDA_ROLE_NAME = "AgentCorePolicyDemoLambdaRole"
CONFIG_FILE = "guardrail_config.json"

# Lambda target definitions
LAMBDA_TARGETS = {
    "ApplicationTool": {
        "js_file": "utils/application_tool.js",
        "schema": [
            {
                "name": "create_application",
                "description": "Create an insurance application with geographic validation. Use message for any free-text notes about the applicant.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "applicant_region": {
                            "type": "string",
                            "description": "Geographic region (US, CA, UK, EU, AU)",
                        },
                        "coverage_amount": {
                            "type": "integer",
                            "description": "Requested coverage in USD",
                        },
                        "message": {
                            "type": "string",
                            "description": "Free-text notes about the applicant or policy. Required for content safety evaluation — the gateway guardrail policies scan this field before the tool is invoked.",
                        },
                    },
                    "required": ["applicant_region", "coverage_amount", "message"],
                },
            }
        ],
    },
    "RiskModelTool": {
        "js_file": "utils/risk_model_tool.js",
        "schema": [
            {
                "name": "invoke_risk_model",
                "description": "Invoke risk scoring model with governance controls",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "API_classification": {
                            "type": "string",
                            "description": "public, internal, or restricted",
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
                    "properties": {
                        "claim_amount": {
                            "type": "integer",
                            "description": "Claim/coverage amount in USD",
                        },
                        "risk_level": {
                            "type": "string",
                            "description": "low, medium, high, or critical",
                        },
                    },
                    "required": ["claim_amount", "risk_level"],
                },
            }
        ],
    },
}


# ── AWS Session Setup ─────────────────────────────────────────────────────────


def get_aws_context(region: str | None = None, profile: str | None = None) -> tuple:
    """Return (session, REGION, ACCOUNT_ID)."""
    session = boto3.Session(profile_name=profile)
    resolved_region = region or session.region_name or os.environ.get("AWS_DEFAULT_REGION")
    if not resolved_region:
        raise ValueError("AWS region not configured. Pass --region or run: aws configure")
    account_id = session.client("sts", region_name=resolved_region).get_caller_identity()["Account"]
    return session, resolved_region, account_id


# ── Step 1: Lambda Deployment ─────────────────────────────────────────────────


def get_or_create_lambda_role(iam_client, account_id: str) -> str:
    """Return ARN of the Lambda execution role, creating it if absent."""
    try:
        return iam_client.get_role(RoleName=LAMBDA_ROLE_NAME)["Role"]["Arn"]
    except iam_client.exceptions.NoSuchEntityException:
        pass

    print(f"  Creating IAM role: {LAMBDA_ROLE_NAME}")
    trust = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}
        ],
    }
    resp = iam_client.create_role(
        RoleName=LAMBDA_ROLE_NAME,
        AssumeRolePolicyDocument=json.dumps(trust),
        Description="Execution role for policy in Amazon Bedrock AgentCore demo Lambda functions",
    )
    iam_client.attach_role_policy(
        RoleName=LAMBDA_ROLE_NAME,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
    )
    print("  Waiting 10s for IAM role propagation...")
    time.sleep(10)
    return resp["Role"]["Arn"]


def deploy_lambda(lambda_client, function_name: str, js_path: str, role_arn: str) -> str:
    """Deploy a Node.js Lambda from a .js file. Returns the function ARN."""
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
            Timeout=30,
            MemorySize=256,
        )
        lambda_client.get_waiter("function_active_v2").wait(FunctionName=function_name)
        print(f"    Created: {resp['FunctionArn']}")
        return resp["FunctionArn"]
    except lambda_client.exceptions.ResourceConflictException:
        resp = lambda_client.update_function_code(FunctionName=function_name, ZipFile=zip_bytes)
        lambda_client.get_waiter("function_updated_v2").wait(FunctionName=function_name)
        print(f"    Updated: {resp['FunctionArn']}")
        return resp["FunctionArn"]


def add_lambda_gateway_permission(lambda_client, function_name: str, gateway_arn: str) -> None:
    statement_id = "AllowAgentCoreGateway"
    try:
        lambda_client.remove_permission(FunctionName=function_name, StatementId=statement_id)
    except ClientError:
        # Statement does not exist yet on first run — safe to ignore and re-add below.
        pass
    lambda_client.add_permission(
        FunctionName=function_name,
        StatementId=statement_id,
        Action="lambda:InvokeFunction",
        Principal="bedrock-agentcore.amazonaws.com",
        SourceArn=gateway_arn,
    )


def deploy_all_lambdas(lambda_client, iam_client, account_id: str) -> dict:
    print("\n[Step 1] Deploying Lambda tool functions...")
    role_arn = get_or_create_lambda_role(iam_client, account_id)
    arns = {}
    for name, cfg in LAMBDA_TARGETS.items():
        arns[name] = deploy_lambda(lambda_client, name, cfg["js_file"], role_arn)
    print(f"  {len(arns)} Lambda functions ready")
    return arns


# ── Step 2: Gateway Setup ─────────────────────────────────────────────────────


def create_gateway(ctrl, region: str, account_id: str) -> dict:
    """Create an AgentCore MCP Gateway with IAM authentication."""
    print("\n[Step 2] Creating AgentCore MCP Gateway...")

    # Check if already exists
    try:
        resp = ctrl.list_gateways()
        for gw in resp.get("items", []):
            if gw.get("name") == GATEWAY_NAME and gw.get("status") in ("READY", "ACTIVE"):
                print(f"  Gateway '{GATEWAY_NAME}' already exists: {gw['gatewayId']}")
                print("  To redeploy, run cleanup.py first.")
                full = ctrl.get_gateway(gatewayIdentifier=gw["gatewayId"])
                return {
                    "gateway_id": gw["gatewayId"],
                    "gateway_arn": full.get(
                        "gatewayArn", f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/{gw['gatewayId']}"
                    ),
                    "gateway_url": full.get("gatewayUrl", ""),
                    "role_arn": full.get("roleArn", ""),
                }
    except ClientError:
        pass

    # Create IAM role for gateway
    iam = boto3.client("iam", region_name=region)
    gateway_role_name = "AgentCoreGuardrailDemoGatewayRole"
    try:
        gw_role_arn = iam.get_role(RoleName=gateway_role_name)["Role"]["Arn"]
        print(f"  Using existing gateway role: {gateway_role_name}")
    except iam.exceptions.NoSuchEntityException:
        trust = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
        role_resp = iam.create_role(
            RoleName=gateway_role_name,
            AssumeRolePolicyDocument=json.dumps(trust),
            Description="Gateway execution role for guardrail demo",
        )
        iam.attach_role_policy(RoleName=gateway_role_name, PolicyArn="arn:aws:iam::aws:policy/AWSLambda_ReadOnlyAccess")
        gw_role_arn = role_resp["Role"]["Arn"]
        print(f"  Created gateway role: {gw_role_arn}")

    # Always ensure inline policy is up to date (covers both create and existing-role paths).
    # bedrock-agentcore:GetPolicyEngine is required for the gateway to resolve the attached
    # policy engine when switching to ENFORCE mode. bedrock:InvokeGuardrailChecks is required
    # for the policy engine to call Bedrock Guardrails on the gateway's behalf (FAS credentials).
    iam.put_role_policy(
        RoleName=gateway_role_name,
        PolicyName="GatewayInlinePolicy",
        PolicyDocument=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {"Effect": "Allow", "Action": "lambda:InvokeFunction", "Resource": "*"},
                    {"Effect": "Allow", "Action": "bedrock-agentcore:*", "Resource": "*"},
                    {"Effect": "Allow", "Action": "bedrock:InvokeGuardrailChecks", "Resource": "*"},
                ],
            }
        ),
    )
    print("  Gateway role inline policy updated")
    time.sleep(15)  # IAM propagation

    resp = ctrl.create_gateway(
        name=GATEWAY_NAME,
        roleArn=gw_role_arn,
        protocolType="MCP",
        authorizerType="AWS_IAM",
    )
    gateway_id = resp["gatewayId"]
    print(f"  Gateway created: {gateway_id}")

    # Wait for READY
    for _ in range(60):
        status = ctrl.get_gateway(gatewayIdentifier=gateway_id).get("status")
        if status == "READY":
            break
        if status in ("FAILED", "CREATE_FAILED"):
            raise RuntimeError("Gateway creation failed")
        print(f"    Status: {status}")
        time.sleep(5)

    gw = ctrl.get_gateway(gatewayIdentifier=gateway_id)
    gateway_arn = gw.get("gatewayArn", f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/{gateway_id}")
    gateway_url = gw.get("gatewayUrl", "")
    print(f"  Gateway READY: {gateway_url}")
    return {"gateway_id": gateway_id, "gateway_arn": gateway_arn, "gateway_url": gateway_url, "role_arn": gw_role_arn}


def create_lambda_targets(ctrl, gateway_id: str, gateway_arn: str, lambda_client, lambda_arns: dict) -> dict:
    """Attach Lambda functions as gateway targets."""
    print("\n[Step 3] Creating Lambda targets...")
    target_ids = {}
    for name, cfg in LAMBDA_TARGETS.items():
        target_name = f"{name}Target"
        resp = ctrl.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name=target_name,
            targetConfiguration={
                "mcp": {
                    "lambda": {
                        "lambdaArn": lambda_arns[name],
                        "toolSchema": {"inlinePayload": cfg["schema"]},
                    }
                }
            },
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        )
        target_ids[name] = {"target_id": resp["targetId"], "target_name": target_name}
        print(f"    Created target: {target_name}")

    # Wait for all targets
    for name, info in target_ids.items():
        for _ in range(30):
            status = ctrl.get_gateway_target(gatewayIdentifier=gateway_id, targetId=info["target_id"]).get("status")
            if status == "READY":
                break
            if status in ("FAILED", "CREATE_FAILED"):
                raise RuntimeError(f"Target {name} failed")
            time.sleep(5)
        print(f"    {name}Target READY")

    # Grant gateway permission to invoke each Lambda
    for name in lambda_arns:
        add_lambda_gateway_permission(lambda_client, name, gateway_arn)

    return target_ids


# ── Step 3: Policy Engine and Guardrail Policies ──────────────────────────────


def create_policy_engine(ctrl) -> dict:
    """Create a new Cedar policy engine."""
    print("\n[Step 4] Creating Policy Engine...")
    engine_name = f"GuardrailDemoEngine_{int(time.time()) % 100000}"
    resp = ctrl.create_policy_engine(
        name=engine_name,
        description="Policy engine with guardrail policies for insurance underwriting demo",
        clientToken=str(uuid.uuid4()),
    )
    engine_id = resp["policyEngineId"]
    engine_arn = resp["policyEngineArn"]
    print(f"  Policy Engine: {engine_id}")

    for _ in range(60):
        status = ctrl.get_policy_engine(policyEngineId=engine_id).get("status")
        if status == "ACTIVE":
            break
        if status in ("CREATE_FAILED", "UPDATE_FAILED"):
            raise RuntimeError(f"Policy Engine failed: {status}")
        print(f"    Status: {status}")
        time.sleep(5)
    print("  Policy Engine ACTIVE")
    return {"policyEngineId": engine_id, "policyEngineArn": engine_arn}


def create_cedar_permit(ctrl, engine_id: str, gateway_arn: str) -> str:
    """
    Create a base Cedar PERMIT policy allowing all traffic to the gateway.

    Cedar is default-deny. Without this permit, guardrail FORBID policies would
    never trigger because all requests would already be blocked. The pattern is:
      PERMIT all traffic (this policy)
      FORBID harmful content (guardrail policies below)
    The guardrail FORBIDs override the permit via deny-overrides semantics.
    """
    print("  Creating base Cedar PERMIT (allow-all + guardrail FORBIDs override)...")
    cedar_statement = f'permit(principal, action, resource == AgentCore::Gateway::"{gateway_arn}");'
    resp = ctrl.create_policy(
        policyEngineId=engine_id,
        name="permit_all_traffic",
        description="Base permit — guardrail FORBIDs override for harmful content",
        definition={"policy": {"statement": cedar_statement}},
        validationMode="IGNORE_ALL_FINDINGS",
        enforcementMode="ACTIVE",
    )
    policy_id = resp["policyId"]
    # Wait for ACTIVE
    for _ in range(20):
        status = ctrl.get_policy(policyEngineId=engine_id, policyId=policy_id).get("status")
        if status == "ACTIVE":
            break
        time.sleep(3)
    print(f"  Base PERMIT created: {policy_id}")
    return policy_id


def create_guardrail_policy(ctrl, engine_id: str, name: str, cedar_statement: str) -> str:
    """
    Create a guardrail policy using Cedar 'when guardrails' syntax.

    Guardrail policies use the same Cedar policy structure as regular Cedar policies,
    but replace the 'when { ... }' condition block with 'when guardrails { ... }'.
    Inside the block, BedrockGuardrails functions evaluate the request content and
    return confidence scores that are compared against the threshold.
    """
    print(f"  Creating guardrail policy: {name}...")
    resp = ctrl.create_policy(
        policyEngineId=engine_id,
        name=name,
        description=f"Guardrail policy: {name}",
        definition={"policy": {"statement": cedar_statement}},
        validationMode="IGNORE_ALL_FINDINGS",
        enforcementMode="ACTIVE",
    )
    policy_id = resp["policyId"]
    for _ in range(20):
        status = ctrl.get_policy(policyEngineId=engine_id, policyId=policy_id).get("status")
        if status == "ACTIVE":
            break
        if status in ("CREATE_FAILED", "UPDATE_FAILED"):
            reasons = ctrl.get_policy(policyEngineId=engine_id, policyId=policy_id).get("statusReasons", [])
            print(f"  WARNING: Policy {name} creation failed: {reasons}")
            return None
        time.sleep(3)
    print(f"    Policy ACTIVE: {policy_id}")
    return policy_id


def create_all_guardrail_policies(ctrl, engine_id: str, gateway_arn: str) -> dict:
    """
    Create all guardrail policies for the insurance underwriting gateway.

    Guardrail policies use Cedar 'when guardrails { BedrockGuardrails::... }' syntax.
    The guardrails scan the `message` argument of create_application for:
      - Violent/threatening content (contentFilter: VIOLENCE)
      - Jailbreak/prompt injection attempts (promptAttack: JAILBREAK)
      - SSN numbers (sensitiveInformation: US_SOCIAL_SECURITY_NUMBER)
      - Credit card numbers (sensitiveInformation: CREDIT_DEBIT_CARD_NUMBER)

    For MCP tools/call, context.input.X maps to params.arguments.X.
    The `message` field in the tool schema is the free-text notes field; it maps
    to context.input.message in Cedar guardrail policies.

    Each policy is scoped to AgentCore::Action::"ApplicationToolTarget___create_application"
    because that is the tool with the free-text `message` field. The Cedar schema
    validator requires action equality constraints; a wildcard action scope is not supported.
    """
    print("\n[Step 5] Creating guardrail policies...")

    resource = f'AgentCore::Gateway::"{gateway_arn}"'
    # Action that carries the message free-text field.
    # Format: <TargetName>___<toolMethodName>
    action = 'AgentCore::Action::"ApplicationToolTarget___create_application"'
    scope = f"principal, action == {action}, resource == {resource}"

    policies = {}

    # Policy 1: Block violent/threatening content
    # Blocks create_application calls where VIOLENCE confidence >= 0.5 in input
    policies["block_violence"] = create_guardrail_policy(
        ctrl,
        engine_id,
        "block_violence",
        f"forbid({scope})\n"
        f"when guardrails {{\n"
        f'  BedrockGuardrails::ContentFilter(["VIOLENCE"], [context.input.message])["VIOLENCE"]\n'
        f"  .confidenceScore\n"
        f'  .greaterThanOrEqual(decimal("0.5"))\n'
        f"}};",
    )

    # Policy 2: Block jailbreak / prompt injection attempts
    # Blocks create_application calls where JAILBREAK confidence >= 0.7 in input
    policies["block_jailbreak"] = create_guardrail_policy(
        ctrl,
        engine_id,
        "block_jailbreak",
        f"forbid({scope})\n"
        f"when guardrails {{\n"
        f'  BedrockGuardrails::PromptAttack(["JAILBREAK"], [context.input.message])["JAILBREAK"]\n'
        f"  .confidenceScore\n"
        f'  .greaterThanOrEqual(decimal("0.7"))\n'
        f"}};",
    )

    # Policy 3: Block SSN in input (PII protection)
    # Blocks create_application calls containing US SSNs with confidence >= 0.5
    policies["block_ssn"] = create_guardrail_policy(
        ctrl,
        engine_id,
        "block_ssn",
        f"forbid({scope})\n"
        f"when guardrails {{\n"
        f'  BedrockGuardrails::SensitiveInformation(["US_SOCIAL_SECURITY_NUMBER"], [context.input.message])["US_SOCIAL_SECURITY_NUMBER"]\n'
        f"  .confidenceScore\n"
        f'  .greaterThanOrEqual(decimal("0.5"))\n'
        f"}};",
    )

    # Policy 4: Block credit card numbers in input (PII protection)
    # Blocks create_application calls containing credit card numbers with confidence >= 0.5
    policies["block_credit_cards"] = create_guardrail_policy(
        ctrl,
        engine_id,
        "block_credit_cards",
        f"forbid({scope})\n"
        f"when guardrails {{\n"
        f'  BedrockGuardrails::SensitiveInformation(["CREDIT_DEBIT_CARD_NUMBER"], [context.input.message])["CREDIT_DEBIT_CARD_NUMBER"]\n'
        f"  .confidenceScore\n"
        f'  .greaterThanOrEqual(decimal("0.5"))\n'
        f"}};",
    )

    return policies


def attach_policy_engine(ctrl, gateway_id: str, gateway_name: str, role_arn: str, engine_arn: str) -> None:
    """Attach the Policy Engine to the Gateway in ENFORCE mode."""
    print("\n[Step 6] Attaching Policy Engine to Gateway (ENFORCE mode)...")
    ctrl.update_gateway(
        gatewayIdentifier=gateway_id,
        name=gateway_name,
        roleArn=role_arn,
        protocolType="MCP",
        authorizerType="AWS_IAM",
        policyEngineConfiguration={"arn": engine_arn, "mode": "ENFORCE"},
    )
    for _ in range(60):
        status = ctrl.get_gateway(gatewayIdentifier=gateway_id).get("status")
        if status == "READY":
            break
        if status in ("FAILED", "UPDATE_UNSUCCESSFUL"):
            raise RuntimeError(f"Gateway update failed: {status}")
        print(f"    Status: {status}")
        time.sleep(5)
    print("  Policy Engine attached in ENFORCE mode")


# ── Main ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Deploy AgentCore Guardrails-as-Policies demo")
    parser.add_argument("--region", default=None, help="AWS region (default: configured default)")
    parser.add_argument("--profile", default=None, help="AWS profile name")
    args = parser.parse_args()

    _, REGION, ACCOUNT_ID = get_aws_context(args.region, args.profile)
    print("=" * 65)
    print("AgentCore Guardrails-as-Policies Demo — Deployment")
    print("=" * 65)
    print(f"  Region:  {REGION}")
    print(f"  Account: {ACCOUNT_ID}")
    print()

    ctrl = boto3.client("bedrock-agentcore-control", region_name=REGION)
    lambda_client = boto3.client("lambda", region_name=REGION)
    iam_client = boto3.client("iam", region_name=REGION)

    # Step 1: Lambda tools
    lambda_arns = deploy_all_lambdas(lambda_client, iam_client, ACCOUNT_ID)

    # Step 2-3: Gateway + targets
    gateway_info = create_gateway(ctrl, REGION, ACCOUNT_ID)
    target_ids = create_lambda_targets(
        ctrl, gateway_info["gateway_id"], gateway_info["gateway_arn"], lambda_client, lambda_arns
    )

    # Step 4: Policy Engine
    engine = create_policy_engine(ctrl)

    # Step 5: Guardrail policies + base permit
    permit_id = create_cedar_permit(ctrl, engine["policyEngineId"], gateway_info["gateway_arn"])
    guardrail_policy_ids = create_all_guardrail_policies(ctrl, engine["policyEngineId"], gateway_info["gateway_arn"])

    # Step 6: Attach engine to gateway
    attach_policy_engine(
        ctrl,
        gateway_info["gateway_id"],
        GATEWAY_NAME,
        gateway_info["role_arn"],
        engine["policyEngineArn"],
    )

    # Save config
    config = {
        "region": REGION,
        "account_id": ACCOUNT_ID,
        "aws_profile": args.profile,
        "lambda_arns": lambda_arns,
        "gateway": {
            "gateway_id": gateway_info["gateway_id"],
            "gateway_arn": gateway_info["gateway_arn"],
            "gateway_url": gateway_info["gateway_url"],
            "role_arn": gateway_info["role_arn"],
            "gateway_name": GATEWAY_NAME,
        },
        "policy_engine": engine,
        "policies": {
            "permit_all": permit_id,
            **guardrail_policy_ids,
        },
        "target_ids": target_ids,
    }
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    print("\n" + "=" * 65)
    print("Deployment complete!")
    print(f"  Gateway URL:       {gateway_info['gateway_url']}")
    print(f"  Policy Engine ID:  {engine['policyEngineId']}")
    print(f"  Config saved to:   {CONFIG_FILE}")
    print()
    print("  Next: python guardrail_demo.py")
    print("=" * 65)


if __name__ == "__main__":
    main()
