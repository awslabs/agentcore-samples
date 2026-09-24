"""
AgentCore Gateway Response Interceptor for Lakehouse Agent

This Lambda function acts as a Gateway Response Interceptor to filter tools
from the tool list returned by the MCP server based on user group permissions.

Response Interceptor Flow:
  MCP Server → Gateway (this interceptor) → Agent/Client

The interceptor:
1. Extracts JWT claims from the Authorization header
2. Looks up allowed tools for the user's group in DynamoDB
3. Filters the tool list to only include allowed tools
4. Always removes 'x_amz_bedrock_agentcore_search' from the list

Reference:
- https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors-types.html
- https://github.com/awslabs/amazon-bedrock-agentcore-samples/tree/main/06-workshops/02-AgentCore-gateway/09-fine-grained-access-control
"""

import json
import logging
import os
import urllib.request
from typing import Any, Optional

import boto3
from jose import JWTError, jwt

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# System tools to always filter out from the tool list
SYSTEM_TOOLS_TO_FILTER = {
    "x_amz_bedrock_agentcore_search",  # Semantic search tool
}

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
        # [COGNITO] upstream verbatim
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
    else:  # okta
        # [OKTA] fork verbatim (canonical §6 okta-* keys)
        region = os.environ.get("AWS_REGION", "us-east-1")
        okta_org_url = os.environ.get("OKTA_ORG_URL", "")
        okta_auth_server_id = os.environ.get("OKTA_AUTH_SERVER_ID", "")
        okta_audience = os.environ.get("OKTA_RESOURCE_SERVER_AUDIENCE", "")

        # If not set, try SSM Parameter Store
        if not okta_org_url or not okta_auth_server_id or not okta_audience:
            logger.info("Loading Okta configuration from SSM Parameter Store...")
            try:
                ssm = boto3.client("ssm", region_name=region)

                if not okta_org_url:
                    okta_org_url = ssm.get_parameter(Name="/app/lakehouse-agent/okta-org-url")["Parameter"]["Value"]
                    logger.info(f"Loaded okta_org_url from SSM: {okta_org_url}")

                if not okta_auth_server_id:
                    okta_auth_server_id = ssm.get_parameter(Name="/app/lakehouse-agent/okta-auth-server-id")[
                        "Parameter"
                    ]["Value"]
                    logger.info(f"Loaded okta_auth_server_id from SSM: {okta_auth_server_id}")

                if not okta_audience:
                    okta_audience = ssm.get_parameter(Name="/app/lakehouse-agent/okta-resource-server-audience")[
                        "Parameter"
                    ]["Value"]
                    logger.info(f"Loaded okta_audience from SSM: {okta_audience}")

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
        logger.info(
            f"Okta configuration loaded: region={region}, org_url={okta_org_url}, auth_server_id={okta_auth_server_id}"
        )

    return _config


def get_public_keys() -> dict[str, Any]:
    """Fetch IdP public keys for JWT validation (DR-8: Cognito vs Okta JWKS URL)."""
    global _jwks

    if _jwks is not None:
        return _jwks

    try:
        config = get_config()
        # Cognito exposes /.well-known/jwks.json; Okta uses /v1/keys.
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
    """
    Validate JWT token and decode claims.

    Args:
        token: JWT bearer token

    Returns:
        Decoded JWT claims or None if invalid
    """
    try:
        config = get_config()

        # Get IdP public keys
        jwks = get_public_keys()

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

        # Decode differs by IdP (DR-8): Cognito access tokens have no 'aud' →
        # validate client_id; Okta access tokens carry 'aud' → validate directly.
        if IDP_PROVIDER == "cognito":
            # [COGNITO] upstream verbatim
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
        else:  # okta
            # [OKTA] fork verbatim: single-path decode with audience validation
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=config["okta_audience"],
                issuer=config["issuer"],
            )

        logger.info(f"Successfully validated JWT for user: {claims.get('username', claims.get('sub'))}")
        return claims

    except JWTError as e:
        logger.error(f"JWT validation error: {e!s}")
        return None
    except Exception as e:
        logger.error(f"Error validating JWT: {e!s}")
        return None


def get_claim_for_authorization(claims: dict[str, Any]) -> tuple[str, str] | None:
    """
    Extract the appropriate claim for authorization check from JWT claims.

    Priority order:
    1. cognito:groups - for group-based authorization
    2. email - for user-specific authorization
    3. username - for username-based authorization

    Args:
        claims: Decoded JWT claims

    Returns:
        Tuple of (claim_name, claim_value) or None if no suitable claim found
    """
    if IDP_PROVIDER == "cognito":
        # [COGNITO] upstream verbatim: cognito:groups (whole-array) → email → username.
        # Priority 1: Check for cognito:groups
        groups = claims.get("cognito:groups")
        if groups:
            # Convert list to JSON string format for DynamoDB lookup
            claim_value = json.dumps(groups)
            logger.info(f"Found cognito:groups claim for authorization: {claim_value}")
            return ("cognito:groups", claim_value)

        # Priority 2: Check for email
        email = claims.get("email")
        if email:
            logger.info(f"Found email claim for authorization: {email}")
            return ("email", email)

        # Priority 3: Check for username
        username = claims.get("username") or claims.get("cognito:username")
        if username:
            logger.info(f"Found username claim for authorization: {username}")
            return ("username", username)
    else:  # okta
        # [OKTA] fork verbatim: Okta `groups` (value '.*') includes built-in groups
        # like `Everyone`, so filter built-ins and iterate; the per-app-group seed
        # (e.g. ["policyholders"]) matches a single-group key, not the whole array.
        OKTA_BUILTIN_GROUPS = {"Everyone"}  # Okta-specific; sibling IdPs revisit
        groups = claims.get("groups")
        if groups:
            for group in groups:
                if group in OKTA_BUILTIN_GROUPS:
                    continue
                claim_value = json.dumps([group])
                logger.info(f"Found groups claim for authorization (per-group iter): {claim_value}")
                return ("groups", claim_value)
            logger.warning(f"⚠️  All groups filtered as built-ins: {groups}")

        # Priority 2: Check for email
        email = claims.get("email")
        if email:
            logger.info(f"Found email claim for authorization: {email}")
            return ("email", email)

        # Priority 3: Check for sub
        sub = claims.get("sub")
        if sub:
            logger.info(f"Found sub claim for authorization: {sub}")
            return ("sub", sub)

    logger.warning("⚠️  No suitable claim found for authorization")
    return None


def get_allowed_tools_from_dynamodb(claim_name: str, claim_value: str) -> list[str]:
    """
    Get allowed tools for a user from DynamoDB.

    Queries the lakehouse_tenant_role_map table to get the list of allowed tools
    for the given claim.

    Args:
        claim_name: The JWT claim name (e.g., "cognito:groups")
        claim_value: The JWT claim value (e.g., '["administrators"]')

    Returns:
        List of allowed tool names, empty list if no mapping found
    """
    try:
        # Get table name from environment or use default
        table_name = os.environ.get("TENANT_ROLE_MAPPING_TABLE")

        if not table_name:
            # Try to get from SSM Parameter Store
            try:
                ssm = boto3.client("ssm")
                response = ssm.get_parameter(Name="/app/lakehouse-agent/tenant-role-mapping-table")
                table_name = response["Parameter"]["Value"]
                logger.info(f"Loaded table name from SSM: {table_name}")
            except Exception as e:
                logger.error(f"Failed to get table name from SSM: {e}")
                # Fallback to default table name
                table_name = "lakehouse_tenant_role_map"
                logger.info(f"Using default table name: {table_name}")

        # Initialize DynamoDB resource
        dynamodb = boto3.resource("dynamodb")
        table = dynamodb.Table(table_name)

        logger.info(f"🔍 Looking up allowed tools: {claim_name}={claim_value}")

        # Query DynamoDB for the claim mapping
        response = table.get_item(Key={"claim_name": claim_name, "claim_value": claim_value})

        # Check if item exists and has allowed_tools
        if "Item" not in response:
            logger.warning(f"⚠️  No mapping found for claim: {claim_name}={claim_value}")
            return []

        item = response["Item"]
        allowed_tools = item.get("allowed_tools", [])

        logger.info(f"✅ Found {len(allowed_tools)} allowed tools for {claim_name}={claim_value}")
        logger.info(f"   Allowed tools: {allowed_tools}")

        return allowed_tools

    except Exception as e:
        logger.error(f"❌ Error getting allowed tools from DynamoDB: {e!s}")
        import traceback

        logger.error(f"Stack trace: {traceback.format_exc()}")
        return []


def filter_tools(tools: list[dict[str, Any]], allowed_tools: list[str]) -> list[dict[str, Any]]:
    """
    Filter tools based on allowed tools list and system tool filters.

    Args:
        tools: List of tool definitions from MCP server
        allowed_tools: List of allowed tool names for the user

    Returns:
        Filtered list of tools
    """
    filtered = []

    for tool in tools:
        tool_name = tool.get("name", "")

        # Extract the actual tool name (after the target separator if present)
        # Format: "target-name___tool_name" -> "tool_name"
        if "___" in tool_name:
            actual_tool_name = tool_name.split("___")[-1]
        else:
            actual_tool_name = tool_name

        # Always filter out system tools
        if actual_tool_name in SYSTEM_TOOLS_TO_FILTER:
            logger.info(f"🚫 Filtering out system tool: {tool_name}")
            continue

        # Check if tool is in allowed list
        if actual_tool_name not in allowed_tools:
            logger.info(f"🚫 Filtering out unauthorized tool: {tool_name}")
            continue

        logger.info(f"✅ Including tool: {tool_name}")
        filtered.append(tool)

    return filtered


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Main Lambda handler for AgentCore Gateway response interceptor.

    Filters the tool list from MCP server responses based on:
    1. User group permissions from DynamoDB
    2. System tool filters (always removes x_amz_bedrock_agentcore_search)

    Event Structure (Input):
    {
        "interceptorInputVersion": "1.0",
        "mcp": {
            "gatewayRequest": {
                "headers": {"Authorization": "Bearer <token>"},
                "body": {"method": "tools/list", ...}
            },
            "gatewayResponse": {
                "statusCode": 200,
                "headers": {...},
                "body": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "tools": [...]
                    }
                }
            }
        }
    }

    Response Structure (Output):
    {
        "interceptorOutputVersion": "1.0",
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": 200,
                "headers": {...},
                "body": {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "tools": [...]  # Filtered list
                    }
                }
            }
        }
    }

    Args:
        event: Lambda event with MCP response structure
        context: Lambda context

    Returns:
        Transformed response in MCP format with filtered tools
    """
    logger.info("🔍 Response interceptor invoked")
    logger.info(f"📦 Event structure: {json.dumps(event, default=str)[:500]}...")

    try:
        # Extract MCP gateway request and response
        mcp_data = event.get("mcp", {})
        gateway_request = mcp_data.get("gatewayRequest", {})
        gateway_response = mcp_data.get("gatewayResponse", {})

        request_headers = gateway_request.get("headers", {})
        request_body = gateway_request.get("body", {})
        response_headers = gateway_response.get("headers", {})
        response_body = gateway_response.get("body", {})

        method = request_body.get("method", "")
        logger.info(f"📋 MCP method: {method}")

        # Only filter tools/list responses
        if method != "tools/list":
            logger.info(f"⏭️  Skipping filtering for method: {method}")
            return {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayResponse": {
                        "statusCode": gateway_response.get("statusCode", 200),
                        "headers": response_headers,
                        "body": response_body,
                    }
                },
            }

        # Extract tools from response
        result = response_body.get("result", {})
        tools = result.get("tools", [])

        if not tools:
            logger.info("ℹ️  No tools found in response")
            return {
                "interceptorOutputVersion": "1.0",
                "mcp": {
                    "transformedGatewayResponse": {
                        "statusCode": gateway_response.get("statusCode", 200),
                        "headers": response_headers,
                        "body": response_body,
                    }
                },
            }

        logger.info(f"📋 Original tools: {[tool.get('name') for tool in tools]}")

        # Extract and validate JWT token
        auth_header = request_headers.get("Authorization") or request_headers.get("authorization", "")

        # Fail CLOSED on every auth-failure path: return an EMPTY tool list
        # (deny-all), never the unfiltered catalog. Same shape as the existing
        # no-DDB-mapping branch. The IdP-specific logic lives inside
        # validate_and_decode_jwt / get_claim_for_authorization (dual-IdP
        # preserved); these failure branches are IdP-invariant.
        if not auth_header.startswith("Bearer "):
            logger.warning("🚫 No Bearer token found — failing closed (empty tool list)")
            filtered_tools = []
        else:
            token = auth_header.replace("Bearer ", "", 1)

            # Validate and decode JWT
            claims = validate_and_decode_jwt(token)

            if not claims:
                logger.warning("🚫 JWT validation failed — failing closed (empty tool list)")
                filtered_tools = []
            else:
                # Get claim for authorization
                claim_for_auth = get_claim_for_authorization(claims)

                if not claim_for_auth:
                    logger.warning("🚫 No suitable claim found — failing closed (empty tool list)")
                    filtered_tools = []
                else:
                    claim_name, claim_value = claim_for_auth

                    # Get allowed tools from DynamoDB
                    allowed_tools = get_allowed_tools_from_dynamodb(claim_name, claim_value)

                    if not allowed_tools:
                        logger.warning(
                            f"🚫 No allowed tools found for {claim_name}={claim_value} — failing closed (empty list)"
                        )
                        filtered_tools = []
                    else:
                        # Filter tools based on allowed list and system filters
                        filtered_tools = filter_tools(tools, allowed_tools)

        logger.info(f"✅ Filtered tools: {[tool.get('name') for tool in filtered_tools]}")
        logger.info(f"🔢 Tool count: {len(tools)} → {len(filtered_tools)}")

        # Update response body with filtered tools
        filtered_body = response_body.copy()
        if "result" in filtered_body:
            filtered_body["result"] = result.copy()
            filtered_body["result"]["tools"] = filtered_tools

        # Return transformed response
        response = {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayResponse": {
                    "statusCode": gateway_response.get("statusCode", 200),
                    "headers": response_headers,
                    "body": filtered_body,
                }
            },
        }

        logger.info("✅ Response filtering completed")
        return response

    except Exception as e:
        logger.error(f"❌ Error in response interceptor: {e!s} — failing closed (empty tool list)")
        import traceback

        logger.error(f"Stack trace: {traceback.format_exc()}")

        # Fail CLOSED on any error: never return the unfiltered catalog. Rebuild a
        # minimal valid tools/list response with an EMPTY tool list, re-derived
        # defensively from the event so this never depends on partially-set locals.
        safe_gw_response = event.get("mcp", {}).get("gatewayResponse", {})
        safe_body = safe_gw_response.get("body", {})
        empty_body = safe_body.copy() if isinstance(safe_body, dict) else {}
        result_obj = empty_body.get("result")
        empty_body["result"] = {**result_obj, "tools": []} if isinstance(result_obj, dict) else {"tools": []}
        return {
            "interceptorOutputVersion": "1.0",
            "mcp": {
                "transformedGatewayResponse": {
                    "statusCode": safe_gw_response.get("statusCode", 200),
                    "headers": safe_gw_response.get("headers", {}),
                    "body": empty_body,
                }
            },
        }
