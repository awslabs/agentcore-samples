# Use Case 1 — Okta flavor (local)

**Start here.** This is the front door of the Okta local example. Read through the design below, then follow the step-by-step path at the bottom.

## What you're going to build

A single-script, laptop-local reproduction of the OBO flow:

> User → Frontend → Agent → Downstream API (`API2`), all under Okta.

Because Okta doesn't have a universally-present downstream API like Microsoft Graph, this example models three roles inside a custom Okta authorization server:

- **Native App** — the frontend / user-facing OIDC client.
- **Service App (API1)** — the middle-tier that performs the OBO exchange.
- **API2** — the downstream target, represented as a custom scope (`oboe2e.apiC.read`) on the authorization server. We stop at validating the OBO'd token's claims rather than calling a live API2.

## Architecture at a glance

```
👤 User
  │
  │ 1. signs in via browser
  ↓
🖥️  Native App ──────────────────────────► 🏛  Okta Authorization Server
  (OIDC client that                          (mints + validates all tokens)
   the frontend uses)                         │
  │                                           │ audience: api://default
  │ 2. receives user access token             │ custom scope: oboe2e.apiC.read
  │    aud=api://default                      │ policy: "Native App may use
  │    cid=<Native App>                       │          Authorization Code
  │    scp=[openid]                           │          grant"
  │    sub=alice@example.com                  │
  ↓                                           │
🤖 Agent (middle tier) ◄────────────────────┤
  │                                           │
  │ 3. uses Service App credentials to do     │
  │    RFC 8693 token exchange                │
  │    subject_token = user token above       │ policy: "Service App may
  │                                           │          use Token Exchange
  │ 4. receives downstream token              │          for scope
  │    aud=api://default (same)              │          oboe2e.apiC.read"
  │    cid=<Service App>  (rotated!)          │
  │    scp=[oboe2e.apiC.read]  (rotated!)    │
  │    sub=alice@example.com  (preserved!)   │
  ↓
🎯 Downstream API (API2)
   (validates token and enforces
    the oboe2e.apiC.read scope)
```

### Why so many Okta objects?

A natural first reaction to `IDP_SETUP.md` is *"why do I need two apps, one auth server, a custom scope, and two access policies just to call one API on a user's behalf?"* Each object plays a distinct role the protocol requires:

| Object | Where in Okta | Role in the OBO flow | Why it can't be merged with something else |
|---|---|---|---|
| **Native App** | Applications → Applications | The OIDC client the frontend uses to sign the user in. Produces the *upstream* user access token. | OIDC requires a client with redirect URIs + PKCE. That capability lives on a Native App, not an API Service. |
| **Service App** | Applications → Applications | The confidential client the agent uses to perform the OBO exchange. Its credentials authenticate the exchange call. Never seen by the end user. | Token Exchange requires the Token Exchange grant on a confidential client — the Native App can't carry this grant. |
| **Authorization Server** | Security → API → Authorization Servers | The issuer / validator. Mints both tokens, hosts JWKS, defines the audience, holds the access policies. | Centralizing grant-and-scope rules in one object is what lets Okta enforce "who can do what" at mint time. |
| **Custom scope `oboe2e.apiC.read`** | Auth Server → Scopes | The permission the downstream token carries. API2 checks for this scope before serving a request. | OBO is only meaningful if the new token has a *different, narrower* scope than the inbound one. |
| **Access policy: "Access API1 (upstream)"** | Auth Server → Access Policies | "The Native App may request user tokens via Authorization Code." | Without it, Okta refuses to mint the upstream user token. |
| **Access policy: "Access API2 (OBO)"** | Auth Server → Access Policies | "The Service App may exchange user tokens for `oboe2e.apiC.read` via Token Exchange." | Without it, Okta refuses the exchange call. |

`IDP_SETUP.md` walks you through creating these six objects. This table is your mental map — come back to it whenever a setup step feels arbitrary.

## What the runnable script teaches

The `02_run_example.py` script isn't just a runnable demo — it's an interactive, guided walkthrough. Five chapters, each pausing to explain what is about to happen, running the API call, then highlighting what changed:

| Chapter | What you'll learn |
|---|---|
| 1. Sign the user in | How AgentCore Identity stands in for a frontend to produce a user access token |
| 2. Inspect the inbound user token | What `aud`, `sub`, `cid`, `scp` mean in Okta's model and why they matter |
| 3. Perform the OBO exchange | The two AgentCore API calls, plus Okta's required `subject_token_type` + `audience` parameters |
| 4. Compare inbound vs outbound tokens | Side-by-side diff showing exactly what OBO changed (and what it preserved) |
| 5. Use the OBO token against the downstream API | What API2 would validate — audience, scope, user identity |

Environment knobs:

| Env var | Effect |
|---|---|
| `INTERACTIVE_NO_PAUSE=1` | Skip all "Press Enter" pauses — runs end-to-end without waiting. Useful for CI / demos. |
| `NO_COLOR=1` | Disable ANSI color codes. |

## Files in this folder

| File | Purpose |
|---|---|
| `README.md` | This file — start here. |
| [`IDP_SETUP.md`](./IDP_SETUP.md) | Step-by-step Okta app and authorization-server setup. Read + follow this before any Python script. |
| `config.example.env` | Env var template (copied to `.env` in IDP_SETUP Step 0). |
| `requirements.txt` | Python dependencies. |
| `01_create_providers.py` | One-time: creates the AgentCore Identity workload + credential providers. |
| `02_run_example.py` | The main runnable example — the 5-chapter interactive walkthrough. |
| `callback_server.py` | Minimal HTTP callback used by the 3LO flow. |
| `teardown.py` | Deletes the AgentCore workload identity and credential providers so you can re-run `01_create_providers.py` from scratch. |
| `generate_user_jwt.py` | Mints + caches a user token for integration tests. |

## The path — read and run in this order

### 1. Read IDP_SETUP.md (then follow it in a browser + `.env` editor)

Open **[`IDP_SETUP.md`](./IDP_SETUP.md)**. It has an architecture overview mirroring the one above, then six numbered steps that walk you through the Okta console. Step 0 creates your `.env`; later steps tell you exactly which values to paste into which env var as you go.

When you finish Step 4, the Okta side is done **except** for one piece you can't do yet — registering the AgentCore-managed redirect URI. That's Step 5, which you'll come back to after running `01_create_providers.py` below.

### 2. Install Python dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Create the AgentCore credential providers (one-time)

```bash
python 01_create_providers.py
```

This creates one workload identity and two OAuth2 credential providers in AgentCore Identity (one for the Native App, one for the Service App). It's idempotent — re-running it is safe. At the end it prints the AgentCore-managed callback URL and tells you how to register it on Okta.

### 4. Finish IDP_SETUP Step 5

Now go back to **[`IDP_SETUP.md` Step 5](./IDP_SETUP.md)** and register the callback URL that `01_create_providers.py` just printed as a Sign-in redirect URI on the Native App. Skipping this will cause the 3LO sign-in in the next step to fail with `redirect_uri_mismatch`.

### 5. Run the end-to-end flow

```bash
python 02_run_example.py
```

This is the 5-chapter interactive walkthrough. Press Enter at each `↵` prompt, or set `INTERACTIVE_NO_PAUSE=1` to run through without pausing.

## What to look for in the output

- Inbound token `cid` = your **Native App's** client ID.
- Outbound token `cid` = your **Service App's** client ID — that's the actor rotation.
- Both tokens have the same `sub` (user's login, e.g. `alice@example.com`) — this is how user identity is preserved across the exchange.
- Inbound `scp` contains `openid`; outbound `scp` contains your custom downstream scope (`oboe2e.apiC.read`).
- `aud` typically stays the same on both tokens — both are for your custom authorization server's audience (e.g. `api://default`). In Okta's model, actor + scope rotate within the same auth server; that's different from Entra, where `aud` rotates to the downstream resource (Microsoft Graph). See [`../README.md`](../README.md) for a short comparison.

## When things go wrong

See the Troubleshooting section in [`IDP_SETUP.md`](./IDP_SETUP.md) — it covers the common 400-level failure modes (missing exchange-time parameters, Token Exchange grant disabled, DPoP still enabled on the Service App, access-policy misconfigurations, scope not marked default/public, stale client secret) and the specific Okta console setting to fix for each.

## Cleanup

When you're done with the example (or want to start fresh with different settings), tear down the AgentCore Identity resources this example created:

```bash
python teardown.py
```

What this deletes, in order:

1. The "client" OAuth2 credential provider (`CLIENT_PROVIDER_NAME` in `.env`).
2. The "actor" OAuth2 credential provider (`ACTOR_PROVIDER_NAME` in `.env`).
3. The workload identity (`WORKLOAD_NAME` in `.env`).

What it does **not** touch:

- Your Okta app registrations (Native App + Service App) and the custom authorization server — they stay in your Okta tenant and can be reused. Delete them in the Okta admin console if you want them gone.
- Your local `.env` file and cached user JWT (`.user-jwt-cache.json`). Remove those manually if they contain values you don't want lingering:
  ```bash
  rm .user-jwt-cache.json
  # and either delete .env entirely or clear out the secret fields in your editor
  ```
- AWS IAM users, roles, or credentials — nothing about this example provisions those.

`teardown.py` is idempotent. Running it twice, or running it when resources are already gone, is safe — it prints `• <resource> already gone` and moves on.

### Starting fresh after teardown

Run the original flow again:

```bash
python 01_create_providers.py
# then register the new callback URL on the Native App (IDP_SETUP.md Step 5)
python 02_run_example.py
```

The Okta apps you set up the first time continue to work — you only need to repeat IDP_SETUP Step 5 if `01_create_providers.py` prints a different callback URL this time around (it normally reuses the same one).
