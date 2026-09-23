# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""Lambda handler that configures PingFederate via the Admin API.

Runs as a CDK custom resource inside the VPC so it can reach the internal ALB directly.
"""

import json
import logging
import os
import time
import urllib.request
import ssl

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# PingFederate configuration constants
CLIENT_ID = "agentcore-client"
CLIENT_SECRET = os.environ.get("PINGFED_CLIENT_SECRET", "agentcore-test-secret-12345")  # pragma: allowlist secret
ATM_ID = "agentcoreJwtAtm"
OIDC_POLICY_ID = "agentcoreOidcPolicy"
SIGNING_KEY_ID = "agentcore-signing-key"


def handler(event, context):
    """CloudFormation custom resource handler."""
    request_type = event.get("RequestType", "")
    response_url = event.get("ResponseURL", "")
    stack_id = event.get("StackId", "")
    request_id = event.get("RequestId", "")
    logical_id = event.get("LogicalResourceId", "")
    physical_id = event.get("PhysicalResourceId", logical_id)

    props = event.get("ResourceProperties", {})
    admin_url = props.get("AdminUrl", "")
    admin_user = props.get("AdminUser", "")
    secret_id = props.get("SecretId", "")
    base_url = props.get("BaseUrl", "")

    # Fetch admin password from Secrets Manager. Wrapped in retry logic because Lambda
    # VPC ENIs can take a few seconds to initialize on cold start, causing
    # "[Errno 16] Device or resource busy" errors on the first network call.
    sm = boto3.client("secretsmanager")
    secret_value = json.loads(_retry_on_eni_busy(lambda: sm.get_secret_value(SecretId=secret_id))["SecretString"])
    admin_password = secret_value["adminPassword"]

    try:
        if request_type in ("Create", "Update"):
            configure_pingfederate(admin_url, admin_user, admin_password, base_url)
            discovery_url = f"{base_url}/.well-known/openid-configuration"
            send_response(
                response_url,
                "SUCCESS",
                stack_id,
                request_id,
                logical_id,
                physical_id,
                {
                    "DiscoveryUrl": discovery_url,
                    "ClientId": CLIENT_ID,
                },
            )
        else:
            # Delete — nothing to tear down
            send_response(response_url, "SUCCESS", stack_id, request_id, logical_id, physical_id)
    except Exception as e:
        logger.exception("Configuration failed")
        send_response(
            response_url,
            "FAILED",
            stack_id,
            request_id,
            logical_id,
            physical_id,
            reason=str(e),
        )


def _retry_on_eni_busy(fn, max_attempts=6, delay=5):
    """Retry a callable that may fail with '[Errno 16] Device or resource busy'.

    Lambda functions in a VPC can experience transient OSError on cold start while
    the ENI is being attached to the execution environment. This retries for up to
    30 seconds (6 attempts × 5s) before giving up.
    """
    for attempt in range(max_attempts):
        try:
            return fn()
        except OSError as e:
            if attempt == max_attempts - 1:
                raise
            logger.warning(f"VPC ENI not ready (attempt {attempt + 1}/{max_attempts}): {e}")
            time.sleep(delay)


def configure_pingfederate(admin_url, admin_user, admin_password, base_url):
    """Configure PingFederate OAuth/OIDC via the Admin API."""
    api = f"{admin_url}/pf-admin-api/v1"
    auth = _basic_auth(admin_user, admin_password)
    ctx = _insecure_ssl_context()

    # Wait for PingFederate to be ready (up to 8 minutes).
    # PingFederate can take 3-5 min to start, plus the ALB target group
    # needs to pass health checks before it routes traffic.
    logger.info("Waiting for PingFederate to be ready...")
    max_attempts = 96  # 96 × 5s = 8 minutes
    for i in range(max_attempts):
        try:
            _api_call("GET", f"{api}/version", auth=auth, ssl_ctx=ctx)
            logger.info("PingFederate is ready")
            break
        except Exception:
            if i == max_attempts - 1:
                raise TimeoutError(f"PingFederate not ready after {max_attempts} attempts")
            time.sleep(5)

    # 1. Generate signing key pair
    logger.info("1. Creating signing key pair...")
    _api_call(
        "POST",
        f"{api}/keyPairs/signing/generate",
        auth=auth,
        ssl_ctx=ctx,
        body={
            "id": SIGNING_KEY_ID,
            "commonName": "AgentCore Signing Key",
            "organization": "AgentCore Sample",
            "country": "US",
            "validDays": 3650,
            "keyAlgorithm": "RSA",
            "keySize": 2048,
            "signatureAlgorithm": "SHA256withRSA",
        },
    )

    # 2. Create JWT Access Token Manager
    logger.info("2. Creating JWT Access Token Manager...")
    _api_call(
        "POST",
        f"{api}/oauth/accessTokenManagers",
        auth=auth,
        ssl_ctx=ctx,
        body={
            "id": ATM_ID,
            "name": "AgentCore JWT Token Manager",
            "pluginDescriptorRef": {
                "id": "com.pingidentity.pf.access.token.management.plugins.JwtBearerAccessTokenManagementPlugin"
            },
            "configuration": {
                "tables": [
                    {"name": "Symmetric Keys", "rows": []},
                    {"name": "Certificates", "rows": []},
                ],
                "fields": [
                    {"name": "Token Lifetime", "value": "120"},
                    {"name": "Use Centralized Signing Key", "value": "true"},
                    {"name": "JWS Algorithm", "value": "RS256"},
                    {"name": "Active Symmetric Key ID", "value": ""},
                    {"name": "Active Signing Certificate Key ID", "value": ""},
                    {"name": "JWE Algorithm", "value": ""},
                    {"name": "JWE Content Encryption Algorithm", "value": ""},
                    {"name": "Active Symmetric Encryption Key ID", "value": ""},
                    {"name": "Asymmetric Encryption Key", "value": ""},
                    {"name": "Asymmetric Encryption JWKS URL", "value": ""},
                    {"name": "Enable Token Revocation", "value": "false"},
                    {"name": "Include Key ID Header Parameter", "value": "true"},
                    {"name": "Include Issued At Claim", "value": "true"},
                    {"name": "Client ID Claim Name", "value": "client_id"},
                    {"name": "Scope Claim Name", "value": "scope"},
                    {"name": "Space Delimit Scope Values", "value": "true"},
                    {"name": "JWT ID Claim Length", "value": "22"},
                    {
                        "name": "Include X.509 Thumbprint Header Parameter",
                        "value": "false",
                    },
                    {"name": "Default JWKS URL Cache Duration", "value": "720"},
                    {"name": "Include JWE Key ID Header Parameter", "value": "true"},
                    {
                        "name": "Include JWE X.509 Thumbprint Header Parameter",
                        "value": "false",
                    },
                    {
                        "name": "Authorization Details Claim Name",
                        "value": "authorization_details",
                    },
                    {"name": "Issuer Claim Value", "value": base_url},
                    {"name": "Audience Claim Value", "value": ""},
                    {"name": "Not Before Claim Offset", "value": ""},
                    {"name": "Access Grant GUID Claim Name", "value": ""},
                    {
                        "name": "Publish Keys to the PingFederate JWKS Endpoint",
                        "value": "false",
                    },
                    {"name": "JWKS Endpoint Path", "value": ""},
                    {"name": "JWKS Endpoint Cache Duration", "value": "720"},
                    {"name": "Publish Key ID X.509 URL", "value": "false"},
                    {"name": "Publish Thumbprint X.509 URL", "value": "false"},
                    {"name": "Expand Scope Groups", "value": "false"},
                    {"name": "Type Header Value", "value": ""},
                ],
            },
            "attributeContract": {
                "coreAttributes": [],
                "extendedAttributes": [
                    {"name": "sub", "multiValued": False},
                    {"name": "scope", "multiValued": False},
                    {"name": "client_id", "multiValued": False},
                ],
                "defaultSubjectAttribute": "sub",
            },
            "selectionSettings": {"resourceUris": []},
            "accessControlSettings": {"restrictClients": False, "allowedClients": []},
            "sessionValidationSettings": {
                "checkValidAuthnSession": False,
                "checkSessionRevocationStatus": False,
                "updateAuthnSessionActivity": False,
                "includeSessionId": False,
            },
        },
    )

    # 3. Set default ATM
    logger.info("3. Setting default access token manager...")
    _api_call(
        "PUT",
        f"{api}/oauth/accessTokenManagers/settings",
        auth=auth,
        ssl_ctx=ctx,
        body={"defaultAccessTokenManagerRef": {"id": ATM_ID}},
    )

    # 4. Configure OAuth auth server settings
    logger.info("4. Configuring OAuth auth server settings...")
    _api_call(
        "PUT",
        f"{api}/oauth/authServerSettings",
        auth=auth,
        ssl_ctx=ctx,
        body={
            "defaultScopeDescription": "",
            "scopes": [
                {"name": "openid", "description": "OpenID Connect", "dynamic": False},
                {"name": "profile", "description": "User profile", "dynamic": False},
                {"name": "email", "description": "Email", "dynamic": False},
            ],
            "scopeGroups": [],
            "exclusiveScopes": [],
            "exclusiveScopeGroups": [],
            "authorizationCodeTimeout": 60,
            "authorizationCodeEntropy": 30,
            "disallowPlainPKCE": False,
            "includeIssuerInAuthorizationResponse": False,
            "persistentGrantLifetime": -1,
            "persistentGrantLifetimeUnit": "DAYS",
            "persistentGrantIdleTimeout": 30,
            "persistentGrantIdleTimeoutTimeUnit": "DAYS",
            "refreshTokenLength": 42,
            "rollRefreshTokenValues": False,
            "refreshTokenRollingGracePeriod": 60,
            "refreshRollingInterval": 0,
            "refreshRollingIntervalTimeUnit": "HOURS",
            "persistentGrantReuseGrantTypes": ["IMPLICIT"],
            "persistentGrantContract": {
                "extendedAttributes": [],
                "coreAttributes": [{"name": "USER_KEY"}, {"name": "USER_NAME"}],
            },
            "bypassAuthorizationForApprovedGrants": False,
            "allowUnidentifiedClientROCreds": False,
            "allowUnidentifiedClientExtensionGrants": False,
            "tokenEndpointBaseUrl": base_url,
            "parReferenceTimeout": 60,
            "parReferenceLength": 24,
            "parStatus": "ENABLED",
            "clientSecretRetentionPeriod": 0,
            "jwtSecuredAuthorizationResponseModeLifetime": 600,
            "dpopProofRequireNonce": False,
            "dpopProofLifetimeSeconds": 120,
            "dpopProofEnforceReplayPrevention": False,
            "bypassAuthorizationForApprovedConsents": False,
            "consentLifetimeDays": -1,
        },
    )

    # 5. Configure server settings
    logger.info("5. Configuring server settings...")
    _api_call(
        "PUT",
        f"{api}/serverSettings",
        auth=auth,
        ssl_ctx=ctx,
        body={
            "contactInfo": {},
            "rolesAndProtocols": {
                "oauthRole": {"enableOauth": True, "enableOpenIdConnect": True},
                "idpRole": {
                    "enable": True,
                    "enableSaml11": True,
                    "enableSaml10": True,
                    "enableWsFed": True,
                    "enableWsTrust": True,
                    "saml20Profile": {"enable": True},
                    "enableOutboundProvisioning": True,
                },
                "spRole": {
                    "enable": True,
                    "enableSaml11": True,
                    "enableSaml10": True,
                    "enableWsFed": True,
                    "enableWsTrust": True,
                    "saml20Profile": {"enable": True, "enableXASP": True},
                    "enableInboundProvisioning": True,
                    "enableOpenIDConnect": True,
                },
                "enableIdpDiscovery": True,
            },
            "federationInfo": {
                "baseUrl": base_url,
                "saml2EntityId": "evaluation",
                "saml1xIssuerId": "",
                "saml1xSourceId": "",
                "wsfedRealm": "",
            },
        },
    )

    # 6. Create OIDC policy
    logger.info("6. Creating OIDC policy...")
    _api_call(
        "POST",
        f"{api}/oauth/openIdConnect/policies",
        auth=auth,
        ssl_ctx=ctx,
        body={
            "id": OIDC_POLICY_ID,
            "name": "AgentCore OIDC Policy",
            "idTokenLifetime": 5,
            "attributeContract": {
                "coreAttributes": [{"name": "sub", "multiValued": False}],
                "extendedAttributes": [
                    {"name": "name", "multiValued": False},
                    {"name": "email", "multiValued": False},
                ],
            },
            "attributeMapping": {
                "attributeSources": [],
                "attributeContractFulfillment": {
                    "sub": {"source": {"type": "NO_MAPPING"}},
                    "name": {"source": {"type": "NO_MAPPING"}},
                    "email": {"source": {"type": "NO_MAPPING"}},
                },
                "issuanceCriteria": {"conditionalCriteria": []},
            },
            "includeSriInIdToken": True,
            "includeUserInfoInIdToken": False,
            "includeSHashInIdToken": False,
            "includeX5tInIdToken": False,
            "idTokenTypHeaderValue": "",
            "returnIdTokenOnRefreshGrant": False,
            "reissueIdTokenInHybridFlow": False,
            "accessTokenManagerRef": {"id": ATM_ID},
            "scopeAttributeMappings": {},
        },
    )

    # 7. Set default OIDC policy
    logger.info("7. Setting default OIDC policy...")
    _api_call(
        "PUT",
        f"{api}/oauth/openIdConnect/settings",
        auth=auth,
        ssl_ctx=ctx,
        body={
            "defaultPolicyRef": {"id": OIDC_POLICY_ID},
            "sessionSettings": {
                "trackUserSessionsForLogout": False,
                "revokeUserSessionOnLogout": True,
                "sessionRevocationLifetime": 490,
            },
        },
    )

    # 8. Create OAuth client
    logger.info("8. Creating OAuth client...")
    _api_call(
        "POST",
        f"{api}/oauth/clients",
        auth=auth,
        ssl_ctx=ctx,
        body={
            "clientId": CLIENT_ID,
            "enabled": True,
            "redirectUris": [
                f"https://bedrock-agentcore.{os.environ.get('AWS_REGION', 'us-east-1')}.amazonaws.com/identities/oauth2/callback",
                "https://localhost/callback",
            ],
            "grantTypes": ["AUTHORIZATION_CODE", "CLIENT_CREDENTIALS", "REFRESH_TOKEN"],
            "name": "AgentCore OAuth Client",
            "refreshRolling": "SERVER_DEFAULT",
            "refreshTokenRollingIntervalType": "SERVER_DEFAULT",
            "persistentGrantExpirationType": "SERVER_DEFAULT",
            "persistentGrantIdleTimeoutType": "SERVER_DEFAULT",
            "persistentGrantReuseType": "SERVER_DEFAULT",
            "bypassApprovalPage": True,
            "restrictScopes": False,
            "restrictedScopes": [],
            "exclusiveScopes": [],
            "restrictedResponseTypes": [],
            "defaultAccessTokenManagerRef": {"id": ATM_ID},
            "restrictToDefaultAccessTokenManager": False,
            "oidcPolicy": {
                "grantAccessSessionRevocationApi": False,
                "grantAccessSessionSessionManagementApi": False,
                "logoutMode": "NONE",
                "pingAccessLogoutCapable": False,
                "pairwiseIdentifierUserType": False,
            },
            "clientAuth": {
                "type": "SECRET",
                "secret": CLIENT_SECRET,
                "secondarySecrets": [],
            },
            "deviceFlowSettingType": "SERVER_DEFAULT",
            "requireProofKeyForCodeExchange": False,
            "refreshTokenRollingGracePeriodType": "SERVER_DEFAULT",
            "clientSecretRetentionPeriodType": "SERVER_DEFAULT",
            "requireDpop": False,
            "requireSignedRequests": False,
        },
    )

    # Verify: request a token using the ALB internal DNS name (not the public domain,
    # which may not resolve from within the VPC). The engine listener is on port 443.
    logger.info("Verifying: requesting client_credentials token...")
    alb_host = admin_url.split("//")[1].split(":")[0]  # extract ALB DNS name
    token_resp = _token_request(f"https://{alb_host}", ctx)
    if "access_token" not in token_resp:
        raise RuntimeError(f"Token verification failed: {token_resp}")
    logger.info("Configuration complete — token verification successful")


def _token_request(base_url, ssl_ctx):
    """Request a client_credentials token to verify the configuration."""
    url = f"{base_url}/as/token.oauth2"
    data = f"grant_type=client_credentials&client_id={CLIENT_ID}&client_secret={CLIENT_SECRET}&scope=openid"
    req = urllib.request.Request(url, data=data.encode(), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, context=ssl_ctx, timeout=30) as resp:  # nosec B310
        return json.loads(resp.read())


def _api_call(method, url, auth, ssl_ctx, body=None):
    """Make an API call to PingFederate."""
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", auth)
    req.add_header("X-XSRF-Header", "PingFederate")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, context=ssl_ctx, timeout=30) as resp:  # nosec B310
        resp_body = resp.read()
        if resp_body:
            result = json.loads(resp_body)
            if "resultId" in result:
                raise RuntimeError(f"API call failed: {result}")
            return result
        return {}


def _basic_auth(user, password):
    """Return a Basic auth header value."""
    import base64

    credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {credentials}"


def _insecure_ssl_context():
    """Create an SSL context that skips certificate verification (private CA)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def send_response(
    response_url,
    status,
    stack_id,
    request_id,
    logical_id,
    physical_id,
    data=None,
    reason="",
):
    """Send a response to the CloudFormation custom resource."""
    body = json.dumps(
        {
            "Status": status,
            "Reason": reason or "See CloudWatch Log Stream",
            "PhysicalResourceId": physical_id,
            "StackId": stack_id,
            "RequestId": request_id,
            "LogicalResourceId": logical_id,
            "Data": data or {},
        }
    ).encode()
    req = urllib.request.Request(response_url, data=body, method="PUT")
    req.add_header("Content-Type", "")
    req.add_header("Content-Length", str(len(body)))
    urllib.request.urlopen(req, timeout=30)  # nosec B310
