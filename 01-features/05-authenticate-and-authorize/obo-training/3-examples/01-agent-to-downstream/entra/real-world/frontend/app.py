"""
FastAPI backend-for-frontend for the OBO Use Case 1 real-world example.

Responsibilities:
  - Serve a minimal HTML UI.
  - Handle Entra ID sign-in via the authorization code flow (using MSAL).
  - Store the user's access token in the session (server-side, via signed cookie).
  - Forward user requests to the deployed AgentCore Runtime agent with the
    user's JWT in the Authorization header.

The user JWT we send to the agent has aud == AGENT_CLIENT_ID. The agent's
inbound auth validates that, then performs the OBO exchange inside the handler.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

import httpx
import msal
import uvicorn
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

TENANT_ID = _env("TENANT_ID")
FRONTEND_CLIENT_ID = _env("FRONTEND_CLIENT_ID")
FRONTEND_CLIENT_SECRET = _env("FRONTEND_CLIENT_SECRET")
AGENT_SCOPE = _env("AGENT_SCOPE")
FRONTEND_REDIRECT_URI = _env("FRONTEND_REDIRECT_URI")
SESSION_SECRET = _env("FRONTEND_SESSION_SECRET", secrets.token_hex(32))
AGENT_RUNTIME_INVOKE_URL = os.environ.get("AGENT_RUNTIME_INVOKE_URL", "").strip()

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"


app = FastAPI(title="OBO Use Case 1 — Real-world frontend")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


def _msal_app() -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=FRONTEND_CLIENT_ID,
        client_credential=FRONTEND_CLIENT_SECRET,
        authority=AUTHORITY,
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
async def login(request: Request) -> RedirectResponse:
    state = secrets.token_urlsafe(16)
    request.session["auth_state"] = state
    auth_url = _msal_app().get_authorization_request_url(
        scopes=[AGENT_SCOPE],
        state=state,
        redirect_uri=FRONTEND_REDIRECT_URI,
    )
    return RedirectResponse(auth_url, status_code=302)


@app.get("/auth/callback")
async def callback(request: Request) -> RedirectResponse:
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or state != request.session.get("auth_state"):
        raise HTTPException(400, "Invalid OAuth callback — missing or mismatched state")

    result = _msal_app().acquire_token_by_authorization_code(
        code=code,
        scopes=[AGENT_SCOPE],
        redirect_uri=FRONTEND_REDIRECT_URI,
    )
    if "error" in result:
        raise HTTPException(400, f"{result['error']}: {result.get('error_description', '')}")

    request.session["user"] = {
        "name": result.get("id_token_claims", {}).get("name"),
        "preferred_username": result.get("id_token_claims", {}).get("preferred_username"),
        "oid": result.get("id_token_claims", {}).get("oid"),
    }
    request.session["access_token"] = result["access_token"]
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
    prompt = form.get("prompt") or "What is my display name?"

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
    # ("data: \"...\"" lines). Concatenate the data chunks into plain text.
    # Fall back to JSON (non-streaming responses) or raw text if parsing fails.
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
            # Each data: value is JSON-encoded (a quoted string). Unwrap it.
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
        {
            "user": user,
            "prompt": prompt,
            "result": result_payload,
        },
    )


if __name__ == "__main__":
    host = _env("FRONTEND_HOST", "localhost")
    port = int(_env("FRONTEND_PORT", "8000"))
    print(f"Starting frontend on http://{host}:{port}")
    print(f"Sign-in URL: http://{host}:{port}/auth/login")
    uvicorn.run(app, host=host, port=port, log_level="info")
