# The frontend (a backend-for-frontend)

A small FastAPI app that stands in for whatever real interface your users would
have. Run it from the sample root:

```bash
python frontend/app.py      # http://localhost:8000
```

## What it does

- Signs the user in to the primary IdP with the authorization code flow, and
  keeps the resulting access token in a **server-side signed session cookie**.
  The browser never receives the token.
- Links to the AgentCore consent portal so the user can connect GitHub. That
  flow is entirely AWS-hosted — this app takes no part in it and sees no GitHub
  credential.
- Forwards prompts to the deployed agent with the user's token as the Bearer
  credential, and renders the reply. AgentCore Runtime streams Server-Sent
  Events, so `_parse_agent_response` concatenates the `data:` lines.

## Structure

| File | Purpose |
| :--- | :--- |
| `app.py` | Routes, session, agent invocation. IdP-agnostic. |
| `auth_entra.py` | MSAL confidential client |
| `auth_okta.py` | authlib, authorization code + PKCE |
| `templates/` | `base.html` and three pages |

`IDP=entra|okta` in `.env` selects the adapter. Both expose the same three
methods — `login_redirect`, `exchange_code`, `label` — so nothing in `app.py`
branches on the identity provider. The Entra adapter is synchronous and the Okta
one is async, which `_maybe_await` absorbs.

## Routes

| Route | Notes |
| :--- | :--- |
| `GET /` | Sign-in state, the portal link, the prompt box |
| `GET /auth/login`, `/auth/callback`, `/auth/logout` | The authorization code flow |
| `POST /ask` | Invokes the agent; renders the consent call-to-action when the reply carries the portal URL |
| `GET /debug/token` | Shows the session's access token so you can decode it at jwt.io |

`/debug/token` exists because the two claims that decide whether this sample
works are in that token: `aud` (must satisfy both the runtime's and the
gateway's authorizer) and the subject claim (must be the same user who consented
on the portal). **Remove this route if you reuse the scaffold** — it displays a
live credential.

## Notes

- `AGENT_RUNTIME_INVOKE_URL` must end with `?qualifier=DEFAULT`; without it the
  invoke returns `404 UnknownOperationException`. `/ask` says so when it sees a
  404, and adds a similar hint for a 401/403 (which means the runtime rejected
  the token, i.e. the runtime and gateway audiences disagree).
- `FRONTEND_REDIRECT_URI` must match what is registered on the frontend app in
  your IdP. Entra requires `http://` redirect URIs to use the literal hostname
  `localhost`, not `127.0.0.1`.
- `FRONTEND_SESSION_SECRET` should be a long random value. If it is unset a
  random one is generated per process, so sessions do not survive a restart.
