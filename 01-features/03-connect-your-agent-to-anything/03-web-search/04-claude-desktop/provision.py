#!/usr/bin/env python3
"""
Provision an Amazon Bedrock AgentCore Gateway exposing the managed Web Search
tool as an MCP tool, so an MCP client (e.g. Claude Cowork) can search the web.

What it creates (least privilege):
  1. An IAM service role the Gateway assumes to call the managed web-search
     connector. It grants ONLY:
        - bedrock-agentcore:InvokeWebSearch on the web-search tool ARN
        - bedrock-agentcore:InvokeGateway on the gateway
     The trust policy is scoped with aws:SourceAccount and aws:SourceArn.
  2. (Optional) A Cognito user pool + domain + resource server + app client(s)
     used as the inbound JWT authorizer for the Gateway. This is only created
     when you do NOT pass --auth-discovery-url / --auth-client-id.
  3. The AgentCore Gateway (protocol MCP, inbound auth CUSTOM_JWT).
  4. The web-search connector target on that Gateway.

Auth model:
  - Inbound (client -> gateway): CUSTOM_JWT. The gateway validates a bearer JWT
    against `discoveryUrl` and the `allowedClients` list. Provide your own IdP
    with --auth-discovery-url + --auth-client-id, or let this script create a
    Cognito user pool for you.
  - Outbound (gateway -> web search): GATEWAY_IAM_ROLE (the role from step 1).

Region: set with --region (or AWS_REGION), default us-east-1. Use any region
where the managed Web Search connector is available (check the AWS docs).

Usage:
  python provision.py create
  python provision.py create --auth-discovery-url <url> --auth-client-id <id>
  python provision.py delete
  python provision.py --help
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:  # allow --help and syntax checks without the dependency
    boto3 = None

    class ClientError(Exception):
        pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default region. Set any region where the managed Web Search connector is
# available — check the AWS documentation for current availability.
DEFAULT_REGION = "us-east-1"
WEB_SEARCH_CONNECTOR_ID = "web-search"
WEB_SEARCH_TOOL_NAME = "WebSearch"
# ARN of the managed web-search tool. NOTE: the working ARN includes the region;
# the docs' empty-region form (arn:aws:bedrock-agentcore::aws:tool/web-search.v1)
# is rejected by IAM policy evaluation.
WEB_SEARCH_TOOL_ARN_TMPL = "arn:aws:bedrock-agentcore:{region}:aws:tool/web-search.v1"

# Default loopback port used by the Bedrock third-party (3P) connector in
# Claude Desktop / Cowork for its OAuth callback (http://127.0.0.1:<PORT>/callback).
# Cognito matches redirect_uri EXACTLY (scheme + host + port + path) with no
# wildcards, so if your connector dialog shows a different port, override it with
# --callback-port (or register it later with `add-callback`).
DEFAULT_CALLBACK_PORT = 62029

# Where we persist the IDs of everything we created, so `delete` can clean up.
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".provision-state.json")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[provision] {msg}", flush=True)


def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    log(f"State written to {STATE_FILE}")


def clients(region: str):
    if boto3 is None:
        raise RuntimeError(
            "boto3 is required to run this command. Install it with "
            "'pip install -r requirements.txt'."
        )
    session = boto3.Session()
    return {
        "acp": session.client("bedrock-agentcore-control", region_name=region),
        "iam": session.client("iam"),  # IAM is global
        "cognito": session.client("cognito-idp", region_name=region),
        "sts": session.client("sts", region_name=region),
    }


# ---------------------------------------------------------------------------
# IAM service role (least privilege)
# ---------------------------------------------------------------------------

def create_service_role(iam, account_id: str, region: str, role_name: str, state: dict) -> str:
    """Create the Gateway service role with least-privilege web-search access."""
    tool_arn = WEB_SEARCH_TOOL_ARN_TMPL.format(region=region)
    gateway_arn_wildcard = f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/*"

    trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowAgentCoreToAssumeRole",
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {"aws:SourceAccount": account_id},
                    "ArnLike": {"aws:SourceArn": gateway_arn_wildcard},
                },
            }
        ],
    }

    permission_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "InvokeGateway",
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeGateway",
                "Resource": gateway_arn_wildcard,
            },
            {
                "Sid": "InvokeWebSearch",
                "Effect": "Allow",
                "Action": "bedrock-agentcore:InvokeWebSearch",
                "Resource": tool_arn,
            },
        ],
    }

    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description="AgentCore Gateway role for the managed Web Search connector.",
        )
        role_arn = resp["Role"]["Arn"]
        log(f"Created IAM role {role_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]
            log(f"IAM role {role_name} already exists, reusing it")
        else:
            raise

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="WebSearchGatewayAccess",
        PolicyDocument=json.dumps(permission_policy),
    )
    log("Attached least-privilege inline policy WebSearchGatewayAccess")

    state["role_name"] = role_name
    state["role_arn"] = role_arn
    # IAM role creation is eventually consistent; give it a moment before the
    # gateway tries to assume it.
    time.sleep(10)
    return role_arn


def delete_service_role(iam, role_name: str) -> None:
    try:
        for pol in iam.list_role_policies(RoleName=role_name)["PolicyNames"]:
            iam.delete_role_policy(RoleName=role_name, PolicyName=pol)
        iam.delete_role(RoleName=role_name)
        log(f"Deleted IAM role {role_name}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            log(f"IAM role {role_name} already gone")
        else:
            raise


# ---------------------------------------------------------------------------
# Cognito (optional inbound authorizer)
# ---------------------------------------------------------------------------

def create_cognito(cognito, region: str, prefix: str, extra_callbacks: list[str],
                   with_m2m: bool, user_email: str | None, user_password: str | None,
                   callback_port: int, identity_providers: list[str], state: dict) -> dict:
    """Create a Cognito user pool, domain, resource server and app client(s).

    The PRIMARY client uses the OAuth **authorization-code** grant, which is what
    Claude Cowork (and every Claude remote/desktop connector) requires. Cognito
    does not support the client_credentials grant for these connectors, so a real
    user must sign in through the hosted UI. The `openid` scope is included so the
    pool can issue id/refresh tokens for that flow.

    A second, optional `client_credentials` client (--with-m2m-client) is only for
    the browserless `curl` sanity check in the README; Cowork cannot use it.

    Returns a dict with discovery_url, allowed_clients, token_endpoint, etc.
    """
    pool = cognito.create_user_pool(PoolName=f"{prefix}-pool")
    pool_id = pool["UserPool"]["Id"]
    state["cognito_user_pool_id"] = pool_id
    log(f"Created Cognito user pool {pool_id}")

    # Domain (hosted UI + OAuth token endpoint). Prefix must be globally unique.
    domain_prefix = f"{prefix}-{secrets.token_hex(4)}".lower()
    cognito.create_user_pool_domain(Domain=domain_prefix, UserPoolId=pool_id)
    state["cognito_domain"] = domain_prefix
    log(f"Created Cognito domain {domain_prefix}")

    # Resource server + custom scope. Used as the M2M scope; the gateway itself
    # only validates the client_id, so the scope is not strictly required for the
    # authorization-code flow.
    resource_server_id = "agentcore-gateway"
    scope_name = "invoke"
    cognito.create_resource_server(
        UserPoolId=pool_id,
        Identifier=resource_server_id,
        Name="AgentCore Gateway",
        Scopes=[{"ScopeName": scope_name, "ScopeDescription": "Invoke the gateway"}],
    )
    full_scope = f"{resource_server_id}/{scope_name}"
    state["cognito_scope"] = full_scope
    log(f"Created resource server scope {full_scope}")

    # PRIMARY client: authorization-code grant for Claude Cowork. `openid` is
    # required so Cognito issues id/refresh tokens for the user login flow.
    callbacks = [f"http://127.0.0.1:{callback_port}/callback"] + extra_callbacks
    # Keep COGNITO plus any federated IdPs (e.g. an IAM Identity Center provider).
    supported_idps = list(dict.fromkeys(["COGNITO"] + (identity_providers or [])))
    user_client = cognito.create_user_pool_client(
        UserPoolId=pool_id,
        ClientName=f"{prefix}-cowork",
        GenerateSecret=True,
        AllowedOAuthFlows=["code"],
        AllowedOAuthScopes=["openid", "email", "profile", full_scope],
        AllowedOAuthFlowsUserPoolClient=True,
        CallbackURLs=callbacks,
        SupportedIdentityProviders=supported_idps,
        ExplicitAuthFlows=["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"],
    )["UserPoolClient"]
    state["cognito_callback_url"] = callbacks[0]
    state["cognito_supported_idps"] = supported_idps
    state["cognito_user_client_id"] = user_client["ClientId"]
    state["cognito_user_client_secret"] = user_client.get("ClientSecret", "")
    log(f"Created authorization-code app client {user_client['ClientId']}")

    allowed_clients = [user_client["ClientId"]]

    # OPTIONAL M2M client (client_credentials) for the browserless curl test only.
    # Cognito does not allow mixing client_credentials with the code flow on one
    # client, so it must be a separate client. Cowork cannot use this.
    if with_m2m:
        m2m_client = cognito.create_user_pool_client(
            UserPoolId=pool_id,
            ClientName=f"{prefix}-m2m",
            GenerateSecret=True,
            AllowedOAuthFlows=["client_credentials"],
            AllowedOAuthScopes=[full_scope],
            AllowedOAuthFlowsUserPoolClient=True,
        )["UserPoolClient"]
        state["cognito_m2m_client_id"] = m2m_client["ClientId"]
        state["cognito_m2m_client_secret"] = m2m_client.get("ClientSecret", "")
        allowed_clients.append(m2m_client["ClientId"])
        log(f"Created M2M app client {m2m_client['ClientId']}")

    # OPTIONAL: create a sign-in user for the hosted UI login.
    if user_email:
        state["cognito_user_email"] = user_email
        if user_password:
            # A password was provided: create the user quietly and set it as
            # permanent. Avoid this in shared/committed configs.
            cognito.admin_create_user(
                UserPoolId=pool_id,
                Username=user_email,
                UserAttributes=[
                    {"Name": "email", "Value": user_email},
                    {"Name": "email_verified", "Value": "true"},
                ],
                MessageAction="SUPPRESS",  # don't email a temp password
            )
            cognito.admin_set_user_password(
                UserPoolId=pool_id,
                Username=user_email,
                Password=user_password,
                Permanent=True,
            )
            log(f"Created sign-in user {user_email} with a permanent password")
        else:
            # RECOMMENDED: no password in code. Cognito emails an invitation with a
            # temporary password, and the user sets their own at first hosted-UI
            # sign-in (FORCE_CHANGE_PASSWORD). Uses Cognito's default email sender
            # (fine for testing; configure SES for real use).
            cognito.admin_create_user(
                UserPoolId=pool_id,
                Username=user_email,
                UserAttributes=[
                    {"Name": "email", "Value": user_email},
                    {"Name": "email_verified", "Value": "true"},
                ],
                DesiredDeliveryMediums=["EMAIL"],
            )
            log(f"Created sign-in user {user_email} and emailed an invitation; "
                f"they set their own password at first sign-in.")

    discovery_url = (
        f"https://cognito-idp.{region}.amazonaws.com/{pool_id}/.well-known/openid-configuration"
    )
    token_endpoint = f"https://{domain_prefix}.auth.{region}.amazoncognito.com/oauth2/token"
    authorize_endpoint = f"https://{domain_prefix}.auth.{region}.amazoncognito.com/oauth2/authorize"

    return {
        "discovery_url": discovery_url,
        "issuer": f"https://cognito-idp.{region}.amazonaws.com/{pool_id}",
        "allowed_clients": allowed_clients,
        "token_endpoint": token_endpoint,
        "authorize_endpoint": authorize_endpoint,
        "scope": full_scope,
        "callback_url": callbacks[0],
        "supported_idps": supported_idps,
    }


def delete_cognito(cognito, state: dict) -> None:
    pool_id = state.get("cognito_user_pool_id")
    if not pool_id:
        return
    domain = state.get("cognito_domain")
    if domain:
        try:
            cognito.delete_user_pool_domain(Domain=domain, UserPoolId=pool_id)
            log(f"Deleted Cognito domain {domain}")
        except ClientError as e:
            log(f"Could not delete domain {domain}: {e}")
    try:
        cognito.delete_user_pool(UserPoolId=pool_id)
        log(f"Deleted Cognito user pool {pool_id}")
    except ClientError as e:
        log(f"Could not delete user pool {pool_id}: {e}")


# ---------------------------------------------------------------------------
# Gateway + web-search target
# ---------------------------------------------------------------------------

def wait_for_gateway(acp, gateway_id: str, timeout: int = 300) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        g = acp.get_gateway(gatewayIdentifier=gateway_id)
        status = g["status"]
        if status == "READY":
            return g
        if status in ("FAILED", "UPDATE_UNSUCCESSFUL"):
            raise RuntimeError(f"Gateway {gateway_id} failed: {g.get('statusReasons')}")
        log(f"Gateway status {status}, waiting...")
        time.sleep(5)
    raise TimeoutError(f"Gateway {gateway_id} not READY within {timeout}s")


def wait_for_target(acp, gateway_id: str, target_id: str, timeout: int = 300) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        t = acp.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
        status = t["status"]
        if status == "READY":
            return t
        if status in ("FAILED", "UPDATE_UNSUCCESSFUL", "SYNCHRONIZE_UNSUCCESSFUL"):
            raise RuntimeError(f"Target {target_id} failed: {t.get('statusReasons')}")
        log(f"Target status {status}, waiting...")
        time.sleep(5)
    raise TimeoutError(f"Target {target_id} not READY within {timeout}s")


def create_gateway(acp, name: str, role_arn: str, discovery_url: str,
                   allowed_clients: list[str], state: dict) -> dict:
    authorizer_config = {
        "customJWTAuthorizer": {
            "discoveryUrl": discovery_url,
            "allowedClients": allowed_clients,
        }
    }

    # IAM role propagation can lag; retry a few times on assume-role validation.
    last_err = None
    for attempt in range(6):
        try:
            resp = acp.create_gateway(
                name=name,
                roleArn=role_arn,
                protocolType="MCP",
                authorizerType="CUSTOM_JWT",
                authorizerConfiguration=authorizer_config,
                description="Gateway exposing the managed Web Search tool over MCP.",
            )
            break
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code in ("ValidationException", "AccessDeniedException"):
                last_err = e
                log(f"create_gateway attempt {attempt + 1} failed ({code}), retrying...")
                time.sleep(10)
                continue
            raise
    else:
        raise last_err

    gateway_id = resp["gatewayId"]
    state["gateway_id"] = gateway_id
    state["gateway_arn"] = resp["gatewayArn"]
    log(f"Created gateway {gateway_id}")
    ready = wait_for_gateway(acp, gateway_id)
    state["gateway_url"] = ready["gatewayUrl"]
    log(f"Gateway READY: {ready['gatewayUrl']}")
    return ready


def create_web_search_target(acp, gateway_id: str, target_name: str, state: dict) -> dict:
    target_config = {
        "mcp": {
            "connector": {
                "source": {"connectorId": WEB_SEARCH_CONNECTOR_ID},
                "configurations": [
                    {"name": WEB_SEARCH_TOOL_NAME, "parameterValues": {}}
                ],
            }
        }
    }
    resp = acp.create_gateway_target(
        gatewayIdentifier=gateway_id,
        name=target_name,
        description="Managed Web Search connector.",
        targetConfiguration=target_config,
        credentialProviderConfigurations=[{"credentialProviderType": "GATEWAY_IAM_ROLE"}],
    )
    target_id = resp["targetId"]
    state["target_id"] = target_id
    log(f"Created web-search target {target_id}")
    wait_for_target(acp, gateway_id, target_id)
    log("Target READY")
    return resp


def delete_gateway_and_target(acp, state: dict) -> None:
    gateway_id = state.get("gateway_id")
    target_id = state.get("target_id")
    if gateway_id and target_id:
        try:
            acp.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=target_id)
            log(f"Deleted target {target_id}")
            time.sleep(5)
        except ClientError as e:
            log(f"Could not delete target {target_id}: {e}")
    if gateway_id:
        try:
            acp.delete_gateway(gatewayIdentifier=gateway_id)
            log(f"Deleted gateway {gateway_id}")
        except ClientError as e:
            log(f"Could not delete gateway {gateway_id}: {e}")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_create(args) -> None:
    region = args.region
    log(f"Using region {region}. Make sure the Web Search connector is available "
        f"there (see the AWS docs); otherwise creation will fail.")

    c = clients(region)
    account_id = c["sts"].get_caller_identity()["Account"]
    state = {"region": region, "created_cognito": False}

    # 1) IAM service role (least privilege)
    role_arn = create_service_role(c["iam"], account_id, region, args.role_name, state)

    # 2) Inbound authorizer: use provided IdP, or create Cognito.
    if args.auth_discovery_url and args.auth_client_id:
        discovery_url = args.auth_discovery_url
        allowed_clients = [args.auth_client_id]
        log("Using provided JWT authorizer (no Cognito created)")
    else:
        log("No --auth-discovery-url/--auth-client-id given: creating a Cognito user pool")
        cog = create_cognito(
            c["cognito"], region, args.prefix, args.extra_callback or [],
            with_m2m=args.with_m2m_client,
            user_email=args.user_email,
            user_password=args.user_password,
            callback_port=args.callback_port,
            identity_providers=args.identity_provider or [],
            state=state,
        )
        discovery_url = cog["discovery_url"]
        allowed_clients = cog["allowed_clients"]
        state["created_cognito"] = True
        state["cognito"] = cog

    # 3) Gateway (CUSTOM_JWT inbound auth)
    create_gateway(c["acp"], args.gateway_name, role_arn, discovery_url, allowed_clients, state)

    # 4) Web-search connector target
    create_web_search_target(c["acp"], state["gateway_id"], args.target_name, state)

    save_state(state)
    _print_summary(state)


def _print_summary(state: dict) -> None:
    print("\n" + "=" * 72)
    print("  AgentCore Web Search Gateway is READY")
    print("=" * 72)
    print(f"  Region            : {state['region']}")
    print(f"  Gateway ID        : {state.get('gateway_id')}")
    print(f"  Gateway MCP URL   : {state.get('gateway_url')}")
    print(f"  Service role ARN  : {state.get('role_arn')}")
    if state.get("created_cognito"):
        cog = state["cognito"]
        print("\n  Inbound auth      : Cognito (created by this script)")
        print(f"  Discovery URL     : {cog['discovery_url']}")
        print(f"  Authorization srv : {cog['issuer']}   (use this in Cowork)")
        print(f"  Token endpoint    : {cog['token_endpoint']}")
        print(f"  Authorize endpoint: {cog['authorize_endpoint']}")
        print(f"  Scope             : {cog['scope']}")
        print(f"  Callback URL      : {cog.get('callback_url')}")
        if cog.get("supported_idps") and cog["supported_idps"] != ["COGNITO"]:
            print(f"  Identity providers: {cog['supported_idps']}")
        print("\n  Cowork OAuth client (authorization-code grant, REQUIRED for Cowork):")
        print(f"    client_id       : {state.get('cognito_user_client_id')}")
        print(f"    client_secret   : <hidden> (key 'cognito_user_client_secret' in {STATE_FILE})")
        if state.get("cognito_user_email"):
            print(f"    sign-in user    : {state.get('cognito_user_email')}")
        else:
            print("    sign-in user    : none created (create one before connecting)")
        if state.get("cognito_m2m_client_id"):
            print("\n  M2M client (client_credentials, ONLY for the curl test; not usable by Cowork):")
            print(f"    client_id       : {state.get('cognito_m2m_client_id')}")
            print(f"    client_secret   : <hidden> (key 'cognito_m2m_client_secret' in {STATE_FILE})")
    else:
        print("\n  Inbound auth      : your own JWT authorizer")
    print("\n  Next: see README.md -> 'Connect to Claude Cowork'.")
    print("=" * 72 + "\n")


def cmd_delete(args) -> None:
    state = load_state()
    if not state:
        log("No state file found; nothing to delete.")
        return
    region = state.get("region", args.region)
    c = clients(region)

    delete_gateway_and_target(c["acp"], state)
    if state.get("created_cognito"):
        delete_cognito(c["cognito"], state)
    if state.get("role_name"):
        delete_service_role(c["iam"], state["role_name"])

    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
        log("Removed state file")
    log("Teardown complete.")


def cmd_add_callback(args) -> None:
    """Append an OAuth callback URL (e.g. the desktop loopback) to the Cognito
    'cowork' app client, preserving its existing configuration."""
    state = load_state()
    if not state.get("created_cognito"):
        raise RuntimeError(
            "No Cognito client was created by this script (you provided your own "
            "IdP). Register the callback URL on your IdP's client instead."
        )
    pool_id = state["cognito_user_pool_id"]
    client_id = state["cognito_user_client_id"]
    region = state.get("region", args.region)
    c = clients(region)

    current = c["cognito"].describe_user_pool_client(
        UserPoolId=pool_id, ClientId=client_id
    )["UserPoolClient"]

    callbacks = list(dict.fromkeys(current.get("CallbackURLs", []) + [args.url]))

    c["cognito"].update_user_pool_client(
        UserPoolId=pool_id,
        ClientId=client_id,
        ClientName=current["ClientName"],
        CallbackURLs=callbacks,
        AllowedOAuthFlows=current.get("AllowedOAuthFlows", ["code"]),
        AllowedOAuthScopes=current.get("AllowedOAuthScopes", []),
        AllowedOAuthFlowsUserPoolClient=current.get(
            "AllowedOAuthFlowsUserPoolClient", True
        ),
        SupportedIdentityProviders=current.get("SupportedIdentityProviders", ["COGNITO"]),
        ExplicitAuthFlows=current.get(
            "ExplicitAuthFlows", ["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"]
        ),
    )
    log(f"Registered callback URL {args.url}")
    log(f"Allowed callback URLs are now: {callbacks}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Provision an AgentCore Gateway with the managed Web Search tool.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Create the gateway, target, IAM role and (optional) Cognito.")
    create.add_argument("--region", default=os.environ.get("AWS_REGION", DEFAULT_REGION),
                        help=f"AWS region for the gateway/Web Search connector "
                             f"(default {DEFAULT_REGION}; use a region where Web Search is available).")
    create.add_argument("--gateway-name", default=os.environ.get("GATEWAY_NAME", "WebSearchGateway"))
    create.add_argument("--target-name", default=os.environ.get("TARGET_NAME", "web-search-tool"))
    create.add_argument("--role-name", default=os.environ.get("ROLE_NAME", "WebSearchGatewayRole"))
    create.add_argument("--prefix", default=os.environ.get("RESOURCE_PREFIX", "agentcore-websearch"),
                        help="Name prefix for auto-created Cognito resources.")
    # Auth variables: provide both to use your own IdP; omit to create Cognito.
    create.add_argument("--auth-discovery-url", default=os.environ.get("AUTH_DISCOVERY_URL"),
                        help="OIDC discovery URL of your IdP (.well-known/openid-configuration).")
    create.add_argument("--auth-client-id", default=os.environ.get("AUTH_CLIENT_ID"),
                        help="Client ID allowed in the incoming JWT (allowedClients).")
    create.add_argument("--extra-callback", action="append",
                        help="Additional OAuth callback URL for the created Cognito client (repeatable).")
    create.add_argument("--with-m2m-client", action="store_true",
                        help="Also create a client_credentials app client for the browserless curl test "
                             "(Cowork cannot use it).")
    create.add_argument("--user-email", default=os.environ.get("USER_EMAIL"),
                        help="Create a Cognito sign-in user with this email. Without --user-password, "
                             "Cognito emails an invitation and the user sets their own password (recommended).")
    create.add_argument("--user-password", default=os.environ.get("USER_PASSWORD"),
                        help="Optional. Set a permanent password for --user-email instead of emailing an "
                             "invitation. Avoid committing this; prefer the invitation flow.")
    create.add_argument("--callback-port", type=int,
                        default=int(os.environ.get("CALLBACK_PORT", DEFAULT_CALLBACK_PORT)),
                        help=f"Loopback port for the Bedrock 3P connector callback "
                             f"(default {DEFAULT_CALLBACK_PORT}); registers http://127.0.0.1:<PORT>/callback.")
    create.add_argument("--identity-provider", action="append",
                        help="Federated identity provider name to add to the app client's supported "
                             "providers, e.g. an IAM Identity Center provider (repeatable).")
    create.set_defaults(func=cmd_create)

    delete = sub.add_parser("delete", help="Delete everything created by 'create' (uses the state file).")
    delete.add_argument("--region", default=os.environ.get("AWS_REGION", DEFAULT_REGION))
    delete.set_defaults(func=cmd_delete)

    add_cb = sub.add_parser(
        "add-callback",
        help="Register an OAuth callback URL (e.g. the Claude desktop loopback) on the Cognito app client.",
    )
    add_cb.add_argument("--url", required=True,
                        help="Callback URL to allow, e.g. http://127.0.0.1:62029/callback")
    add_cb.add_argument("--region", default=os.environ.get("AWS_REGION", DEFAULT_REGION))
    add_cb.set_defaults(func=cmd_add_callback)

    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        args.func(args)
        return 0
    except (ClientError, RuntimeError, TimeoutError) as e:
        log(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
