"""
FastAPI backend-for-frontend for the OBO Use Case 1 real-world example (Okta).

Responsibilities:
  - Serve a minimal HTML UI.
  - Handle Okta sign-in via the authorization code flow (using authlib).
  - Store the user's access token in a server-side signed session cookie.
  - Forward user requests to the deployed AgentCore Runtime agent with the
    user's JWT in the Authorization header.

The user JWT we send to the agent has `aud == OKTA_AUDIENCE` and `cid` equal
to the Web App client ID. The agent's inbound auth validates these against
Okta's OIDC discovery document, then performs the OBO exchange inside the
handler with the Service App's credentials.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from authlib.integrations.starlette_client import OAuth
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"Required env var {name} is not set")
    return value


load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

OKTA_DOMAIN = _env("OKTA_DOMAIN")
OKTA_AUTH_SERVER_ID = _env("OKTA_AUTH_SERVER_ID")
FRONTEND_CLIENT_ID = _env("FRONTEND_CLIENT_ID")
FRONTEND_CLIENT_SECRET = _env("FRONTEND_CLIENT_SECRET")
UPSTREAM_SCOPE = _env("UPSTREAM_SCOPE", "openid profile email")
FRONTEND_REDIRECT_URI = _env("FRONTEND_REDIRECT_URI")
SESSION_SECRET = _env("FRONTEND_SESSION_SECRET", secrets.token_hex(32))
AGENT_RUNTIME_INVOKE_URL = os.environ.get("AGENT_RUNTIME_INVOKE_URL", "").strip()

DISCOVERY_URL = f"https://{OKTA_DOMAIN}/oauth2/{OKTA_AUTH_SERVER_ID}/.well-known/openid-configuration"


app = FastAPI(title="OBO Use Case 1 — Real-world frontend (Okta)")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))

oauth = OAuth()
oauth.register(
    name="okta",
    client_id=FRONTEND_CLIENT_ID,
    client_secret=FRONTEND_CLIENT_SECRET,
    server_metadata_url=DISCOVERY_URL,
    client_kwargs={
        "scope": UPSTREAM_SCOPE,
        # Okta's Web App is configured with "Require PKCE as additional
        # verification" (see IDP_SETUP.md Step 1.9). authlib only sends the
        # code_challenge when we opt in here — otherwise Okta rejects the
        # authorize request with "PKCE code challenge is required by the
        # application" (HTTP 400, error=invalid_request).
        "code_challenge_method": "S256",
    },
)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> Any:
    user = request.session.get("user")
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "user": user,
            "agent_configured": bool(AGENT_RUNTIME_INVOKE_URL),
        },
    )


@app.get("/auth/login")
async def login(request: Request):
    return await oauth.okta.authorize_redirect(request, FRONTEND_REDIRECT_URI)


@app.get("/auth/callback")
async def callback(request: Request) -> RedirectResponse:
    try:
        token = await oauth.okta.authorize_access_token(request)
    except Exception as e:
        raise HTTPException(400, f"OAuth callback failed: {e}")

    access_token = token.get("access_token")
    if not access_token:
        raise HTTPException(400, "No access_token in Okta response")

    # authlib parses id_token claims when 'openid' is in the scope.
    claims = token.get("userinfo") or {}
    request.session["user"] = {
        "name": claims.get("name"),
        "preferred_username": claims.get("preferred_username") or claims.get("email"),
        "sub": claims.get("sub"),
    }
    request.session["access_token"] = access_token
    return RedirectResponse("/", status_code=302)


@app.get("/auth/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/", status_code=302)


@app.post("/ask", response_class=HTMLResponse)
async def ask(request: Request) -> Any:
    """Forward a user request to the deployed agent with the user's JWT as bearer."""
    access_token = request.session.get("access_token")
    user = request.session.get("user")
    if not access_token or not user:
        return RedirectResponse("/auth/login", status_code=302)

    if not AGENT_RUNTIME_INVOKE_URL:
        raise HTTPException(
            503,
            "AGENT_RUNTIME_INVOKE_URL is not set. Deploy the agent with the "
            "AgentCore CLI (`agentcore deploy -y -v` inside the scaffolded "
            "project folder), grab the invoke URL from `agentcore status`, "
            "add it to .env, and restart the frontend.",
        )

    form = await request.form()
    prompt = form.get("prompt") or "What is my name?"

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            response = await client.post(
                AGENT_RUNTIME_INVOKE_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json={"prompt": prompt},
            )
        except httpx.HTTPError as e:
            return templates.TemplateResponse(
                request,
                "result.html",
                {
                    "user": user,
                    "prompt": prompt,
                    "error": f"Network error calling agent: {e}",
                },
            )

    if response.status_code != 200:
        return templates.TemplateResponse(
            request,
            "result.html",
            {
                "user": user,
                "prompt": prompt,
                "error": f"Agent returned {response.status_code}: {response.text}",
            },
        )

    # AgentCore Runtime streams the agent's response as Server-Sent Events
    # ("data: \"...\"" lines). Concatenate data chunks into plain text.
    # Fall back to JSON (non-streaming) or raw text if parsing fails.
    result_payload: Any
    content_type = response.headers.get("content-type", "")
    raw_text = response.text

    if "text/event-stream" in content_type or raw_text.startswith("data:"):
        chunks: list[str] = []
        for line in raw_text.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if not payload:
                continue
            try:
                chunks.append(json.loads(payload))
            except ValueError:
                chunks.append(payload)
        result_payload = {"answer": "".join(chunks).strip()}
    else:
        try:
            result_payload = response.json()
        except ValueError:
            result_payload = {"answer": raw_text}

    return templates.TemplateResponse(
        request,
        "result.html",
        {"user": user, "prompt": prompt, "result": result_payload},
    )


if __name__ == "__main__":
    host = _env("FRONTEND_HOST", "localhost")
    port = int(_env("FRONTEND_PORT", "8000"))
    print(f"Starting frontend on http://{host}:{port}")
    print(f"Sign-in URL: http://{host}:{port}/auth/login")
    uvicorn.run(app, host=host, port=port, log_level="info")
