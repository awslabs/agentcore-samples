# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
RFC 8693 OAuth 2.0 Token Exchange Service.

Implements token exchange for agent-to-agent communication with scope attenuation.
The coordinator agent exchanges the user's original JWT for a new token with
reduced scopes before invoking each sub-agent, following RFC 8693 semantics.

RFC 8693 Reference: https://datatracker.ietf.org/doc/html/rfc8693

Key RFC 8693 concepts implemented:
- grant_type: urn:ietf:params:oauth:grant-type:token-exchange
- subject_token / subject_token_type: The original JWT being exchanged
- requested_token_type: The desired output token type
- audience: The target agent receiving the exchanged token
- scope: The requested (attenuated) scope set
- act claim: Delegation chain tracking which agent performed the exchange
"""

import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

# OpenTelemetry tracing for token exchange observability
try:
    from opentelemetry import trace

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

logger = logging.getLogger(__name__)

# RFC 8693 Constants
GRANT_TYPE_TOKEN_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
TOKEN_TYPE_JWT = "urn:ietf:params:oauth:token-type:jwt"
TOKEN_TYPE_ACCESS_TOKEN = "urn:ietf:params:oauth:token-type:access_token"

# Default token exchange issuer identifier
DEFAULT_EXCHANGE_ISSUER = "urn:agentcore:token-exchange-service"

# Default exchanged token lifetime (seconds)
DEFAULT_TOKEN_LIFETIME = 300  # 5 minutes


@dataclass
class TokenExchangeRequest:
    """
    RFC 8693 Token Exchange Request.

    See: https://datatracker.ietf.org/doc/html/rfc8693#section-2.1
    """

    grant_type: str = GRANT_TYPE_TOKEN_EXCHANGE
    subject_token: str = ""
    subject_token_type: str = TOKEN_TYPE_JWT
    requested_token_type: str = TOKEN_TYPE_JWT
    audience: str = ""
    scope: str = ""
    resource: str = ""
    actor_token: Optional[str] = None
    actor_token_type: Optional[str] = None

    def validate(self) -> List[str]:
        """Validate the request per RFC 8693 Section 2.1."""
        errors = []
        if self.grant_type != GRANT_TYPE_TOKEN_EXCHANGE:
            errors.append(f"Invalid grant_type: expected {GRANT_TYPE_TOKEN_EXCHANGE}, got {self.grant_type}")
        if not self.subject_token:
            errors.append("subject_token is required")
        if self.subject_token_type not in (TOKEN_TYPE_JWT, TOKEN_TYPE_ACCESS_TOKEN):
            errors.append(f"Unsupported subject_token_type: {self.subject_token_type}")
        if not self.audience:
            errors.append("audience is required (target agent identifier)")
        return errors


@dataclass
class TokenExchangeResponse:
    """
    RFC 8693 Token Exchange Response.

    See: https://datatracker.ietf.org/doc/html/rfc8693#section-2.2
    """

    access_token: str = ""
    issued_token_type: str = TOKEN_TYPE_JWT
    token_type: str = "Bearer"
    expires_in: int = DEFAULT_TOKEN_LIFETIME
    scope: str = ""
    refresh_token: Optional[str] = None

    # Additional metadata (not part of RFC but useful for observability)
    original_scopes: List[str] = field(default_factory=list)
    granted_scopes: List[str] = field(default_factory=list)
    removed_scopes: List[str] = field(default_factory=list)
    exchange_id: str = ""
    subject_token_issuer: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to RFC 8693 response format."""
        response = {
            "access_token": self.access_token,
            "issued_token_type": self.issued_token_type,
            "token_type": self.token_type,
            "expires_in": self.expires_in,
            "scope": self.scope,
        }
        if self.refresh_token:
            response["refresh_token"] = self.refresh_token
        return response


@dataclass
class ScopePolicy:
    """
    Defines the allowed scopes for a target agent.

    This is the policy enforcement point — it determines which scopes
    from the original token are permitted for each target agent.
    """

    target_agent: str
    allowed_scopes: Set[str]
    description: str = ""

    def attenuate(self, original_scopes: List[str]) -> List[str]:
        """
        Compute the intersection of original scopes and allowed scopes.

        Per RFC 8693 Section 2.1: "The requested scope MUST NOT include any
        scope not originally granted to the subject_token."

        Args:
            original_scopes: Scopes from the original token

        Returns:
            Attenuated scope list (intersection of original and allowed)
        """
        return sorted(set(original_scopes) & self.allowed_scopes)


class TokenExchangeError(Exception):
    """Base exception for token exchange errors."""

    def __init__(self, error: str, error_description: str):
        self.error = error
        self.error_description = error_description
        super().__init__(f"{error}: {error_description}")


class InvalidRequestError(TokenExchangeError):
    """RFC 8693 invalid_request error."""

    def __init__(self, description: str):
        super().__init__("invalid_request", description)


class InvalidTokenError(TokenExchangeError):
    """RFC 8693 invalid_target / invalid subject token error."""

    def __init__(self, description: str):
        super().__init__("invalid_token", description)


class InsufficientScopeError(TokenExchangeError):
    """Raised when requested scopes exceed original token scopes."""

    def __init__(self, description: str):
        super().__init__("insufficient_scope", description)


class TokenExchangeService:
    """
    RFC 8693 Token Exchange Service for AgentCore agent-to-agent communication.

    This service enables the coordinator agent to exchange a user's JWT for
    a new token with attenuated scopes before invoking each sub-agent. This
    implements the principle of least privilege across agent boundaries.

    Architecture:
        User -> [Full JWT] -> Coordinator -> [Exchange] -> [Attenuated JWT] -> Sub-Agent

    Key properties:
    - Scope attenuation: Exchanged tokens have fewer scopes than the original
    - Delegation chain: The `act` claim tracks which agent performed the exchange
    - Short-lived: Exchanged tokens have a 5-minute lifetime
    - Auditable: Every exchange is logged with a unique exchange_id
    - Non-elevating: Exchanged tokens can never have more scopes than the original
    """

    def __init__(
        self,
        issuer: str = DEFAULT_EXCHANGE_ISSUER,
        token_lifetime: int = DEFAULT_TOKEN_LIFETIME,
        scope_policies: Optional[Dict[str, ScopePolicy]] = None,
        signing_key: Optional[rsa.RSAPrivateKey] = None,
    ):
        """
        Initialize the Token Exchange Service.

        Args:
            issuer: The issuer identifier for exchanged tokens
            token_lifetime: Lifetime of exchanged tokens in seconds
            scope_policies: Mapping of target agent names to their scope policies
            signing_key: RSA private key for signing tokens (generated if not provided)
        """
        self.issuer = issuer
        self.token_lifetime = token_lifetime

        # Generate or use provided RSA key pair
        if signing_key:
            self._private_key = signing_key
        else:
            self._private_key = rsa.generate_private_key(
                public_exponent=65537,
                key_size=2048,
            )
        self._public_key = self._private_key.public_key()

        # Key ID for JWKS identification
        self._kid = f"tex-{uuid.uuid4().hex[:8]}"

        # Scope policies per target agent
        self._scope_policies = scope_policies or self._default_scope_policies()

        # Exchange counter for observability
        self._exchange_count = 0

        logger.info(
            f"TokenExchangeService initialized: issuer={issuer}, "
            f"token_lifetime={token_lifetime}s, kid={self._kid}, "
            f"policies={list(self._scope_policies.keys())}"
        )

    @staticmethod
    def _default_scope_policies() -> Dict[str, ScopePolicy]:
        """
        Default scope policies for the financial services agents.

        These define the maximum scopes each sub-agent can receive using
        fine-grained, resource-level scopes:
        - Profile agent: OIDC identity + profile:personal + profile:preferences (no accounts)
        - Accounts agent: openid + all accounts:* scopes (no profile/email)
        """
        return {
            "profile": ScopePolicy(
                target_agent="customer_profile_agent",
                allowed_scopes={
                    "openid",
                    "profile",
                    "email",
                    # Fine-grained profile scopes
                    "profile:personal:read",
                    "profile:personal:write",
                    "profile:preferences:read",
                    "profile:preferences:write",
                },
                description="Profile agent: OIDC identity + profile read/write, no account access",
            ),
            "accounts": ScopePolicy(
                target_agent="accounts_agent",
                allowed_scopes={
                    "openid",
                    # Fine-grained account scopes
                    "accounts:savings:read",
                    "accounts:savings:write",
                    "accounts:transaction:read",
                    "accounts:credit:read",
                    "accounts:credit:write",
                    "accounts:investment:read",
                },
                description="Accounts agent: openid + account-type scopes, no profile access",
            ),
        }

    def get_scope_policy(self, target_agent: str) -> Optional[ScopePolicy]:
        """Get the scope policy for a target agent."""
        return self._scope_policies.get(target_agent)

    def get_public_key_pem(self) -> str:
        """Get the public key in PEM format for token verification."""
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

    def get_jwks(self) -> Dict[str, Any]:
        """
        Get the JSON Web Key Set for this service.

        Sub-agents use this to validate exchanged tokens.

        Returns:
            JWKS dictionary with the service's public key
        """
        public_numbers = self._public_key.public_numbers()

        def _int_to_base64url(n: int) -> str:
            byte_length = (n.bit_length() + 7) // 8
            n_bytes = n.to_bytes(byte_length, byteorder="big")
            return base64.urlsafe_b64encode(n_bytes).rstrip(b"=").decode("ascii")

        return {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": self._kid,
                    "n": _int_to_base64url(public_numbers.n),
                    "e": _int_to_base64url(public_numbers.e),
                }
            ]
        }

    def exchange_token(
        self,
        request: TokenExchangeRequest,
        actor_id: str = "coordinator-agent",
    ) -> TokenExchangeResponse:
        """
        Exchange a subject token for an attenuated token per RFC 8693.

        This is the core method that implements the token exchange:
        1. Validates the request format (RFC 8693 Section 2.1)
        2. Decodes and validates the subject token
        3. Resolves scope policy for the target audience
        4. Computes attenuated scopes (intersection of original and allowed)
        5. Builds new token with act claim and reduced scopes
        6. Signs with RSA key and returns response

        Args:
            request: The token exchange request
            actor_id: Identifier for the agent performing the exchange

        Returns:
            TokenExchangeResponse with the new attenuated token

        Raises:
            InvalidRequestError: If the request format is invalid
            InvalidTokenError: If the subject token is invalid or expired
            InsufficientScopeError: If scope attenuation results in empty scopes
        """
        exchange_id = f"tex-{uuid.uuid4().hex[:12]}"
        self._exchange_count += 1

        logger.info(
            json.dumps(
                {
                    "event": "token_exchange_started",
                    "exchange_id": exchange_id,
                    "audience": request.audience,
                    "requested_scope": request.scope,
                    "exchange_number": self._exchange_count,
                }
            )
        )

        # Acquire OTEL tracer if available
        tracer = trace.get_tracer(__name__) if OTEL_AVAILABLE else None
        span = None
        if tracer:
            span = tracer.start_span("token_exchange")
            span.set_attribute("token_exchange.exchange_id", exchange_id)
            span.set_attribute("token_exchange.audience", request.audience)
            span.set_attribute("token_exchange.requested_scope", request.scope)

        try:
            # Step 1: Validate request format
            errors = request.validate()
            if errors:
                logger.warning(f"Token exchange request validation failed: {errors}")
                raise InvalidRequestError("; ".join(errors))

            # Step 2: Decode subject token claims by reading the payload directly.
            # Signature was already validated by AgentCore's JWT Authorizer on inbound,
            # so we only need to extract claims here for scope attenuation.
            try:
                parts = request.subject_token.split(".")
                if len(parts) != 3:
                    raise InvalidTokenError("Invalid JWT format: expected 3 segments")
                payload_b64 = parts[1]
                # Restore base64 padding
                payload_b64 += "=" * (4 - len(payload_b64) % 4)
                subject_claims = json.loads(base64.urlsafe_b64decode(payload_b64))
            except (ValueError, json.JSONDecodeError) as e:
                raise InvalidTokenError(f"Cannot decode subject_token: {e}")

            # Step 3: Check subject token expiration
            exp = subject_claims.get("exp", 0)
            if exp and exp < time.time():
                raise InvalidTokenError("subject_token has expired")

            # Step 4: Resolve scope policy for target audience
            original_scopes = self._extract_scopes(subject_claims)
            scope_policy = self._resolve_scope_policy(request.audience)

            if scope_policy:
                granted_scopes = scope_policy.attenuate(original_scopes)
            elif request.scope:
                # If no policy, use explicitly requested scopes (must be subset)
                requested = set(request.scope.split())
                if not requested.issubset(set(original_scopes)):
                    elevated = requested - set(original_scopes)
                    raise InsufficientScopeError(f"Requested scopes exceed original token: {elevated}")
                granted_scopes = sorted(requested)
            else:
                # No policy and no explicit scopes — pass through original
                granted_scopes = original_scopes

            # Step 5: Ensure we have at least one scope
            if not granted_scopes:
                raise InsufficientScopeError(
                    f"Scope attenuation resulted in empty scope set for "
                    f"audience={request.audience}. Original scopes: {original_scopes}, "
                    f"Policy allows: {scope_policy.allowed_scopes if scope_policy else 'N/A'}"
                )

            removed_scopes = sorted(set(original_scopes) - set(granted_scopes))

            # Step 6: Build exchanged token claims
            now = int(time.time())
            exchanged_claims = {
                # Preserved from original — user identity stays the same
                "sub": subject_claims.get("sub"),
                "email": subject_claims.get("email"),
                "name": subject_claims.get("name"),
                # New token metadata
                "iss": self.issuer,
                "aud": request.audience,
                "exp": now + self.token_lifetime,
                "iat": now,
                "nbf": now,
                "jti": exchange_id,
                # Attenuated scope
                "scope": " ".join(granted_scopes),
                # RFC 8693 Section 4.4: act claim — delegation chain
                "act": {
                    "sub": actor_id,
                },
                # Audit and traceability
                "original_issuer": subject_claims.get("iss", ""),
                "original_audience": subject_claims.get("aud", ""),
                "exchange_id": exchange_id,
                # Preserve custom claims (namespace-prefixed)
                **self._extract_custom_claims(subject_claims),
            }

            # Step 7: Sign the token
            exchanged_token = jwt.encode(
                exchanged_claims,
                self._private_key,
                algorithm="RS256",
                headers={"kid": self._kid, "typ": "at+jwt"},
            )

            # Build response
            response = TokenExchangeResponse(
                access_token=exchanged_token,
                issued_token_type=TOKEN_TYPE_JWT,
                token_type="Bearer",
                expires_in=self.token_lifetime,
                scope=" ".join(granted_scopes),
                original_scopes=original_scopes,
                granted_scopes=granted_scopes,
                removed_scopes=removed_scopes,
                exchange_id=exchange_id,
                subject_token_issuer=subject_claims.get("iss", ""),
            )

            # Record span attributes for scope attenuation observability
            if span:
                span.set_attribute("token_exchange.original_scope_count", len(original_scopes))
                span.set_attribute("token_exchange.granted_scope_count", len(granted_scopes))
                span.set_attribute("token_exchange.removed_scope_count", len(removed_scopes))
                span.set_attribute("token_exchange.original_scopes", ", ".join(original_scopes))
                span.set_attribute("token_exchange.granted_scopes", ", ".join(granted_scopes))
                span.set_attribute("token_exchange.removed_scopes", ", ".join(removed_scopes))
                span.set_attribute("token_exchange.actor_id", actor_id)
                span.set_attribute("token_exchange.token_lifetime", self.token_lifetime)
                span.set_attribute("token_exchange.subject_token_issuer", subject_claims.get("iss", ""))

            logger.info(
                json.dumps(
                    {
                        "event": "token_exchange_completed",
                        "exchange_id": exchange_id,
                        "audience": request.audience,
                        "original_scope_count": len(original_scopes),
                        "granted_scope_count": len(granted_scopes),
                        "removed_scope_count": len(removed_scopes),
                        "original_scopes": original_scopes,
                        "granted_scopes": granted_scopes,
                        "removed_scopes": removed_scopes,
                        "token_lifetime": self.token_lifetime,
                        "actor": actor_id,
                    }
                )
            )

            return response

        except Exception:
            if span:
                span.record_exception(exception=__import__("sys").exc_info()[1])
                span.set_status(trace.StatusCode.ERROR)
            raise
        finally:
            if span:
                span.end()

    def validate_exchanged_token(
        self,
        token: str,
        expected_audience: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Validate a token issued by this exchange service.

        Used by sub-agents to verify tokens they receive from the coordinator.

        Args:
            token: The JWT to validate
            expected_audience: Expected audience claim value

        Returns:
            Decoded and validated claims dictionary

        Raises:
            InvalidTokenError: If the token is invalid
        """
        try:
            options = {"verify_aud": bool(expected_audience)}
            kwargs = {}
            if expected_audience:
                kwargs["audience"] = expected_audience

            claims = jwt.decode(
                token,
                self._public_key,
                algorithms=["RS256"],
                options=options,
                **kwargs,
            )

            # Verify this token was issued by us
            if claims.get("iss") != self.issuer:
                raise InvalidTokenError(f"Token issuer mismatch: expected {self.issuer}, got {claims.get('iss')}")

            # Verify act claim is present (exchanged tokens must have it)
            if "act" not in claims:
                raise InvalidTokenError("Missing act claim — not an exchanged token")

            return claims

        except jwt.ExpiredSignatureError:
            raise InvalidTokenError("Exchanged token has expired")
        except jwt.InvalidTokenError as e:
            raise InvalidTokenError(f"Invalid exchanged token: {e}")

    def _extract_scopes(self, claims: Dict[str, Any]) -> List[str]:
        """Extract scopes from token claims."""
        scope = claims.get("scope", "")
        if isinstance(scope, str):
            return scope.split() if scope else []
        elif isinstance(scope, list):
            return scope
        return []

    def _extract_custom_claims(self, claims: Dict[str, Any]) -> Dict[str, Any]:
        """Extract custom namespaced claims to preserve in exchanged token."""
        namespace = "https://agentcore.example.com/"
        custom = {}
        for key, value in claims.items():
            if key.startswith(namespace):
                custom[key] = value
        return custom

    def _resolve_scope_policy(self, audience: str) -> Optional[ScopePolicy]:
        """
        Resolve which scope policy applies for a given audience.

        Matches by checking if the audience string contains any policy key.
        """
        audience_lower = audience.lower()
        for key, policy in self._scope_policies.items():
            if key in audience_lower or policy.target_agent in audience_lower:
                return policy
        return None

    def get_exchange_stats(self) -> Dict[str, Any]:
        """Get exchange service statistics for observability."""
        return {
            "issuer": self.issuer,
            "kid": self._kid,
            "token_lifetime": self.token_lifetime,
            "total_exchanges": self._exchange_count,
            "policies": {
                name: {
                    "target": policy.target_agent,
                    "allowed_scopes": sorted(policy.allowed_scopes),
                    "description": policy.description,
                }
                for name, policy in self._scope_policies.items()
            },
        }
