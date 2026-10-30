"""
MCP Server for OpenSearch Claim Notes - AgentCore Identity OBO Pattern

This MCP server provides full-text search over claim-note documents in
Amazon OpenSearch Serverless, with per-user row-level security enforced by
a query-time `owner_user_sub` term filter (opensearch_tools.py).

Deployment: notebook 05b-deploy-notes-gateway runs
`agentcore_cli_deploy.py opensearch-mcp`, which deploys the agentcore/ project
in this directory as an AgentCore Runtime (its own CloudFormation stack) and
stores the runtime ARN/ID in SSM (opensearch-mcp-runtime-arn / -id). The notes
gateway created later in the same notebook targets this runtime.

Identity: the caller's `sub` is resolved differently per IdP, selected by
IDP_PROVIDER (see `_resolve_idp_provider`):
- [OKTA] The notes gateway's target performs an OBO token exchange and
  forwards the exchanged, user-scoped token in the Authorization header.
  The runtime's customJWTAuthorizer validates it, and agentcore.json's
  `requestHeaderAllowlist: ["Authorization"]` makes the header readable here
  (`extract_user_sub_from_headers`).
- [COGNITO] The notes REQUEST interceptor Lambda
  (5a-gateway-setup/interceptor-notes) validates the user JWT and injects
  the caller `sub` as params.arguments.context.user_id; the tool reads it
  from its `context` argument.
In both cases this code does no signature verification of its own: the
Okta-path decode is an unverified PyJWT decode used purely to extract claims,
because the runtime authorizer has already validated signature, issuer,
audience and expiry.

Configuration (env vars are set from agentcore.json envVars at deploy time;
each falls back to SSM under /app/lakehouse-agent/ when unset):
- IDP_PROVIDER            → SSM idp-provider (default "cognito")
- OPENSEARCH_COLLECTION_ENDPOINT → SSM opensearch-collection-endpoint
  (written by 5b-obo-gateway-setup/01_deploy_opensearch_collection.py)
- AWS_REGION (falls back to the boto3 session region, then us-east-1)
- LOG_LEVEL (optional, default INFO)
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

# Also send log lines to stderr; stderr is line-buffered below, so each line is
# written as soon as it is logged.
stderr_handler = logging.StreamHandler(sys.stderr)
stderr_handler.setLevel(logging.INFO)
stderr_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
logger.addHandler(stderr_handler)

# Ensure stdout is unbuffered
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# Initialize MCP server (stateless streamable-http, the transport the
# AgentCore Runtime fronts)
mcp = FastMCP(host="0.0.0.0", stateless_http=True)

from opensearch_tools import OpenSearchClaimNotesTools

logger.info("🔒 Using AOSS query-time owner_user_sub filter (OBO production mode)")
print("🔒 Using AOSS query-time owner_user_sub filter (OBO production mode)")

# Global tools instance (one SigV4-signed OpenSearch client per process)
opensearch_tools = None

# Configuration cache, filled on the first get_config() call
_config_cache = None


def _resolve_idp_provider() -> str:
    """IdP selector: IDP_PROVIDER env (set from agentcore.json envVars at deploy
    time) → SSM /app/lakehouse-agent/idp-provider → "cognito" default."""
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

    Env vars win; each missing one is read from SSM /app/lakehouse-agent/<name>.
    The result is cached for the life of the process.
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

    # Get account ID (diagnostic only; nothing below depends on it)
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
    """Fail loudly at startup if region or the AOSS collection endpoint is missing."""
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
    """Lazily build the OpenSearchClaimNotesTools client on first tool call and reuse it."""
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

    [OKTA] path only. The bearer is the OBO-exchanged token the notes gateway
    forwarded, not the user's original login token. The AgentCore Runtime
    customJWTAuthorizer has already validated signature/issuer/audience/expiry;
    this code uses an unverified PyJWT decode purely to extract claims. The
    `requestHeaderAllowlist: ["Authorization"]` entry in agentcore.json makes
    the header readable here.

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
        # same pattern as the claims server (4a-mcp-lakehouse-server/server.py).
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
    (params.arguments.context.user_id = caller sub, set by the notes REQUEST
    interceptor); unused on the Okta OBO path, where identity comes from the
    forwarded bearer. `limit` is validated (1-100) in opensearch_tools.
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
        # Append an invocation record to /tmp as a debugging aid (same pattern
        # as the claims server); failure to write never affects the tool.
        try:
            with open("/tmp/mcp_tool_invoked.log", "a") as f:
                import datetime

                f.write(f"{datetime.datetime.now()} - search_claim_notes invoked\n")
                f.write(f"  query: {query}, limit: {limit}\n")
                f.flush()
        except Exception:
            pass  # Don't fail if we can't write to file

        # Identity source differs by IdP: Okta forwards a user-scoped
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
            # No bearer `sub` (Okta) or no context.user_id (Cognito): return an
            # error and no data. Without a `sub` there is nothing to filter
            # owner_user_sub on, so the only safe answer is an empty one.
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
    # Startup: validate config (exit 1 if incomplete), then serve MCP over
    # streamable-http, the transport the AgentCore Runtime expects.
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
