"""
Helper functions for provisioning infrastructure used by the oauth_gateway.py script.

Each function is self-contained: it creates one logical resource group,
prints progress, and returns a dict with the values the caller needs.
All functions are idempotent — safe to re-run if a resource already exists.
"""

import io
import json
import time
import uuid
import zipfile

import boto3

# Gateways report UPDATE_UNSUCCESSFUL rather than FAILED when an update fails, and
# targets add SYNCHRONIZE_UNSUCCESSFUL. These are the only terminal-failure values
# in the GatewayStatus / TargetStatus enums — CREATE_FAILED and DELETE_FAILED are
# harness statuses and never appear here, so testing for them guards nothing.
GATEWAY_FAILURE_STATUSES = ("FAILED", "UPDATE_UNSUCCESSFUL")
TARGET_FAILURE_STATUSES = (*GATEWAY_FAILURE_STATUSES, "SYNCHRONIZE_UNSUCCESSFUL")

# A target with outbound credentials can stop in one of these until authorization
# is completed out of band. This sample uses GATEWAY_IAM_ROLE, so it should not
# reach them — but waiting out the full timeout on a state that will never clear
# by itself is worth naming rather than silently spinning.
TARGET_PENDING_AUTH_STATUSES = (
    "CREATE_PENDING_AUTH",
    "UPDATE_PENDING_AUTH",
    "SYNCHRONIZE_PENDING_AUTH",
)

GATEWAY_POLL_INTERVAL = 10
GATEWAY_POLL_TIMEOUT = 300


# ─────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────


def _poll_until_ready(describe, label, failure_statuses, pending_auth_statuses=()):
    """Poll `describe()` until it reports READY. Raises on failure or timeout.

    Falling through instead of raising was the original behaviour, and it moved
    the error a long way from its cause: a gateway stuck in a failure state was
    polled as "not ready yet" for the full timeout, then the caller carried on to
    create a target and a harness against it and surfaced something unrelated.
    """
    deadline = time.monotonic() + GATEWAY_POLL_TIMEOUT
    while True:
        status = describe()
        if status == "READY":
            print(f"  {label} status: {status}")
            return status
        if status in failure_statuses:
            raise RuntimeError(f"{label} entered terminal status {status}")
        if status in pending_auth_statuses:
            raise RuntimeError(
                f"{label} is {status}: it needs an out-of-band authorization that this sample does not perform."
            )
        if time.monotonic() > deadline:
            raise TimeoutError(f"{label} not READY after {GATEWAY_POLL_TIMEOUT}s (last status: {status})")
        time.sleep(GATEWAY_POLL_INTERVAL)


def iter_paginated(client, operation, result_key, **kwargs):
    """Yield every item from a paginated list operation.

    ListHarnesses, ListGateways and ListGatewayTargets all return a nextToken,
    and reading only the first page has two distinct consequences here: a lookup
    after ConflictException fails with "conflict but not found" even though the
    resource plainly exists, and cleanup reports "not found" and walks past a
    resource it created — leaking it, and in the harness case leaking its managed
    memory with it. Neither failure mentions pagination, so both are hard to trace.
    """
    for page in client.get_paginator(operation).paginate(**kwargs):
        yield from page.get(result_key, [])


def _iter_user_pools(cog):
    """Yield every Cognito user pool in the account, following pagination.

    A single ListUserPools page caps out at 60 pools. In an account with more
    than that, an existing pool on a later page looked absent — so the caller
    created a duplicate, and cleanup left the original behind.
    """
    for page in cog.get_paginator("list_user_pools").paginate(PaginationConfig={"PageSize": 60}):
        yield from page.get("UserPools", [])


def _find_pool_by_name(cog, pool_name):
    """Return pool ID if a Cognito user pool with this name exists, else None."""
    for p in _iter_user_pools(cog):
        if p["Name"] == pool_name:
            return p["Id"]
    return None


def _find_client_by_name(cog, pool_id, client_name):
    """Return (client_id, client_secret|None) if an app client exists."""
    for c in cog.list_user_pool_clients(UserPoolId=pool_id, MaxResults=60)["UserPoolClients"]:
        if c["ClientName"] == client_name:
            full = cog.describe_user_pool_client(
                UserPoolId=pool_id,
                ClientId=c["ClientId"],
            )["UserPoolClient"]
            return full["ClientId"], full.get("ClientSecret")
    return None, None


def _ensure_role(iam_c, role_name, trust_doc):
    """Create an IAM role if it doesn't exist. Returns the role ARN."""
    try:
        return iam_c.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=trust_doc,
        )["Role"]["Arn"]
    except iam_c.exceptions.EntityAlreadyExistsException:
        return iam_c.get_role(RoleName=role_name)["Role"]["Arn"]


def _ensure_policy(iam_c, account_id, policy_name, policy_doc):
    """Create a managed policy, or update it if it already exists. Returns the ARN.

    A pre-existing policy from an older run of this sample can carry an outdated
    document, and simply swallowing EntityAlreadyExists left it in force — the
    permissions the caller asked for were never actually granted. Publish a new
    default version instead. IAM allows five versions per policy, so prune the
    oldest non-default one when the limit is reached.
    """
    arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
    try:
        iam_c.create_policy(PolicyName=policy_name, PolicyDocument=policy_doc)
        return arn
    except iam_c.exceptions.EntityAlreadyExistsException:
        pass

    versions = iam_c.list_policy_versions(PolicyArn=arn)["Versions"]
    if len(versions) >= 5:
        oldest = min(
            (v for v in versions if not v["IsDefaultVersion"]),
            key=lambda v: v["CreateDate"],
        )
        iam_c.delete_policy_version(PolicyArn=arn, VersionId=oldest["VersionId"])
    iam_c.create_policy_version(PolicyArn=arn, PolicyDocument=policy_doc, SetAsDefault=True)
    print(f"  Policy {policy_name} already existed — updated to the current document")
    return arn


# ─────────────────────────────────────────────────────────────────────
# Cognito Pool #1 — User Auth (USER_PASSWORD_AUTH)
# ─────────────────────────────────────────────────────────────────────


def create_user_auth_pool(region: str, prefix: str, username: str, password: str) -> dict:
    """Create (or reuse) a Cognito user pool for end-user authentication.

    Returns dict with keys: pool_id, client_id, discovery_url
    """
    cog = boto3.client("cognito-idp", region_name=region)
    pool_name = f"{prefix}-user-pool"
    client_name = f"{prefix}-user-client"

    pool_id = _find_pool_by_name(cog, pool_name)
    if pool_id:
        print(f"  Pool #1 already exists: {pool_id}")
    else:
        pool_id = cog.create_user_pool(
            PoolName=pool_name,
            Policies={"PasswordPolicy": {"MinimumLength": 8}},
            AutoVerifiedAttributes=["email"],
        )["UserPool"]["Id"]
        print(f"  Pool #1 created: {pool_id}")

    client_id, _ = _find_client_by_name(cog, pool_id, client_name)
    if client_id:
        print(f"  Pool #1 client already exists: {client_id}")  # codeql[py/clear-text-logging-sensitive-data]
    else:
        client_id = cog.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName=client_name,
            ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
            GenerateSecret=False,
        )["UserPoolClient"]["ClientId"]
        print(f"  Pool #1 client created: {client_id}")

    try:
        cog.admin_create_user(UserPoolId=pool_id, Username=username, MessageAction="SUPPRESS")
        print(f'  Test user "{username}" created')
    except cog.exceptions.UsernameExistsException:
        print(f'  Test user "{username}" already exists')
    cog.admin_set_user_password(UserPoolId=pool_id, Username=username, Password=password, Permanent=True)

    discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
    return {"pool_id": pool_id, "client_id": client_id, "discovery_url": discovery_url}


# ─────────────────────────────────────────────────────────────────────
# Cognito Pool #2 — M2M (client_credentials + resource server)
# ─────────────────────────────────────────────────────────────────────


def create_m2m_pool(region: str, prefix: str) -> dict:
    """Create (or reuse) a Cognito user pool for machine-to-machine auth.

    Returns dict with keys: pool_id, client_id, client_secret, discovery_url,
      scope, domain_prefix, token_endpoint
    """
    cog = boto3.client("cognito-idp", region_name=region)
    pool_name = f"{prefix}-m2m-pool"
    client_name = f"{prefix}-m2m-client"
    rs_id = f"{prefix}-gateway"
    scope = f"{rs_id}/invoke"

    pool_id = _find_pool_by_name(cog, pool_name)
    if pool_id:
        print(f"  Pool #2 already exists: {pool_id}")
    else:
        pool_id = cog.create_user_pool(
            PoolName=pool_name,
            Policies={"PasswordPolicy": {"MinimumLength": 8}},
        )["UserPool"]["Id"]
        print(f"  Pool #2 created: {pool_id}")

    try:
        cog.create_resource_server(
            UserPoolId=pool_id,
            Identifier=rs_id,
            Name="Gateway Resource Server",
            Scopes=[{"ScopeName": "invoke", "ScopeDescription": "Invoke gateway tools"}],
        )
        print(f"  Resource server created, scope: {scope}")
    except Exception as e:
        if "already exists" in str(e).lower():
            print(f"  Resource server already exists, scope: {scope}")
        else:
            raise

    desc = cog.describe_user_pool(UserPoolId=pool_id)["UserPool"]
    domain_prefix = desc.get("Domain")
    if domain_prefix:
        print(f"  Domain already exists: {domain_prefix}")
    else:
        domain_prefix = f"{prefix}-{uuid.uuid4().hex[:8]}"
        cog.create_user_pool_domain(Domain=domain_prefix, UserPoolId=pool_id)
        print(f"  Domain created: {domain_prefix}")
    token_endpoint = f"https://{domain_prefix}.auth.{region}.amazoncognito.com/oauth2/token"

    client_id, client_secret = _find_client_by_name(cog, pool_id, client_name)
    if client_id and client_secret:
        print(f"  Pool #2 client already exists: {client_id}")  # codeql[py/clear-text-logging-sensitive-data]
    else:
        resp = cog.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName=client_name,
            GenerateSecret=True,
            AllowedOAuthFlows=["client_credentials"],
            AllowedOAuthScopes=[scope],
            AllowedOAuthFlowsUserPoolClient=True,
            SupportedIdentityProviders=["COGNITO"],
        )
        client_id = resp["UserPoolClient"]["ClientId"]
        client_secret = resp["UserPoolClient"]["ClientSecret"]
        print(f"  Pool #2 client created: {client_id}")

    discovery_url = f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
    return {
        "pool_id": pool_id,
        "client_id": client_id,
        "client_secret": client_secret,
        "discovery_url": discovery_url,
        "scope": scope,
        "domain_prefix": domain_prefix,
        "token_endpoint": token_endpoint,
    }


# ─────────────────────────────────────────────────────────────────────
# OAuth2 Credential Provider (AgentCore Identity)
# ─────────────────────────────────────────────────────────────────────


def create_credential_provider(
    region: str, prefix: str, discovery_url: str, client_id: str, client_secret: str
) -> dict:
    """Create (or reuse) an OAuth2 credential provider in AgentCore Identity.

    Returns dict with keys: name, arn
    """
    ac = boto3.client("bedrock-agentcore-control", region_name=region)
    name = f"{prefix}-m2m-provider"

    # Only "not found" means "go ahead and create it". The original bare `except:
    # pass` also swallowed AccessDenied and throttling, so a permissions problem
    # looked like an absent provider and resurfaced as a confusing create failure.
    # Anything unexpected still falls through to the create below — same behaviour
    # as before — but it now says what went wrong first.
    try:
        existing = ac.get_oauth2_credential_provider(name=name)
        arn = existing["credentialProviderArn"]
        print(f"  Credential provider already exists: {arn}")
        return {"name": name, "arn": arn}
    except ac.exceptions.ResourceNotFoundException:
        pass
    except Exception as e:  # noqa: BLE001 - fall through to create, but not silently
        print(f"  Could not check for an existing credential provider ({e}); attempting to create")

    resp = ac.create_oauth2_credential_provider(
        name=name,
        credentialProviderVendor="CustomOauth2",
        oauth2ProviderConfigInput={
            "customOauth2ProviderConfig": {
                "oauthDiscovery": {"discoveryUrl": discovery_url},
                "clientId": client_id,
                "clientSecret": client_secret,
            }
        },
    )
    arn = resp["credentialProviderArn"]
    print(f"  Credential provider created: {arn}")
    return {"name": name, "arn": arn}


# ─────────────────────────────────────────────────────────────────────
# Lambda Function
# ─────────────────────────────────────────────────────────────────────


def deploy_lambda(region: str, prefix: str) -> dict:
    """Deploy (or reuse) the order-management Lambda function.

    Returns dict with keys: function_name, function_arn, role_name
    """
    # Resolve path to lambda_function_code.py relative to this file
    import os

    lambda_code_path = os.path.join(os.path.dirname(__file__), "lambda_function_code.py")

    iam_c = boto3.client("iam")
    lam = boto3.client("lambda", region_name=region)

    role_name = f"{prefix}-lambda-role"
    trust = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    )
    role_arn = _ensure_role(iam_c, role_name, trust)
    try:
        iam_c.attach_role_policy(
            RoleName=role_name,
            PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole",
        )
    except Exception as e:  # noqa: BLE001 - already attached is fine; anything else must be visible
        # Silently passing here hid a genuine failure to grant the Lambda its
        # logging permissions, which then showed up only as missing CloudWatch logs.
        print(f"  Could not attach AWSLambdaBasicExecutionRole ({e})")
    print(f"  Role: {role_name}")

    fn_name = f"{prefix}-order-mgmt"

    try:
        fn_arn = lam.get_function(FunctionName=fn_name)["Configuration"]["FunctionArn"]
        print(f"  Lambda already exists: {fn_name}")
    except lam.exceptions.ResourceNotFoundException:
        print("  Waiting 10 s for IAM propagation...")
        time.sleep(10)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(lambda_code_path, "lambda_function.py")
        buf.seek(0)

        fn_arn = lam.create_function(
            FunctionName=fn_name,
            Runtime="python3.12",
            Role=role_arn,
            Handler="lambda_function.lambda_handler",
            Code={"ZipFile": buf.read()},
            Description="Order management for AgentCore Gateway",
            Timeout=30,
        )["FunctionArn"]
        print(f"  Lambda created: {fn_name}")

    lam.get_waiter("function_active_v2").wait(FunctionName=fn_name)
    print("  Lambda is Active")
    return {"function_name": fn_name, "function_arn": fn_arn, "role_name": role_name}


# ─────────────────────────────────────────────────────────────────────
# Gateway + Lambda target
# ─────────────────────────────────────────────────────────────────────


def create_gateway_with_lambda_target(
    region: str,
    prefix: str,
    account_id: str,
    discovery_url: str,
    allowed_client: str,
    allowed_scope: str,
    lambda_arn: str,
    lambda_function_name: str,
) -> dict:
    """Create (or reuse) an AgentCore Gateway with CUSTOM_JWT inbound auth and Lambda target.

    Returns dict with keys: gateway_id, gateway_arn, gateway_url, target_id, role_name, policy_name
    """
    iam_c = boto3.client("iam")
    ac = boto3.client("bedrock-agentcore-control", region_name=region)

    gw_role_name = f"{prefix}-gateway-role"
    trust = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    )
    gw_role_arn = _ensure_role(iam_c, gw_role_name, trust)
    print(f"  Gateway role: {gw_role_name}")

    gw_policy_name = f"{prefix}-gw-lambda-policy"
    policy_doc = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "lambda:InvokeFunction",
                    "Resource": f"arn:aws:lambda:{region}:{account_id}:function:{lambda_function_name}",
                }
            ],
        }
    )
    gw_policy_arn = _ensure_policy(iam_c, account_id, gw_policy_name, policy_doc)
    iam_c.attach_role_policy(RoleName=gw_role_name, PolicyArn=gw_policy_arn)

    gateway_name = f"{prefix}-gateway"
    try:
        print("  Waiting 10 s for IAM propagation...")
        time.sleep(10)
        gw = ac.create_gateway(
            name=gateway_name,
            protocolType="MCP",
            roleArn=gw_role_arn,
            authorizerType="CUSTOM_JWT",
            authorizerConfiguration={
                "customJWTAuthorizer": {
                    "discoveryUrl": discovery_url,
                    "allowedClients": [allowed_client],
                    "allowedScopes": [allowed_scope],
                }
            },
        )
        gw_id = gw["gatewayId"]
        gw_arn = gw["gatewayArn"]
        gw_url = gw.get("gatewayUrl", "")
        print(f"  Gateway created: {gw_id}")
    except ac.exceptions.ConflictException:
        gw_id = None
        # ListGateways already reports `name`, so match on the list entry and only
        # describe the one that matched — the original described every gateway in
        # the account to find one, which on a busy account is a lot of calls and
        # risks throttling during what is meant to be a recovery path.
        for g in iter_paginated(ac, "list_gateways", "items"):
            if g.get("name") == gateway_name:
                info = ac.get_gateway(gatewayIdentifier=g["gatewayId"])
                gw_id = info["gatewayId"]
                gw_arn = info.get("gatewayArn", "")
                gw_url = info.get("gatewayUrl", "")
                break
        if not gw_id:
            raise RuntimeError(f"Gateway {gateway_name} conflict but not found")
        print(f"  Gateway already exists: {gw_id}")

    print("  Waiting for gateway READY...")
    _poll_until_ready(
        lambda: ac.get_gateway(gatewayIdentifier=gw_id)["status"],
        f"Gateway {gw_id}",
        GATEWAY_FAILURE_STATUSES,
    )

    tool_schemas = [
        {
            "name": "get_order",
            "description": "Look up an order by ID. Returns item, status, amount.",
            "inputSchema": {
                "type": "object",
                "properties": {"orderId": {"type": "string", "description": "e.g. ORD-001"}},
                "required": ["orderId"],
            },
        },
        {
            "name": "update_order_status",
            "description": "Update the status of an existing order.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "orderId": {"type": "string"},
                    "status": {"type": "string"},
                },
                "required": ["orderId", "status"],
            },
        },
    ]
    target_name = f"{prefix}-lambda-target"
    try:
        tgt = ac.create_gateway_target(
            gatewayIdentifier=gw_id,
            name=target_name,
            targetConfiguration={
                "mcp": {
                    "lambda": {
                        "lambdaArn": lambda_arn,
                        "toolSchema": {"inlinePayload": tool_schemas},
                    }
                }
            },
            credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
        )
        tgt_id = tgt["targetId"]
        print(f"  Target created: {tgt_id}")
    except ac.exceptions.ConflictException:
        tgt_id = None
        # Match on the name that actually conflicted. The original took whichever
        # target came back first, so a gateway carrying any other target could hand
        # back the wrong id — and the poll below would then wait on a resource this
        # script never created.
        for t in iter_paginated(ac, "list_gateway_targets", "items", gatewayIdentifier=gw_id):
            if t.get("name") == target_name:
                tgt_id = t["targetId"]
                break
        if not tgt_id:
            raise RuntimeError(f"Target {target_name} conflict but not found")
        print(f"  Target already exists: {tgt_id}")

    print("  Waiting for target READY...")
    _poll_until_ready(
        lambda: ac.get_gateway_target(gatewayIdentifier=gw_id, targetId=tgt_id)["status"],
        f"Target {tgt_id}",
        TARGET_FAILURE_STATUSES,
        TARGET_PENDING_AUTH_STATUSES,
    )

    gw_full = ac.get_gateway(gatewayIdentifier=gw_id)
    return {
        "gateway_id": gw_id,
        "gateway_arn": gw_full.get("gatewayArn", gw_arn),
        "gateway_url": gw_full.get("gatewayUrl", gw_url),
        "target_id": tgt_id,
        "role_name": gw_role_name,
        "policy_name": gw_policy_name,
    }


# ─────────────────────────────────────────────────────────────────────
# Harness execution role
# ─────────────────────────────────────────────────────────────────────


def create_harness_execution_role(region: str, prefix: str, account_id: str) -> dict:
    """Create (or reuse) the IAM execution role the harness assumes at runtime.

    Returns dict with keys: role_arn, role_name, policy_name
    """
    iam_c = boto3.client("iam")
    role_name = f"{prefix}-harness-role"
    trust = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    )
    role_arn = _ensure_role(iam_c, role_name, trust)
    print(f"  Role: {role_name} (already exists or created)")

    policy_name = f"{prefix}-harness-policy"
    policy_doc = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "Bedrock",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock:InvokeModel",
                        "bedrock:InvokeModelWithResponseStream",
                    ],
                    "Resource": [
                        "arn:aws:bedrock:*::foundation-model/*",
                        f"arn:aws:bedrock:{region}:{account_id}:*",
                    ],
                },
                {
                    "Sid": "Gateway",
                    "Effect": "Allow",
                    "Action": "bedrock-agentcore:InvokeGateway",
                    "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/*",
                },
                {
                    "Sid": "OAuth2TokenVault",
                    "Effect": "Allow",
                    "Action": "bedrock-agentcore:GetResourceOauth2Token",
                    "Resource": [
                        f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default",
                        f"arn:aws:bedrock-agentcore:{region}:{account_id}:token-vault/default/oauth2credentialprovider/*",
                        f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default",
                        f"arn:aws:bedrock-agentcore:{region}:{account_id}:workload-identity-directory/default/workload-identity/*",
                    ],
                },
                {
                    "Sid": "OAuth2Secret",
                    "Effect": "Allow",
                    "Action": "secretsmanager:GetSecretValue",
                    "Resource": f"arn:aws:secretsmanager:{region}:{account_id}:secret:bedrock-agentcore-identity!default/oauth2/*",
                },
                {
                    "Sid": "WorkloadIdentity",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetWorkloadAccessToken",
                        "bedrock-agentcore:GetWorkloadAccessTokenForJWT",
                    ],
                    "Resource": ["*"],
                },
                {
                    # Creating a harness auto-provisions a managed memory
                    # (harness_<HarnessName>_*), and the very first InvokeHarness
                    # reads it. Without these actions that call fails with
                    # AccessDeniedException on ListEvents before the agent ever
                    # runs. Enumerated rather than wildcarded, matching
                    # utils/iam.py, so the role cannot delete stores it only reads.
                    "Sid": "Memory",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:GetMemory",
                        "bedrock-agentcore:DeleteMemory",
                        "bedrock-agentcore:RetrieveMemoryRecords",
                        "bedrock-agentcore:CreateEvent",
                        "bedrock-agentcore:GetEvent",
                        "bedrock-agentcore:ListEvents",
                    ],
                    "Resource": f"arn:aws:bedrock-agentcore:{region}:{account_id}:memory/*",
                },
                {
                    # CreateMemory and ListMemories are account-scoped — neither
                    # takes a memory ID, so neither can be granted on a memory/*
                    # resource: IAM evaluates that to an implicit deny. Verified
                    # with iam:SimulateCustomPolicy, which reports them allowed
                    # only against "*".
                    "Sid": "MemoryAccountScoped",
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:CreateMemory",
                        "bedrock-agentcore:ListMemories",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "EcrPublic",
                    "Effect": "Allow",
                    "Action": [
                        "ecr-public:GetAuthorizationToken",
                        "sts:GetServiceBearerToken",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "CloudWatch",
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "logs:DescribeLogGroups",
                        "logs:DescribeLogStreams",
                    ],
                    "Resource": f"arn:aws:logs:{region}:{account_id}:log-group:/aws/bedrock-agentcore/*",
                },
                {
                    "Sid": "XRay",
                    "Effect": "Allow",
                    "Action": [
                        "xray:PutTraceSegments",
                        "xray:PutTelemetryRecords",
                        "xray:GetSamplingRules",
                        "xray:GetSamplingTargets",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "CWMetrics",
                    "Effect": "Allow",
                    "Action": "cloudwatch:PutMetricData",
                    "Resource": "*",
                    "Condition": {"StringEquals": {"cloudwatch:namespace": "bedrock-agentcore"}},
                },
            ],
        }
    )
    policy_arn = _ensure_policy(iam_c, account_id, policy_name, policy_doc)
    iam_c.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)
    print(f"  Policy: {policy_name}")
    print("  Waiting 15 s for IAM propagation...")
    time.sleep(15)
    return {"role_arn": role_arn, "role_name": role_name, "policy_name": policy_name}


# ─────────────────────────────────────────────────────────────────────
# Cleanup — discover-and-delete all resources by name
# ─────────────────────────────────────────────────────────────────────


def cleanup_all(region: str, prefix: str):
    """Delete every resource created by this script, in reverse order.

    Discovers resources by name so it works even after a fresh Python session.
    Skips gracefully if a resource was never created.
    """
    account_id = boto3.client("sts", region_name=region).get_caller_identity()["Account"]
    cog = boto3.client("cognito-idp", region_name=region)
    lam = boto3.client("lambda", region_name=region)
    iam_c = boto3.client("iam")
    ac = boto3.client("bedrock-agentcore-control", region_name=region)

    harness_name = f"{prefix}-harness".replace("-", "_")
    gateway_name = f"{prefix}-gateway"
    fn_name = f"{prefix}-order-mgmt"
    cred_name = f"{prefix}-m2m-provider"
    lambda_role = f"{prefix}-lambda-role"
    gw_role = f"{prefix}-gateway-role"
    harness_role = f"{prefix}-harness-role"
    gw_policy = f"{prefix}-gw-lambda-policy"
    harness_policy = f"{prefix}-harness-policy"
    pool1_name = f"{prefix}-user-pool"
    pool2_name = f"{prefix}-m2m-pool"

    deleted, skipped = [], []

    def ok(m):
        deleted.append(m)
        print(f"  ✓ {m}")

    def skip(m):
        skipped.append(m)
        print(f"  - {m}")

    def _wait_gone(check_fn, retries=24, delay=5, **kwargs):
        """Poll until `check_fn` raises (i.e. the resource is gone). True if it went.

        Returns False on timeout instead of nothing: the caller used to print
        "deleted" unconditionally after this returned, so a resource still
        DELETING after two minutes was reported as successfully removed.
        """
        for _ in range(retries):
            try:
                check_fn(**kwargs)
                time.sleep(delay)
            except Exception:  # noqa: BLE001 - the describe failing is how "gone" is reported
                return True
        return False

    # 1. Harness
    print("\n[1/7] Harness")
    try:
        matches = [h for h in iter_paginated(ac, "list_harnesses", "harnesses") if h.get("harnessName") == harness_name]
        if matches:
            hid = matches[0]["harnessId"]
            ac.delete_harness(harnessId=hid)
            ok(f"Harness {hid} delete initiated")
            # Deleting the harness is also what releases the managed memory it
            # auto-provisioned (harness_<name>_*): the memory cannot be deleted
            # directly — DeleteMemory rejects it as managed — and it only
            # disappears once this delete finishes. So waiting here is what keeps
            # the memory from being left behind.
            if _wait_gone(ac.get_harness, harnessId=hid):
                ok("Harness deleted")
            else:
                skip(f"Harness {hid} still deleting — its managed memory may linger briefly")
        else:
            skip("Harness not found")
    except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
        skip(f"Harness: {e}")

    # 2. Gateway + targets
    print("\n[2/7] Gateway + targets")
    try:
        gws = [g for g in iter_paginated(ac, "list_gateways", "items") if g.get("name") == gateway_name]
        if gws:
            gid = gws[0]["gatewayId"]
            for t in iter_paginated(ac, "list_gateway_targets", "items", gatewayIdentifier=gid):
                tid = t["targetId"]
                ac.delete_gateway_target(gatewayIdentifier=gid, targetId=tid)
                if _wait_gone(ac.get_gateway_target, gatewayIdentifier=gid, targetId=tid):
                    ok(f"Target {tid} deleted")
                else:
                    skip(f"Target {tid} still deleting")
            ac.delete_gateway(gatewayIdentifier=gid)
            if _wait_gone(ac.get_gateway, gatewayIdentifier=gid):
                ok("Gateway deleted")
            else:
                skip(f"Gateway {gid} still deleting")
        else:
            skip("Gateway not found")
    except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
        skip(f"Gateway: {e}")

    # 3. Credential provider
    print("\n[3/7] Credential provider")
    try:
        ac.get_oauth2_credential_provider(name=cred_name)
        ac.delete_oauth2_credential_provider(name=cred_name)
        ok(f"{cred_name} deleted")
    except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
        skip("Credential provider not found" if "not found" in str(e).lower() else str(e))

    # 4. Lambda
    print("\n[4/7] Lambda")
    try:
        lam.get_function(FunctionName=fn_name)
        lam.delete_function(FunctionName=fn_name)
        ok(f"{fn_name} deleted")
    except lam.exceptions.ResourceNotFoundException:
        skip("Lambda not found")
    except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
        skip(f"Lambda: {e}")

    # 5. IAM roles & policies
    print("\n[5/7] IAM roles & policies")

    def _del_role(rname, policy_names=None):
        try:
            iam_c.get_role(RoleName=rname)
        except iam_c.exceptions.NoSuchEntityException:
            skip(f"Role {rname} not found")
            return
        try:
            for p in iam_c.list_attached_role_policies(RoleName=rname)["AttachedPolicies"]:
                iam_c.detach_role_policy(RoleName=rname, PolicyArn=p["PolicyArn"])
        except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
            # A failure here blocks the DeleteRole below, so name it: otherwise the
            # role just appears to "still be in use" for no stated reason.
            skip(f"Could not detach policies from {rname}: {e}")
        for pn in policy_names or []:
            parn = f"arn:aws:iam::{account_id}:policy/{pn}"
            try:
                # DeletePolicy refuses to run while non-default versions exist, so
                # a policy that was updated by a re-run cannot be deleted until
                # they are pruned. Without this the policy silently survived
                # cleanup, and the next run inherited it.
                for v in iam_c.list_policy_versions(PolicyArn=parn)["Versions"]:
                    if not v["IsDefaultVersion"]:
                        iam_c.delete_policy_version(PolicyArn=parn, VersionId=v["VersionId"])
            except iam_c.exceptions.NoSuchEntityException:
                pass
            except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
                skip(f"Could not prune versions of policy {pn}: {e}")
            try:
                iam_c.delete_policy(PolicyArn=parn)
                ok(f"Policy {pn} deleted")
            except iam_c.exceptions.NoSuchEntityException:
                skip(f"Policy {pn} not found")
            except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
                # This used to pass silently, so a policy left behind by cleanup was
                # invisible — and it then blocked the next run with EntityAlreadyExists.
                skip(f"Policy {pn}: {e}")
        try:
            iam_c.delete_role(RoleName=rname)
            ok(f"Role {rname} deleted")
        except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
            skip(f"Role {rname}: {e}")

    _del_role(lambda_role)
    _del_role(gw_role, [gw_policy])
    _del_role(harness_role, [harness_policy])

    # 6. Cognito Pool #2
    print("\n[6/7] Cognito Pool #2")
    try:
        m = [p for p in _iter_user_pools(cog) if p["Name"] == pool2_name]
        if m:
            pid = m[0]["Id"]
            try:
                d = cog.describe_user_pool(UserPoolId=pid)["UserPool"].get("Domain")
                if d:
                    cog.delete_user_pool_domain(Domain=d, UserPoolId=pid)
                    ok(f"Domain {d} deleted")
            except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
                # The pool delete below fails while a domain is still attached, so
                # swallowing this produced a confusing "pool still in use" error.
                skip(f"Could not delete the domain on pool {pid}: {e}")
            cog.delete_user_pool(UserPoolId=pid)
            ok(f"Pool #2 {pid} deleted")
        else:
            skip("Pool #2 not found")
    except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
        skip(f"Pool #2: {e}")

    # 7. Cognito Pool #1
    print("\n[7/7] Cognito Pool #1")
    try:
        m = [p for p in _iter_user_pools(cog) if p["Name"] == pool1_name]
        if m:
            pid = m[0]["Id"]
            cog.delete_user_pool(UserPoolId=pid)
            ok(f"Pool #1 {pid} deleted")
        else:
            skip("Pool #1 not found")
    except Exception as e:  # noqa: BLE001 - cleanup must continue regardless
        skip(f"Pool #1: {e}")

    print(f"\n{'=' * 50}")
    print(f"Deleted {len(deleted)} resources, skipped {len(skipped)}")
    # "Cleanup complete!" used to print even when items were skipped, which reads
    # as "nothing left behind" when something may well have been. A skip is
    # normal on a partial run (a resource that was never created), so this is not
    # an error — but it does need to be visible enough to check.
    if skipped:
        print("⚠️  Cleanup finished with skips — review the '-' lines above:")
        for m in skipped:
            print(f"     - {m}")
    else:
        print("✅ Cleanup complete!")
    return {"deleted": deleted, "skipped": skipped}
