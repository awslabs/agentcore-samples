"""
AgentCore Gateway REQUEST Interceptor for the Notes (OpenSearch) gateway — Cognito path.

This is the GW2 counterpart to the claims REQUEST interceptor. On the **Cognito**
path GW2 cannot use OBO (no RFC 8693 token exchange), so a thin REQUEST
interceptor validates the Cognito JWT, extracts the caller `sub`, and forwards
it to the OpenSearch MCP server on the **proven body-context channel**
(`params.arguments.context.user_id`) — mirroring how the claims interceptor
propagates identity. The OpenSearch server then applies its `owner_user_sub`
term filter using that `sub` (match-by-construction with the seeded value).

Deliberately THIN vs the claims REQUEST interceptor: it does NOT perform the
DynamoDB tenant-role → STS exchange and does NOT do tool-gating (notes RLS is
the `owner_user_sub` filter; there is a single notes tool). It only validates
identity and forwards the `sub`. Fail-closed: no resolvable `sub` → error, no
data (R5.4).

The shared JWT-validation helpers (`get_config`, `get_public_keys`,
`validate_and_decode_jwt`) mirror the claims interceptor's DR-8 Cognito|Okta
branch so the code is familiar; in practice this Lambda is only attached on the
Cognito GW2 path (Okta GW2 is interceptor-less / OBO).
"""

import json
import logging
import os
import urllib.request
from typing import Any, Optional

import boto3
from jose import JWTError, jwt

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Cache for configuration and keys
_config = None
_jwks = None


def _resolve_idp_provider() -> str:
    """IdP selector for the Lambda (DR-8): env (set at deploy time) → SSM → cognito."""
    v = os.environ.get("IDP_PROVIDER")
    if v:
        return v.strip().lower()
    try:
        region = os.environ.get("AWS_REGION", "us-east-1")
        ssm = boto3.client("ssm", region_name=region)
        return ssm.get_parameter(Name="/app/lakehouse-agent/idp-provider")["Parameter"]["Value"].strip().lower()
    except Exception:
        return "cognito"


IDP_PROVIDER = _resolve_idp_provider()


def get_config() -> dict[str, str]:
    """Get IdP configuration from environment variables or SSM (DR-8 branch)."""
    global _config

    if _config is not None:
        return _config

    if IDP_PROVIDER == "cognito":
        # [COGNITO] user-pool issuer (access tokens carry no 'aud' → client_id check)
        region = os.environ.get("COGNITO_REGION") or os.environ.get("AWS_REGION", "us-west-2")
        user_pool_id = os.environ.get("COGNITO_USER_POOL_ID", "")
        app_client_id = os.environ.get("COGNITO_APP_CLIENT_ID", "")

        if not user_pool_id or not app_client_id:
            logger.info("Loading Cognito configuration from SSM Parameter Store...")
            try:
                ssm = boto3.client("ssm", region_name=region)
                if not user_pool_id:
                    user_pool_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-user-pool-id")["Parameter"][
                        "Value"
                    ]
                if not app_client_id:
                    app_client_id = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-app-client-id")["Parameter"][
                        "Value"
                    ]
            except Exception as e:
                logger.error(f"Error loading configuration from SSM: {e}")
                raise

        _config = {
            "region": region,
            "user_pool_id": user_pool_id,
            "app_client_id": app_client_id,
            "issuer": f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}",
        }
        logger.info(f"Cognito configuration loaded: region={region}, user_pool_id={user_pool_id}")
    else:  # okta
        # [OKTA] custom-auth-server issuer (present for parity; GW2-Okta is OBO/no-interceptor)
        region = os.environ.get("AWS_REGION", "us-east-1")
        okta_org_url = os.environ.get("OKTA_ORG_URL", "")
        okta_auth_server_id = os.environ.get("OKTA_AUTH_SERVER_ID", "")
        okta_audience = os.environ.get("OKTA_RESOURCE_SERVER_AUDIENCE", "")

        if not okta_org_url or not okta_auth_server_id or not okta_audience:
            logger.info("Loading Okta configuration from SSM Parameter Store...")
            try:
                ssm = boto3.client("ssm", region_name=region)
                if not okta_org_url:
                    okta_org_url = ssm.get_parameter(Name="/app/lakehouse-agent/okta-org-url")["Parameter"]["Value"]
                if not okta_auth_server_id:
                    okta_auth_server_id = ssm.get_parameter(Name="/app/lakehouse-agent/okta-auth-server-id")[
                        "Parameter"
                    ]["Value"]
                if not okta_audience:
                    okta_audience = ssm.get_parameter(Name="/app/lakehouse-agent/okta-resource-server-audience")[
                        "Parameter"
                    ]["Value"]
            except Exception as e:
                logger.error(f"Error loading configuration from SSM: {e}")
                raise

        _config = {
            "region": region,
            "okta_org_url": okta_org_url,
            "okta_auth_server_id": okta_auth_server_id,
            "okta_audience": okta_audience,
            "issuer": f"https://{okta_org_url}/oauth2/{okta_auth_server_id}",
        }
        logger.info(f"Okta configuration loaded: region={region}, org_url={okta_org_url}")

    return _config


def get_public_keys() -> dict[str, Any]:
    """Fetch IdP public keys for JWT validation (DR-8: Cognito vs Okta JWKS URL)."""
    global _jwks

    if _jwks is not None:
        return _jwks

    try:
        config = get_config()
        if IDP_PROVIDER == "cognito":
            jwks_url = f"{config['issuer']}/.well-known/jwks.json"
        else:  # okta
            jwks_url = f"{config['issuer']}/v1/keys"
        logger.info(f"Fetching JWKS from: {jwks_url}")

        with urllib.request.urlopen(jwks_url) as response:  # nosec B310
            _jwks = json.loads(response.read())
            logger.info("Successfully fetched public keys")
            return _jwks
    except Exception as e:
        logger.error(f"Error fetching public keys: {e!s}")
        raise


def validate_and_decode_jwt(token: str) -> dict[str, Any] | None:
    """Validate JWT and decode claims (DR-8 Cognito|Okta branch, mirrors GW1)."""
    try:
        config = get_config()
        jwks = get_public_keys()

        unverified_headers = jwt.get_unverified_header(token)
        kid = unverified_headers.get("kid")

        key = None
        for k in jwks.get("keys", []):
            if k.get("kid") == kid:
                key = k
                break

        if not key:
            logger.error("Public key not found for token")
            return None

        if IDP_PROVIDER == "cognito":
            # [COGNITO] access tokens carry no 'aud' → validate client_id
            try:
                claims = jwt.decode(
                    token,
                    key,
                    algorithms=["RS256"],
                    audience=config["app_client_id"],
                    issuer=config["issuer"],
                )
            except JWTError as e:
                if "audience" in str(e).lower() or "aud" in str(e).lower():
                    logger.info("Retrying JWT validation without audience check (access token)")
                    claims = jwt.decode(
                        token,
                        key,
                        algorithms=["RS256"],
                        issuer=config["issuer"],
                        options={"verify_aud": False},
                    )
                    if claims.get("client_id") != config["app_client_id"]:
                        logger.error(f"Client ID mismatch: {claims.get('client_id')} != {config['app_client_id']}")
                        return None
                else:
                    raise
        else:  # okta
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=config["okta_audience"],
                issuer=config["issuer"],
            )

        logger.info(f"Successfully validated JWT for sub: {claims.get('sub')}")
        return claims

    except JWTError as e:
        logger.error(f"JWT validation error: {e!s}")
        return None
    except Exception as e:
        logger.error(f"Error validating JWT: {e!s}")
        return None


def extract_bearer_token_from_mcp(event: dict[str, Any]) -> str | None:
    """Extract the bearer token from the MCP gateway request structure."""
    try:
        headers = event.get("mcp", {}).get("gatewayRequest", {}).get("headers", {})
        auth_header = headers.get("Authorization") or headers.get("authorization")
        if not auth_header:
            logger.warning("⚠️  Bearer token not found in MCP gateway request headers")
            return None
        parts = auth_header.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1]
        return auth_header
    except Exception as e:
        logger.error(f"❌ Error extracting bearer token: {e!s}")
        return None


def build_error_response(message: str, body: dict[str, Any], status_code: int = 401) -> dict[str, Any]:
    """Return an MCP-style error response (fail-closed: no data without identity)."""
    return {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": status_code,
                "body": {
                    "jsonrpc": "2.0",
                    "id": body.get("id", "unknown") if isinstance(body, dict) else "unknown",
                    "error": {"code": -32600, "message": message},
                },
            }
        },
    }


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Validate the Cognito JWT, extract the caller `sub`, and inject it into the
    MCP request body context (`params.arguments.context.user_id`) so the
    OpenSearch MCP server can apply its owner_user_sub filter. Fail-closed.
    """
    logger.info("🔍 Notes gateway REQUEST interceptor invoked")

    try:
        gateway_request = event.get("mcp", {}).get("gatewayRequest", {})
        body = gateway_request.get("body", {})

        token = extract_bearer_token_from_mcp(event)
        if not token:
            logger.error("❌ No bearer token found in request")
            return build_error_response("Bearer token required in Authorization header", body, 401)

        claims = validate_and_decode_jwt(token)
        if not claims:
            logger.error("❌ JWT validation failed")
            return build_error_response("Invalid or expired JWT token", body, 401)

        # Notes RLS keys on the caller `sub` — matches the seeded owner_user_sub
        # by construction (loader seeds each user's Cognito `sub`). Fail-closed (R5.4).
        user_sub = claims.get("sub")
        if not user_sub:
            logger.error("❌ `sub` not found in token claims — refusing (fail-closed)")
            return build_error_response("User identity (sub) not found in token claims", body, 401)

        # Inject on the PROVEN body-context channel (mirrors the claims interceptor);
        # the OpenSearch server reads context.user_id on the Cognito path.
        transformed_body = body.copy()
        params = transformed_body.get("params")
        if isinstance(params, dict) and "arguments" in params:
            if "context" not in params["arguments"]:
                params["arguments"]["context"] = {}
            params["arguments"]["context"]["user_id"] = user_sub

        # Minimal headers; X-User-Identity is for observability only (server reads context).
        transformed_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-User-Identity": user_sub,
        }

        logger.info(f"✅ Notes request authorized for sub: {user_sub}")
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayRequest": {
                    "headers": transformed_headers,
                    "body": transformed_body,
                }
            },
        }

    except Exception as e:
        logger.error(f"❌ Error in notes interceptor: {e!s}")
        import traceback

        logger.error(f"Stack trace: {traceback.format_exc()}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal Server Error", "message": f"Error processing request: {e!s}"}),
        }
