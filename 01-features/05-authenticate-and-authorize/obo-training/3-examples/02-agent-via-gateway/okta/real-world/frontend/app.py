"""
FastAPI backend-for-frontend for the OBO Use Case 2 real-world example (Okta).

Responsibilities (same shape as UC2 Entra, but uses authlib instead of MSAL):
  - Serve a minimal HTML UI.
  - Handle Okta sign-in via the authorization code flow (with PKCE) using
    authlib's OAuth client.
  - Store the user's access token in a server-side signed session cookie.
  - Forward user requests to the deployed AgentCore Runtime agent with the
    user's JWT (T_user) in the Authorization header.

The frontend is intentionally identical in shape to UC2 Entra — what differs
is what happens AFTER the agent receives the token. In UC2 the agent does
OBO #1 to mint a token for the Gateway, and the Gateway does OBO #2 to mint
a token for the mock downstream API. The frontend doesn't know about either
OBO hop; it just sends the user's JWT and renders the answer.

The user JWT we send to the agent has:
  - aud = OKTA_AUDIENCE (typically api://default)
  - cid = FRONTEND_CLIENT_ID (Okta records the frontend as the actor for
          the initial sign-in — this will rotate to AGENT_CLIENT_ID in
          T_gateway after OBO #1)
  - sub = the user's login (e.g. alice@example.com) — this claim stays
          the same across every T_* in the chain (identity propagation)
  - scp = [..., agent.access] — the custom scope authorizing this call
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
UPSTREAM_SCOPE = _env("UPSTREAM_SCOPE", "openid profile email agent.access")
FRONTEND_REDIRECT_URI = _env("FRONTEND_REDIRECT_URI")
SESSION_SECRET = _env("FRONTEND_SESSION_SECRET", secrets.token_hex(32))
AGENT_RUNTIME_INVOKE_URL = os.environ.get("AGENT_RUNTIME_INVOKE_URL", "").strip()

DISCOVERY_URL = f"https://{OKTA_DOMAIN}/oauth2/{OKTA_AUTH_SERVER_ID}/.well-known/openid-configuration"


app = FastAPI(title="OBO Use Case 2 — Real-world frontend (Okta)")
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
        # verification" (see IDP_SETUP.md). authlib only sends the
        # code_challenge when we opt in here — otherwise Okta rejects the
        # authorize request with "PKCE code challenge is required by the
        # application" (HTTP 400).
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
        # `sub` is the Okta user's login (typically the email). It's the
        # seam claim that stays constant across the whole OBO chain.
        "sub": claims.get("sub"),
    }
    request.session["access_token"] = access_token
    return RedirectResponse("/", status_code=302)


@app.get("/auth/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/", status_code=302)


@app.get("/debug/token", response_class=HTMLResponse)
async def debug_token(request: Request) -> Any:
    """Show the current session's access token — for the LEARNING_GUIDE tour.

    Convenience route for the guide's Chapter 1 (decoding T_user at jwt.io).
    Access it at http://localhost:8000/debug/token after signing in. Copy
    the displayed token and paste into https://jwt.io to inspect claims.

    Not intended for production; remove or gate behind a flag if you copy
    this scaffold into a real deployment.
    """
    access_token = request.session.get("access_token")
    user = request.session.get("user")
    if not access_token or not user:
        return RedirectResponse("/auth/login", status_code=302)
    who = user.get("name") or user.get("preferred_username") or "signed-in user"
    html = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Debug: T_user</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; color: #222; }}
  .warn {{ background: #fff4e5; border: 1px solid #f0c080; border-radius: 8px; padding: 1rem 1.25rem; margin: 1rem 0; }}
  textarea {{ width: 100%; height: 220px; font-family: ui-monospace, monospace; font-size: 0.75rem; padding: 0.75rem; border: 1px solid #ccc; border-radius: 6px; }}
  code {{ background: #eee; padding: 0.1rem 0.3rem; border-radius: 3px; }}
  a {{ color: #0b63ce; }}
</style></head>
<body>
  <h1>T_user for {who}</h1>
  <div class='warn'>
    <p><strong>What is this?</strong> The access token the BFF received from Okta during sign-in. It's what the frontend sends to the AgentCore Runtime as <code>Authorization: Bearer</code>. In the OBO chain diagrams, this is <em>T_user</em>.</p>
    <p>Debug route only — see <a href='../LEARNING_GUIDE.md'>LEARNING_GUIDE.md</a> Chapter 1. Not intended for production; the token is sensitive.</p>
  </div>
  <p><strong>Copy this into <a href='https://jwt.io' target='_blank'>jwt.io</a></strong> to inspect claims (<code>aud</code>, <code>cid</code>, <code>sub</code>, <code>scp</code>, <code>uid</code>, <code>exp</code>). You'll compare these against T_gateway and T_downstream in later chapters.</p>
  <textarea readonly onclick='this.select()'>{access_token}</textarea>
  <p style='margin-top: 1rem;'>
    Or fetch as plain text: <code>curl -sb "session=&lt;cookie&gt;" http://localhost:8000/debug/token/raw</code>.
    Easiest is to triple-click the textarea above, copy, paste.
  </p>
  <p><a href='/'>← back to home</a></p>
</body></html>"""
    return HTMLResponse(html)


@app.get("/debug/token/raw", response_class=HTMLResponse)
async def debug_token_raw(request: Request) -> Any:
    """Plain-text token endpoint — for piping into scripts.

    Example: curl -sb "session=<cookie>" http://localhost:8000/debug/token/raw
    """
    access_token = request.session.get("access_token")
    if not access_token:
        return HTMLResponse("not signed in", status_code=401)
    return HTMLResponse(access_token, media_type="text/plain")


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
    prompt = form.get("prompt") or "Call the downstream API and confirm it responded."

    async with httpx.AsyncClient(timeout=120.0) as client:
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
