"""
MCP Server for OpenSearch Claim Notes - AgentCore Identity OBO Pattern

This MCP server provides full-text search over claim-note documents in
Amazon OpenSearch Serverless, with per-user row-level security enforced by
a query-time `owner_user_sub` term filter.

Security Architecture:
- OAuth authentication (Okta JWT tokens, validated by AgentCore Runtime
  customJWTAuthorizer)
- User identity extracted from the validated Authorization header forwarded
  via requestHeaderAllowlist: ["Authorization"] (this server reads the
  forwarded header directly)
- Query-time term filter on owner_user_sub (no SQL string interpolation,
  no application-level identity propagation; AOSS document-field contract)

IMPORTANT: This server reads the validated Authorization header from
ctx.request_context.request.headers. The AgentCore Runtime authorizer has
already validated signature/issuer/audience/expiry, so this code uses an
unverified PyJWT decode purely to extract claims.

Configuration:
- Reads from SSM Parameter Store under /app/lakehouse-agent/
- Auto-detects region from boto3 session
"""

import logging
import os
import sys
from typing import Any, Optional

import boto3
import jwt
from mcp.server.fastmcp import FastMCP

# Configure logging to stdout (captured by CloudWatch)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
    force=True,  # Override any existing configuration
)
logger = logging.getLogger(__name__)

# Also add a stderr handler to ensure logs appear
stderr_handler = logging.StreamHandler(sys.stderr)
stderr_handler.setLevel(logging.INFO)
stderr_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logger.addHandler(stderr_handler)

# Ensure stdout is unbuffered
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Initialize MCP server
mcp = FastMCP(host="0.0.0.0", stateless_http=True)

from opensearch_tools import OpenSearchClaimNotesTools

logger.info("🔒 Using AOSS query-time owner_user_sub filter (OBO production mode)")
print("🔒 Using AOSS query-time owner_user_sub filter (OBO production mode)")

# Global tools instance
opensearch_tools = None

# Configuration cache
_config_cache = None


def _resolve_idp_provider() -> str:
    """IdP selector (DR-8): env (set at deploy time) → SSM → cognito default."""
    v = os.environ.get("IDP_PROVIDER")
    if v:
        return v.strip().lower()
    try:
        session = boto3.Session()
        region = os.environ.get("AWS_REGION") or session.region_name or "us-east-1"
        ssm = boto3.client("ssm", region_name=region)
        return ssm.get_parameter(Name="/app/lakehouse-agent/idp-provider")["Parameter"]["Value"].strip().lower()
    except Exception:
        return "cognito"


IDP_PROVIDER = _resolve_idp_provider()


def get_config() -> dict[str, str | None]:
    """
    Load configuration from environment variables and SSM Parameter Store.
    """
    global _config_cache

    if _config_cache is not None:
        return _config_cache

    config = {}

    # Get region from boto3 session with proper fallback
    try:
        session = boto3.Session()
        config["region"] = (
            os.environ.get("AWS_REGION") or session.region_name or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        )
        if not session.region_name:
            print("⚠️  No region in AWS config, using fallback")
        print(f"✅ Region: {config['region']}")
    except Exception as e:
        print(f"⚠️  Could not detect region: {e}")
        config["region"] = "us-east-1"

    # Get account ID
    try:
        sts = boto3.client("sts", region_name=config["region"])
        config["account_id"] = sts.get_caller_identity()["Account"]
    except Exception as e:
        print(f"⚠️  Could not get account ID: {e}")
        config["account_id"] = None

    ssm = boto3.client("ssm", region_name=config["region"])

    def get_param(name: str, env_var: str | None = None, default: str | None = None) -> str | None:
        if env_var and env_var in os.environ:
            value = os.environ[env_var]
            print(f"✅ {name} from environment: {value}")
            return value

        try:
            response = ssm.get_parameter(Name=f"/app/lakehouse-agent/{name}")
            value = response["Parameter"]["Value"]
            print(f"✅ {name} from SSM: {value}")
            return value
        except ssm.exceptions.ParameterNotFound:
            if default:
                print(f"ℹ️  {name} using default: {default}")
                return default
            print(f"⚠️  {name} not found")
            return None
        except Exception as e:
            print(f"❌ Error getting {name}: {e}")
            return default

    config["opensearch_collection_endpoint"] = get_param(
        "opensearch-collection-endpoint", "OPENSEARCH_COLLECTION_ENDPOINT"
    )
    config["log_level"] = os.environ.get("LOG_LEVEL", "INFO")

    _config_cache = config
    return config


def validate_config(config: dict[str, str | None]) -> bool:
    required_params = [
        ("region", "AWS Region"),
        ("opensearch_collection_endpoint", "OpenSearch Serverless Collection Endpoint"),
    ]

    missing = []
    for param, display_name in required_params:
        if not config.get(param):
            missing.append(display_name)

    if missing:
        print(f"❌ Missing required configuration: {', '.join(missing)}")
        return False

    return True


def get_opensearch_tools():
    global opensearch_tools
    if opensearch_tools is None:
        config = get_config()

        logger.info("Initializing OpenSearch tools...")
        logger.info(f"  Region: {config['region']}")
        logger.info(f"  Collection endpoint: {config['opensearch_collection_endpoint']}")
        print("Initializing OpenSearch tools...")
        print(f"  Region: {config['region']}")
        print(f"  Collection endpoint: {config['opensearch_collection_endpoint']}")

        opensearch_tools = OpenSearchClaimNotesTools(
            region=config["region"], collection_endpoint=config["opensearch_collection_endpoint"]
        )

        print("✅ OpenSearch tools initialized")

    return opensearch_tools


def extract_user_sub_from_headers() -> str | None:
    """
    Extract the Okta `sub` claim from the validated Authorization header.

    The AgentCore Runtime customJWTAuthorizer has already validated
    signature/issuer/audience/expiry; this code uses an unverified PyJWT
    decode purely to extract claims. The requestHeaderAllowlist:
    ["Authorization"] runtime config makes the header readable here.

    Returns:
        The `sub` claim string, or None if header is missing / malformed.
    """
    try:
        # FastMCP exposes the request via get_context().request_context
        ctx = mcp.get_context()
        headers = ctx.request_context.request.headers

        # Header keys are case-insensitive in HTTP; FastMCP/Starlette typically
        # exposes them lowercased. Try common variants.
        auth_header = headers.get("authorization") or headers.get("Authorization")

        if not auth_header:
            print("❌ No Authorization header found in request context")
            print(f"   Available headers: {list(headers.keys()) if hasattr(headers, 'keys') else 'unknown'}")
            return None

        # Strip "Bearer " prefix
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            print(f"❌ Authorization header is not a Bearer token: {auth_header[:30]}...")
            return None

        token = parts[1]

        # Unverified decode — the authorizer already validated the token;
        # same pattern as the Claims-side server.py reads its claims.
        claims = jwt.decode(token, options={"verify_signature": False})
        sub = claims.get("sub")

        if not sub:
            print(f"❌ Token has no `sub` claim. Claim keys: {list(claims.keys())}")
            return None

        print(f"✅ Extracted user sub from Authorization header: {sub}")
        # OBO identity-propagation observability — claim metadata only, no full
        # token dump. Logs the exchanged token's aud/scp/iss so the OBO path can
        # be confirmed as a genuine token EXCHANGE (re-scoped, re-audienced) at
        # the runtime rather than a passthrough of the user's original token.
        print(f"   token claims — aud={claims.get('aud')!r} scp={claims.get('scp')!r} iss={claims.get('iss')!r}")
        return sub

    except Exception as e:
        print(f"❌ Error extracting sub from headers: {e}")
        import traceback

        print(f"   Stack trace: {traceback.format_exc()}")
        return None


@mcp.tool(
    name="search_claim_notes",
    description=(
        "Search free-text claim notes (adjuster narratives, damage assessments, "
        "call summaries) for the authenticated user. Use this tool for natural-"
        "language queries about claim *notes* and unstructured text — NOT for "
        "structured claim records (claim_id, status, amounts, dates), which "
        "live in the lakehouse and are served by the claims/* tools."
    ),
)
def search_claim_notes(query: str, limit: int = 10, context: dict[str, Any] = None) -> dict[str, Any]:
    """Search claim notes for the authenticated user.

    `context` carries interceptor-injected identity on the Cognito path
    (params.arguments.context.user_id = caller sub); unused on the Okta OBO path
    (identity comes from the forwarded bearer).
    """
    msg = f"{'=' * 60}\n🔧 TOOL INVOKED: search_claim_notes\n{'=' * 60}"
    logger.info(msg)
    print(msg, file=sys.stderr, flush=True)
    print(msg, flush=True)

    logger.info("📥 INPUT PARAMETERS:")
    logger.info(f"   query: {query}")
    logger.info(f"   limit: {limit}")

    print("📥 INPUT PARAMETERS:", file=sys.stderr, flush=True)
    print(f"   query: {query}", file=sys.stderr, flush=True)
    print(f"   limit: {limit}", file=sys.stderr, flush=True)

    print("📥 INPUT PARAMETERS:")
    print(f"   query: {query}")
    print(f"   limit: {limit}")

    try:
        # Write to a file as absolute proof the tool was called
        # (mirrors the Claims-side debug pattern; handy for confirming the
        # tool actually ran end-to-end)
        try:
            with open("/tmp/mcp_tool_invoked.log", "a") as f:
                import datetime

                f.write(f"{datetime.datetime.now()} - search_claim_notes invoked\n")
                f.write(f"  query: {query}, limit: {limit}\n")
                f.flush()
        except Exception:
            pass  # Don't fail if we can't write to file

        # Identity source differs by IdP (DR-8/DR-9): Okta forwards a user-scoped
        # bearer via OBO (decode `sub` from the header); Cognito's notes REQUEST
        # interceptor injects the caller `sub` on the body-context channel
        # (params.arguments.context.user_id). Either way the value is the caller
        # `sub` and matches the seeded owner_user_sub by construction.
        if IDP_PROVIDER == "cognito":
            user_sub = (context or {}).get("user_id")
            if user_sub:
                print(f"✅ Caller sub from interceptor-injected context: {user_sub}")
        else:  # okta
            user_sub = extract_user_sub_from_headers()
        logger.info(f"👤 USER SUB: {user_sub}")
        print(f"👤 USER SUB: {user_sub}", file=sys.stderr, flush=True)
        print(f"👤 USER SUB: {user_sub}")

        if not user_sub:
            # Per R4.5: if no Authorization header / no sub, return error and
            # do NOT return any data. This is the load-bearing fail-closed
            # behavior of the OBO path.
            logger.error("❌ User sub not found — refusing to query without identity")
            return {
                "success": False,
                "error": "User identity not found in request (missing or malformed Authorization header)",
            }

        logger.info(f"🔍 QUERY: {query}")
        print(f"🔍 QUERY: {query}")
        sys.stdout.flush()

        tools = get_opensearch_tools()
        result = tools.search_claim_notes(user_sub, query, limit)

        logger.info("📤 OUTPUT:")
        logger.info(f"   success: {result.get('success', 'N/A')}")
        print("📤 OUTPUT:")
        print(f"   success: {result.get('success', 'N/A')}")
        if result.get("success"):
            hits_count = len(result.get("hits", []))
            logger.info(f"   hits_count: {hits_count}")
            logger.info(f"   total_matched: {result.get('total_matched', 'N/A')}")
            print(f"   hits_count: {hits_count}")
            print(f"   total_matched: {result.get('total_matched', 'N/A')}")
        else:
            logger.error(f"   error: {result.get('error', 'N/A')}")
            print(f"   error: {result.get('error', 'N/A')}")

        logger.info("=" * 60)
        print("=" * 60)
        sys.stdout.flush()
        return result

    except Exception as e:
        logger.error(f"❌ ERROR in search_claim_notes: {e!s}")
        print(f"❌ ERROR in search_claim_notes: {e!s}")
        import traceback

        error_trace = traceback.format_exc()
        logger.error(f"   Stack trace: {error_trace}")
        print(f"   Stack trace: {error_trace}")
        logger.error("=" * 60)
        print("=" * 60)
        sys.stdout.flush()
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    # Log startup
    logger.info("=" * 70)
    logger.info("🚀 MCP SERVER STARTING (OpenSearch / OBO)")
    logger.info("=" * 70)
    print("=" * 70)
    print("🚀 MCP SERVER STARTING (OpenSearch / OBO)")
    print("=" * 70)
    sys.stdout.flush()

    print("\n🔍 Validating configuration...")
    logger.info("🔍 Validating configuration...")

    config = get_config()

    if not validate_config(config):
        logger.error("❌ Configuration is invalid!")
        print("\n❌ Configuration is invalid!")
        sys.exit(1)

    logger.info("✅ Configuration validated")
    logger.info("🔒 AOSS query-time owner_user_sub filter enabled (per-user RLS)")
    print("✅ Configuration validated")
    print("🔒 AOSS query-time owner_user_sub filter enabled (per-user RLS)")

    startup_msg = f"""
Starting MCP Server for OpenSearch claim-notes access:
  Region: {config["region"]}
  Collection endpoint: {config["opensearch_collection_endpoint"]}
"""
    logger.info(startup_msg)
    print(startup_msg)
    sys.stdout.flush()

    logger.info("🌐 Starting MCP server on streamable-http transport...")
    print("🌐 Starting MCP server on streamable-http transport...")
    sys.stdout.flush()

    mcp.run(transport="streamable-http")
