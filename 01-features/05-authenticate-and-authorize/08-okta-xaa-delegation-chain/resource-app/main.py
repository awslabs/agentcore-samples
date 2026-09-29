"""Todo resource API — the thing the agent reaches on the user's behalf.

It only *validates* tokens; it never issues or exchanges anything. Every request must
present a `T_tool`: an access token minted by **AS 2** (the resource authorization
server) at ID-JAG leg 2.

What it checks, and why each matters:

  iss   must be AS 2. This is the crux of the sample's security story -- the tokens
        the agent itself holds (T_user, T_gateway) come from AS 1, so they fail here
        even though they are valid Okta tokens for the same tenant.
  aud   must be RESOURCE_AUDIENCE (api://todo).
  scp   must contain RESOURCE_SCOPE (todos.read).
  sig   verified against AS 2's JWKS, fetched once and cached.
  sub   identifies the human. Todos are stored per subject, so two users calling the
        same agent see different data -- that is the whole point of the chain.
  act   optional. Okta stamps the acting agent's client id here (RFC 8693). Set
        EXPECTED_ACT_SUB to require a specific agent rather than any agent.

Run locally:
    cp .env.example .env    # set RESOURCE_AS_ISSUER
    python main.py          # http://localhost:5001
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Annotated, Any

import jwt
from fastapi import Depends, FastAPI, HTTPException, Request
from jwt import PyJWKClient
from pydantic import BaseModel

# Load .env BEFORE reading config. Doing it in __main__ would be too late: the
# module-level values below are captured at import time, so a local run would see an
# empty issuer and every request would 500.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # not installed in the Lambda bundle; env vars come from Lambda
    pass

ISSUER = os.environ.get("RESOURCE_AS_ISSUER", "").rstrip("/")
AUDIENCE = os.environ.get("RESOURCE_AUDIENCE", "api://todo")
REQUIRED_SCOPE = os.environ.get("RESOURCE_SCOPE", "todos.read")
EXPECTED_ACT_SUB = os.environ.get("EXPECTED_ACT_SUB", "").strip()

app = FastAPI(title="XAA todo resource API")

_jwk_client: PyJWKClient | None = None


def jwk_client() -> PyJWKClient:
    """Cache the JWKS client so keys are fetched once per container, not per request."""
    global _jwk_client
    if _jwk_client is None:
        if not ISSUER:
            raise HTTPException(500, "RESOURCE_AS_ISSUER is not configured")
        _jwk_client = PyJWKClient(f"{ISSUER}/v1/keys", cache_keys=True)
    return _jwk_client


# Seed data, keyed by the token's `sub`. A real API would use a database; the point
# here is that the rows are per user, reached with the user's own authorization.
_SEED = [
    "Try Okta Cross App Access",
    "Wire up AgentCore Identity",
    "Read the ID-JAG two-leg explainer",
]
_todos: dict[str, list[dict[str, Any]]] = {}


class Caller(BaseModel):
    sub: str
    act_sub: str | None = None
    scopes: list[str] = []
    client_id: str | None = None


def todos_for(sub: str) -> list[dict[str, Any]]:
    if sub not in _todos:
        _todos[sub] = [{"id": i + 1, "title": t, "done": False} for i, t in enumerate(_SEED)]
    return _todos[sub]


def require_token(request: Request) -> Caller:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = auth.split(" ", 1)[1].strip()

    try:
        signing_key = jwk_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.InvalidAudienceError as exc:
        raise HTTPException(403, f"wrong audience: this API only accepts {AUDIENCE}") from exc
    except jwt.InvalidIssuerError as exc:
        # The most instructive failure in this sample: a token the agent holds is a
        # real Okta token, but from AS 1, so it is rejected right here.
        raise HTTPException(403, f"wrong issuer: this API only trusts {ISSUER}") from exc
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(401, "token expired") from exc
    except jwt.PyJWTError as exc:
        raise HTTPException(401, f"invalid token: {exc}") from exc

    scopes = claims.get("scp") or (claims.get("scope") or "").split()
    if REQUIRED_SCOPE not in scopes:
        raise HTTPException(403, f"token is missing the {REQUIRED_SCOPE} scope")

    act_sub = ((claims.get("act") or {}) or {}).get("sub")
    if EXPECTED_ACT_SUB and act_sub != EXPECTED_ACT_SUB:
        raise HTTPException(403, f"unexpected acting agent: {act_sub!r}")

    return Caller(
        sub=claims["sub"],
        act_sub=act_sub,
        scopes=list(scopes),
        client_id=claims.get("cid"),
    )


# Annotated dependency alias: FastAPI's documented modern form, and it keeps the
# Depends() call out of a default argument.
CallerDep = Annotated[Caller, Depends(require_token)]


@app.get("/health")
def health() -> dict[str, Any]:
    """Unauthenticated, so deployment can be checked before any token exists."""
    return {"ok": True, "issuer": ISSUER or None, "audience": AUDIENCE, "scope": REQUIRED_SCOPE}


@app.get("/whoami")
def whoami(caller: CallerDep) -> dict[str, Any]:
    """Echo the identity the API resolved. The clearest proof the chain preserved it."""
    return {
        "user": caller.sub,
        "acting_agent": caller.act_sub,
        "client_id": caller.client_id,
        "scopes": caller.scopes,
    }


@app.get("/todos")
def list_todos(caller: CallerDep) -> dict[str, Any]:
    return {"user": caller.sub, "acting_agent": caller.act_sub, "todos": todos_for(caller.sub)}


@app.post("/todos")
def add_todo(payload: dict[str, Any], caller: CallerDep) -> dict[str, Any]:
    title = (payload or {}).get("title", "").strip()
    if not title:
        raise HTTPException(400, "title is required")
    items = todos_for(caller.sub)
    item = {"id": max((t["id"] for t in items), default=0) + 1, "title": title, "done": False}
    items.append(item)
    return {"user": caller.sub, "added": item}


@app.post("/todos/{todo_id}/complete")
def complete_todo(todo_id: int, caller: CallerDep) -> dict[str, Any]:
    for item in todos_for(caller.sub):
        if item["id"] == todo_id:
            item["done"] = True
            return {"user": caller.sub, "updated": item}
    raise HTTPException(404, f"no todo with id {todo_id}")


if __name__ == "__main__":
    import uvicorn

    print(f"trusting issuer {ISSUER or '<UNSET>'} for audience {AUDIENCE}")
    if not ISSUER:
        print("  WARNING: RESOURCE_AS_ISSUER is unset -- every request will 500.")
        print("  cp .env.example .env and set it to the AS 2 issuer.")
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "5001")))
