"""
AgentCore payments — Tutorial Utilities

Only contains what the AgentCore SDK does NOT provide:
- Environment loading and validation
- IAM role assumption
- Async resource polling (wait_for_status)
- Idempotent resource creation
- Cognito setup (for Gateway integration)
- Config persistence across tutorials (via .env)
- Observability setup (vended logs + X-Ray)
- Privy-specific helpers
- Display helpers
"""

import json
import os
import time
import uuid

import boto3
import botocore.exceptions
from dotenv import load_dotenv


# ── Fixed IAM Role Names ────────────────────────────────────────
CONTROL_PLANE_ROLE = "AgentCorePaymentsControlPlaneRole"
MANAGEMENT_ROLE = "AgentCorePaymentsManagementRole"
PROCESS_PAYMENT_ROLE = "AgentCorePaymentsProcessPaymentRole"
RESOURCE_RETRIEVAL_ROLE = "AgentCorePaymentsResourceRetrievalRole"


# ═════════════════════════════════════════════════════════════════
# Environment
# ═════════════════════════════════════════════════════════════════


def load_payment_env(env_file=".env"):
    """Load .env file and return config dict."""
    load_dotenv(env_file, override=True)
    return {
        "region": os.environ.get("AWS_REGION", "us-west-2"),
        "cp_endpoint": os.environ.get(
            "PAYMENTS_CP_ENDPOINT",
            f"https://bedrock-agentcore-control.{os.environ.get('AWS_REGION', 'us-west-2')}.amazonaws.com",
        ),
        "dp_endpoint": os.environ.get(
            "PAYMENTS_DP_ENDPOINT",
            f"https://bedrock-agentcore.{os.environ.get('AWS_REGION', 'us-west-2')}.amazonaws.com",
        ),
        "cred_endpoint": os.environ.get(
            "CREDENTIAL_PROVIDER_ENDPOINT",
            os.environ.get(
                "PAYMENTS_CP_ENDPOINT",
                f"https://bedrock-agentcore-control.{os.environ.get('AWS_REGION', 'us-west-2')}.amazonaws.com",
            ),
        ),
    }


def require_env(key):
    """Get required environment variable or raise with a clear message."""
    val = os.environ.get(key, "").strip()
    if not val or val.startswith("<"):
        raise ValueError(f"Missing or placeholder value for {key} in .env")
    return val


# ═════════════════════════════════════════════════════════════════
# IAM Role Assumption
# ═════════════════════════════════════════════════════════════════


def assume_role(session, role_arn, session_name="tutorial-session"):
    """Assume an IAM role and return a new boto3 Session.

    Verifies the assumed identity immediately. Raises on failure.

    Args:
        session: Existing boto3.Session (used to call STS).
        role_arn: Full ARN of the role to assume.
        session_name: STS session name.

    Returns:
        boto3.Session with temporary credentials.
    """
    sts = session.client("sts")
    creds = sts.assume_role(RoleArn=role_arn, RoleSessionName=session_name)["Credentials"]

    new_session = boto3.Session(
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretAccessKey"],
        aws_session_token=creds["SessionToken"],
        region_name=session.region_name,
    )

    assumed_arn = new_session.client("sts").get_caller_identity()["Arn"]
    print(f"  Assumed: {assumed_arn}")
    return new_session


# ═════════════════════════════════════════════════════════════════
# IAM Role Setup (creates roles inline — no external scripts needed)
# ═════════════════════════════════════════════════════════════════

# Role definitions — permissions for each persona.
# ProcessPaymentRole includes read actions the Strands plugin needs at runtime.
#
# These roles cover AgentCore payments operations only.
# Infrastructure actions (CloudWatch, X-Ray, Cognito, Gateway) run under
# the caller's default AWS credentials — not these roles.
# See Tutorial 00 Step 8b and Tutorial 04 prerequisites for details.
PAYMENT_ROLE_DEFINITIONS = {
    CONTROL_PLANE_ROLE: {
        "description": "AgentCore payments: control plane operations",
        "trust": "account",
        "allow": [
            "bedrock-agentcore:CreatePaymentManager",
            "bedrock-agentcore:GetPaymentManager",
            "bedrock-agentcore:ListPaymentManagers",
            "bedrock-agentcore:DeletePaymentManager",
            "bedrock-agentcore:UpdatePaymentManager",
            "bedrock-agentcore:CreatePaymentConnector",
            "bedrock-agentcore:GetPaymentConnector",
            "bedrock-agentcore:ListPaymentConnectors",
            "bedrock-agentcore:DeletePaymentConnector",
            "bedrock-agentcore:UpdatePaymentConnector",
            "bedrock-agentcore:CreatePaymentCredentialProvider",
            "bedrock-agentcore:GetPaymentCredentialProvider",
            "bedrock-agentcore:ListPaymentCredentialProviders",
            "bedrock-agentcore:DeletePaymentCredentialProvider",
            "bedrock-agentcore:UpdatePaymentCredentialProvider",
            "bedrock-agentcore:CreateTokenVault",
            "bedrock-agentcore:AllowVendedLogDeliveryForResource",
        ],
        "pass_role": True,
        "secrets_manager_write": True,
    },
    MANAGEMENT_ROLE: {
        "description": "AgentCore payments: data plane management (instruments, sessions)",
        "trust": "account",
        "allow": [
            "bedrock-agentcore:CreatePaymentInstrument",
            "bedrock-agentcore:GetPaymentInstrument",
            "bedrock-agentcore:ListPaymentInstruments",
            "bedrock-agentcore:DeletePaymentInstrument",
            "bedrock-agentcore:GetPaymentInstrumentBalance",
            "bedrock-agentcore:CreatePaymentSession",
            "bedrock-agentcore:GetPaymentSession",
            "bedrock-agentcore:ListPaymentSessions",
            "bedrock-agentcore:UpdatePaymentSession",
        ],
        "deny": ["bedrock-agentcore:ProcessPayment"],
    },
    PROCESS_PAYMENT_ROLE: {
        "description": "AgentCore payments: agent runtime (ProcessPayment + read queries)",
        "trust": "account",
        "allow": [
            "bedrock-agentcore:ProcessPayment",
            "bedrock-agentcore:GetPaymentInstrument",
            "bedrock-agentcore:GetPaymentInstrumentBalance",
            "bedrock-agentcore:GetPaymentSession",
        ],
    },
    RESOURCE_RETRIEVAL_ROLE: {
        "description": "AgentCore payments: service role for credential retrieval",
        "trust": "service",
        "allow": [
            "bedrock-agentcore:GetWorkloadAccessToken",
            "bedrock-agentcore:CreateWorkloadIdentity",
            "bedrock-agentcore:GetResourcePaymentToken",
        ],
        "secrets_manager": True,
    },
}


def setup_payment_roles(region=None):
    """Create the 4 IAM roles needed for AgentCore payments tutorials.

    Checks if each role exists first. Creates only what's missing.
    Idempotent — safe to run multiple times.

    Note: Creates IAM roles that persist until explicitly deleted. Run the
    cleanup cell in Tutorial 00 to remove them when no longer needed.

    Args:
        region: AWS region. Defaults to AWS_REGION env var or us-west-2.

    Returns:
        Dict mapping short names to role ARNs:
        {
            "control_plane": "arn:aws:iam::...:role/AgentCorePaymentsControlPlaneRole",
            "management": "arn:aws:iam::...:role/AgentCorePaymentsManagementRole",
            "process_payment": "arn:aws:iam::...:role/AgentCorePaymentsProcessPaymentRole",
            "resource_retrieval": "arn:aws:iam::...:role/AgentCorePaymentsResourceRetrievalRole",
        }
    """
    region = region or os.environ.get("AWS_REGION", "us-west-2")
    session = boto3.Session(region_name=region)
    sts = session.client("sts")
    iam = session.client("iam")

    identity = sts.get_caller_identity()
    account_id = identity["Account"]
    caller_arn = identity["Arn"]

    # Resolve caller to base role ARN (handles assumed-role format)
    caller_role_arn = None
    if ":assumed-role/" in caller_arn:
        role_name = caller_arn.split(":")[-1].split("/")[1]
        try:
            caller_role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
        except Exception:
            caller_role_arn = f"arn:aws:iam::{account_id}:role/{role_name}"

    # Build trust policies
    account_principal = f"arn:aws:iam::{account_id}:root"
    principals = [account_principal]
    if caller_role_arn:
        principals.append(caller_role_arn)

    account_trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"AWS": principals},
                "Action": "sts:AssumeRole",
            }
        ],
    }
    service_trust = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {"StringEquals": {"aws:SourceAccount": account_id}},
            },
        ],
    }

    created_count = 0
    role_arns = {}
    short_names = {
        CONTROL_PLANE_ROLE: "control_plane",
        MANAGEMENT_ROLE: "management",
        PROCESS_PAYMENT_ROLE: "process_payment",
        RESOURCE_RETRIEVAL_ROLE: "resource_retrieval",
    }

    for role_name, config in PAYMENT_ROLE_DEFINITIONS.items():
        short = short_names[role_name]
        trust = service_trust if config["trust"] == "service" else account_trust

        # Check if role exists
        try:
            existing = iam.get_role(RoleName=role_name)
            role_arn = existing["Role"]["Arn"]
            role_arns[short] = role_arn
            # Update trust policy and permissions (idempotent)
            iam.update_assume_role_policy(
                RoleName=role_name,
                PolicyDocument=json.dumps(trust),
            )
        except iam.exceptions.NoSuchEntityException:
            # Create the role
            resp = iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(trust),
                Description=config["description"],
            )
            role_arn = resp["Role"]["Arn"]
            role_arns[short] = role_arn
            created_count += 1

        # Attach allow policy
        allow_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "Allow",
                    "Effect": "Allow",
                    "Action": config["allow"],
                    "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*",
                }
            ],
        }

        # Add SecretsManager write access for ControlPlaneRole
        # Resource must be "*" because CreateSecret targets secrets that don't exist yet
        if config.get("secrets_manager_write"):
            allow_policy["Statement"].append(
                {
                    "Sid": "SecretsManagerWrite",
                    "Effect": "Allow",
                    "Action": [
                        "secretsmanager:CreateSecret",
                        "secretsmanager:PutSecretValue",
                        "secretsmanager:UpdateSecret",
                        "secretsmanager:DeleteSecret",
                        "secretsmanager:TagResource",
                    ],
                    "Resource": "*",
                }
            )

        # Add SecretsManager access for ResourceRetrievalRole
        if config.get("secrets_manager"):
            allow_policy["Statement"].append(
                {
                    "Sid": "SecretsManagerAccess",
                    "Effect": "Allow",
                    "Action": ["secretsmanager:GetSecretValue"],
                    "Resource": f"arn:aws:secretsmanager:*:{account_id}:secret:*",
                }
            )
            allow_policy["Statement"].append(
                {
                    "Sid": "StsSetContext",
                    "Effect": "Allow",
                    "Action": "sts:SetContext",
                    "Resource": f"arn:aws:sts::{account_id}:self",
                }
            )

        iam.put_role_policy(
            RoleName=role_name,
            PolicyName="AllowPolicy",
            PolicyDocument=json.dumps(allow_policy),
        )

        # Attach deny policy if specified
        if config.get("deny"):
            deny_policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "Deny",
                        "Effect": "Deny",
                        "Action": config["deny"],
                        "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:*",
                    }
                ],
            }
            iam.put_role_policy(
                RoleName=role_name,
                PolicyName="DenyPolicy",
                PolicyDocument=json.dumps(deny_policy),
            )

        # Attach PassRole for ControlPlaneRole
        # Scoped to ResourceRetrievalRole + condition per doc best practice
        if config.get("pass_role"):
            rr_arn = f"arn:aws:iam::{account_id}:role/{RESOURCE_RETRIEVAL_ROLE}"
            iam.put_role_policy(
                RoleName=role_name,
                PolicyName="PassRolePolicy",
                PolicyDocument=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": "iam:PassRole",
                                "Resource": rr_arn,
                                "Condition": {
                                    "StringEquals": {"iam:PassedToService": "bedrock-agentcore.amazonaws.com"}
                                },
                            }
                        ],
                    }
                ),
            )

    # Wait for IAM propagation if new roles were created
    if created_count > 0:
        print(f"  Created {created_count} new role(s). Waiting for IAM propagation...")
        time.sleep(10)

    print(f"  ✅ IAM roles ready ({len(role_arns)} roles)")
    for short, arn in role_arns.items():
        print(f"     {short}: {arn}")

    return role_arns


# ═════════════════════════════════════════════════════════════════
# Async Resource Polling
# ═════════════════════════════════════════════════════════════════


def wait_for_status(client_fn, expected_status, poll_interval=5, timeout=120, **kwargs):
    """Poll a Get* API until the resource reaches expected_status.

    Resolves status from top-level ``status`` or nested ``paymentInstrument.status``.
    Raises TimeoutError if not reached within timeout.
    Raises RuntimeError immediately on terminal failure states (*_FAILED).
    """
    deadline = time.time() + timeout
    while True:
        resp = client_fn(**kwargs)
        status = resp.get("status") or resp.get("paymentInstrument", {}).get("status")
        print(f"   Status: {status}")
        if isinstance(status, str) and status.endswith("_FAILED"):
            raise RuntimeError(f"Resource reached failure state: '{status}'")
        if status == expected_status:
            return resp
        if time.time() >= deadline:
            raise TimeoutError(f"Resource still in '{status}' after {timeout}s")
        time.sleep(poll_interval)


# ═════════════════════════════════════════════════════════════════
# Idempotent Resource Creation
# ═════════════════════════════════════════════════════════════════


def idempotent_create(create_fn, conflict_msg="Resource already exists", **kwargs):
    """Call create_fn; handle ConflictException gracefully.

    Returns the API response on success, or None if the resource already exists.
    """
    try:
        return create_fn(**kwargs)
    except botocore.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] == "ConflictException":
            print(f"  ⚠️  {conflict_msg} — skipping create")
            return None
        raise


# ═════════════════════════════════════════════════════════════════
# Cognito (Gateway integration only)
# ═════════════════════════════════════════════════════════════════


def setup_cognito_user_pool(pool_name="AgentCorePaymentsPool"):
    """Create a Cognito user pool with M2M client for Gateway integration.

    Returns dict with pool_id, client_id, client_secret, token_url.
    """
    session = boto3.Session()
    region = session.region_name
    cognito = boto3.client("cognito-idp", region_name=region)

    pool_resp = cognito.create_user_pool(
        PoolName=pool_name,
        Policies={"PasswordPolicy": {"MinimumLength": 8}},
    )
    pool_id = pool_resp["UserPool"]["Id"]

    domain = pool_id.replace("_", "").lower()
    cognito.create_user_pool_domain(Domain=domain, UserPoolId=pool_id)

    resource_server_id = "agentcore-payments"
    cognito.create_resource_server(
        UserPoolId=pool_id,
        Identifier=resource_server_id,
        Name="AgentCore payments",
        Scopes=[{"ScopeName": "payments", "ScopeDescription": "Payment operations"}],
    )

    client_resp = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName="PaymentsTutorialClient",
        GenerateSecret=True,
        AllowedOAuthFlows=["client_credentials"],
        AllowedOAuthScopes=[f"{resource_server_id}/payments"],
        AllowedOAuthFlowsUserPoolClient=True,
        SupportedIdentityProviders=["COGNITO"],
    )

    token_url = f"https://{domain}.auth.{region}.amazoncognito.com/oauth2/token"
    print(f"Cognito pool created: {pool_id}")

    return {
        "pool_id": pool_id,
        "client_id": client_resp["UserPoolClient"]["ClientId"],
        "client_secret": client_resp["UserPoolClient"]["ClientSecret"],
        "token_url": token_url,
    }


def get_oauth_token(token_url, client_id, client_secret):
    """Exchange client credentials for an OAuth2 access token."""
    import requests

    resp = requests.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ═════════════════════════════════════════════════════════════════
# Config Persistence (via .env — replaces payment_config.json)
# ═════════════════════════════════════════════════════════════════

# Tutorial 00 writes resource IDs into .env. Every downstream tutorial
# reads them with load_dotenv() + os.environ. No JSON, no custom loader.

TUTORIAL_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def save_tutorial_config(config_dict, env_path=None):
    """Write resource IDs from Tutorial 00 into .env for downstream tutorials.

    Appends to the existing .env (which already has provider credentials and
    AWS config). Uses update_env_file() for idempotent upsert.

    Args:
        config_dict: Dict of {ENV_VAR: value} pairs to write.
            Expected keys: PAYMENT_MANAGER_ARN, PAYMENT_MANAGER_ID,
            PAYMENT_CONNECTOR_ID, CREDENTIAL_PROVIDER_ARN, USER_ID,
            INSTRUMENT_ID, WALLET_ADDRESS, SESSION_ID, CREDENTIAL_PROVIDER_TYPE, etc.
        env_path: Path to .env file. Defaults to 00-getting-started/.env.

    Example:
        save_tutorial_config({
            "PAYMENT_MANAGER_ARN": manager_arn,
            "PAYMENT_CONNECTOR_ID": connector_id,
            "USER_ID": "test-user-001",
            "INSTRUMENT_ID": instrument_id,
            "SESSION_ID": session_id,
        })
    """
    path = env_path or TUTORIAL_ENV_FILE
    update_env_file(path, config_dict)
    print(f"  ✅ Tutorial config saved to {os.path.basename(path)}")


def load_tutorial_env(env_path=None):
    """Load .env and return a dict with the standard fields for plugin config.

    Call this at the top of any downstream tutorial (01-07) to get the
    values needed for AgentCorePaymentsPluginConfig.

    Args:
        env_path: Path to .env file. Defaults to 00-getting-started/.env.

    Returns:
        Dict with: payment_manager_arn, user_id, instrument_id, session_id,
        connector_id, region, provider_type, wallet_address.
        For multi-provider setups (Tutorial 07), also includes
        multi_provider=True and instruments/connectors dicts.
        Missing keys are None (not raised).

    Example:
        cfg = load_tutorial_env()
        plugin = AgentCorePaymentsPlugin(config=AgentCorePaymentsPluginConfig(
            payment_manager_arn=cfg["payment_manager_arn"],
            user_id=cfg["user_id"],
            payment_instrument_id=cfg["instrument_id"],
            payment_session_id=cfg["session_id"],
            region=cfg["region"],
        ))
    """
    path = env_path or TUTORIAL_ENV_FILE
    if not os.path.exists(path):
        raise FileNotFoundError(f"{os.path.basename(path)} not found. Run Tutorial 00 first.\n  Expected at: {path}")
    load_dotenv(path, override=True)

    result = {
        "payment_manager_arn": os.environ.get("PAYMENT_MANAGER_ARN"),
        "payment_manager_id": os.environ.get("PAYMENT_MANAGER_ID"),
        "connector_id": os.environ.get("PAYMENT_CONNECTOR_ID"),
        "credential_provider_arn": os.environ.get("CREDENTIAL_PROVIDER_ARN"),
        "user_id": os.environ.get("USER_ID"),
        "instrument_id": os.environ.get("INSTRUMENT_ID"),
        "wallet_address": os.environ.get("WALLET_ADDRESS"),
        "session_id": os.environ.get("SESSION_ID"),
        "region": os.environ.get("AWS_REGION", "us-west-2"),
        "provider_type": os.environ.get("CREDENTIAL_PROVIDER_TYPE"),
    }

    # Multi-provider support (Tutorial 07)
    coinbase_instr = os.environ.get("COINBASE_INSTRUMENT_ID")
    privy_instr = os.environ.get("PRIVY_INSTRUMENT_ID")

    if coinbase_instr and privy_instr:
        result["multi_provider"] = True
        result["instruments"] = {
            "coinbase": {
                "instrument_id": coinbase_instr,
                "connector_id": os.environ.get("COINBASE_CONNECTOR_ID"),
                "wallet_address": os.environ.get("COINBASE_WALLET_ADDRESS"),
            },
            "stripe_privy": {
                "instrument_id": privy_instr,
                "connector_id": os.environ.get("PRIVY_CONNECTOR_ID"),
                "wallet_address": os.environ.get("PRIVY_WALLET_ADDRESS"),
            },
        }
    else:
        result["multi_provider"] = False

    return result


# ═════════════════════════════════════════════════════════════════
# Display Helpers
# ═════════════════════════════════════════════════════════════════


def pp(label, response):
    """Pretty-print an API response, stripping ResponseMetadata."""
    data = {k: v for k, v in response.items() if k != "ResponseMetadata"}
    print(f"\n{'=' * 60}")
    print(f"  {label}")
    print(f"{'=' * 60}")
    print(json.dumps(data, indent=2, default=str))


def print_summary(title, **kwargs):
    """Pretty-print a summary block for notebook output."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")
    for key, value in kwargs.items():
        label = key.replace("_", " ").title()
        print(f"  {label:30s}: {value}")
    print(f"{'=' * 60}\n")


def client_token():
    """Generate idempotency token (>= 33 chars)."""
    return f"{uuid.uuid4()}-{uuid.uuid4().hex[:8]}"


# ═════════════════════════════════════════════════════════════════
# Observability (Vended Logs + Traces)
# ═════════════════════════════════════════════════════════════════


def enable_observability(resource_arn, resource_id, account_id, region="us-west-2", enable_xray_spans=False):
    """Enable CloudWatch vended logs and X-Ray traces for an AgentCore payments resource.

    Creates delivery sources, destinations, and deliveries for both APPLICATION_LOGS
    and TRACES. Optionally configures X-Ray span delivery to CloudWatch Logs.

    **Cost notice:** This function creates CloudWatch log groups, delivery sources,
    delivery destinations, and X-Ray resources that may incur AWS charges. Delete
    the log group ``/aws/vendedlogs/bedrock-agentcore/<manager-id>`` when finished.

    After enabling, any data plane API call (CreateInstrument, ProcessPayment, etc.)
    produces logs and trace data in the configured CloudWatch Log Group.

    Args:
        resource_arn: ARN of the Payment Manager.
        resource_id: Payment Manager ID (short ID).
        account_id: AWS account ID.
        region: AWS region.
        enable_xray_spans: If True, configure X-Ray to deliver spans to CloudWatch Logs.

    Returns:
        Dict with logs_delivery_id and traces_delivery_id.

    Prerequisites:
        The calling role needs these permissions:
        - logs:CreateDelivery, logs:CreateLogGroup, logs:CreateLogStream,
          logs:DeleteDelivery, logs:DeleteDeliveryDestination, logs:DeleteDeliverySource,
          logs:DescribeLogGroups, logs:DescribeResourcePolicies,
          logs:GetDelivery, logs:GetDeliveryDestination, logs:GetDeliverySource,
          logs:PutDeliveryDestination, logs:PutDeliverySource,
          logs:PutLogEvents, logs:PutResourcePolicy, logs:PutRetentionPolicy
        - xray:GetTraceSegmentDestination, xray:ListResourcePolicies,
          xray:PutResourcePolicy, xray:PutTelemetryRecords, xray:PutTraceSegments,
          xray:UpdateTraceSegmentDestination (if enable_xray_spans=True)
        - application-signals:StartDiscovery, cloudtrail:CreateServiceLinkedChannel
        - iam:CreateServiceLinkedRole (for AWSServiceRoleForCloudWatchApplicationSignals)
        - bedrock-agentcore:AllowVendedLogDeliveryForResource
    """
    # Step 1: Allow vended log delivery for the resource
    # This authorizes the Bedrock AgentCore service to publish vended logs to your account.
    # Must be called before creating delivery sources/destinations.
    agentcore_client = boto3.client("bedrock-agentcore-control", region_name=region)
    try:
        agentcore_client.allow_vended_log_delivery_for_resource(resourceArn=resource_arn)
        print(f"  Allowed vended log delivery for {resource_arn}")
    except agentcore_client.exceptions.ConflictException:
        print(f"  Vended log delivery already allowed for {resource_arn}")
    except Exception as e:
        # Non-fatal — some accounts may already have this enabled or the API may not
        # be available in all regions yet.
        print(f"  Note: AllowVendedLogDeliveryForResource returned: {e}")

    logs_client = boto3.client("logs", region_name=region)

    # Step 2: Create log group
    log_group_name = f"/aws/vendedlogs/bedrock-agentcore/{resource_id}"
    try:
        logs_client.create_log_group(logGroupName=log_group_name)
        print(f"  Created log group: {log_group_name}")
    except logs_client.exceptions.ResourceAlreadyExistsException:
        print(f"  Log group already exists: {log_group_name}")

    log_group_arn = f"arn:aws:logs:{region}:{account_id}:log-group:{log_group_name}"

    # X-Ray spans setup
    if enable_xray_spans:
        _setup_xray_spans(logs_client, region)

    # Step 3: Create delivery sources
    print("  Creating delivery sources (APPLICATION_LOGS + TRACES)...")
    logs_src = logs_client.put_delivery_source(
        name=f"{resource_id}-logs-source",
        logType="APPLICATION_LOGS",
        resourceArn=resource_arn,
    )
    traces_src = logs_client.put_delivery_source(
        name=f"{resource_id}-traces-source", logType="TRACES", resourceArn=resource_arn
    )

    # Step 4: Create delivery destinations
    print("  Creating delivery destinations (CWL + XRAY)...")
    logs_dst = logs_client.put_delivery_destination(
        name=f"{resource_id}-logs-destination",
        deliveryDestinationType="CWL",
        deliveryDestinationConfiguration={"destinationResourceArn": log_group_arn},
    )
    traces_dst = logs_client.put_delivery_destination(
        name=f"{resource_id}-traces-destination",
        deliveryDestinationType="XRAY",
    )

    # Step 5: Connect sources to destinations
    print("  Creating deliveries...")
    logs_delivery = logs_client.create_delivery(
        deliverySourceName=logs_src["deliverySource"]["name"],
        deliveryDestinationArn=logs_dst["deliveryDestination"]["arn"],
    )
    traces_delivery = logs_client.create_delivery(
        deliverySourceName=traces_src["deliverySource"]["name"],
        deliveryDestinationArn=traces_dst["deliveryDestination"]["arn"],
    )

    print(f"  ✅ Observability enabled for {resource_id}")
    return {
        "logs_delivery_id": logs_delivery["delivery"]["id"],
        "traces_delivery_id": traces_delivery["delivery"]["id"],
        "log_group_name": log_group_name,
    }


def _setup_xray_spans(logs_client, region):
    """Configure X-Ray to deliver spans to CloudWatch Logs."""
    import json as _json

    logs_client.put_resource_policy(
        policyName="XRaySpansPolicy",
        policyDocument=_json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "XRayAccess",
                        "Effect": "Allow",
                        "Principal": {"Service": "xray.amazonaws.com"},
                        "Action": [
                            "logs:PutLogEvents",
                            "logs:CreateLogGroup",
                            "logs:CreateLogStream",
                        ],
                        "Resource": "arn:aws:logs:*:*:log-group:/aws/vendedlogs/bedrock-agentcore/*",
                    }
                ],
            }
        ),
    )

    xray_client = boto3.client("xray", region_name=region)
    try:
        xray_client.update_trace_segment_destination(Destination="CloudWatchLogs")
    except xray_client.exceptions.InvalidRequestException as e:
        if "already set to CloudWatchLogs" not in str(e):
            raise
        print("  X-Ray already set to CloudWatchLogs")

    # Wait for ACTIVE
    for attempt in range(1, 25):
        resp = xray_client.get_trace_segment_destination()
        destination = resp.get("Destination", {})
        status = (
            destination.get("Status", resp.get("Status", "UNKNOWN"))
            if isinstance(destination, dict)
            else str(destination)
        )
        if status == "ACTIVE":
            print("  ✅ X-Ray trace segment destination ACTIVE")
            return
        time.sleep(5)
    raise RuntimeError("X-Ray trace segment destination did not become ACTIVE")


# ═════════════════════════════════════════════════════════════════
# Privy Helpers (StripePrivy provider only)
# ═════════════════════════════════════════════════════════════════
#
# These helpers automate the parts of Privy setup that have a public API,
# so the tutorial can avoid sending developers through the Privy dashboard
# more than the minimum required. What stays manual:
#   1. Creating the Privy app itself (no public API).
#   2. Adding allowed origins (dashboard-only).
#   3. Running the local Privy reference frontend (browser + localhost).
#
# Everything else — generating the P-256 keypair, registering the key
# quorum, updating .env, verifying consent landed — runs from here.

PRIVY_API_BASE = "https://api.privy.io/v1"


def update_env_file(env_path_or_updates, updates=None):
    """Idempotently upsert key=value pairs into a .env file.

    Creates the file if it doesn't exist. Preserves existing lines, comments,
    and blank lines. Each key in ``updates`` is either replaced in place (if
    already present) or appended in a trailing block.

    Supports two call signatures:
        update_env_file('.env', {'KEY': 'val'})
        update_env_file({'KEY': 'val'})  # defaults to '.env'

    Returns:
        Dict with ``added`` and ``updated`` lists of keys, for reporting.
    """
    if updates is None:
        updates = env_path_or_updates
        env_path = os.path.join(os.path.dirname(__file__), ".env")
    else:
        env_path = env_path_or_updates
    env_path = os.path.abspath(env_path)
    existing_lines = []
    if os.path.exists(env_path):
        with open(env_path) as f:
            existing_lines = f.readlines()

    remaining = dict(updates)
    updated_keys = []
    new_lines = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in remaining:
            new_lines.append(f"{key}={remaining.pop(key)}\n")
            updated_keys.append(key)
        else:
            new_lines.append(line)

    added_keys = list(remaining.keys())
    if added_keys:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines[-1] = new_lines[-1] + "\n"
        if new_lines:
            new_lines.append("\n")
        new_lines.append("# --- Generated by tutorial setup ---\n")
        for key, value in remaining.items():
            new_lines.append(f"{key}={value}\n")

    os.makedirs(os.path.dirname(env_path) or ".", exist_ok=True)
    with open(env_path, "w") as f:
        f.writelines(new_lines)
    try:
        os.chmod(env_path, 0o600)
    except (OSError, NotImplementedError):
        pass

    print(f"  ✅ Updated {env_path}")
    if updated_keys:
        print(f"     replaced: {', '.join(updated_keys)}")
    if added_keys:
        print(f"     added:    {', '.join(added_keys)}")
    return {"updated": updated_keys, "added": added_keys}


def render_frontend_env_local(app_id, app_secret, signer_id, network_mode="testnet"):
    """Build the contents of the Privy reference frontend's ``.env.local`` file.

    Pure string builder — no filesystem access, no shell instructions. The
    notebook prints the returned string for the developer to paste into the
    Privy reference frontend's ``.env.local`` on their local machine.

    Args:
        app_id: Privy app ID (``NEXT_PUBLIC_PRIVY_APP_ID``).
        app_secret: Privy app secret (``PRIVY_APP_SECRET`` — server-only).
        signer_id: Privy key quorum ID (``NEXT_PUBLIC_PRIVY_SIGNER_ID``).
        network_mode: ``testnet`` or ``mainnet``.

    Returns:
        The ``.env.local`` body as a string.
    """
    return (
        f"NEXT_PUBLIC_PRIVY_APP_ID={app_id}\n"
        f"PRIVY_APP_SECRET={app_secret}\n"
        f"NEXT_PUBLIC_PRIVY_SIGNER_ID={signer_id}\n"
        f"NEXT_PUBLIC_NETWORK_MODE={network_mode}\n"
    )


def save_privy_authorization_key(env_path, authorization_id, authorization_private_key):
    """Save Privy authorization key credentials to .env, stripping the wallet-auth: prefix.

    Privy's dashboard displays the authorization private key with a ``wallet-auth:``
    prefix. That prefix is not part of the key itself and must be removed before the
    key is passed to the Bedrock AgentCore ``authorizationPrivateKey`` field —
    Bedrock AgentCore validation rejects the prefixed form.

    Args:
        env_path: Path to the .env file.
        authorization_id: The authorization ID from the Privy dashboard (public
            identifier, safe to log).
        authorization_private_key: The private key as copied from the Privy
            dashboard, with or without the ``wallet-auth:`` prefix. The prefix is
            stripped if present.

    Returns:
        The result of :func:`update_env_file`.
    """
    prefix = "wallet-auth:"
    key = authorization_private_key.strip()
    if key.startswith(prefix):
        key = key[len(prefix) :].strip()
        print("  ℹ️  Stripped 'wallet-auth:' prefix from the private key.")

    return update_env_file(
        env_path,
        {
            "PRIVY_AUTHORIZATION_ID": authorization_id,
            "PRIVY_AUTHORIZATION_PRIVATE_KEY": key,
        },
    )


def verify_privy_signer_on_wallet(app_id, app_secret, wallet_address_or_id, quorum_id):
    """Check whether a key quorum is registered as a signer on a Privy wallet.

    After the end user grants signer access in the Privy reference frontend (Step 7b of the
    main setup notebook), Privy adds the key quorum to the wallet's
    ``additional_signers``. Call this to confirm consent landed before
    attempting ``ProcessPayment`` — missing delegation is the single most
    common cause of ProcessPayment failures with the StripePrivy provider.

    Accepts either a Privy wallet ID (a CUID2 like ``trv721k23pqzjd3pdqmh54o7``)
    or an on-chain wallet address (``0x…`` for EVM, base58 for Solana). Uses
    the right Privy endpoint for each:

    - Wallet ID:  ``GET  /v1/wallets/{wallet_id}``
    - Address:    ``POST /v1/wallets/address``  (body: ``{"address": "…"}``)

    Args:
        app_id: Privy app ID (``PRIVY_APP_ID``).
        app_secret: Privy app secret (``PRIVY_APP_SECRET``).
        wallet_address_or_id: Privy wallet ID or on-chain address.
        quorum_id: Key quorum ID to look for. The Bedrock AgentCore
            ``PRIVY_AUTHORIZATION_ID`` field.

    Returns:
        True if the quorum is present in ``additional_signers``, else False.

    Raises:
        RuntimeError: if Privy returns an unexpected error.
    """
    import re
    import requests

    auth = (app_id, app_secret)
    headers = {"privy-app-id": app_id, "Content-Type": "application/json"}

    # Fetch the wallet object using whichever endpoint fits the input format.
    # Wallet IDs are CUID2 (24 lowercase alphanumeric, starting with a letter).
    # Anything else is treated as an on-chain address.
    is_wallet_id = bool(re.fullmatch(r"[a-z][a-z0-9]{23}", wallet_address_or_id))

    if is_wallet_id:
        resp = requests.get(
            f"{PRIVY_API_BASE}/wallets/{wallet_address_or_id}",
            auth=auth,
            headers=headers,
            timeout=30,
        )
    else:
        resp = requests.post(
            f"{PRIVY_API_BASE}/wallets/address",
            auth=auth,
            headers=headers,
            timeout=30,
            json={"address": wallet_address_or_id},
        )

    if resp.status_code == 404:
        raise RuntimeError(
            f"Privy wallet not found for {wallet_address_or_id!r}. "
            "Check that PRIVY_APP_ID matches the app the wallet was created in, "
            "and that the wallet has been provisioned (Step 7 in the main notebook)."
        )
    if not resp.ok:
        raise RuntimeError(f"Privy wallet fetch failed ({resp.status_code}): {resp.text}")

    wallet = resp.json()
    signers = wallet.get("additional_signers") or wallet.get("additionalSigners") or []
    # Entries can be dicts ({"signer_id": "..."}) or bare strings; handle both.
    signer_ids = {
        (s.get("signer_id") or s.get("id") or s.get("key_quorum_id")) if isinstance(s, dict) else s for s in signers
    }
    return quorum_id in signer_ids
