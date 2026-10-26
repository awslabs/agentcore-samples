"""Todo *resource app* for Okta Cross App Access (XAA) — AI Agents model.

In Okta's productized XAA / AI Agents model the resource is fronted by an Okta
**custom Authorization Server** (the "Resource AS"). The two-leg ID-JAG exchange
happens entirely at Okta:

  Leg 1 (Okta ORG AS):      user ID token          -> ID-JAG   (aud = Resource AS)
  Leg 2 (Okta Resource AS): ID-JAG (jwt-bearer)    -> access token (sub = user)

So this service is a **pure resource server**: it does not run its own OAuth
token endpoint. It simply *validates* the access token minted by the Resource AS
(RS256, verified against the AS JWKS, with `iss`/`aud`/scope checks) and serves
the user's todos. This mirrors the downstream API in AWS's reference sample.

Config (env):
  RESOURCE_AS_ISSUER  the Okta custom AS issuer, e.g.
                      https://<tenant>.okta.com/oauth2/<as-id>   (required)
  RESOURCE_API        the token `aud` this API expects (the AS audience),
                      e.g. api://todo                            (required)
  PORT                listen port (default 5001)
"""

from __future__ import annotations

import os
import uuid
from typing import Annotated

import jwt
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from jwt import PyJWKClient
from pydantic import BaseModel

load_dotenv()

# --- Configuration -----------------------------------------------------------

# The Okta custom Authorization Server that issues (and signs) the access tokens
# this API accepts. Its JWKS lives at `<issuer>/v1/keys`.
RESOURCE_AS_ISSUER = os.environ.get("RESOURCE_AS_ISSUER", "").rstrip("/")

# The audience the token must carry (the custom AS's configured audience).
RESOURCE_API = os.environ.get("RESOURCE_API", "api://todo")

SUPPORTED_SCOPES = {"todos.read", "todos.write"}

# --- JWKS client for validating the AS-issued access token (cached) ----------

_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        if not RESOURCE_AS_ISSUER:
            raise RuntimeError("RESOURCE_AS_ISSUER is not configured")
        _jwks_client = PyJWKClient(f"{RESOURCE_AS_ISSUER}/v1/keys")
    return _jwks_client


# --- In-memory todo store (per subject) --------------------------------------

_TODOS: dict[str, list[dict]] = {}


def _seed_user(sub: str) -> None:
    if sub not in _TODOS:
        _TODOS[sub] = [
            {"id": str(uuid.uuid4()), "title": "Try Okta Cross App Access", "done": False},
            {"id": str(uuid.uuid4()), "title": "Wire up AgentCore Identity", "done": False},
        ]


# --- App ---------------------------------------------------------------------

app = FastAPI(title="XAA Todo Resource App", version="0.2.0")


class Principal(BaseModel):
    sub: str
    scopes: set[str]


def require_token(
    authorization: Annotated[str | None, Header()] = None,
    x_resource_token: Annotated[str | None, Header()] = None,
) -> Principal:
    """Validate the access token minted by the Okta Resource AS.

    The token is read from `Authorization: Bearer <token>` normally, or from the
    `X-Resource-Token` header when the caller must use `Authorization` for
    something else (e.g. SigV4 when this API is behind an IAM-authed Lambda
    Function URL).
    """
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization.split(" ", 1)[1]
    elif x_resource_token:
        raw = x_resource_token.split(" ", 1)[1] if x_resource_token.lower().startswith("bearer ") else x_resource_token
    if not raw:
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token_str = raw
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token_str)
        claims = jwt.decode(
            token_str,
            signing_key.key,
            algorithms=["RS256"],
            audience=RESOURCE_API,
            issuer=RESOURCE_AS_ISSUER,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid access token: {exc}") from exc
    # Okta carries scopes in `scp` (list); accept `scope` (string) as a fallback.
    scp = claims.get("scp")
    scopes = set(scp) if isinstance(scp, list) else set(claims.get("scope", "").split())
    return Principal(sub=claims["sub"], scopes=scopes)


def require_scope(principal: Principal, scope: str) -> None:
    if scope not in principal.scopes:
        raise HTTPException(status_code=403, detail=f"Missing required scope: {scope}")


# --- Todo resource API -------------------------------------------------------


class TodoIn(BaseModel):
    title: str


class TodoPatch(BaseModel):
    done: bool


@app.get("/todos")
def list_todos(principal: Annotated[Principal, Depends(require_token)]) -> dict:
    require_scope(principal, "todos.read")
    _seed_user(principal.sub)
    return {"todos": _TODOS[principal.sub]}


@app.post("/todos")
def add_todo(body: TodoIn, principal: Annotated[Principal, Depends(require_token)]) -> dict:
    require_scope(principal, "todos.write")
    _seed_user(principal.sub)
    todo = {"id": str(uuid.uuid4()), "title": body.title, "done": False}
    _TODOS[principal.sub].append(todo)
    return todo


@app.patch("/todos/{todo_id}")
def update_todo(
    todo_id: str,
    body: TodoPatch,
    principal: Annotated[Principal, Depends(require_token)],
) -> dict:
    require_scope(principal, "todos.write")
    _seed_user(principal.sub)
    for todo in _TODOS[principal.sub]:
        if todo["id"] == todo_id:
            todo["done"] = body.done
            return todo
    raise HTTPException(status_code=404, detail="Todo not found")


@app.get("/healthz")
def healthz() -> dict:
    return {
        "status": "ok",
        "resource_api": RESOURCE_API,
        "resource_as_issuer": RESOURCE_AS_ISSUER or None,
    }


if __name__ == "__main__":
    import uvicorn  # local dev only; not needed on Lambda

    port = int(os.environ.get("PORT", "5001"))
    # Bind to loopback by default; set HOST=0.0.0.0 explicitly for container use.
    host = os.environ.get("HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port)
