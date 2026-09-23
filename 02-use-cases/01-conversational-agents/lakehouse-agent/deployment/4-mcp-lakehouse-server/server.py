"""
MCP Server for Health Lakehouse Data - Production Security with Lake Formation

This MCP server provides tools for querying and managing health lakehouse data
with enterprise-grade row-level security enforced by AWS Lake Formation.

Security Architecture:
- OAuth authentication (Cognito JWT tokens)
- User identity extraction from Gateway interceptor
- Lake Formation session tag-based row-level security
- No SQL string interpolation (eliminates SQL injection risk)

IMPORTANT: This server ONLY supports Lake Formation security mode.
Application-level SQL filtering has been removed for security reasons.

Configuration:
- Reads from SSM Parameter Store
- Auto-detects region from boto3 session
- Tenant credentials provided by Gateway interceptor
"""

import sys
import os
import logging
from typing import Any, Dict, Optional
import boto3
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

# PRODUCTION ONLY: Use Lake Formation row-level security
from athena_tools_secure import SecureAthenaClaimsTools as AthenaTools

logger.info("🔒 Using Lake Formation row-level security (production mode)")
print("🔒 Using Lake Formation row-level security (production mode)")

# Global Athena tools instance
athena_tools = None

# Configuration cache
_config_cache = None


def get_config() -> Dict[str, Optional[str]]:
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

    def get_param(name: str, env_var: str = None, default: str = None) -> Optional[str]:
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

    config["s3_bucket_name"] = get_param("s3-bucket-name", "S3_BUCKET_NAME")
    config["database_name"] = get_param("database-name", "ATHENA_DATABASE_NAME")
    config["catalog_name"] = get_param("catalog-name", "CATALOG_NAME")  # For S3 Tables
    config["log_level"] = os.environ.get("LOG_LEVEL", "INFO")

    if config["s3_bucket_name"]:
        config["s3_output_location"] = f"s3://{config['s3_bucket_name']}/athena-results/"
    else:
        config["s3_output_location"] = None

    config["test_user"] = os.environ.get("TEST_USER_1", "policyholder001@example.com")
    config["local_development"] = os.environ.get("LOCAL_DEVELOPMENT", "false").lower() == "true"

    _config_cache = config
    return config


def validate_config(config: Dict[str, Optional[str]]) -> bool:
    required_params = [
        ("region", "AWS Region"),
        ("s3_bucket_name", "S3 Bucket Name"),
        ("database_name", "Athena Database Name"),
    ]

    missing = []
    for param, display_name in required_params:
        if not config.get(param):
            missing.append(display_name)

    if missing:
        print(f"❌ Missing required configuration: {', '.join(missing)}")
        return False

    return True


def get_athena_tools():
    global athena_tools
    if athena_tools is None:
        config = get_config()

        logger.info("Initializing Athena tools with Lake Formation RLS...")
        logger.info(f"  Region: {config['region']}")
        logger.info(f"  Database: {config['database_name']}")
        logger.info(f"  S3 Output: {config['s3_output_location']}")
        print("Initializing Athena tools with Lake Formation RLS...")
        print(f"  Region: {config['region']}")
        print(f"  Database: {config['database_name']}")
        print(f"  Catalog: {config.get('catalog_name', 'default')}")
        print(f"  S3 Output: {config['s3_output_location']}")

        athena_tools = AthenaTools(
            region=config["region"],
            database_name=config["database_name"],
            s3_output_location=config["s3_output_location"],
            catalog_name=config.get("catalog_name"),
        )

        print("✅ Athena tools initialized with Lake Formation RLS")

    return athena_tools


def get_user_id_with_fallback(
    context_arg: Dict[str, Any] = None,
) -> tuple[str, Optional[Dict[str, str]]]:
    """
    Get user ID and tenant credentials from context argument or fallback to test user.

    Returns:
        Tuple of (user_id, tenant_credentials)
    """
    config = get_config()
    user_id = None
    tenant_credentials = None

    if context_arg:
        print(f"📋 Context argument received: {list(context_arg.keys())}")
        user_id = context_arg.get("user_id")
        if user_id:
            print(f"   Got user_id from context: {user_id}")

        # Extract tenant credentials if present
        tenant_creds = context_arg.get("tenant_credentials")
        if tenant_creds:
            print("   Got tenant_credentials from context")
            print(f"   Role: {tenant_creds.get('role_name', 'N/A')}")
            print(f"   Expiration: {tenant_creds.get('expiration', 'N/A')}")
            tenant_credentials = tenant_creds

        if user_id:
            return user_id, tenant_credentials

    if config["local_development"]:
        user_id = config["test_user"]
        print(f"⚠️  Using test user for local development: {user_id}")
        return user_id, None

    print("❌ User identity not found in request")
    return None, None


@mcp.tool(
    name="query_claims",
    description="Query health lakehouse data for the authenticated user with optional filters",
)
def query_claims(
    claim_status: str = None,
    claim_type: str = None,
    start_date: str = None,
    end_date: str = None,
    context: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Query lakehouse data for the authenticated user."""
    # Log to both logger and stderr to ensure visibility
    msg = f"{'=' * 60}\n🔧 TOOL INVOKED: query_claims\n{'=' * 60}"
    logger.info(msg)
    print(msg, file=sys.stderr, flush=True)
    print(msg, flush=True)

    logger.info("📥 INPUT PARAMETERS:")
    logger.info(f"   claim_status: {claim_status}")
    logger.info(f"   claim_type: {claim_type}")
    logger.info(f"   start_date: {start_date}")
    logger.info(f"   end_date: {end_date}")
    logger.info(f"   context keys: {list(context.keys()) if context else None}")

    print("📥 INPUT PARAMETERS:", file=sys.stderr, flush=True)
    print(f"   claim_status: {claim_status}", file=sys.stderr, flush=True)
    print(f"   claim_type: {claim_type}", file=sys.stderr, flush=True)

    print("📥 INPUT PARAMETERS:")
    print(f"   claim_status: {claim_status}")
    print(f"   claim_type: {claim_type}")
    print(f"   start_date: {start_date}")
    print(f"   end_date: {end_date}")
    print(f"   context: {context}")

    try:
        # Write to a file as absolute proof the tool was called
        try:
            with open("/tmp/mcp_tool_invoked.log", "a") as f:
                import datetime

                f.write(f"{datetime.datetime.now()} - query_claims invoked\n")
                f.write(f"  Parameters: status={claim_status}, type={claim_type}\n")
                f.write(f"  Context keys: {list(context.keys()) if context else None}\n")
                f.flush()
        except Exception:
            pass  # Don't fail if we can't write to file

        user_id, tenant_credentials = get_user_id_with_fallback(context)
        logger.info(f"👤 USER ID: {user_id}")
        print(f"👤 USER ID: {user_id}", file=sys.stderr, flush=True)
        print(f"👤 USER ID: {user_id}")
        if tenant_credentials:
            logger.info(f"🔑 TENANT CREDENTIALS: Role {tenant_credentials.get('role_name')}")
            print(
                f"🔑 TENANT CREDENTIALS: Role {tenant_credentials.get('role_name')}",
                file=sys.stderr,
                flush=True,
            )
            print(f"🔑 TENANT CREDENTIALS: Role {tenant_credentials.get('role_name')}")

        if not user_id:
            logger.error("❌ User identity not found in request")
            return {"success": False, "error": "User identity not found in request"}

        filters = {
            k: v
            for k, v in {
                "claim_status": claim_status,
                "claim_type": claim_type,
                "start_date": start_date,
                "end_date": end_date,
            }.items()
            if v is not None
        }

        logger.info(f"🔍 FILTERS: {filters}")
        print(f"🔍 FILTERS: {filters}")
        sys.stdout.flush()
        print(f"🔍 FILTERS: {filters}")

        tools = get_athena_tools()
        result = tools.query_claims(user_id, filters if filters else None, tenant_credentials)

        logger.info("📤 OUTPUT:")
        logger.info(f"   success: {result.get('success', 'N/A')}")
        print("📤 OUTPUT:")
        print(f"   success: {result.get('success', 'N/A')}")
        if result.get("success"):
            claims_count = len(result.get("claims", []))
            logger.info(f"   claims_count: {claims_count}")
            print(f"   claims_count: {claims_count}")
        else:
            logger.error(f"   error: {result.get('error', 'N/A')}")
            print(f"   error: {result.get('error', 'N/A')}")

        logger.info("=" * 60)
        print("=" * 60)
        sys.stdout.flush()
        return result

    except Exception as e:
        logger.error(f"❌ ERROR in query_claims: {str(e)}")
        print(f"❌ ERROR in query_claims: {str(e)}")
        import traceback

        error_trace = traceback.format_exc()
        logger.error(f"   Stack trace: {error_trace}")
        print(f"   Stack trace: {error_trace}")
        logger.error("=" * 60)
        print("=" * 60)
        sys.stdout.flush()
        return {"success": False, "error": str(e)}


@mcp.tool(
    name="get_claim_details",
    description="Get detailed information about a specific claim by ID",
)
def get_claim_details(claim_id: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
    """Get details of a specific claim."""
    print("=" * 60)
    print("🔧 TOOL INVOKED: get_claim_details")
    print("=" * 60)

    print("📥 INPUT PARAMETERS:")
    print(f"   claim_id: {claim_id}")
    print(f"   context: {context}")

    try:
        user_id, tenant_credentials = get_user_id_with_fallback(context)
        print(f"👤 USER ID: {user_id}")
        if tenant_credentials:
            print(f"🔑 TENANT CREDENTIALS: Role {tenant_credentials.get('role_name')}")

        if not user_id:
            return {"success": False, "error": "User identity not found in request"}

        tools = get_athena_tools()
        result = tools.get_claim_details(user_id, claim_id, tenant_credentials)

        print("📤 OUTPUT:")
        print(f"   success: {result.get('success', 'N/A')}")
        if result.get("success"):
            claim_data = result.get("claim", {})
            print(f"   claim_id: {claim_data.get('claim_id', 'N/A')}")
            print(f"   claim_status: {claim_data.get('claim_status', 'N/A')}")
        else:
            print(f"   error: {result.get('error', 'N/A')}")

        print("=" * 60)
        return result

    except Exception as e:
        print(f"❌ ERROR in get_claim_details: {str(e)}")
        import traceback

        print(f"   Stack trace: {traceback.format_exc()}")
        print("=" * 60)
        return {"success": False, "error": str(e)}


@mcp.tool(
    name="get_claims_summary",
    description="Get summary statistics of all claims for the authenticated user",
)
def get_claims_summary(context: Dict[str, Any] = None) -> Dict[str, Any]:
    """Get claims summary for the user."""
    print("=" * 60)
    print("🔧 TOOL INVOKED: get_claims_summary")
    print("=" * 60)

    print("📥 INPUT PARAMETERS:")
    print(f"   context: {context}")

    try:
        user_id, tenant_credentials = get_user_id_with_fallback(context)
        print(f"👤 USER ID: {user_id}")
        if tenant_credentials:
            print(f"🔑 TENANT CREDENTIALS: Role {tenant_credentials.get('role_name')}")

        if not user_id:
            return {"success": False, "error": "User identity not found in request"}

        tools = get_athena_tools()
        result = tools.get_claims_summary(user_id, tenant_credentials)

        print("📤 OUTPUT:")
        print(f"   success: {result.get('success', 'N/A')}")
        if result.get("success"):
            summary = result.get("summary", {})
            print(f"   total_claims: {summary.get('total_claims', 'N/A')}")
            print(f"   total_amount: {summary.get('total_amount', 'N/A')}")
            print(f"   by_status: {summary.get('by_status', 'N/A')}")
        else:
            print(f"   error: {result.get('error', 'N/A')}")

        print("=" * 60)
        return result

    except Exception as e:
        print(f"❌ ERROR in get_claims_summary: {str(e)}")
        import traceback

        print(f"   Stack trace: {traceback.format_exc()}")
        print("=" * 60)
        return {"success": False, "error": str(e)}


@mcp.tool(name="query_login_audit", description="Query user login audit logs from DynamoDB.")
def query_login_audit(
    user_id: str = None,
    limit: int = 10,
    start_date: str = None,
    context: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    Query login audit logs from DynamoDB.

    SECURITY: This tool is RESTRICTED to admin group only.
    Access control should be enforced at the Gateway level using fine-grained access control (FGAC).

    Args:
        user_id: Optional user ID to filter logs (email address). If not provided, returns recent logins across all users.
        limit: Maximum number of records to return (default: 10, max: 100)
        start_date: Optional ISO 8601 date to filter logs from (e.g., "2024-01-01")
        context: Request context containing authenticated user information

    Returns:
        Dictionary with success status and login records
    """
    print("=" * 60)
    print("🔧 TOOL INVOKED: query_login_audit")
    print("⚠️  ADMIN ONLY TOOL - Should be restricted via Gateway FGAC")
    print("=" * 60)

    print("📥 INPUT PARAMETERS:")
    print(f"   user_id: {user_id}")
    print(f"   limit: {limit}")
    print(f"   start_date: {start_date}")
    print(f"   context: {context}")

    try:
        # Get authenticated user from context
        authenticated_user, tenant_credentials = get_user_id_with_fallback(context)
        print(f"👤 AUTHENTICATED USER: {authenticated_user}")

        if not authenticated_user:
            return {"success": False, "error": "User identity not found in request"}

        # Check if user is in administrators group
        # Note: This is a secondary check. Primary access control should be at Gateway level.
        user_groups = []
        if context and "user_groups" in context:
            user_groups = context.get("user_groups", [])
            print(f"👥 USER GROUPS: {user_groups}")

        # Validate limit
        if limit < 1 or limit > 100:
            return {"success": False, "error": "Limit must be between 1 and 100"}

        config = get_config()

        # Use tenant credentials if available, otherwise use default credentials
        if tenant_credentials:
            print("🔑 Using tenant credentials for DynamoDB access")
            dynamodb = boto3.resource(
                "dynamodb",
                region_name=config["region"],
                aws_access_key_id=tenant_credentials["access_key_id"],
                aws_secret_access_key=tenant_credentials["secret_access_key"],
                aws_session_token=tenant_credentials["session_token"],
            )
        else:
            print("⚠️  No tenant credentials found, using default credentials")
            dynamodb = boto3.resource("dynamodb", region_name=config["region"])
        table_name = "lakehouse_user_login_audit"

        try:
            table = dynamodb.Table(table_name)
            print(f"📊 Querying DynamoDB table: {table_name}")

            if user_id:
                # Query specific user's login history
                print(f"🔍 Querying logs for user: {user_id}")

                from boto3.dynamodb.conditions import Key

                key_condition = Key("user_id").eq(user_id)

                # Add date filter if provided
                if start_date:
                    key_condition = key_condition & Key("login_timestamp").gte(start_date)

                response = table.query(
                    KeyConditionExpression=key_condition,
                    ScanIndexForward=False,  # Most recent first
                    Limit=limit,
                )

                records = response.get("Items", [])

            else:
                # Scan for recent logins across all users (use with caution)
                print(f"🔍 Scanning for recent logins across all users (limit: {limit})")

                scan_params = {"Limit": limit}

                # Note: Scan doesn't support date filtering efficiently
                # For production, consider adding a GSI on login_timestamp
                if start_date:
                    from boto3.dynamodb.conditions import Attr

                    scan_params["FilterExpression"] = Attr("login_timestamp").gte(start_date)

                response = table.scan(**scan_params)
                records = response.get("Items", [])

                # Sort by timestamp (most recent first)
                records = sorted(records, key=lambda x: x.get("login_timestamp", ""), reverse=True)

            # Format records for output
            formatted_records = []
            for record in records:
                formatted_records.append(
                    {
                        "user_id": record.get("user_id", ""),
                        "login_timestamp": record.get("login_timestamp", ""),
                        "email": record.get("email", ""),
                        "groups": record.get("groups", "[]"),
                        "source_ip": record.get("source_ip", ""),
                        "user_agent": record.get("user_agent", ""),
                        "client_id": record.get("client_id", ""),
                        "event_type": record.get("event_type", ""),
                    }
                )

            result = {
                "success": True,
                "records": formatted_records,
                "count": len(formatted_records),
                "queried_user": user_id if user_id else "all_users",
                "authenticated_as": authenticated_user,
            }

            print("📤 OUTPUT:")
            print("   success: True")
            print(f"   records_count: {len(formatted_records)}")
            print(f"   queried_user: {user_id if user_id else 'all_users'}")

            print("=" * 60)
            return result

        except dynamodb.meta.client.exceptions.ResourceNotFoundException:
            error_msg = f"DynamoDB table '{table_name}' not found. Run deploy_post_auth_lambda.sh to create it."
            print(f"❌ ERROR: {error_msg}")
            print("=" * 60)
            return {"success": False, "error": error_msg}

    except Exception as e:
        print(f"❌ ERROR in query_login_audit: {str(e)}")
        import traceback

        print(f"   Stack trace: {traceback.format_exc()}")
        print("=" * 60)
        return {"success": False, "error": str(e)}


@mcp.tool(
    name="text_to_sql",
    description="""**This tool is to be used by admin group. admin group users should use this tool to access lakehouse databases.**
    Convert natural language query to SQL and execute it against the lakehouse database. 
    Automatically reads schema from data catalog and generates appropriate SQL query.""",
)
def text_to_sql(natural_language_query: str, context: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Convert natural language to SQL and execute.

    This tool allows users to query the database using natural language.
    It automatically:
    - Reads the database schema from Glue Data Catalog
    - Generates SQL using Amazon Bedrock
    - Executes the query with proper security context
    - Returns results with the generated SQL for transparency

    Example queries:
    - "Show me all pending claims"
    - "What is the total claim amount by claim type?"
    - "List claims submitted in the last 30 days"
    """
    print("=" * 60)
    print("🔧 TOOL INVOKED: text_to_sql")
    print("=" * 60)

    print("📥 INPUT PARAMETERS:")
    print(f"   natural_language_query: {natural_language_query}")
    print(f"   context: {context}")

    try:
        user_id, tenant_credentials = get_user_id_with_fallback(context)
        print(f"👤 USER ID: {user_id}")
        if tenant_credentials:
            print(f"🔑 TENANT CREDENTIALS: Role {tenant_credentials.get('role_name')}")

        if not user_id:
            return {"success": False, "error": "User identity not found in request"}

        tools = get_athena_tools()
        result = tools.text_to_sql(user_id, natural_language_query, tenant_credentials)

        print("📤 OUTPUT:")
        print(f"   success: {result.get('success', 'N/A')}")
        if result.get("success"):
            print(f"   generated_sql: {result.get('generated_sql', 'N/A')}")
            print(f"   results_count: {result.get('count', 0)}")
        else:
            print(f"   error: {result.get('error', 'N/A')}")

        print("=" * 60)
        return result

    except Exception as e:
        print(f"❌ ERROR in text_to_sql: {str(e)}")
        import traceback

        print(f"   Stack trace: {traceback.format_exc()}")
        print("=" * 60)
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    # Log startup
    logger.info("=" * 70)
    logger.info("🚀 MCP SERVER STARTING")
    logger.info("=" * 70)
    print("=" * 70)
    print("🚀 MCP SERVER STARTING")
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
    logger.info("🔒 Lake Formation row-level security enabled")
    print("✅ Configuration validated")
    print("🔒 Lake Formation row-level security enabled")

    startup_msg = f"""
Starting MCP Server for data lakehouse access:
  Region: {config["region"]}
  Database: {config["database_name"]}
  S3 Output: {config["s3_output_location"]}
"""
    logger.info(startup_msg)
    print(startup_msg)
    sys.stdout.flush()

    logger.info("🌐 Starting MCP server on streamable-http transport...")
    print("🌐 Starting MCP server on streamable-http transport...")
    sys.stdout.flush()

    mcp.run(transport="streamable-http")
