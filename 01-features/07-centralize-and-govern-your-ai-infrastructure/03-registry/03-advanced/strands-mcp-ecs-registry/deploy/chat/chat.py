"""chat.py — Chat Interface Service

Simple FastAPI chat service that sits between the user and the Strands agent.

Auth inbound  : Cognito JWT (user logs in via Cognito, passes Bearer token)
Auth outbound : Forwards the same Cognito JWT to the agent service
                (agent validates it independently via its own JWKS check)

Environment variables:
  AGENT_URL         — internal URL of the agent ECS service
                      e.g. http://agent.internal:8080
  COGNITO_JWKS_URL  — Cognito JWKS endpoint for JWT validation
                      e.g. https://cognito-idp.us-east-1.amazonaws.com/<pool>/.well-known/jwks.json
  PORT              — listen port (default 8080)
"""

import json
import logging
import os
from typing import Optional

import httpx
import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwk, jwt
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger("chat")

AGENT_URL = os.environ.get("AGENT_URL", "http://localhost:8081")
_raw_jwks = os.environ.get("COGNITO_JWKS_URL", "")
COGNITO_JWKS_URL = _raw_jwks if _raw_jwks.startswith("https://") else ""  # skip JWT if not a real URL

app = FastAPI(title="Financial Analyst Chat")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
bearer_scheme = HTTPBearer(auto_error=False)

# ── JWT validation ─────────────────────────────────────────────────────────────

_jwks_cache: dict | None = None


def _get_jwks() -> dict:
    global _jwks_cache
    if _jwks_cache is None and COGNITO_JWKS_URL:
        import urllib.request

        with urllib.request.urlopen(COGNITO_JWKS_URL, timeout=5) as r:
            _jwks_cache = json.loads(r.read())
    return _jwks_cache or {}


def _verify_token(token: str) -> dict:
    if not COGNITO_JWKS_URL:
        return {}  # dev mode
    try:
        jwks = _get_jwks()
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        key = next((k for k in jwks.get("keys", []) if k["kid"] == kid), None)
        if not key:
            raise HTTPException(status_code=401, detail="Unknown signing key")
        public_key = jwk.construct(key)
        return jwt.decode(token, public_key, algorithms=["RS256"])
    except JWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid token: {exc}")


def require_auth(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),  # noqa: B008
) -> tuple[dict, str]:
    """Returns (claims, raw_token). Token is forwarded to agent."""
    if not COGNITO_JWKS_URL:
        return {}, ""
    if not creds:
        raise HTTPException(status_code=401, detail="Authorization header required")
    claims = _verify_token(creds.credentials)
    return claims, creds.credentials


# ── Models ─────────────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    message: str
    history: list = []  # [{role: "user"|"assistant", content: "..."}]


class ChatResponse(BaseModel):
    response: str
    user: str | None = None


# ── Endpoints ──────────────────────────────────────────────────────────────────


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    auth: tuple = Depends(require_auth),
):
    """Accept a user message, forward to the agent, return the response."""
    claims, raw_token = auth
    user = claims.get("email") or claims.get("sub", "anon")
    log.info("Chat from %s: %s", user, req.message[:80])

    headers = {"Content-Type": "application/json"}
    if raw_token:
        headers["Authorization"] = f"Bearer {raw_token}"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{AGENT_URL}/invoke",
                json={"message": req.message},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        log.error("Agent returned %s: %s", exc.response.status_code, exc.response.text)
        raise HTTPException(status_code=502, detail="Agent error")
    except httpx.RequestError as exc:
        log.error("Could not reach agent: %s", exc)
        raise HTTPException(status_code=503, detail="Agent unreachable")

    return ChatResponse(response=data["response"], user=user)


@app.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    auth: tuple = Depends(require_auth),
):
    """Stream SSE progress events from the agent back to the browser."""
    claims, raw_token = auth
    user = claims.get("email") or claims.get("sub", "anon")
    log.info("Stream chat from %s: %s", user, req.message[:80])

    headers = {"Content-Type": "application/json"}
    if raw_token:
        headers["Authorization"] = f"Bearer {raw_token}"

    async def generate():
        try:
            async with (
                httpx.AsyncClient(timeout=180.0) as client,
                client.stream(
                    "POST",
                    f"{AGENT_URL}/invoke/stream",
                    json={"message": req.message, "history": req.history},
                    headers=headers,
                ) as resp,
            ):
                resp.raise_for_status()
                # Pass raw bytes straight through — do NOT use aiter_lines()
                # because it strips blank lines, destroying \n\n SSE delimiters.
                async for chunk in resp.aiter_bytes():
                    if chunk:
                        yield chunk
        except httpx.HTTPStatusError as exc:
            yield f"event: error\ndata: {json.dumps({'text': f'Agent error {exc.response.status_code}'})}\n\n".encode()
        except httpx.RequestError:
            yield f"event: error\ndata: {json.dumps({'text': 'Agent unreachable'})}\n\n".encode()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Static frontend (must be mounted last, after all API routes) ───────────────
_static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_static_dir):
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="static")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
