"""Entra ID sign-in adapter for the BFF (MSAL confidential client).

Implements the three-method interface app.py expects:
    login_redirect(request)  -> RedirectResponse to the IdP
    exchange_code(request)    -> (user_dict, access_token)
    label()                   -> button text

The scope requested is GATEWAY_SCOPE, the fully qualified
`api://<GATEWAY_CLIENT_ID>/access_as_user`. Entra's /authorize only accepts the
qualified form, and the resulting token's `aud` is the bare GUID — which is
what both the runtime and the gateway are configured to accept. One token,
both hops: that is the passthrough contract.
"""

from __future__ import annotations

import os
import secrets

import msal
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse


class EntraAuth:
    name = "entra"

    def __init__(self) -> None:
        self.tenant_id = _env("TENANT_ID")
        self.client_id = _env("FRONTEND_CLIENT_ID")
        self.client_secret = _env("FRONTEND_CLIENT_SECRET")
        self.scope = _env("GATEWAY_SCOPE")
        self.redirect_uri = os.environ.get("FRONTEND_REDIRECT_URI", "http://localhost:8000/auth/callback")
        self.authority = f"https://login.microsoftonline.com/{self.tenant_id}"

    def label(self) -> str:
        return "Sign in with Microsoft"

    def _client(self) -> msal.ConfidentialClientApplication:
        return msal.ConfidentialClientApplication(
            client_id=self.client_id,
            client_credential=self.client_secret,
            authority=self.authority,
        )

    def login_redirect(self, request: Request) -> RedirectResponse:
        state = secrets.token_urlsafe(16)
        request.session["auth_state"] = state
        url = self._client().get_authorization_request_url(
            scopes=[self.scope], state=state, redirect_uri=self.redirect_uri
        )
        return RedirectResponse(url, status_code=302)

    def exchange_code(self, request: Request) -> tuple[dict, str]:
        code = request.query_params.get("code")
        state = request.query_params.get("state")
        if not code or state != request.session.get("auth_state"):
            raise HTTPException(400, "Invalid OAuth callback — missing or mismatched state")
        result = self._client().acquire_token_by_authorization_code(
            code=code, scopes=[self.scope], redirect_uri=self.redirect_uri
        )
        if "error" in result:
            raise HTTPException(400, f"{result['error']}: {result.get('error_description', '')}")
        claims = result.get("id_token_claims", {}) or {}
        user = {
            "name": claims.get("name"),
            "username": claims.get("preferred_username"),
            # Entra's `sub` is pairwise per application, so `oid` is the
            # stable per-user value worth showing here.
            "subject_label": "oid",
            "subject": claims.get("oid"),
        }
        return user, result["access_token"]


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required env var {name} is not set (see config.example.env)")
    return value
