"""FastAPI backend-for-frontend for the consent-portal + GitHub MCP sample.

Responsibilities, and deliberately nothing more:
  - Sign the user in to the primary IdP (authorization code flow) and keep the
    resulting access token in a server-side signed session cookie. The browser
    never sees the token.
  - Link to the AgentCore consent portal, where the user grants GitHub access.
    That flow is entirely AWS-hosted; this app takes no part in it.
  - Forward prompts to the deployed AgentCore Runtime agent with the user's
    token as the Bearer credential.

Which IdP is used comes from IDP=entra|okta in .env, written by
deploy/00_create_*_apps.py. The two adapters (auth_entra.py, auth_okta.py)
share one interface so everything below is IdP-agnostic.

Run from the sample root, after the agent is deployed:
    python frontend/app.py
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

FRONTEND_DIR = Path(__file__).resolve().parent
SAMPLE_ROOT = FRONTEND_DIR.parent
sys.path.insert(0, str(FRONTEND_DIR))

# Honour the same CONSENT_ENV_FILE override the deploy scripts use, so one
# checkout can serve either identity provider's deployment:
#     CONSENT_ENV_FILE=.env.okta python frontend/app.py
_env_file = os.environ.get("CONSENT_ENV_FILE", "").strip() or ".env"
ENV_PATH = Path(_env_file) if Path(_env_file).is_absolute() else SAMPLE_ROOT / _env_file
load_dotenv(ENV_PATH, override=True)

IDP = os.environ.get("IDP", "").strip().lower()
if IDP == "entra":
    from auth_entra import EntraAuth

    auth = EntraAuth()
elif IDP == "okta":
    from auth_okta import OktaAuth

    auth = OktaAuth()
else:
    raise RuntimeError(
        "IDP must be 'entra' or 'okta' in .env. Run deploy/00_create_entra_apps.py "
        "or deploy/00_create_okta_apps.py first."
    )

SESSION_SECRET = os.environ.get("FRONTEND_SESSION_SECRET") or secrets.token_hex(32)
AGENT_RUNTIME_INVOKE_URL = os.environ.get("AGENT_RUNTIME_INVOKE_URL", "").strip()
# Stored bare in .env; give it a scheme for the browser.
PORTAL_URL = os.environ.get("PORTAL_URL", "").strip()
PORTAL_LINK = f"https://{PORTAL_URL.removeprefix('https://').rstrip('/')}" if PORTAL_URL else ""

DEFAULT_PROMPT = "Search GitHub for amazon-bedrock-agentcore-samples and summarize the top result."

app = FastAPI(title="AgentCore consent portal + GitHub MCP sample")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax")
templates = Jinja2Templates(directory=str(FRONTEND_DIR / "templates"))


# Identifies this deployment. A session cookie is only meaningful for the
# deployment that minted it: the access token inside it was issued by one
# identity provider, for one gateway's audience. Running a second variant from
# the same checkout — with the same FRONTEND_SESSION_SECRET — would otherwise
# leave you apparently signed in with a token the other runtime rejects, which
# looks like "the wrong deployment is showing".
DEPLOYMENT_ID = hashlib.sha256(f"{IDP}|{PORTAL_URL}|{AGENT_RUNTIME_INVOKE_URL}".encode()).hexdigest()[:16]


async def _maybe_await(value: Any) -> Any:
    """Adapters are sync for Entra (MSAL) and async for Okta (authlib)."""
    return await value if inspect.isawaitable(value) else value


def _drop_foreign_session(request: Request) -> bool:
    """Clear a session that belongs to a different deployment. True if dropped."""
    if not request.session.get("user"):
        return False
    if request.session.get("deployment") == DEPLOYMENT_ID:
        return False
    request.session.clear()
    return True


def _page_context(request: Request, **extra: Any) -> dict:
    return {
        "user": request.session.get("user"),
        "idp": IDP,
        "signin_label": auth.label(),
        "portal_link": PORTAL_LINK,
        "agent_configured": bool(AGENT_RUNTIME_INVOKE_URL),
        "default_prompt": DEFAULT_PROMPT,
        **extra,
    }


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> Any:
    dropped = _drop_foreign_session(request)
    return templates.TemplateResponse(request, "home.html", _page_context(request, session_dropped=dropped))


@app.get("/auth/login")
async def login(request: Request):
    return await _maybe_await(auth.login_redirect(request))


@app.get("/auth/callback")
async def callback(request: Request) -> RedirectResponse:
    user, access_token = await _maybe_await(auth.exchange_code(request))
    request.session["user"] = user
    request.session["access_token"] = access_token
    request.session["deployment"] = DEPLOYMENT_ID
    return RedirectResponse("/", status_code=302)


@app.get("/auth/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/", status_code=302)


def _decode_claims(jwt: str) -> dict:
    """Decode a JWT payload without verifying its signature.

    Display only. The claims that decide whether this sample works are `aud`
    (must satisfy both the runtime's and the gateway's authorizer), `iss`, and
    the subject — which is the identity AgentCore stores GitHub consent under.
    """
    import base64

    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, TypeError):
        return {}


@app.get("/debug/token", response_class=HTMLResponse)
async def debug_token(request: Request) -> Any:
    """Show the session's access token and its decoded claims.

    Useful for confirming the passthrough contract, and for diagnosing the
    "Connect succeeded but tool calls still ask for consent" case: compare the
    subject claim shown here against the user id the consent portal displays.
    If they differ, consent is being stored under one identity and looked up
    under another. Debug route only — remove it if you reuse this scaffold.
    """
    _drop_foreign_session(request)
    access_token = request.session.get("access_token")
    if not access_token:
        return RedirectResponse("/auth/login", status_code=302)
    claims = _decode_claims(access_token)
    # azp/appid is the CLIENT that requested the token; aud is the resource.
    # Under Entra `sub` is pairwise on the *resource*, not the client — verified
    # live: this app and the portal sign in through different clients against
    # the same audience and get identical `sub` values. Both are shown so a
    # subject mismatch can be attributed to the right one.
    interesting = {
        k: claims.get(k)
        for k in ("aud", "iss", "sub", "oid", "azp", "appid", "scp", "preferred_username", "ver")
        if claims.get(k) is not None
    }
    return templates.TemplateResponse(
        request,
        "token.html",
        _page_context(request, access_token=access_token, claims=interesting),
    )


def _parse_agent_response(response: httpx.Response) -> dict:
    """AgentCore Runtime streams Server-Sent Events; concatenate the data lines."""
    content_type = response.headers.get("content-type", "")
    raw = response.text
    if "text/event-stream" in content_type or raw.startswith("data:"):
        chunks: list[str] = []
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if not payload:
                continue
            try:
                chunks.append(json.loads(payload))
            except ValueError:
                chunks.append(payload)
        return {"answer": "".join(chunks).strip()}
    try:
        return response.json()
    except ValueError:
        return {"answer": raw}


async def _invoke_agent(request: Request, body: dict, label: str) -> Any:
    """Send one prompt to the runtime and render the reply."""
    _drop_foreign_session(request)
    access_token = request.session.get("access_token")
    if not access_token or not request.session.get("user"):
        return RedirectResponse("/auth/login", status_code=302)

    if not AGENT_RUNTIME_INVOKE_URL:
        raise HTTPException(
            503,
            "AGENT_RUNTIME_INVOKE_URL is not set. Deploy the agent "
            "(`agentcore deploy -y -v`), take the invoke URL from "
            "`agentcore status`, append ?qualifier=DEFAULT, add it to .env, and "
            "restart this frontend.",
        )

    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            response = await client.post(
                AGENT_RUNTIME_INVOKE_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
        except httpx.HTTPError as e:
            return templates.TemplateResponse(
                request,
                "result.html",
                _page_context(request, prompt=label, error=f"Network error calling the agent: {e}"),
            )

    if response.status_code != 200:
        hint = ""
        if response.status_code == 404:
            hint = (
                "\n\nA 404 UnknownOperationException usually means "
                "AGENT_RUNTIME_INVOKE_URL is missing the ?qualifier=DEFAULT suffix."
            )
        elif response.status_code in (401, 403):
            hint = (
                "\n\nA 401/403 here means the runtime rejected your token. Check that "
                "the runtime's allowedAudience matches the gateway's — re-run "
                "deploy/05_patch_agentcore_json.py and redeploy."
            )
        return templates.TemplateResponse(
            request,
            "result.html",
            _page_context(
                request,
                prompt=label,
                error=f"Agent returned {response.status_code}: {response.text}{hint}",
            ),
        )

    result = _parse_agent_response(response)
    # The agent replies with the portal URL when the caller has not consented.
    # Flagging it lets the template render it as a call to action rather than
    # burying it in prose.
    answer = result.get("answer") or ""
    needs_consent = bool(PORTAL_LINK) and PORTAL_LINK in answer
    return templates.TemplateResponse(
        request,
        "result.html",
        _page_context(request, prompt=label, result=result, needs_consent=needs_consent),
    )


@app.post("/ask", response_class=HTMLResponse)
async def ask(request: Request) -> Any:
    """The model picks which GitHub tool to call. Needs Bedrock model access."""
    form = await request.form()
    prompt = form.get("prompt") or DEFAULT_PROMPT
    return await _invoke_agent(request, {"prompt": prompt}, prompt)


if __name__ == "__main__":
    host = os.environ.get("FRONTEND_HOST") or "localhost"
    port = int(os.environ.get("FRONTEND_PORT") or "8000")
    print(f"IdP: {IDP}")
    print(f"Consent portal: {PORTAL_LINK or '(PORTAL_URL not set)'}")
    print(f"Starting frontend on http://{host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")
