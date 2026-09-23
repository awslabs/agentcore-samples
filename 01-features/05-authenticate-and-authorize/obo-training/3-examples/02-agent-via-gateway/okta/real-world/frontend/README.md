# Frontend — FastAPI BFF (Okta)

Same shape as UC2 Entra's frontend — the only difference is the OAuth library (authlib for Okta instead of MSAL for Entra) and the scope requested at sign-in. Everything downstream of "send T_user to the agent" is identical to Entra.

## What it does

- Serves a minimal HTML UI with "Sign in" and "Ask agent" actions.
- Runs the Okta authorization-code flow with PKCE via authlib.
- Stores the user's access token in a server-side signed session cookie.
- Forwards user requests to the deployed AgentCore Runtime agent, passing the user's JWT (T_user) in the `Authorization: Bearer ...` header.

## Why BFF pattern (not SPA-with-token)

- No client-side token storage — the browser only sees a session cookie.
- Cross-origin complexity stays off the browser side.
- Easier to extend: additional business logic, caching, rate limiting, etc., all live on the backend.

## What T_user looks like

The access token the BFF holds after sign-in has:

| Claim | Value | What it means |
|---|---|---|
| `iss` | `https://<OKTA_DOMAIN>/oauth2/<auth-server-id>` | Okta's default (or custom) auth server URL. |
| `aud` | `OKTA_AUDIENCE` (usually `api://default`) | The auth server's audience — same for every token minted here. |
| `cid` | `FRONTEND_CLIENT_ID` | The Web App's client ID — Okta's way of recording "who requested this token." |
| `sub` | `alice@example.com` | The user's login. **Stays constant across every T_* in the chain.** |
| `uid` | `00u...` | The Okta user's internal ID. Also constant across the chain. |
| `scp` | `["openid", "profile", "email", "agent.access"]` | What the frontend requested at sign-in. |
| `exp` | Unix timestamp | Token expiry (default 1 hour). |

The BFF sends this token to the agent's invoke URL. The agent's `customJwtAuthorizer` validates signature, issuer, audience, and expiry before the handler runs.

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Home page |
| GET | `/auth/login` | Kick off Okta sign-in |
| GET | `/auth/callback` | Receive auth code, exchange for tokens, store in session |
| GET | `/auth/logout` | Clear session |
| GET | `/debug/token` | LEARNING_GUIDE Chapter 1 — display T_user in a copyable textarea |
| GET | `/debug/token/raw` | Plain-text T_user for piping into scripts |
| POST | `/ask` | Forward a prompt to the deployed agent (with T_user as Bearer) |

## Running

```bash
python frontend/app.py
```

Then open `http://localhost:8000`.

## Security notes

- Session cookie is signed with `FRONTEND_SESSION_SECRET`. In production, put this in a secret manager and rotate it.
- `SameSite=lax` on the session cookie prevents CSRF on state-changing requests from cross-origin contexts.
- `state` parameter on the OAuth redirect prevents CSRF on the callback (authlib handles this automatically).
- **PKCE is required.** The Okta Web App is configured with "Require PKCE" (see `IDP_SETUP.md`), and authlib is told to send `code_challenge_method=S256` in `client_kwargs`. Missing either side of that pair produces `PKCE code challenge is required by the application` on Okta's error page.
- We store the raw access token in the session for the demo. In production, consider encrypting session contents at rest or using a server-side session store (Redis, etc.).
- The `/debug/token` routes are learning aids — remove or gate behind a flag before deploying.
