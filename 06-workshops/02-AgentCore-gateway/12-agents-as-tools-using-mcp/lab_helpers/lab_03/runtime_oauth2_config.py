"""
Lab 03: Runtime OAuth2 Configuration - Token Validation Setup

Configures AgentCore Runtime to accept and validate M2M OAuth2 tokens from Gateway.

Architecture:
- Runtime receives requests from Gateway with Authorization: Bearer {M2M_token}
- Runtime validates JWT signature using Cognito public keys
- Runtime checks token scopes and authorizes operations
- Only allows requests with valid tokens and required scopes

Token Validation Flow:
1. Gateway sends Bearer token in Authorization header
2. Runtime intercepts request and extracts JWT
3. Runtime fetches Cognito public key using kid from JWT header
4. Runtime verifies JWT signature
5. Runtime checks scopes in token claims
6. Runtime allows/denies operation based on scopes
"""

import json
import boto3
from typing import Dict, Optional
from lab_helpers.config import AWS_REGION, AWS_PROFILE
from lab_helpers.parameter_store import get_parameter, put_parameter
from lab_helpers.constants import PARAMETER_PATHS


class RuntimeOAuth2Configuration:
    """Configure Runtime to validate incoming M2M OAuth2 tokens"""

    def __init__(self, region: str = AWS_REGION, profile: str = AWS_PROFILE):
        """Initialize Runtime OAuth2 configuration"""
        self.session = boto3.Session(profile_name=profile, region_name=region)
        self.agentcore = self.session.client("bedrock-agentcore-control", region_name=region)
        self.ssm = self.session.client("ssm", region_name=region)
        self.sts = self.session.client("sts", region_name=region)

        self.region = region
        self.account_id = self.sts.get_caller_identity()["Account"]
        self.prefix = "aiml301"

    def configure_runtime_token_validation(self, runtime_id: str, cognito_config: Optional[Dict] = None) -> Dict:
        """
        Configure Runtime to validate M2M tokens from Gateway

        Args:
            runtime_id: AgentCore Runtime ID
            cognito_config: Cognito configuration (fetches from SSM if not provided)

        Returns:
            Runtime OAuth2 validation configuration
        """
        print("\n" + "=" * 70)
        print("CONFIGURING RUNTIME TO VALIDATE M2M TOKENS")
        print("=" * 70 + "\n")

        # Get Cognito configuration if not provided
        if not cognito_config:
            try:
                user_pool_id = get_parameter(PARAMETER_PATHS["cognito"]["user_pool_id"])
                token_endpoint = get_parameter(PARAMETER_PATHS["cognito"]["token_endpoint"])
                resource_server_id = get_parameter(PARAMETER_PATHS["cognito"]["resource_server_identifier"])
                m2m_client_id = get_parameter(PARAMETER_PATHS["cognito"]["m2m_client_id"])

                cognito_config = {
                    "user_pool_id": user_pool_id,
                    "token_endpoint": token_endpoint,
                    "resource_server_id": resource_server_id,
                    "m2m_client_id": m2m_client_id,
                    "region": self.region,
                }

                print("✅ Retrieved Cognito configuration from SSM")
            except Exception as e:
                print(f"❌ Failed to retrieve Cognito configuration: {e}")
                raise

        print("\nRuntime OAuth2 Configuration:")
        print(f"  Runtime ID: {runtime_id}")
        print(f"  User Pool: {cognito_config['user_pool_id']}")
        print(f"  M2M Client: {cognito_config['m2m_client_id']}")
        print(f"  Resource Server: {cognito_config['resource_server_id']}\n")

        # Build Runtime OAuth2 configuration
        runtime_oauth2_config = {
            "runtime_id": runtime_id,
            "inbound_auth_type": "OAUTH2_JWT",
            "oauth2_config": {
                "issuer": f"https://cognito-idp.{self.region}.amazonaws.com/{cognito_config['user_pool_id']}",
                "jwks_uri": f"https://cognito-idp.{self.region}.amazonaws.com/{cognito_config['user_pool_id']}/.well-known/jwks.json",
                "audience": [cognito_config["m2m_client_id"]],
                "token_use": "access",
            },
            "scope_config": {
                "required_scopes": [
                    f"{cognito_config['resource_server_id']}/mcp.invoke",
                    f"{cognito_config['resource_server_id']}/runtime.access",
                ],
                "scope_strategy": "REQUIRE_ANY",  # Require at least one scope
            },
            "token_validation": {
                "validate_signature": True,
                "validate_expiration": True,
                "validate_issuer": True,
                "validate_audience": True,
                "clock_skew_seconds": 60,  # Allow 60 second clock skew
            },
        }

        print("Runtime OAuth2 Validation Configuration:")
        print(
            f"  Inbound Auth Type: {runtime_oauth2_config['inbound_auth_type']}"
        )  # codeql[py/clear-text-logging-sensitive-data]
        print(
            f"  Issuer: {runtime_oauth2_config['oauth2_config']['issuer']}"
        )  # codeql[py/clear-text-logging-sensitive-data]
        print(
            f"  JWKS URI: {runtime_oauth2_config['oauth2_config']['jwks_uri']}"
        )  # codeql[py/clear-text-logging-sensitive-data]
        print(f"  Required Scopes: {', '.join(runtime_oauth2_config['scope_config']['required_scopes'])}")
        print(
            f"  Validate Signature: {runtime_oauth2_config['token_validation']['validate_signature']}"
        )  # codeql[py/clear-text-logging-sensitive-data]
        print(
            f"  Validate Expiration: {runtime_oauth2_config['token_validation']['validate_expiration']}\n"
        )  # codeql[py/clear-text-logging-sensitive-data]

        # Save configuration to SSM
        put_parameter(
            f"/{self.prefix}/lab-03/runtime-oauth2-config",
            json.dumps(runtime_oauth2_config, indent=2),
        )

        print("✅ Runtime OAuth2 configuration saved to SSM Parameter Store")

        return runtime_oauth2_config

    def create_runtime_iam_policy_for_token_validation(self, runtime_role_arn: str) -> None:
        """
        Create IAM policy for Runtime to validate tokens

        Permissions needed:
        - Fetch Cognito public keys (JWKS endpoint)
        - Access token validation service

        Args:
            runtime_role_arn: Runtime IAM role ARN
        """
        print("\n" + "=" * 70)
        print("UPDATING RUNTIME IAM ROLE FOR TOKEN VALIDATION")
        print("=" * 70 + "\n")

        # Extract role name from ARN
        role_name = runtime_role_arn.split("/")[-1]

        print(f"Updating IAM role: {role_name}\n")

        # Create token validation policy
        token_validation_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "CognitoJWKSAccess",
                    "Effect": "Allow",
                    "Action": [
                        "cognito-idp:GetSigningCertificate",
                        "cognito-idp:GetUserPoolMxconfigAttribute",
                    ],
                    "Resource": f"arn:aws:cognito-idp:{self.region}:{self.account_id}:userpool/*",
                },
                {
                    "Sid": "CognitoUserPoolAccess",
                    "Effect": "Allow",
                    "Action": ["cognito-idp:DescribeUserPool"],
                    "Resource": f"arn:aws:cognito-idp:{self.region}:{self.account_id}:userpool/*",
                },
                {
                    "Sid": "CloudWatchLogsForTokenValidation",
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                    ],
                    "Resource": f"arn:aws:logs:{self.region}:{self.account_id}:log-group:/aws/bedrock-agentcore/runtime/token-validation*",
                },
            ],
        }

        try:
            iam = boto3.client("iam")
            iam.put_role_policy(
                RoleName=role_name,
                PolicyName=f"{self.prefix}-runtime-token-validation-policy",
                PolicyDocument=json.dumps(token_validation_policy),
            )

            print("✅ Runtime IAM role updated with token validation permissions")
            print(f"   Policy: {self.prefix}-runtime-token-validation-policy")
            print("   Permissions:")
            print("     • Cognito JWKS access")
            print("     • User pool inspection")
            print("     • Token validation logging\n")

        except Exception as e:
            print(f"❌ Failed to update IAM role: {e}")
            raise

    def generate_runtime_token_validation_code(self) -> str:
        """
        Generate Python code for Runtime to validate tokens

        This code runs in the Runtime MCP server and validates incoming tokens.

        Returns:
            Python code for token validation
        """
        return '''
# Token Validation Module for AgentCore Runtime
import json
import jwt
from typing import Dict, Optional
from functools import lru_cache
import requests
from datetime import datetime, timedelta

class TokenValidator:
    """Validates incoming OAuth2 M2M tokens from Gateway"""

    def __init__(self, user_pool_id: str, region: str):
        self.user_pool_id = user_pool_id
        self.region = region
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self.jwks_uri = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"
        self.jwks_cache = {}
        self.cache_expiration = None

    @lru_cache(maxsize=1)
    def _fetch_jwks(self) -> Dict:
        """Fetch and cache Cognito public keys"""
        try:
            response = requests.get(self.jwks_uri, timeout=5)
            response.raise_for_status()
            self.jwks_cache = response.json()
            # Cache for 1 hour
            self.cache_expiration = datetime.now() + timedelta(hours=1)
            return self.jwks_cache
        except Exception as e:
            print(f"Error fetching JWKS: {e}")
            raise

    def get_signing_key(self, token_header: Dict) -> Dict:
        """Get signing key from JWKS matching token kid"""
        kid = token_header.get('kid')
        jwks = self._fetch_jwks()

        for key in jwks.get('keys', []):
            if key.get('kid') == kid:
                return key

        raise ValueError(f"Signing key not found for kid: {kid}")

    def validate_token(
        self,
        token: str,
        required_scopes: list,
        m2m_client_id: str
    ) -> Dict:
        """
        Validate incoming M2M token

        Args:
            token: JWT access token from Authorization header
            required_scopes: List of required scopes
            m2m_client_id: Expected M2M client ID

        Returns:
            Decoded token claims if valid

        Raises:
            jwt.InvalidTokenError: If token is invalid
        """
        try:
            # Decode header to get kid (without verification first)
            header = jwt.get_unverified_header(token)
            signing_key = self.get_signing_key(header)

            # Verify and decode token
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=['RS256'],
                issuer=self.issuer,
                audience=m2m_client_id,
                options={
                    'verify_exp': True,
                    'verify_iss': True,
                    'verify_aud': True
                }
            )

            # Verify scopes
            token_scope = claims.get('scope', '')
            token_scopes = set(token_scope.split())
            required_scope_set = set(required_scopes)

            # Check if token has at least one required scope
            if not token_scopes & required_scope_set:
                raise jwt.InvalidScopeError(
                    f"Token missing required scopes. "
                    f"Token scopes: {token_scopes}, Required: {required_scope_set}"
                )

            print(f"✅ Token validated successfully")
            print(f"   Client: {claims.get('client_id')}")
            print(f"   Scopes: {token_scope}")
            print(f"   Exp: {datetime.fromtimestamp(claims.get('exp'))}")

            return claims

        except jwt.ExpiredSignatureError:
            raise jwt.InvalidTokenError("Token has expired")
        except jwt.InvalidSignatureError:
            raise jwt.InvalidTokenError("Invalid token signature")
        except jwt.InvalidIssuerError:
            raise jwt.InvalidTokenError("Invalid token issuer")
        except jwt.InvalidAudienceError:
            raise jwt.InvalidTokenError("Invalid token audience")
        except Exception as e:
            raise jwt.InvalidTokenError(f"Token validation failed: {str(e)}")

    def extract_token_from_header(self, auth_header: str) -> str:
        """Extract Bearer token from Authorization header"""
        if not auth_header:
            raise ValueError("Missing Authorization header")

        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            raise ValueError("Invalid Authorization header format")

        return parts[1]


# Example usage in MCP server request handler:
"""
from fastmcp import FastMCP

validator = TokenValidator(
    user_pool_id="us-west-2_u7o1G39EX",
    region="us-west-2"
)

@mcp_server.post("/mcp")
async def handle_mcp_request(request: Request):
    # Extract and validate token
    auth_header = request.headers.get("Authorization")

    try:
        token = validator.extract_token_from_header(auth_header)
        claims = validator.validate_token(
            token=token,
            required_scopes=[
                "aiml301-agentcore-runtime/mcp.invoke",
                "aiml301-agentcore-runtime/runtime.access"
            ],
            m2m_client_id="41msff1c7p1brqi0jj7pr1bl9f"
        )

        # Token is valid, proceed with MCP request
        # Extract client from claims for audit logging
        client_id = claims.get("client_id")
        print(f"Processing MCP request from {client_id}")

    except jwt.InvalidTokenError as e:
        return {"error": f"Unauthorized: {str(e)}"}, 401
    except ValueError as e:
        return {"error": f"Bad Request: {str(e)}"}, 400
"""
'''

    def print_runtime_token_validation_guide(self) -> None:
        """Print guide for implementing token validation in Runtime"""
        print("\n" + "=" * 70)
        print("RUNTIME TOKEN VALIDATION IMPLEMENTATION GUIDE")
        print("=" * 70 + "\n")

        print("To implement token validation in your Runtime MCP server:\n")

        print("1️⃣  Add OAuth2 token validation library:")
        print("   pip install PyJWT requests\n")

        print("2️⃣  Import TokenValidator module (see generated code):\n")

        print("3️⃣  Initialize validator in your MCP server:\n")
        print("   ```python")
        print("   validator = TokenValidator(")
        print("       user_pool_id='us-west-2_u7o1G39EX',")
        print("       region='us-west-2'")
        print("   )\n")
        print("   ```\n")

        print("4️⃣  Validate tokens in request handler:\n")
        print("   ```python")
        print("   @mcp_server.post('/mcp')")
        print("   async def handle_request(request):")
        print("       auth_header = request.headers.get('Authorization')")
        print("       token = validator.extract_token_from_header(auth_header)")
        print("       claims = validator.validate_token(")
        print("           token=token,")
        print("           required_scopes=['aiml301-agentcore-runtime/mcp.invoke'],")
        print("           m2m_client_id='41msff1c7p1brqi0jj7pr1bl9f'")
        print("       )")
        print("       # Process request with validated claims")
        print("   ```\n")

        print("5️⃣  Token validation checks:")
        print("   ✓ JWT signature (RS256)")
        print("   ✓ Token expiration")
        print("   ✓ Issuer (Cognito User Pool)")
        print("   ✓ Audience (M2M Client ID)")
        print("   ✓ Required scopes\n")

        print("6️⃣  Audit logging:")
        print("   Include in logs:")
        print("   - client_id (from token)")
        print("   - scopes (authorization)")
        print("   - operation requested")
        print("   - timestamp\n")

        print("=" * 70 + "\n")


def setup_runtime_oauth2_validation_complete(runtime_id: str, runtime_role_arn: str) -> Dict:
    """
    Complete setup workflow for Runtime OAuth2 token validation

    Args:
        runtime_id: Runtime ID
        runtime_role_arn: Runtime IAM role ARN

    Returns:
        Complete OAuth2 validation configuration
    """
    print("\n" + "=" * 70)
    print("SETTING UP RUNTIME OAUTH2 TOKEN VALIDATION")
    print("=" * 70 + "\n")

    config = RuntimeOAuth2Configuration()

    # Step 1: Configure token validation
    runtime_oauth2_config = config.configure_runtime_token_validation(runtime_id=runtime_id)

    # Step 2: Update IAM role with token validation permissions
    config.create_runtime_iam_policy_for_token_validation(runtime_role_arn=runtime_role_arn)

    # Step 3: Print implementation guide
    config.print_runtime_token_validation_guide()

    # Step 4: Save token validation code
    validation_code = config.generate_runtime_token_validation_code()
    put_parameter("/aiml301/lab-03/runtime-token-validation-code", validation_code)

    print("✅ Token validation code saved to SSM Parameter Store")
    print("   Path: /aiml301/lab-03/runtime-token-validation-code\n")

    return runtime_oauth2_config
