"""Okta sign-in adapter for the BFF (authlib, authorization code + PKCE).

Implements the same three-method interface as auth_entra.py:
    login_redirect(request)  -> RedirectResponse to the IdP
    exchange_code(request)    -> (user_dict, access_token)
    label()                   -> button text

The scope requested is `openid profile email` plus GATEWAY_SCOPE (the short
`access_as_user`; Okta uses one spelling end to end). The token is issued by
the CUSTOM authorization server, so its `aud` is that server's `audiences`
value — which is what both the runtime and the gateway accept. One token, both
hops: that is the passthrough contract.
"""

from __future__ import annotations

import os

from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request


class OktaAuth:
    name = "okta"

    def __init__(self) -> None:
        domain = _env("OKTA_DOMAIN")
        as_id = os.environ.get("OKTA_AUTH_SERVER_ID") or "default"
        self.redirect_uri = os.environ.get("FRONTEND_REDIRECT_URI", "http://localhost:8000/auth/callback")
        scope = f"openid profile email {_env('GATEWAY_SCOPE')}"
        self.oauth = OAuth()
        self.oauth.register(
            name="okta",
            client_id=_env("FRONTEND_CLIENT_ID"),
            client_secret=_env("FRONTEND_CLIENT_SECRET"),
            server_metadata_url=(f"https://{domain}/oauth2/{as_id}/.well-known/openid-configuration"),
            client_kwargs={
                "scope": scope,
                # PKCE is sent even though this is a confidential client with a
                # secret, and even though 00_create_okta_apps.py does not set
                # "Require PKCE" on the app. authlib omits the code challenge
                # unless asked, so this is opt-in: it costs nothing, and it means
                # the flow still works if you later tighten the app to require
                # PKCE.
                "code_challenge_method": "S256",
            },
        )

    def label(self) -> str:
        return "Sign in with Okta"

    async def login_redirect(self, request: Request):
        return await self.oauth.okta.authorize_redirect(request, self.redirect_uri)

    async def exchange_code(self, request: Request) -> tuple[dict, str]:
        try:
            token = await self.oauth.okta.authorize_access_token(request)
        except Exception as e:
            raise HTTPException(400, f"OAuth callback failed: {e}") from e
        access_token = token.get("access_token")
        if not access_token:
            raise HTTPException(400, "No access_token in the Okta response")
        claims = token.get("userinfo") or {}
        user = {
            "name": claims.get("name"),
            "username": claims.get("preferred_username") or claims.get("email"),
            # Okta's `sub` is stable per user across client apps, which is why
            # the consent portal can use its own login app here.
            "subject_label": "sub",
            "subject": claims.get("sub"),
        }
        return user, access_token


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Required env var {name} is not set (see config.example.env)")
    return value
