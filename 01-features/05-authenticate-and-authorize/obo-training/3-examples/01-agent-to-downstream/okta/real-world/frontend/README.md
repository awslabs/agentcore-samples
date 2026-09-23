# Frontend — FastAPI BFF (Okta)

## What it does

- Serves a minimal HTML UI with "Sign in" and "Ask agent" actions.
- Runs the Okta authorization-code flow via [authlib](https://docs.authlib.org/).
- Stores the user's access token in a server-side signed session cookie.
- Forwards user requests to the deployed AgentCore Runtime agent, passing the user's JWT in the `Authorization: Bearer …` header.

## Why BFF pattern (not SPA-with-token)

- No client-side token storage — the browser only sees a session cookie.
- Cross-origin complexity stays off the browser side.
- Easier to extend: additional business logic, caching, rate limiting, etc., all live on the backend.

## Running

```bash
python frontend/app.py
```

Then open `http://localhost:8000`.

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Home page |
| GET | `/auth/login` | Kick off Okta sign-in |
| GET | `/auth/callback` | Receive auth code, exchange for tokens, store in session |
| GET | `/auth/logout` | Clear session |
| POST | `/ask` | Forward a prompt to the deployed agent |

## Security notes

- Session cookie is signed with `FRONTEND_SESSION_SECRET`. In production, put this in a secret manager and rotate it.
- `SameSite=lax` on the session cookie prevents CSRF on state-changing requests from cross-origin contexts.
- authlib handles the OAuth `state` and PKCE parameters automatically.
- We store the raw access token in the session. In production, consider encrypting session contents at rest or using a server-side session store (Redis, DynamoDB, etc.).
