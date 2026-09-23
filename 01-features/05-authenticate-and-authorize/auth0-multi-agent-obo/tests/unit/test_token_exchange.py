# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for RFC 8693 Token Exchange Service.

Tests the TokenExchangeService, TokenExchangeRequest, TokenExchangeResponse,
and ScopePolicy classes that implement OAuth 2.0 Token Exchange for
agent-to-agent communication with scope attenuation.

RFC 8693 Reference: https://datatracker.ietf.org/doc/html/rfc8693
"""

import importlib.util
import sys
import time
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

# Load token_exchange module directly to avoid the broken shared.auth.__init__
# import chain (shared.auth -> claims_extractor -> shared.models -> stale imports).
_TOKEN_EXCHANGE_PATH = Path(__file__).resolve().parents[2] / "shared" / "auth" / "token_exchange.py"
_spec = importlib.util.spec_from_file_location("shared.auth.token_exchange", _TOKEN_EXCHANGE_PATH)
_token_exchange = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _token_exchange
_spec.loader.exec_module(_token_exchange)

# Re-export everything we need from the dynamically loaded module
TokenExchangeService = _token_exchange.TokenExchangeService
TokenExchangeRequest = _token_exchange.TokenExchangeRequest
TokenExchangeResponse = _token_exchange.TokenExchangeResponse
ScopePolicy = _token_exchange.ScopePolicy
InvalidRequestError = _token_exchange.InvalidRequestError
InvalidTokenError = _token_exchange.InvalidTokenError
InsufficientScopeError = _token_exchange.InsufficientScopeError
GRANT_TYPE_TOKEN_EXCHANGE = _token_exchange.GRANT_TYPE_TOKEN_EXCHANGE
TOKEN_TYPE_JWT = _token_exchange.TOKEN_TYPE_JWT
TOKEN_TYPE_ACCESS_TOKEN = _token_exchange.TOKEN_TYPE_ACCESS_TOKEN
DEFAULT_EXCHANGE_ISSUER = _token_exchange.DEFAULT_EXCHANGE_ISSUER
DEFAULT_TOKEN_LIFETIME = _token_exchange.DEFAULT_TOKEN_LIFETIME

# Re-use the conftest secret for building subject tokens
SAMPLE_JWT_SECRET = "test-secret-key-for-jwt-signing-in-tests-only"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def exchange_service() -> TokenExchangeService:
    """Create a TokenExchangeService instance with default policies."""
    return TokenExchangeService()


@pytest.fixture
def custom_signing_key() -> rsa.RSAPrivateKey:
    """Generate a dedicated RSA key for tests that need a specific key."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def exchange_service_with_key(custom_signing_key) -> TokenExchangeService:
    """Create a TokenExchangeService with a known signing key."""
    return TokenExchangeService(signing_key=custom_signing_key)


@pytest.fixture
def profile_exchange_request(sample_jwt_token: str) -> TokenExchangeRequest:
    """Token exchange request targeting the profile agent."""
    return TokenExchangeRequest(
        subject_token=sample_jwt_token,
        audience="customer_profile_agent",
        scope=(
            "openid profile email "
            "profile:personal:read profile:personal:write "
            "profile:preferences:read profile:preferences:write"
        ),
    )


@pytest.fixture
def accounts_exchange_request(sample_jwt_token: str) -> TokenExchangeRequest:
    """Token exchange request targeting the accounts agent."""
    return TokenExchangeRequest(
        subject_token=sample_jwt_token,
        audience="accounts_agent",
        scope=(
            "openid "
            "accounts:savings:read accounts:savings:write "
            "accounts:transaction:read "
            "accounts:credit:read accounts:credit:write "
            "accounts:investment:read"
        ),
    )


@pytest.fixture
def valid_exchange_request(sample_jwt_token: str) -> TokenExchangeRequest:
    """A generic valid token exchange request."""
    return TokenExchangeRequest(
        subject_token=sample_jwt_token,
        audience="some-target-agent",
        scope="openid profile",
    )


@pytest.fixture
def profile_scope_policy() -> ScopePolicy:
    """Scope policy for the profile agent (fine-grained)."""
    return ScopePolicy(
        target_agent="customer_profile_agent",
        allowed_scopes={
            "openid",
            "profile",
            "email",
            "profile:personal:read",
            "profile:personal:write",
            "profile:preferences:read",
            "profile:preferences:write",
        },
        description="Profile agent: OIDC identity + profile read/write",
    )


@pytest.fixture
def accounts_scope_policy() -> ScopePolicy:
    """Scope policy for the accounts agent (fine-grained)."""
    return ScopePolicy(
        target_agent="accounts_agent",
        allowed_scopes={
            "openid",
            "accounts:savings:read",
            "accounts:savings:write",
            "accounts:transaction:read",
            "accounts:credit:read",
            "accounts:credit:write",
            "accounts:investment:read",
        },
        description="Accounts agent: openid + account-type scopes",
    )


# ---------------------------------------------------------------------------
# TestTokenExchangeRequest
# ---------------------------------------------------------------------------


class TestTokenExchangeRequest:
    """Test TokenExchangeRequest validation per RFC 8693 Section 2.1."""

    def test_valid_request_passes_validation(self, sample_jwt_token: str):
        """A well-formed request should produce no validation errors."""
        request = TokenExchangeRequest(
            grant_type=GRANT_TYPE_TOKEN_EXCHANGE,
            subject_token=sample_jwt_token,
            subject_token_type=TOKEN_TYPE_JWT,
            audience="customer_profile_agent",
            scope="openid profile",
        )
        errors = request.validate()
        assert errors == []

    def test_missing_subject_token_fails(self):
        """Request without a subject_token must fail validation."""
        request = TokenExchangeRequest(
            grant_type=GRANT_TYPE_TOKEN_EXCHANGE,
            subject_token="",
            audience="customer_profile_agent",
        )
        errors = request.validate()
        assert any("subject_token" in e for e in errors)

    def test_wrong_grant_type_fails(self, sample_jwt_token: str):
        """Request with an incorrect grant_type must fail validation."""
        request = TokenExchangeRequest(
            grant_type="authorization_code",
            subject_token=sample_jwt_token,
            audience="customer_profile_agent",
        )
        errors = request.validate()
        assert any("grant_type" in e for e in errors)

    def test_missing_audience_fails(self, sample_jwt_token: str):
        """Request without an audience must fail validation."""
        request = TokenExchangeRequest(
            subject_token=sample_jwt_token,
            audience="",
        )
        errors = request.validate()
        assert any("audience" in e for e in errors)

    def test_unsupported_token_type_fails(self, sample_jwt_token: str):
        """Request with an unsupported subject_token_type must fail."""
        request = TokenExchangeRequest(
            subject_token=sample_jwt_token,
            subject_token_type="urn:unsupported:type",
            audience="customer_profile_agent",
        )
        errors = request.validate()
        assert any("subject_token_type" in e for e in errors)


# ---------------------------------------------------------------------------
# TestScopePolicy
# ---------------------------------------------------------------------------


class TestScopePolicy:
    """Test ScopePolicy scope attenuation logic."""

    def test_attenuation_computes_intersection(self, profile_scope_policy: ScopePolicy):
        """Attenuation should return only scopes in both the original set and the policy."""
        original = [
            "openid",
            "profile",
            "email",
            "profile:personal:read",
            "profile:personal:write",
            "profile:preferences:read",
            "profile:preferences:write",
            "accounts:savings:read",
            "accounts:credit:read",  # should be stripped
        ]
        result = profile_scope_policy.attenuate(original)
        assert set(result) == {
            "openid",
            "profile",
            "email",
            "profile:personal:read",
            "profile:personal:write",
            "profile:preferences:read",
            "profile:preferences:write",
        }
        assert "accounts:savings:read" not in result
        assert "accounts:credit:read" not in result

    def test_empty_intersection_returns_empty(self):
        """When original scopes share nothing with the policy, result is empty."""
        policy = ScopePolicy(
            target_agent="restricted_agent",
            allowed_scopes={"admin:superuser"},
        )
        result = policy.attenuate(["openid", "profile", "profile:personal:read"])
        assert result == []

    def test_no_elevation_possible(self, accounts_scope_policy: ScopePolicy):
        """Requesting scopes not in the original token must not appear in the result."""
        original = ["openid", "profile"]
        result = accounts_scope_policy.attenuate(original)
        # accounts_scope_policy allows openid + all accounts:* scopes
        # but original only has openid and profile
        assert "accounts:savings:read" not in result
        assert "accounts:credit:read" not in result
        assert result == ["openid"]


# ---------------------------------------------------------------------------
# TestTokenExchangeService
# ---------------------------------------------------------------------------


class TestTokenExchangeService:
    """Test the core token exchange flow."""

    def test_exchange_produces_valid_jwt(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """Exchanged token must be a valid JWT decodable with the service public key."""
        response = exchange_service.exchange_token(profile_exchange_request)
        public_key_pem = exchange_service.get_public_key_pem()

        decoded = jwt.decode(
            response.access_token,
            public_key_pem,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert decoded["sub"] == "auth0|123456789"
        assert "act" in decoded

    def test_exchanged_token_has_attenuated_scopes(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """Profile agent should only receive profile-related scopes."""
        response = exchange_service.exchange_token(profile_exchange_request)
        decoded = jwt.decode(
            response.access_token,
            exchange_service.get_public_key_pem(),
            algorithms=["RS256"],
            options={"verify_aud": False},
        )

        granted = set(decoded["scope"].split())
        # Profile policy allows: openid, profile, email, profile:personal:*, profile:preferences:*
        # Original token has all scopes. Intersection keeps profile-related scopes.
        assert "openid" in granted
        assert "profile:personal:read" in granted
        assert "profile:preferences:read" in granted
        # No account scopes should be present
        assert "accounts:savings:read" not in granted
        assert "accounts:credit:read" not in granted
        assert "accounts:investment:read" not in granted

    def test_accounts_agent_scope_attenuation(
        self,
        exchange_service: TokenExchangeService,
        accounts_exchange_request: TokenExchangeRequest,
    ):
        """Accounts agent should receive only account-related scopes."""
        response = exchange_service.exchange_token(accounts_exchange_request)
        decoded = jwt.decode(
            response.access_token,
            exchange_service.get_public_key_pem(),
            algorithms=["RS256"],
            options={"verify_aud": False},
        )

        granted = set(decoded["scope"].split())
        # accounts policy allows: openid + accounts:savings:read/write, accounts:transaction:read,
        #   accounts:credit:read/write, accounts:investment:read (+ legacy compat)
        # original token has all scopes including all accounts:* scopes
        # intersection: openid + all accounts:* scopes
        assert "openid" in granted
        assert "accounts:savings:read" in granted
        assert "accounts:transaction:read" in granted
        assert "accounts:credit:read" in granted
        assert "accounts:investment:read" in granted
        # No profile scopes should be present
        assert "profile" not in granted
        assert "email" not in granted
        assert "profile:personal:read" not in granted
        assert "profile:preferences:read" not in granted

    def test_act_claim_present(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """Exchanged token must contain an act claim per RFC 8693 Section 4.4."""
        response = exchange_service.exchange_token(profile_exchange_request)
        decoded = jwt.decode(
            response.access_token,
            exchange_service.get_public_key_pem(),
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert "act" in decoded
        assert isinstance(decoded["act"], dict)

    def test_act_claim_has_actor_sub(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """The act claim must contain the sub of the actor performing the exchange."""
        response = exchange_service.exchange_token(
            profile_exchange_request,
            actor_id="coordinator-agent",
        )
        decoded = jwt.decode(
            response.access_token,
            exchange_service.get_public_key_pem(),
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert decoded["act"]["sub"] == "coordinator-agent"

    def test_sub_claim_preserved(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """The user's sub claim must remain unchanged after exchange."""
        response = exchange_service.exchange_token(profile_exchange_request)
        decoded = jwt.decode(
            response.access_token,
            exchange_service.get_public_key_pem(),
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert decoded["sub"] == "auth0|123456789"

    def test_custom_claims_preserved(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """Namespace-prefixed custom claims must be forwarded to the exchanged token."""
        response = exchange_service.exchange_token(profile_exchange_request)
        decoded = jwt.decode(
            response.access_token,
            exchange_service.get_public_key_pem(),
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert decoded["https://agentcore.example.com/customer_id"] == "CUST-12345"
        assert decoded["https://agentcore.example.com/roles"] == ["customer", "premium"]

    def test_new_audience_set(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """The exchanged token audience must match the target agent."""
        response = exchange_service.exchange_token(profile_exchange_request)
        decoded = jwt.decode(
            response.access_token,
            exchange_service.get_public_key_pem(),
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert decoded["aud"] == "customer_profile_agent"

    def test_new_issuer_set(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """The exchanged token issuer must be the exchange service, not Auth0."""
        response = exchange_service.exchange_token(profile_exchange_request)
        decoded = jwt.decode(
            response.access_token,
            exchange_service.get_public_key_pem(),
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert decoded["iss"] == DEFAULT_EXCHANGE_ISSUER
        assert decoded["iss"] != "https://your-tenant.auth0.com/"

    def test_short_expiry(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """Exchanged token must have a short lifetime (5 minutes), not the original 24 hours."""
        response = exchange_service.exchange_token(profile_exchange_request)
        decoded = jwt.decode(
            response.access_token,
            exchange_service.get_public_key_pem(),
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        # Token lifetime should be ~DEFAULT_TOKEN_LIFETIME (300 seconds)
        token_lifetime = decoded["exp"] - decoded["iat"]
        assert token_lifetime == DEFAULT_TOKEN_LIFETIME
        assert token_lifetime == 300
        assert response.expires_in == DEFAULT_TOKEN_LIFETIME

    def test_exchange_id_unique_per_call(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """Each exchange must produce a unique exchange_id."""
        response1 = exchange_service.exchange_token(profile_exchange_request)
        response2 = exchange_service.exchange_token(profile_exchange_request)
        assert response1.exchange_id != response2.exchange_id
        assert response1.exchange_id.startswith("tex-")
        assert response2.exchange_id.startswith("tex-")

    def test_expired_subject_token_rejected(
        self,
        exchange_service: TokenExchangeService,
        expired_jwt_token: str,
    ):
        """An expired subject token must be rejected."""
        request = TokenExchangeRequest(
            subject_token=expired_jwt_token,
            audience="customer_profile_agent",
        )
        with pytest.raises(InvalidTokenError, match="expired"):
            exchange_service.exchange_token(request)

    def test_invalid_subject_token_rejected(
        self,
        exchange_service: TokenExchangeService,
    ):
        """A malformed subject token must be rejected."""
        request = TokenExchangeRequest(
            subject_token="not.a.valid.jwt",
            audience="customer_profile_agent",
        )
        with pytest.raises(InvalidTokenError, match="(Cannot decode|Invalid JWT format)"):
            exchange_service.exchange_token(request)

    def test_scope_never_elevated(
        self,
        exchange_service: TokenExchangeService,
    ):
        """Requesting more scopes than the original token has must fail."""
        # Create a token with minimal scopes
        now = int(time.time())
        payload = {
            "iss": "https://your-tenant.auth0.com/",
            "sub": "auth0|123456789",
            "aud": "https://agentcore-financial-api",
            "exp": now + 3600,
            "iat": now,
            "scope": "openid",
        }
        minimal_token = jwt.encode(payload, SAMPLE_JWT_SECRET, algorithm="HS256")

        # Target an audience that has no scope policy, request elevated scopes
        request = TokenExchangeRequest(
            subject_token=minimal_token,
            audience="unknown_agent_with_no_policy",
            scope="openid admin:superuser",
        )
        with pytest.raises(InsufficientScopeError, match="exceed original"):
            exchange_service.exchange_token(request)

    def test_empty_scope_attenuation_fails(
        self,
        exchange_service: TokenExchangeService,
    ):
        """When scope attenuation results in zero scopes, it must raise."""
        # Create a token whose scopes have zero overlap with any policy
        now = int(time.time())
        payload = {
            "iss": "https://your-tenant.auth0.com/",
            "sub": "auth0|123456789",
            "aud": "https://agentcore-financial-api",
            "exp": now + 3600,
            "iat": now,
            "scope": "admin:superuser admin:delete",
        }
        no_overlap_token = jwt.encode(payload, SAMPLE_JWT_SECRET, algorithm="HS256")

        request = TokenExchangeRequest(
            subject_token=no_overlap_token,
            audience="customer_profile_agent",
        )
        with pytest.raises(InsufficientScopeError, match="empty scope set"):
            exchange_service.exchange_token(request)

    def test_original_issuer_in_claims(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """The exchanged token must record the original issuer for audit trail."""
        response = exchange_service.exchange_token(profile_exchange_request)
        decoded = jwt.decode(
            response.access_token,
            exchange_service.get_public_key_pem(),
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        assert decoded["original_issuer"] == "https://your-tenant.auth0.com/"

    def test_response_has_scope_diff(
        self,
        exchange_service: TokenExchangeService,
        accounts_exchange_request: TokenExchangeRequest,
    ):
        """The response must report original, granted, and removed scopes."""
        response = exchange_service.exchange_token(accounts_exchange_request)

        # Original scopes from the sample JWT payload (fine-grained)
        assert "openid" in response.original_scopes
        assert "accounts:savings:read" in response.original_scopes

        # Granted scopes are the attenuated set
        assert len(response.granted_scopes) > 0
        assert set(response.granted_scopes).issubset(set(response.original_scopes))

        # Removed scopes = original - granted (profile scopes should be removed)
        assert len(response.removed_scopes) > 0
        assert "profile" in response.removed_scopes
        assert "profile:personal:read" in response.removed_scopes
        expected_removed = set(response.original_scopes) - set(response.granted_scopes)
        assert set(response.removed_scopes) == expected_removed


# ---------------------------------------------------------------------------
# TestTokenExchangeValidation
# ---------------------------------------------------------------------------


class TestTokenExchangeValidation:
    """Test validation of exchanged tokens by sub-agents."""

    def test_validate_own_token_succeeds(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """The service must be able to validate tokens it has issued."""
        response = exchange_service.exchange_token(profile_exchange_request)
        claims = exchange_service.validate_exchanged_token(response.access_token)
        assert claims["sub"] == "auth0|123456789"
        assert "act" in claims

    def test_validate_with_wrong_key_fails(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """Validation must fail when a different key is used."""
        response = exchange_service.exchange_token(profile_exchange_request)

        # Create a second service with a different key pair
        other_service = TokenExchangeService()
        with pytest.raises(InvalidTokenError):
            other_service.validate_exchanged_token(response.access_token)

    def test_validate_expired_token_fails(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """Validation must reject expired exchanged tokens."""
        # Use a service with 0-second lifetime to produce an already-expired token
        short_lived_service = TokenExchangeService(
            token_lifetime=0,
            signing_key=exchange_service._private_key,
            issuer=exchange_service.issuer,
        )
        response = short_lived_service.exchange_token(profile_exchange_request)

        # Give it a moment so the token is certainly expired
        time.sleep(1)

        with pytest.raises(InvalidTokenError, match="expired"):
            short_lived_service.validate_exchanged_token(response.access_token)

    def test_validate_wrong_issuer_fails(
        self,
        exchange_service: TokenExchangeService,
        profile_exchange_request: TokenExchangeRequest,
    ):
        """Validation must reject tokens issued by a different issuer."""
        # Create a service with a different issuer but same signing key
        other_issuer_service = TokenExchangeService(
            issuer="urn:some-other-issuer",
            signing_key=exchange_service._private_key,
        )
        response = other_issuer_service.exchange_token(profile_exchange_request)

        # Validate against original service (expects DEFAULT_EXCHANGE_ISSUER)
        with pytest.raises(InvalidTokenError, match="issuer mismatch"):
            exchange_service.validate_exchanged_token(response.access_token)

    def test_validate_missing_act_claim_fails(
        self,
        exchange_service: TokenExchangeService,
    ):
        """Validation must reject tokens without an act claim (not exchanged tokens)."""
        # Manually build a token signed by the service key but missing act
        now = int(time.time())
        claims = {
            "sub": "auth0|123456789",
            "iss": DEFAULT_EXCHANGE_ISSUER,
            "aud": "some-agent",
            "exp": now + 300,
            "iat": now,
        }
        token = jwt.encode(claims, exchange_service._private_key, algorithm="RS256")

        with pytest.raises(InvalidTokenError, match="Missing act claim"):
            exchange_service.validate_exchanged_token(token)


# ---------------------------------------------------------------------------
# TestTokenExchangeServiceInit
# ---------------------------------------------------------------------------


class TestTokenExchangeServiceInit:
    """Test TokenExchangeService initialization and configuration."""

    def test_default_policies_created(self):
        """Service should create default scope policies for profile and accounts agents."""
        service = TokenExchangeService()
        profile_policy = service.get_scope_policy("profile")
        accounts_policy = service.get_scope_policy("accounts")

        assert profile_policy is not None
        assert accounts_policy is not None
        # Profile policy has fine-grained profile scopes
        assert "profile:personal:read" in profile_policy.allowed_scopes
        assert "profile:preferences:write" in profile_policy.allowed_scopes
        # Accounts policy has fine-grained account scopes
        assert "accounts:savings:read" in accounts_policy.allowed_scopes
        assert "accounts:credit:read" in accounts_policy.allowed_scopes
        assert "accounts:investment:read" in accounts_policy.allowed_scopes
        # Accounts policy should NOT have profile scopes
        assert "profile:personal:read" not in accounts_policy.allowed_scopes
        assert "profile" not in accounts_policy.allowed_scopes

    def test_custom_policies_used(self):
        """Service should use custom policies when provided."""
        custom_policies = {
            "special": ScopePolicy(
                target_agent="special_agent",
                allowed_scopes={"openid", "special:read"},
                description="Special agent policy",
            ),
        }
        service = TokenExchangeService(scope_policies=custom_policies)

        assert service.get_scope_policy("special") is not None
        assert service.get_scope_policy("profile") is None
        assert "special:read" in service.get_scope_policy("special").allowed_scopes

    def test_jwks_format_valid(self):
        """JWKS output must conform to the expected structure."""
        service = TokenExchangeService()
        jwks = service.get_jwks()

        assert "keys" in jwks
        assert len(jwks["keys"]) == 1

        key = jwks["keys"][0]
        assert key["kty"] == "RSA"
        assert key["use"] == "sig"
        assert key["alg"] == "RS256"
        assert "kid" in key
        assert "n" in key
        assert "e" in key

    def test_public_key_pem_format(self):
        """Public key PEM should start and end with standard markers."""
        service = TokenExchangeService()
        pem = service.get_public_key_pem()

        assert pem.startswith("-----BEGIN PUBLIC KEY-----")
        assert pem.strip().endswith("-----END PUBLIC KEY-----")
