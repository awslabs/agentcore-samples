"""FastAPI BFF: signs the user in to Okta and invokes the agent.

Two things make this more than boilerplate:

  1. **It signs in through the AI Agent's linked app**, authenticating with
     `private_key_jwt` because that app has no client secret. Okta's *User access*
     binding means this is the app the user must be signed in to for the agent to act.
  2. **It does not need to send an ID token.** Okta's Machine access configuration lets
     ID-JAG leg 1 exchange the access token the gateway already validated, so the chain
     carries one credential rather than two. Set `SEND_ID_TOKEN=true` only if the
     interceptor runs in `XAA_LEG1_SUBJECT=id_token` mode.

Tokens never reach the browser. The session cookie is signed, HttpOnly and
SameSite=Lax; it is deliberately not Secure so this works on http://localhost, which
means adding `https_only=True` before serving it anywhere else.

    python frontend/app.py        # http://localhost:8000
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import jwt as pyjwt
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

SAMPLE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SAMPLE_ROOT / "deploy"))
from _common import env, load_env, must_env, okta_org_url

load_env()

FRONTEND_DIR = Path(__file__).resolve().parent
ORG = okta_org_url()
AS1 = must_env("AGENTCORE_AS_ISSUER")
CLIENT_ID = must_env("LOGIN_CLIENT_ID")
AUTH_METHOD = env("LOGIN_CLIENT_AUTH_METHOD", "private_key_jwt")
REDIRECT_URI = env("FRONTEND_REDIRECT_URI", "http://localhost:8000/auth/callback")
SCOPES = f"openid profile email {env('SCOPE_AGENT_ACCESS', 'agent.access')}"
RUNTIME_URL = env("AGENT_RUNTIME_INVOKE_URL", "")
KEY_PATH = SAMPLE_ROOT / "scripts" / "keys" / "okta_private_key.pem"
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
DEFAULT_PROMPT = "What is on my todo list?"

app = FastAPI(title="Okta XAA delegation chain sample")
app.add_middleware(SessionMiddleware, secret_key=must_env("FRONTEND_SESSION_SECRET"), same_site="lax")
templates = Jinja2Templates(directory=str(FRONTEND_DIR / "templates"))


def client_auth(form: dict[str, Any]) -> dict[str, Any]:
    """Add client authentication to a token request.

    The AI Agent's linked app uses Public/private key, so there is no secret to send;
    we sign an assertion with the same key the interceptor uses for the ID-JAG legs.
    """
    if AUTH_METHOD != "private_key_jwt":
        form["client_secret"] = must_env("LOGIN_CLIENT_SECRET")
        return form
    now = int(time.time())
    form["client_assertion_type"] = CLIENT_ASSERTION_TYPE
    form["client_assertion"] = pyjwt.encode(
        {
            "iss": CLIENT_ID,
            "sub": CLIENT_ID,
            "aud": f"{AS1}/v1/token",
            "iat": now,
            "exp": now + 300,
            "jti": secrets.token_urlsafe(16),
        },
        KEY_PATH.read_text(),
        algorithm="RS256",
        headers={"kid": must_env("AI_AGENT_KEY_KID")},
    )
    return form


def claims(token: str) -> dict[str, Any]:
    try:
        part = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (ValueError, IndexError, json.JSONDecodeError):
        return {}


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = request.session.get("user")
    return templates.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "user": user,
            "default_prompt": DEFAULT_PROMPT,
            "runtime_configured": bool(RUNTIME_URL),
        },
    )


@app.get("/auth/login")
async def login(request: Request):
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(40)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(16)
    request.session["pkce_verifier"] = verifier
    request.session["auth_state"] = state
    query = urllib.parse.urlencode(
        {
            "client_id": CLIENT_ID,
            "response_type": "code",
            "scope": SCOPES,
            "redirect_uri": REDIRECT_URI,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    return RedirectResponse(f"{AS1}/v1/authorize?{query}")


@app.get("/auth/callback")
async def callback(request: Request):
    if request.query_params.get("error"):
        return HTMLResponse(
            f"<h3>Okta returned an error</h3><pre>{request.query_params.get('error')}: "
            f"{request.query_params.get('error_description', '')}</pre>",
            status_code=400,
        )
    code = request.query_params.get("code")
    if not code or request.query_params.get("state") != request.session.get("auth_state"):
        return HTMLResponse("<h3>Invalid OAuth callback (missing code or state mismatch)</h3>", 400)

    form = client_auth(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": request.session.get("pkce_verifier", ""),
            "client_id": CLIENT_ID,
        }
    )
    async with httpx.AsyncClient(timeout=45) as http:
        resp = await http.post(f"{AS1}/v1/token", data=form)
    if resp.status_code >= 400:
        return HTMLResponse(f"<h3>Token exchange failed</h3><pre>{resp.text[:600]}</pre>", 400)
    tokens = resp.json()

    # Both tokens stay server-side. T_user invokes the agent; the ID token is what the
    # interceptor needs for ID-JAG leg 1.
    request.session["access_token"] = tokens["access_token"]
    request.session["id_token"] = tokens["id_token"]
    request.session["user"] = claims(tokens["access_token"]).get("sub", "unknown")
    request.session.pop("pkce_verifier", None)
    request.session.pop("auth_state", None)
    return RedirectResponse("/")


@app.get("/auth/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")


@app.get("/debug/token", response_class=HTMLResponse)
async def debug_token(request: Request):
    """Show the session's token claims. Debug route -- remove it if you reuse this."""
    if not request.session.get("access_token"):
        return RedirectResponse("/auth/login")
    return templates.TemplateResponse(
        request=request,
        name="token.html",
        context={
            "user": request.session.get("user"),
            "access": claims(request.session["access_token"]),
            "id": claims(request.session["id_token"]),
        },
    )


@app.post("/ask", response_class=HTMLResponse)
async def ask(request: Request, prompt: str = Form(default=DEFAULT_PROMPT)):
    if not request.session.get("access_token"):
        return RedirectResponse("/auth/login")
    if not RUNTIME_URL:
        return templates.TemplateResponse(
            request=request,
            name="result.html",
            context={
                "user": request.session.get("user"),
                "prompt": prompt,
                "error": (
                    "AGENT_RUNTIME_INVOKE_URL is not set in .env.\n\n"
                    "Deploy the agent, then copy the invoke URL from `agentcore status`. "
                    "It must end with ?qualifier=DEFAULT, or the call returns "
                    "404 UnknownOperationException."
                ),
            },
        )

    # No id_token here. The agent mints T_gateway and the gateway's interceptor exchanges
    # THAT at ID-JAG leg 1, which Okta's Machine access configuration authorises. Set
    # SEND_ID_TOKEN=true to also forward it, which orgs running the interceptor in
    # id_token mode need.
    payload: dict[str, Any] = {"prompt": prompt}
    if env("SEND_ID_TOKEN", "").lower() == "true":
        payload["id_token"] = request.session["id_token"]
    headers = {
        "Authorization": f"Bearer {request.session['access_token']}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=180) as http:
        resp = await http.post(RUNTIME_URL, json=payload, headers=headers)

    error = None if resp.status_code < 400 else f"HTTP {resp.status_code}\n\n{resp.text[:1500]}"
    answer = ""
    if not error:
        # The runtime streams text fragments; concatenate them for display.
        body = resp.text
        try:
            parsed = json.loads(body)
            answer = parsed if isinstance(parsed, str) else parsed.get("result", body)
        except json.JSONDecodeError:
            answer = body
    return templates.TemplateResponse(
        request=request,
        name="result.html",
        context={"user": request.session.get("user"), "prompt": prompt, "answer": answer, "error": error},
    )


if __name__ == "__main__":
    import uvicorn

    host = env("FRONTEND_HOST", "localhost")
    port = int(env("FRONTEND_PORT", "8000"))
    print(f"signing in via {CLIENT_ID} ({AUTH_METHOD}) at {AS1}")
    print(f"agent runtime: {RUNTIME_URL or '<not deployed yet>'}")
    print(f"open http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
