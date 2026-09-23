"""
AgentCore Gateway Interceptor for Health Lakehouse Data

This Lambda function acts as a Gateway Interceptor following the AgentCore MCP protocol:
1. Extracts JWT bearer tokens from MCP gateway request structure
2. Validates JWT tokens against Cognito
3. Extracts user principal (email/username) from JWT claims
4. Validates tool access based on user groups and allowed tools in DynamoDB
5. Exchanges JWT claims to IAM credentials via DynamoDB role mapping
6. Adds user identity and credentials to request for downstream MCP server
7. Returns responses in proper MCP interceptor format

Reference: https://github.com/awslabs/amazon-bedrock-agentcore-samples/blob/main/06-workshops/02-AgentCore-gateway/14-token-exchange-at-request-interceptor/

OAuth Flow:
  Streamlit → lakehouse-agent → Gateway (this interceptor) → MCP server

The interceptor extracts the principal from the JWT token, validates tool access,
and exchanges it for IAM credentials based on tenant role mappings for Lake Formation
row-level security.
"""

import json
import logging
import os
import boto3
from typing import Dict, Any, Optional
import urllib.request
from jose import jwt, JWTError

# Import token exchange module
from token_exchange import exchange_jwt_to_iam, get_claim_for_exchange

# Import tool validation module
from tool_validation import validate_tool_access

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Cache for configuration and keys
_config = None
_jwks = None


def get_config() -> Dict[str, str]:
    """
    Get Cognito configuration from environment variables or SSM.

    Returns:
        Dictionary with Cognito configuration
    """
    global _config

    if _config is not None:
        return _config

    # First try environment variables
    region = os.environ.get("COGNITO_REGION") or os.environ.get("AWS_REGION", "us-west-2")
    user_pool_id = os.environ.get("COGNITO_USER_POOL_ID", "")
    app_client_id = os.environ.get("COGNITO_APP_CLIENT_ID", "")

    # If not set, try SSM Parameter Store
    if not user_pool_id or not app_client_id:
        logger.info("Loading Cognito configuration from SSM Parameter Store...")
        try:
            ssm = boto3.client("ssm", region_name=region)

            if not user_pool_id:
                response = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-user-pool-id")
                user_pool_id = response["Parameter"]["Value"]
                logger.info(f"Loaded user_pool_id from SSM: {user_pool_id}")

            if not app_client_id:
                response = ssm.get_parameter(Name="/app/lakehouse-agent/cognito-app-client-id")
                app_client_id = response["Parameter"]["Value"]
                logger.info(f"Loaded app_client_id from SSM: {app_client_id}")

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
    return _config


def get_cognito_public_keys() -> Dict[str, Any]:
    """
    Fetch Cognito public keys for JWT validation.

    Returns:
        Dictionary of public keys
    """
    global _jwks

    if _jwks is not None:
        return _jwks

    try:
        config = get_config()
        jwks_url = f"{config['issuer']}/.well-known/jwks.json"
        logger.info(f"Fetching JWKS from: {jwks_url}")

        with urllib.request.urlopen(jwks_url) as response:  # nosec B310
            _jwks = json.loads(response.read())
            logger.info("Successfully fetched Cognito public keys")
            return _jwks
    except Exception as e:
        logger.error(f"Error fetching Cognito public keys: {str(e)}")
        raise


def validate_and_decode_jwt(token: str) -> Optional[Dict[str, Any]]:
    """
    Validate JWT token and decode claims.

    Args:
        token: JWT bearer token

    Returns:
        Decoded JWT claims or None if invalid
    """
    try:
        config = get_config()

        # Get Cognito public keys
        jwks = get_cognito_public_keys()

        # Decode token header to get key ID
        unverified_headers = jwt.get_unverified_header(token)
        kid = unverified_headers.get("kid")

        # Find the correct public key
        key = None
        for k in jwks.get("keys", []):
            if k.get("kid") == kid:
                key = k
                break

        if not key:
            logger.error("Public key not found for token")
            return None

        # Validate and decode JWT
        # Note: For access tokens, we don't validate audience since Cognito
        # access tokens don't have 'aud' claim. We validate client_id instead.
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=config["app_client_id"],
                issuer=config["issuer"],
            )
        except JWTError as e:
            # If audience validation fails, try without audience (for access tokens)
            if "audience" in str(e).lower() or "aud" in str(e).lower():
                logger.info("Retrying JWT validation without audience check (access token)")
                claims = jwt.decode(
                    token,
                    key,
                    algorithms=["RS256"],
                    issuer=config["issuer"],
                    options={"verify_aud": False},
                )
                # Manually verify client_id for access tokens
                if claims.get("client_id") != config["app_client_id"]:
                    logger.error(f"Client ID mismatch: {claims.get('client_id')} != {config['app_client_id']}")
                    return None
            else:
                raise

        logger.info(f"Successfully validated JWT for user: {claims.get('username', claims.get('sub'))}")
        return claims

    except JWTError as e:
        logger.error(f"JWT validation error: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Error validating JWT: {str(e)}")
        return None


def extract_bearer_token_from_mcp(event: Dict[str, Any]) -> Optional[str]:
    """
    Extract bearer token from MCP gateway request structure.

    Following AgentCore Gateway MCP protocol, the event structure is:
    {
        "mcp": {
            "gatewayRequest": {
                "headers": {"Authorization": "Bearer <token>"},
                "body": {...}
            }
        }
    }

    Args:
        event: Lambda event with MCP structure

    Returns:
        Bearer token (without 'Bearer ' prefix) or None if not found
    """
    try:
        # Extract from MCP structure
        mcp_data = event.get("mcp", {})
        gateway_request = mcp_data.get("gatewayRequest", {})
        headers = gateway_request.get("headers", {})

        # Check Authorization header (case-insensitive)
        auth_header = headers.get("Authorization") or headers.get("authorization")

        if auth_header:
            # Remove 'Bearer ' prefix if present
            if auth_header.startswith("Bearer "):
                token = auth_header.replace("Bearer ", "", 1)
            elif auth_header.startswith("bearer "):
                token = auth_header.replace("bearer ", "", 1)
            else:
                token = auth_header

            logger.info("✅ Bearer token extracted from MCP gateway request")
            return token

        logger.warning("⚠️  Bearer token not found in MCP gateway request headers")
        return None

    except Exception as e:
        logger.error(f"❌ Error extracting bearer token from MCP structure: {str(e)}")
        return None


def extract_user_principal(claims: Dict[str, Any]) -> Optional[str]:
    """
    Extract user principal (identity) from JWT claims.

    The principal is used for Lake Formation row-level security.
    Priority order:
    1. email (preferred for user identification)
    2. username
    3. cognito:username
    4. sub (user ID as fallback)

    Args:
        claims: Decoded JWT claims

    Returns:
        User principal (email/username) or None
    """
    # Try multiple claim fields in priority order
    principal = claims.get("email") or claims.get("username") or claims.get("cognito:username") or claims.get("sub")

    if principal:
        logger.info(f"✅ Extracted user principal: {principal}")
        return principal

    logger.warning("⚠️  User principal not found in JWT claims")
    return None


def get_user_scopes(claims: Dict[str, Any]) -> list:
    """
    Extract OAuth scopes from JWT claims for logging and context.

    Args:
        claims: Decoded JWT claims

    Returns:
        List of scopes
    """
    # Scopes can be in 'scope' claim (space-separated) or 'cognito:groups'
    scope_string = claims.get("scope", "")
    scopes = scope_string.split() if scope_string else []

    # Add groups as scopes
    groups = claims.get("cognito:groups", [])
    if isinstance(groups, list):
        scopes.extend(groups)

    return scopes


def build_error_response(message, body, status_code=403):
    """Return an MCP-style error response"""
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


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Main Lambda handler for AgentCore Gateway interceptor.

    Follows the MCP protocol for request interception:
    1. Extracts JWT token from MCP gateway request structure
    2. Validates JWT and extracts user principal
    3. Adds user identity to request for downstream MCP server
    4. Returns transformed request in MCP format

    Event Structure (Input):
    {
        "mcp": {
            "gatewayRequest": {
                "headers": {"Authorization": "Bearer <token>"},
                "body": {...}
            }
        }
    }

    Response Structure (Output):
    {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayRequest": {
                "headers": {...},
                "body": {...}
            }
        }
    }

    Args:
        event: Lambda event with MCP structure
        context: Lambda context

    Returns:
        Transformed request in MCP format or error response
    """
    logger.info("🔍 Gateway interceptor invoked")
    logger.info(f"📦 Event structure: {json.dumps(event, default=str)[:500]}...")

    try:
        # Extract MCP gateway request
        mcp_data = event.get("mcp", {})
        gateway_request = mcp_data.get("gatewayRequest", {})
        headers = gateway_request.get("headers", {})
        body = gateway_request.get("body", {})

        logger.info(f"📋 Headers present: {list(headers.keys())}")
        logger.info(f"📋 Body keys: {list(body.keys())}")

        # Extract bearer token from MCP structure
        token = extract_bearer_token_from_mcp(event)

        if not token:
            logger.error("❌ No bearer token found in request")
            return {
                "statusCode": 401,
                "body": json.dumps(
                    {
                        "error": "Unauthorized",
                        "message": "Bearer token required in Authorization header",
                    }
                ),
            }

        # Validate and decode JWT
        claims = validate_and_decode_jwt(token)

        if not claims:
            logger.error("❌ JWT validation failed")
            return {
                "statusCode": 401,
                "body": json.dumps({"error": "Unauthorized", "message": "Invalid or expired JWT token"}),
            }

        # Extract user principal from JWT claims
        user_principal = extract_user_principal(claims)

        if not user_principal:
            logger.error("❌ User principal not found in JWT claims")
            return {
                "statusCode": 401,
                "body": json.dumps(
                    {
                        "error": "Unauthorized",
                        "message": "User principal not found in token claims",
                    }
                ),
            }

        # Get user scopes for logging
        scopes = get_user_scopes(claims)
        logger.info(f"👤 User: {user_principal}, Scopes: {scopes}")

        # Validate tool access before proceeding
        is_authorized, error_message, tool_name = validate_tool_access(claims, body)

        if not is_authorized:
            logger.error(f"❌ Tool access denied: {error_message}")
            return build_error_response(error_message, body, status_code=403)

        if tool_name:
            logger.info(f"✅ Tool access authorized: {tool_name}")

        # Exchange JWT claims to IAM credentials via DynamoDB role mapping
        tenant_credentials = None
        claim_for_exchange = get_claim_for_exchange(claims)

        if claim_for_exchange:
            claim_name, claim_value = claim_for_exchange
            logger.info(f"🔄 Attempting token exchange for: {claim_name}={claim_value}")
            tenant_credentials = exchange_jwt_to_iam(claim_name, claim_value)

            if tenant_credentials:
                logger.info(
                    f"🔑 Obtained temporary credentials for role: {tenant_credentials['RoleName']}"
                )  # codeql[py/clear-text-logging-sensitive-data]
            else:
                logger.warning("⚠️  Failed to exchange JWT to IAM credentials")
        else:
            logger.warning("⚠️  No suitable claim found for token exchange")

        # Add user identity to headers for downstream MCP server
        # The MCP server will use X-User-Identity for Lake Formation RLS
        transformed_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-User-Identity": user_principal,
            "X-User-Scopes": ",".join(scopes) if scopes else "",
        }

        # Add tenant role information to headers if credentials were obtained
        if tenant_credentials:
            transformed_headers["X-Tenant-Role"] = tenant_credentials["RoleName"]
            transformed_headers["X-Tenant-Role-Arn"] = tenant_credentials["RoleArn"]

        # Also add user context to body if it has params/arguments
        # This ensures the MCP server can access user identity and credentials
        transformed_body = body.copy()
        if "params" in transformed_body and "arguments" in transformed_body["params"]:
            if "context" not in transformed_body["params"]["arguments"]:
                transformed_body["params"]["arguments"]["context"] = {}
            transformed_body["params"]["arguments"]["context"]["user_id"] = user_principal
            transformed_body["params"]["arguments"]["context"]["scopes"] = scopes

            # Add tenant credentials to context if available
            if tenant_credentials:
                transformed_body["params"]["arguments"]["context"]["tenant_credentials"] = {
                    "access_key_id": tenant_credentials["AccessKeyId"],
                    "secret_access_key": tenant_credentials["SecretAccessKey"],
                    "session_token": tenant_credentials["SessionToken"],
                    "role_arn": tenant_credentials["RoleArn"],
                    "role_name": tenant_credentials["RoleName"],
                    "expiration": tenant_credentials["Expiration"].isoformat(),
                }

        # Return transformed request in MCP format
        response = {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayRequest": {
                    "headers": transformed_headers,
                    "body": transformed_body,
                }
            },
        }

        logger.info(f"✅ Request authorized for user: {user_principal}")
        logger.info("📤 Returning transformed request")

        return response

    except Exception as e:
        logger.error(f"❌ Error in gateway interceptor: {str(e)}")
        import traceback

        logger.error(f"Stack trace: {traceback.format_exc()}")

        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "error": "Internal Server Error",
                    "message": f"Error processing request: {str(e)}",
                }
            ),
        }
