# ============================================================================
# IMPORTS
# ============================================================================

from .config import get_oauth_settings
from . import mylogger

logger = mylogger.get_logger()

# Global variables for OAuth state
_oauth_initialized = False
_token_getter = None

# ============================================================================
# OAUTH SETUP
# ============================================================================


def setup_oauth():
    """
    Set up OAuth token getter using bedrock_agentcore.identity.

    Returns:
        bool: True if successful, False if not available
    """
    global _oauth_initialized, _token_getter

    if _oauth_initialized:
        return True

    # Try multiple import paths for bedrock_agentcore.identity
    import_attempts = [
        "bedrock_agentcore.identity",
        "bedrock_agentcore.runtime.identity",
        "agentcore.identity",
        "agentcore.runtime.identity",
    ]

    requires_access_token = None

    for import_path in import_attempts:
        try:
            logger.info(f"🔍 Trying to import from: {import_path}")
            if import_path == "bedrock_agentcore.identity":
                from bedrock_agentcore.identity import requires_access_token
            elif import_path == "bedrock_agentcore.runtime.identity":
                from bedrock_agentcore.runtime.identity import requires_access_token
            elif import_path == "agentcore.identity":
                from agentcore.identity import requires_access_token
            elif import_path == "agentcore.runtime.identity":
                from agentcore.runtime.identity import requires_access_token

            logger.info(f"✅ Successfully imported from: {import_path}")
            break

        except ImportError as e:
            logger.info(f"⚠️ Import failed for {import_path}: {e}")
            continue

    if requires_access_token is None:
        logger.warning("⚠️ bedrock_agentcore.identity not available in any import path - OAuth disabled")
        return False

    try:
        # Get OAuth settings
        oauth_settings = get_oauth_settings()
        provider_name = oauth_settings["provider_name"]
        scopes = oauth_settings["scopes"]
        auth_flow = oauth_settings["auth_flow"]

        # logger.info(f"🔐 Setting up OAuth with provider: {provider_name}")
        # logger.info(f"🔐 Scopes: {scopes}")
        # logger.info(f"🔐 Auth flow: {auth_flow}")

        # Create token getter function
        @requires_access_token(
            provider_name=provider_name,
            scopes=scopes,
            auth_flow=auth_flow,
            force_authentication=False,
        )
        def get_token_sync(*, access_token: str):
            return access_token

        _token_getter = get_token_sync
        _oauth_initialized = True

        logger.info("✅ OAuth initialized.")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to initialize OAuth: {e}")
        return False


# ============================================================================
# TOKEN MANAGEMENT
# ============================================================================


def get_m2m_token():
    """
    Get M2M token for gateway access.

    Returns:
        str: OAuth token or None if not available
    """
    global _token_getter

    if not _oauth_initialized or not _token_getter:
        logger.warning("⚠️ OAuth not initialized - no token available")
        return None

    try:
        logger.info("🔑 Requesting M2M token from OAuth provider...")
        token = _token_getter()
        if token:
            logger.info("✅ M2M token obtained successfully")
            logger.info(f"🔑 Token length: {len(token)} characters")
            logger.info(f"🔑 Token starts with: {token[:20]}...")
            return token
        else:
            logger.warning("⚠️ No token returned from OAuth provider")
            return None

    except Exception as e:
        logger.error(f"❌ Failed to get M2M token: {e}")
        import traceback

        logger.error(f"❌ Full traceback: {traceback.format_exc()}")
        return None


# ============================================================================
# ERROR HANDLING
# ============================================================================


def is_oauth_available():
    """
    Check if OAuth functionality is available.

    Returns:
        bool: True if OAuth is available and initialized
    """
    return _oauth_initialized and _token_getter is not None
