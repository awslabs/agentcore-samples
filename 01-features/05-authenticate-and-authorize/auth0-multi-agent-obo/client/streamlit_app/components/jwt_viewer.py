# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
JWT Deep Dive Viewer Component

Educational component that provides detailed visibility into JWT tokens,
their structure, claims, and validation process.
"""

import streamlit as st
import json
import base64
import time
from typing import Dict, Any, Optional, Tuple
from datetime import datetime


def decode_jwt_parts(token: str) -> Tuple[Dict[str, Any], Dict[str, Any], str]:
    """
    Decode JWT into its three parts without verification.

    Args:
        token: Raw JWT string

    Returns:
        Tuple of (header, payload, signature_base64)
    """
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {}, {}, ""

        def decode_part(part: str) -> Dict[str, Any]:
            # Add padding if needed
            padding = 4 - len(part) % 4
            if padding != 4:
                part += "=" * padding
            decoded = base64.urlsafe_b64decode(part)
            return json.loads(decoded)

        header = decode_part(parts[0])
        payload = decode_part(parts[1])
        signature = parts[2]

        return header, payload, signature
    except Exception as e:
        return {"error": str(e)}, {}, ""


def render_jwt_viewer(access_token: Optional[str], id_token: Optional[str], claims_namespace: str = ""):
    """
    Render comprehensive JWT token viewer for educational purposes.

    Args:
        access_token: OAuth access token
        id_token: OIDC ID token
        claims_namespace: Namespace prefix for custom claims
    """
    st.markdown("## JWT Token Deep Dive")
    st.markdown("""
    This view provides educational insight into how JWT (JSON Web Tokens) work
    in the authentication flow. JWTs are the foundation of secure, stateless
    authentication in this application.
    """)

    # Token selector
    token_type = st.radio(
        "Select token to analyze:",
        ["Access Token", "ID Token", "Compare Both"],
        horizontal=True,
        key="jwt_viewer_token_type",
    )

    st.markdown("---")

    if token_type == "Access Token" and access_token:
        render_single_token(access_token, "Access Token", claims_namespace)
    elif token_type == "ID Token" and id_token:
        render_single_token(id_token, "ID Token", claims_namespace)
    elif token_type == "Compare Both":
        render_token_comparison(access_token, id_token, claims_namespace)
    else:
        st.warning("No token available to display.")


def render_single_token(token: str, token_name: str, claims_namespace: str = ""):
    """Render detailed view of a single token."""
    header, payload, signature = decode_jwt_parts(token)

    if "error" in header:
        st.error(f"Failed to decode token: {header['error']}")
        return

    # Token structure visualization
    st.markdown(f"### {token_name} Structure")

    # Visual representation of JWT parts
    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown(
            """
        <div style="background: #ff6b6b; color: white; padding: 15px; border-radius: 8px; text-align: center;">
            <strong>HEADER</strong><br>
            <small>Algorithm & Type</small>
        </div>
        """,
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            """
        <div style="background: #4ecdc4; color: white; padding: 15px; border-radius: 8px; text-align: center;">
            <strong>PAYLOAD</strong><br>
            <small>Claims & Data</small>
        </div>
        """,
            unsafe_allow_html=True,
        )

    with col3:
        st.markdown(
            """
        <div style="background: #45b7d1; color: white; padding: 15px; border-radius: 8px; text-align: center;">
            <strong>SIGNATURE</strong><br>
            <small>Verification</small>
        </div>
        """,
            unsafe_allow_html=True,
        )

    st.markdown("<br>", unsafe_allow_html=True)

    # Detailed breakdown
    tab1, tab2, tab3, tab4 = st.tabs(["Header", "Payload", "Claims Analysis", "Raw Token"])

    with tab1:
        render_header_section(header)

    with tab2:
        render_payload_section(payload, claims_namespace)

    with tab3:
        render_claims_analysis(payload, claims_namespace)

    with tab4:
        render_raw_token(token, header, payload, signature)


def render_header_section(header: Dict[str, Any]):
    """Render JWT header section with explanations."""
    st.markdown("### JWT Header")
    st.markdown("""
    The header typically consists of two parts: the type of token (JWT)
    and the signing algorithm being used (e.g., RS256).
    """)

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Algorithm (alg)**")
        alg = header.get("alg", "Unknown")
        alg_explanations = {
            "RS256": "RSA Signature with SHA-256 - Asymmetric algorithm using public/private key pair",
            "HS256": "HMAC with SHA-256 - Symmetric algorithm using shared secret",
            "ES256": "ECDSA with P-256 and SHA-256 - Elliptic curve digital signature",
        }
        st.code(alg)
        st.info(alg_explanations.get(alg, "Algorithm used for signing the token"))

    with col2:
        st.markdown("**Type (typ)**")
        typ = header.get("typ", "JWT")
        st.code(typ)
        st.info("Token type - always 'JWT' for JSON Web Tokens")

    # Key ID if present
    if "kid" in header:
        st.markdown("**Key ID (kid)**")
        st.code(header["kid"])
        st.info("""
        The Key ID identifies which key from the JWKS (JSON Web Key Set)
        was used to sign this token. This allows key rotation without
        breaking existing tokens.
        """)

    # Full header JSON
    with st.expander("View Full Header JSON"):
        st.json(header)


def render_payload_section(payload: Dict[str, Any], claims_namespace: str = ""):
    """Render JWT payload section with explanations."""
    st.markdown("### JWT Payload (Claims)")
    st.markdown("""
    The payload contains claims - statements about the user and additional metadata.
    Claims are divided into three types: Registered, Public, and Private.
    """)

    # Categorize claims
    registered_claims = {}
    custom_claims = {}
    other_claims = {}

    registered_claim_names = {
        "iss": ("Issuer", "Who issued this token (Auth0 domain)"),
        "sub": ("Subject", "Unique identifier for the user"),
        "aud": ("Audience", "Intended recipient(s) of the token"),
        "exp": ("Expiration Time", "When this token expires (Unix timestamp)"),
        "nbf": ("Not Before", "Token not valid before this time"),
        "iat": ("Issued At", "When this token was issued"),
        "jti": ("JWT ID", "Unique identifier for this token"),
    }

    for key, value in payload.items():
        if key in registered_claim_names:
            registered_claims[key] = value
        elif claims_namespace and key.startswith(claims_namespace):
            custom_claims[key] = value
        else:
            other_claims[key] = value

    # Registered Claims
    st.markdown("#### Registered Claims (Standard)")
    if registered_claims:
        for key, value in registered_claims.items():
            name, description = registered_claim_names.get(key, (key, ""))
            col1, col2, col3 = st.columns([1, 2, 2])
            with col1:
                st.markdown(f"**{key}**")
            with col2:
                # Format timestamps nicely
                if key in ("exp", "iat", "nbf") and isinstance(value, (int, float)):
                    dt = datetime.fromtimestamp(value)
                    st.code(f"{value}\n({dt.strftime('%Y-%m-%d %H:%M:%S')})")
                else:
                    st.code(str(value)[:50] + ("..." if len(str(value)) > 50 else ""))
            with col3:
                st.caption(f"{name}: {description}")
    else:
        st.info("No registered claims found")

    # Custom Claims
    st.markdown("#### Custom Claims (Application-Specific)")
    st.markdown(f"*Namespace: `{claims_namespace or 'None configured'}`*")

    if custom_claims:
        for key, value in custom_claims.items():
            short_key = key.replace(claims_namespace, "") if claims_namespace else key
            col1, col2 = st.columns([1, 3])
            with col1:
                st.markdown(f"**{short_key}**")
            with col2:
                if isinstance(value, (list, dict)):
                    st.json(value)
                else:
                    st.code(str(value))
    else:
        st.info("No custom claims found with the configured namespace")

    # Other Claims
    if other_claims:
        with st.expander("Other Claims"):
            for key, value in other_claims.items():
                st.markdown(f"**{key}:** `{value}`")


def render_claims_analysis(payload: Dict[str, Any], claims_namespace: str = ""):
    """Render analysis of claims for authorization decisions."""
    st.markdown("### Claims Analysis for Authorization")
    st.markdown("""
    This section shows how claims are used for authorization decisions
    in the application. The agent uses these claims to determine what
    resources the user can access.
    """)

    # Token validity
    st.markdown("#### Token Validity")

    exp = payload.get("exp")
    iat = payload.get("iat")
    now = time.time()

    col1, col2, col3 = st.columns(3)

    with col1:
        if exp:
            remaining = exp - now
            if remaining > 0:
                minutes = int(remaining / 60)
                seconds = int(remaining % 60)
                st.success(f"Valid: {minutes}m {seconds}s remaining")
            else:
                st.error("EXPIRED")
        else:
            st.warning("No expiration set")

    with col2:
        if iat:
            age = now - iat
            minutes = int(age / 60)
            st.info(f"Token Age: {minutes} minutes")

    with col3:
        if exp and iat:
            lifetime = exp - iat
            st.info(f"Total Lifetime: {int(lifetime / 3600)}h {int((lifetime % 3600) / 60)}m")

    # Scopes
    st.markdown("#### Scopes (Permissions)")
    scope = payload.get("scope", "")
    if scope:
        scopes = scope.split(" ") if isinstance(scope, str) else scope
        st.markdown("The following scopes are granted to this token:")

        scope_explanations = {
            "openid": "Required for OIDC - returns sub claim",
            "profile": "Access to user profile information",
            "email": "Access to user email address",
            "offline_access": "Allows refresh tokens for long-lived sessions",
            "read:accounts": "Permission to read account information",
            "write:accounts": "Permission to modify account information",
            "read:transactions": "Permission to read transaction history",
            "read:profile": "Permission to read customer profile",
            "write:profile": "Permission to modify customer profile",
        }

        for s in scopes:
            explanation = scope_explanations.get(s, "Application-specific permission")
            st.markdown(f"- `{s}` - {explanation}")
    else:
        st.warning("No scopes found in token")

    # User Identity
    st.markdown("#### User Identity")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Subject (sub)**")
        sub = payload.get("sub", "Not found")
        st.code(sub)
        st.caption("Unique identifier for the user in Auth0")

    with col2:
        # Check for custom customer ID
        customer_id = None
        for key, value in payload.items():
            if "customer_id" in key.lower():
                customer_id = value
                break

        st.markdown("**Customer ID**")
        if customer_id:
            st.code(customer_id)
            st.caption("Business identifier linking user to customer records")
        else:
            st.warning("Not found in claims")

    # Authorization context
    st.markdown("#### Authorization Context Summary")

    auth_context = {
        "user_id": payload.get("sub", "Unknown"),
        "issuer": payload.get("iss", "Unknown"),
        "audience": payload.get("aud", "Unknown"),
        "scopes": scope.split(" ") if scope else [],
        "is_valid": exp and exp > now if exp else False,
    }

    # Add custom claims
    for key, value in payload.items():
        if claims_namespace and key.startswith(claims_namespace):
            short_key = key.replace(claims_namespace, "")
            auth_context[short_key] = value

    st.json(auth_context)


def render_raw_token(token: str, header: Dict, payload: Dict, signature: str):
    """Render raw token view with copy functionality."""
    st.markdown("### Raw Token")
    st.markdown("""
    The complete JWT is a Base64URL-encoded string consisting of three parts
    separated by dots: `header.payload.signature`
    """)

    # Token parts with syntax highlighting
    parts = token.split(".")

    st.markdown("#### Encoded Token Parts")

    st.markdown("**Header (Base64URL):**")
    st.code(parts[0] if len(parts) > 0 else "", language=None)

    st.markdown("**Payload (Base64URL):**")
    st.code(parts[1] if len(parts) > 1 else "", language=None)

    st.markdown("**Signature (Base64URL):**")
    st.code(parts[2] if len(parts) > 2 else "", language=None)

    # Full token
    with st.expander("View Full Token String"):
        st.code(token, language=None)
        st.caption("This token can be decoded at jwt.io for verification")

    # Decoded JSON
    st.markdown("#### Decoded JSON")

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Header:**")
        st.json(header)
    with col2:
        st.markdown("**Payload:**")
        st.json(payload)


def render_token_comparison(access_token: Optional[str], id_token: Optional[str], claims_namespace: str = ""):
    """Render side-by-side comparison of access and ID tokens."""
    st.markdown("### Token Comparison: Access Token vs ID Token")

    st.markdown("""
    | Aspect | Access Token | ID Token |
    |--------|--------------|----------|
    | **Purpose** | Authorization - proves user can access resources | Authentication - proves user identity |
    | **Audience** | API/Resource Server | Client Application |
    | **Used By** | AgentCore Runtime to authorize requests | Streamlit app to display user info |
    | **Contains** | Scopes, permissions, API access claims | User profile, email, name, identity claims |
    """)

    st.markdown("---")

    col1, col2 = st.columns(2)

    # Decode both tokens
    access_header, access_payload, _ = decode_jwt_parts(access_token or "")
    id_header, id_payload, _ = decode_jwt_parts(id_token or "")

    with col1:
        st.markdown("#### Access Token")
        if access_token and "error" not in access_header:
            render_compact_token_view(access_payload, "Access", claims_namespace)
        else:
            st.warning("Access token not available or invalid")

    with col2:
        st.markdown("#### ID Token")
        if id_token and "error" not in id_header:
            render_compact_token_view(id_payload, "ID", claims_namespace)
        else:
            st.warning("ID token not available or invalid")

    # Claims diff
    st.markdown("---")
    st.markdown("#### Claims Comparison")

    if access_payload and id_payload:
        all_keys = set(access_payload.keys()) | set(id_payload.keys())

        comparison_data = []
        for key in sorted(all_keys):
            access_val = access_payload.get(key, "-")
            id_val = id_payload.get(key, "-")

            # Truncate long values
            if isinstance(access_val, str) and len(access_val) > 30:
                access_val = access_val[:30] + "..."
            if isinstance(id_val, str) and len(id_val) > 30:
                id_val = id_val[:30] + "..."

            comparison_data.append(
                {
                    "Claim": key,
                    "Access Token": str(access_val),
                    "ID Token": str(id_val),
                    "Same": str(access_payload.get(key)) == str(id_payload.get(key))
                    if key in access_payload and key in id_payload
                    else False,
                }
            )

        # Display as table
        st.dataframe(
            comparison_data,
            column_config={
                "Claim": st.column_config.TextColumn("Claim", width="medium"),
                "Access Token": st.column_config.TextColumn("Access Token", width="large"),
                "ID Token": st.column_config.TextColumn("ID Token", width="large"),
                "Same": st.column_config.CheckboxColumn("Match", width="small"),
            },
            hide_index=True,
            use_container_width=True,
        )


def render_compact_token_view(payload: Dict[str, Any], token_type: str, claims_namespace: str = ""):
    """Render a compact view of token for comparison."""
    # Validity
    exp = payload.get("exp")
    now = time.time()

    if exp:
        remaining = exp - now
        if remaining > 0:
            st.success(f"Valid ({int(remaining / 60)}m remaining)")
        else:
            st.error("EXPIRED")

    # Key claims
    st.markdown("**Key Claims:**")

    important_claims = ["sub", "iss", "aud", "scope", "azp"]
    for claim in important_claims:
        if claim in payload:
            value = payload[claim]
            if isinstance(value, str) and len(value) > 40:
                value = value[:40] + "..."
            st.markdown(f"- `{claim}`: {value}")

    # Custom claims
    custom = {k: v for k, v in payload.items() if claims_namespace and k.startswith(claims_namespace)}
    if custom:
        st.markdown("**Custom Claims:**")
        for k, v in custom.items():
            short_k = k.replace(claims_namespace, "")
            st.markdown(f"- `{short_k}`: {v}")

    # Full payload
    with st.expander("Full Payload"):
        st.json(payload)


def render_token_exchange_info(exchange_data: dict):
    """
    Render RFC 8693 Token Exchange information in an educational format.

    Shows scope attenuation, delegation chain, and exchange metadata
    so that users can understand how token exchange works in the
    multi-agent AgentCore architecture.

    Args:
        exchange_data: Dictionary containing token exchange metadata.
            Expected keys:
                - original_scopes (list[str]): Scopes from the user's original JWT
                - granted_scopes (list[str]): Attenuated scopes granted to the target agent
                - removed_scopes (list[str]): Scopes that were stripped during exchange
                - act (dict): The 'act' claim from the exchanged token (delegation chain)
                - exchange_id (str): Unique identifier for this exchange
                - target_audience (str): The audience the exchanged token is issued for
                - token_lifetime (int|float): Lifetime of the exchanged token in seconds
    """
    st.markdown("---")
    st.markdown("## RFC 8693 Token Exchange")
    st.markdown("""
    [RFC 8693](https://datatracker.ietf.org/doc/html/rfc8693) defines an OAuth 2.0
    Token Exchange protocol. In AgentCore's multi-agent architecture, the coordinator
    agent exchanges the user's token for a **scoped-down** token before forwarding
    requests to downstream agents. This enforces the **principle of least privilege**
    -- each agent only receives the permissions it needs.
    """)

    # ------------------------------------------------------------------
    # Scope attenuation metric
    # ------------------------------------------------------------------
    original_scopes = exchange_data.get("original_scopes", [])
    granted_scopes = exchange_data.get("granted_scopes", [])
    removed_scopes = exchange_data.get("removed_scopes", [])

    st.markdown("### Scope Attenuation")

    metric_cols = st.columns(3)
    with metric_cols[0]:
        st.metric(
            label="Original Scopes",
            value=len(original_scopes),
            help="Number of scopes in the user's original JWT",
        )
    with metric_cols[1]:
        st.metric(
            label="Granted Scopes",
            value=len(granted_scopes),
            delta=f"-{len(removed_scopes)}" if removed_scopes else "0",
            delta_color="inverse",
            help="Number of scopes granted to the target agent after attenuation",
        )
    with metric_cols[2]:
        st.metric(
            label="Removed Scopes",
            value=len(removed_scopes),
            help="Scopes stripped to enforce least-privilege for the target agent",
        )

    # Visual attenuation indicator
    st.markdown(f"**Attenuation:** `{len(original_scopes)} scopes` --> `{len(granted_scopes)} scopes`")

    # Scope comparison table
    render_scope_diff(original_scopes, granted_scopes, removed_scopes)

    # ------------------------------------------------------------------
    # Delegation chain (act claim)
    # ------------------------------------------------------------------
    act_claim = exchange_data.get("act")
    if act_claim:
        st.markdown("### Delegation Chain (`act` claim)")
        st.markdown("""
        The `act` (actor) claim records **who is acting on behalf of whom**.
        In a multi-agent system the coordinator acts on behalf of the user,
        and downstream agents see the full delegation chain.
        """)

        actor_id = act_claim.get("sub") or act_claim.get("actor_id", "Unknown")
        st.markdown(f"**Actor (acting party):** `{actor_id}`")

        with st.expander("Full `act` claim JSON"):
            st.json(act_claim)

        # Visual chain
        st.markdown("**Delegation flow:**")
        st.markdown(f"```\nUser (subject) --> Coordinator ({actor_id}) --> Target Agent\n```")

    # ------------------------------------------------------------------
    # Exchange metadata
    # ------------------------------------------------------------------
    exchange_id = exchange_data.get("exchange_id")
    target_audience = exchange_data.get("target_audience")
    token_lifetime = exchange_data.get("token_lifetime")

    if any([exchange_id, target_audience, token_lifetime]):
        st.markdown("### Exchange Metadata")

        meta_cols = st.columns(3)
        with meta_cols[0]:
            st.markdown("**Exchange ID**")
            st.code(exchange_id or "N/A")
        with meta_cols[1]:
            st.markdown("**Target Audience**")
            st.code(target_audience or "N/A")
        with meta_cols[2]:
            st.markdown("**Token Lifetime**")
            if token_lifetime is not None:
                minutes = int(token_lifetime) // 60
                seconds = int(token_lifetime) % 60
                st.code(f"{minutes}m {seconds}s ({int(token_lifetime)}s)")
            else:
                st.code("N/A")

    # Full exchange data
    with st.expander("View Full Exchange Data"):
        st.json(exchange_data)


def render_scope_diff(original_scopes: list, granted_scopes: list, removed_scopes: list):
    """
    Render a colored scope comparison table.

    Displays each scope from the union of original scopes with a visual
    indicator: green checkmark for granted scopes, red X for removed scopes.

    Args:
        original_scopes: All scopes from the user's original JWT.
        granted_scopes: Scopes that were granted to the target agent.
        removed_scopes: Scopes that were removed during token exchange.
    """
    # Build a set of all scopes to display (preserving order from original)
    all_scopes_ordered: list = list(original_scopes)
    for s in granted_scopes:
        if s not in all_scopes_ordered:
            all_scopes_ordered.append(s)
    for s in removed_scopes:
        if s not in all_scopes_ordered:
            all_scopes_ordered.append(s)

    granted_set = set(granted_scopes)
    removed_set = set(removed_scopes)

    # Common scope descriptions for educational context
    scope_descriptions = {
        "openid": "Required for OIDC - returns sub claim",
        "profile": "Access to user profile information",
        "email": "Access to user email address",
        "offline_access": "Allows refresh tokens",
        "read:accounts": "Read account information",
        "write:accounts": "Modify account information",
        "read:transactions": "Read transaction history",
        "read:profile": "Read customer profile",
        "write:profile": "Modify customer profile",
    }

    with st.expander("Scope Comparison Details", expanded=True):
        # Table header
        st.markdown(
            "| Status | Scope | Description |\n"
            "|--------|-------|-------------|\n"
            + "\n".join(
                _scope_table_row(scope, granted_set, removed_set, scope_descriptions) for scope in all_scopes_ordered
            )
        )

        # Legend
        st.caption("Legend: :white_check_mark: = Granted to target agent | :x: = Removed during exchange")


def _scope_table_row(scope: str, granted_set: set, removed_set: set, descriptions: dict) -> str:
    """Build a single Markdown table row for the scope diff table."""
    desc = descriptions.get(scope, "Application-specific scope")
    if scope in granted_set:
        return f"| :white_check_mark: Granted | `{scope}` | {desc} |"
    elif scope in removed_set:
        return f"| :x: **Removed** | `{scope}` | {desc} |"
    else:
        return f"| :grey_question: Unknown | `{scope}` | {desc} |"


def render_token_timeline(tokens_info: Dict[str, Any]):
    """Render visual timeline of token lifecycle."""
    st.markdown("### Token Lifecycle Timeline")

    iat = tokens_info.get("issued_at")
    exp = tokens_info.get("expires_at")
    now = time.time()

    if not (iat and exp):
        st.info("Token timing information not available")
        return

    total_lifetime = exp - iat
    elapsed = now - iat
    remaining = exp - now

    # Progress bar
    progress = min(elapsed / total_lifetime, 1.0)

    st.markdown(f"""
    **Token Lifetime Progress:**

    Issued: {datetime.fromtimestamp(iat).strftime("%H:%M:%S")} |
    Expires: {datetime.fromtimestamp(exp).strftime("%H:%M:%S")} |
    Now: {datetime.fromtimestamp(now).strftime("%H:%M:%S")}
    """)

    st.progress(progress)

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Elapsed", f"{int(elapsed / 60)}m {int(elapsed % 60)}s")
    with col2:
        st.metric(
            "Remaining",
            f"{int(remaining / 60)}m {int(remaining % 60)}s" if remaining > 0 else "EXPIRED",
        )
    with col3:
        st.metric("Total Lifetime", f"{int(total_lifetime / 60)}m")
