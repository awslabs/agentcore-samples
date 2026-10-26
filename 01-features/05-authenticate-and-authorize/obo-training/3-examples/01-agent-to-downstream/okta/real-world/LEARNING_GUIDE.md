# Learning Guide — Real-World OBO (Okta)

A companion to the [README](./README.md). Once your stack is deployed and the frontend is running, work through these six chapters to **see** OBO happening across the real system.

Each chapter has:
- **Objective** — what concept you'll observe.
- **Action** — what to do in the browser or terminal.
- **Expected result** — what you should see.
- **Key observation** — the teaching moment.

Keep two terminals open:
- **Terminal 1**: `python frontend/app.py` running (from `real-world/`).
- **Terminal 2**: agent logs. From inside `real-world/oboUc1OktaAgent/` (or whatever your `$AGENT_RUNTIME_NAME` is):
  ```bash
  agentcore logs --help      # check which streaming flag your version exposes
  ```
  Most versions support one of:
  ```bash
  agentcore logs --tail
  agentcore logs -f
  agentcore logs --follow
  ```
  If none of those, poll:
  ```bash
  watch -n 3 'agentcore logs --since 1m'
  ```

And one browser window at `http://localhost:8000`.

---

## Chapter 1 — Sign the user in (observe the frontend's OAuth dance)

**Objective:** See the user establishing an authenticated session, backed by an Okta access token, and understand where it's stored.

**Action:**
1. In the browser, navigate to `http://localhost:8000`.
2. Click **Sign in with Okta**.
3. Sign in with a test user from your tenant.
4. After the redirect, you land back at `http://localhost:8000` with a signed-in banner.

**Expected result:**
- The home page shows your name, preferred username, and Okta `sub`.
- In Terminal 1 (frontend logs), you should see:
  ```
  GET /auth/login HTTP/1.1 302 Found
  GET /auth/callback?code=... HTTP/1.1 302 Found
  GET / HTTP/1.1 200 OK
  ```

**Key observation — where does the token live?**

The user access token lives in an **HTTP-only signed session cookie** on the BFF — never in JavaScript, never in `localStorage`. The browser holds a tiny session cookie; the FastAPI process holds the actual access token server-side. An XSS injected into the page couldn't exfiltrate this token. **This is the BFF pattern, and it's why you keep OAuth on the backend.**

Open your browser's DevTools → Application → Cookies → `localhost:8000` → `session`. You'll see a long signed value — no JSON, no obvious JWT, just an opaque signed blob.

---

## Chapter 2 — Invoke the agent (observe JWT propagation)

**Objective:** See the user JWT traveling from the browser → BFF → Runtime → agent handler.

**Action:**
1. In the UI, type a prompt: *"What is my preferred username?"*
2. Click **Ask agent about me**.
3. Watch both terminals.

**Expected result:**

In the **frontend log** (Terminal 1):
```
POST /ask HTTP/1.1 200 OK
```

In the **agent log** (Terminal 2):
```
Returning streaming response (generator) (0.000s)
Invoking agent for OBO use case 1 (Okta)
```

The response renders on the result page with your preferred username.

**Key observation — Runtime validated the JWT before the handler ran.**

Between your `POST /ask` and the agent's `Invoking agent for OBO use case 1 (Okta)` log line, AgentCore Runtime validated:
- JWT signature (against Okta's JWKS).
- Issuer (`iss`) matches the Okta authority for the configured auth server.
- Audience (`aud`) matches `OKTA_AUDIENCE` (typically `api://default`).
- Not expired.

If any of that had failed, the Runtime would have returned **401** to the BFF *before your agent code ran*.

**Prove it fails correctly:**
```bash
curl -X POST "$AGENT_RUNTIME_INVOKE_URL" -H 'Content-Type: application/json' -d '{"prompt":"hi"}'
# → HTTP 401: Missing Authorization header (or similar from Runtime)
```

The 401 comes from the Runtime itself — your handler is untouched.

---

## Chapter 3 — Perform the OBO exchange (observe the token transformation)

**Objective:** See a fresh downstream-scoped token being minted on demand, with the user's identity preserved and the actor rotated.

**Action:**
1. Still watching Terminal 2, note the log sequence for a single invocation.
2. Grep for the OBO-specific steps:
   ```bash
   agentcore logs --since 1m | grep -E "GetWorkloadAccessToken|GetResourceOauth2Token|get_my_profile|OBO"
   ```

**Expected result:**
You should see something like:
```
botocore.credentials Found credentials from IAM Role: execution_role
Tool #1: get_my_profile
```

And no error lines. If you see `AccessDeniedException` on `GetWorkloadAccessTokenForJWT` or `secretsmanager:GetSecretValue`, you missed step 10 — re-run `python ../deploy/03_grant_agent_iam_permissions.py`.

**Key observation — AgentCore Identity is the broker.**

Look at [`agent/agent.py`](./agent/agent.py). `_obo_exchange()` makes two `boto3` calls:

```python
workload_token   = _ac_identity.get_workload_access_token_for_jwt(...)["workloadAccessToken"]
downstream_token = _ac_identity.get_resource_oauth2_token(
    ...,
    oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
    customParameters={"subject_token_type": "urn:ietf:params:oauth:token-type:access_token"},
    audiences=[OKTA_AUDIENCE],
)["accessToken"]
```

Nowhere in your code is the Service App client secret referenced. Nowhere is Okta's token endpoint called directly. **AgentCore Identity is the credential broker** — it stores the Service App client secret in Secrets Manager, constructs the RFC 8693 POST body, hits Okta's token endpoint, and returns the downstream token.

**Inspect the exchange request format:**

The POST AgentCore Identity sends to Okta (under the hood):
```
POST https://<OKTA_DOMAIN>/oauth2/<OKTA_AUTH_SERVER_ID>/v1/token
Authorization: Basic base64(<Service App client_id>:<client_secret>)

grant_type=urn:ietf:params:oauth:grant-type:token-exchange
subject_token=<your inbound user JWT>
subject_token_type=urn:ietf:params:oauth:token-type:access_token
audience=api://default
scope=agent.downstream
```

#### How to verify this exchange actually happened

Three independent ways, same shape as the Entra variant:

**Option A — Inspect the returned downstream token (strongest evidence).**
Decode both tokens and compare. If the outbound token's `iss` matches Okta's, its `cid` is the Service App, and its `sub` is the same as the inbound's, OBO via RFC 8693 is the only way those claims could appear together. See Chapter 4.

**Option B — Okta's system logs (auditable, tenant-admin only).**
Okta logs every token request.

1. Okta admin → **Reports → System Log**.
2. Filter by event type `app.oauth2.token.grant` or event description mentioning "Token Exchange".
3. Find the event for your Service App. Expand it:
   - `debugContext.debugData.grantType` = `urn:ietf:params:oauth:grant-type:token-exchange` (confirms RFC 8693).
   - `target` list includes the Service App (the actor) and the user (the subject).
   - `outcome.result` = `SUCCESS`.

Most authoritative evidence — it's Okta's own audit trail.

**Option C — CloudTrail event for the agent-side call (AWS-side evidence).**

```bash
aws cloudtrail lookup-events \
  --region us-west-2 \
  --lookup-attributes AttributeKey=EventName,AttributeValue=GetResourceOauth2Token \
  --max-results 10 \
  --query 'Events[].[EventTime,Username,Resources[0].ResourceName]' \
  --output table
```

You should see entries from your agent's execution role matching the invocation time.

A+B+C triangulate:
- **A** — cryptographic evidence (Okta signed a token that only comes from a valid exchange).
- **B** — Okta's audit trail of the request it received.
- **C** — AWS's audit trail of the API call that triggered it.

---

## Chapter 4 — Compare the inbound and outbound tokens

**Objective:** See exactly what changed in the token (actor, scope) and what stayed the same (user identity, audience).

**Action:**

1. Edit `agent/agent.py` to temporarily log both tokens' claims. **Two edits:**

   **Edit 4a.** After the existing imports, add a debug decoder:

   ```python
   # TEMPORARY DEBUG HELPER — remove before committing.
   import base64 as _b64
   import json as _json

   def _decode_jwt(token: str) -> dict:
       payload = token.split(".")[1]
       payload += "=" * (-len(payload) % 4)
       return _json.loads(_b64.urlsafe_b64decode(payload))
   ```

   **Edit 4b.** Replace `_obo_exchange` so it captures both tokens and logs their decoded claims before returning. Change:

   ```python
   def _obo_exchange(user_token: str) -> str:
       workload_token = _ac_identity.get_workload_access_token_for_jwt(
           workloadName=WORKLOAD_NAME, userToken=user_token,
       )["workloadAccessToken"]
       scopes = DOWNSTREAM_SCOPE.split()
       return _ac_identity.get_resource_oauth2_token(
           workloadIdentityToken=workload_token,
           resourceCredentialProviderName=ACTOR_PROVIDER_NAME,
           scopes=scopes,
           oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
           customParameters={
               "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
           },
           audiences=[OKTA_AUDIENCE],
       )["accessToken"]
   ```

   to:

   ```python
   def _obo_exchange(user_token: str) -> str:
       workload_token = _ac_identity.get_workload_access_token_for_jwt(
           workloadName=WORKLOAD_NAME, userToken=user_token,
       )["workloadAccessToken"]
       scopes = DOWNSTREAM_SCOPE.split()
       downstream_token = _ac_identity.get_resource_oauth2_token(
           workloadIdentityToken=workload_token,
           resourceCredentialProviderName=ACTOR_PROVIDER_NAME,
           scopes=scopes,
           oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
           customParameters={
               "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
           },
           audiences=[OKTA_AUDIENCE],
       )["accessToken"]

       # ─── TEMPORARY DEBUG LOGGING — remove before committing ──────────────
       log.info("CLAIMS_INBOUND: %s", _json.dumps(_decode_jwt(user_token)))
       log.info("CLAIMS_OUTBOUND: %s", _json.dumps(_decode_jwt(downstream_token)))
       # ──────────────────────────────────────────────────────────────────────

       return downstream_token
   ```

2. Redeploy:
   ```bash
   # from inside oboUc1OktaAgent/
   cp ../agent/agent.py app/oboUc1OktaAgent/main.py
   agentcore deploy -y -v
   ```

3. Restart the frontend (Ctrl+C, then `python frontend/app.py` again). Open an incognito window so you get a fresh session cookie.

4. Ask another question in the UI.

5. Compare in Terminal 2. Requires `jq` (`brew install jq`):

   ```bash
   logs=$(agentcore logs --since 2m)
   inb=$(echo "$logs" | grep "CLAIMS_INBOUND:"  | tail -1 | sed -E 's/.*CLAIMS_INBOUND: //')
   out=$(echo "$logs" | grep "CLAIMS_OUTBOUND:" | tail -1 | sed -E 's/.*CLAIMS_OUTBOUND: //')

   {
     printf "claim\tinbound (user → agent)\toutbound (agent → userinfo)\n"
     printf "─────\t──────────────────────\t──────────────────────────\n"
     for claim in iss aud sub cid scp uid iat exp; do
       iv=$(echo "$inb" | jq -r --arg k "$claim" '.[$k] // "—"')
       ov=$(echo "$out" | jq -r --arg k "$claim" '.[$k] // "—"')
       printf "%s\t%s\t%s\n" "$claim" "$iv" "$ov"
     done
   } | column -t -s $'\t'
   ```

   Things to notice:
   - `iss` and `aud` are **identical** on both sides — same auth server, same audience. This is Okta-specific: the audience is the *auth server*, not the downstream API, so `aud` does not change within one auth server.
   - `sub` is **identical** — the user never changed.
   - `cid` **rotated** — inbound is the Web App's client ID; outbound is the Service App's. That's the OBO breadcrumb: Okta records who *acted* on the user's behalf in `cid`.
   - `scp` **rotates** — inbound carries the user's consented OIDC scopes (`openid profile email`); outbound carries the downstream custom scope (`agent.downstream`). That's the audience/capability boundary — the OBO'd token can only access what the downstream policy rule granted.
   - `exp` on the outbound token is typically **later** than the inbound's — Okta mints a fresh token rather than reusing the user's.

> **Revert before moving on.** The debug edits log raw JWT payloads. Never leave them in a deployed agent:
> ```bash
> git checkout -- agent/agent.py   # if versioned
> # or manually remove edits 4a and 4b
> cp ../agent/agent.py app/oboUc1OktaAgent/main.py
> agentcore deploy -y -v
> ```

**Expected result — side-by-side:**

| Claim | Inbound (user → agent) | Outbound (agent → userinfo) |
|---|---|---|
| `iss` | `https://<domain>/oauth2/<auth-server>` | **Same** |
| `aud` | `api://default` | **Same** — auth-server-scoped in Okta |
| `sub` | Alice's Okta sub | **Same Alice sub** |
| `cid` | `<Web App client ID>` | **Rotated** to `<Service App client ID>` |
| `scp` | `["openid","profile","email"]` | `["agent.downstream"]` |

**Key observation — the OBO guarantee in three claims (Okta flavor):**

- `sub` **stays the same** → the user's identity is preserved. `/v1/userinfo` will return Alice's profile. **No privilege escalation.**
- `cid` **rotates** from the Web App to the Service App → the token records who *acted* on the user's behalf. This is the OBO breadcrumb (Okta's flavor — stored in `cid`, not a nested `act` claim or Entra's `appid`).
- `aud` **does not change** within one auth server → both tokens are for the same Okta audience. Cross-auth-server OBO (rarer) would change `aud`, but it's not the common case.

Compare this with Entra in the sibling example: there, `aud` rotates to Microsoft Graph. Which claim rotates depends on the IdP's architecture — Okta centralizes issuance in the auth server, so `aud` is stable; Entra mints to the resource, so `aud` moves.

---

## Chapter 5 — Call userinfo, and observe scope enforcement

**Objective:** Prove the agent can only see data the user consented to — not more.

**Action — try to exceed your scope:**

1. Temporarily reduce your consented scope. In `.env`, change:
   ```
   UPSTREAM_SCOPE=openid
   ```
   (dropping `profile` and `email`)

2. Restart `python frontend/app.py` to pick up the new env var.

3. Sign out and sign back in so you get a new token with the narrower scope.

4. Ask *"What is my email?"* in the UI.

**Expected result:**

The agent's OBO exchange still succeeds (it uses the custom `agent.downstream` scope, which isn't affected by what the user consented to in Sign-In — that scope is under the Service App's policy, not the user's). But Okta's `/v1/userinfo` endpoint — which the agent calls with the **inbound user token** — returns only the `sub` claim because the token doesn't have `profile` or `email` scope. The LLM responds along the lines of: *"I'm sorry, I don't have access to your email based on the available profile data."*

**Key observation — two layers of scope enforcement.**

Two different scope boundaries fire in this flow:

- **User consent narrows what the downstream APIs can read on the user's behalf.** Drop `profile email` from `UPSTREAM_SCOPE` and `/v1/userinfo` stops returning email, because the user never consented to it. This is the OBO guarantee: *the agent can only do what the user agreed to.*
- **Access policy rules bound what the agent can ever mint, full stop.** The OBO exchange is capped at `agent.downstream` by the `Access agent (OBO)` policy. Even if the agent tried to request `profile` or `email` on the exchange (which Okta would reject anyway for OIDC reasons), the policy rule is a hard ceiling.

The blast radius of a compromised agent is limited by the intersection: only scopes the user consented to AND scopes the OBO access policy grants. That's the end-game of OBO.

Revert `UPSTREAM_SCOPE=openid profile email` in `.env`, restart the frontend, sign back in. Move on.

---

## Chapter 6 (bonus) — Trace the full chain in CloudWatch

**Objective:** See the OBO flow as a distributed trace in AWS console, which is how you'd debug this in production.

**Action:**
1. Wait 5–10 minutes after your last invocation (CloudWatch trace indexing).
2. Open the [CloudWatch GenAI Observability console](https://console.aws.amazon.com/cloudwatch/home#gen-ai/agents).
3. Navigate to **Bedrock AgentCore → Agents → `oboUc1OktaAgent_oboUc1OktaAgent` → Traces**.
4. Click into the latest trace.

**Expected result:**
A visual trace showing:
- The inbound HTTP invocation.
- The Strands agent's LLM call to Bedrock.
- The `get_my_profile` tool invocation.
- The two AgentCore Identity calls (workload token + resource OAuth2 token).
- The userinfo API call (instrumented if botocore + httpx auto-instrumentation is on).

**Key observation — production-grade observability is free.**

AgentCore Runtime auto-instruments your agent with OpenTelemetry. You didn't add any tracing code, yet you get distributed traces across the whole OBO flow with timing per hop. In an incident, this is how you'd pinpoint whether the user's request is slow because Bedrock is slow, or AgentCore Identity is slow, or Okta is slow, or `/v1/userinfo` is slow.

---

## Summary — what you've learned

By running and observing this real-world example, you've confirmed firsthand that:

1. **The browser never holds a JWT.** BFF pattern keeps tokens off the client.
2. **The Runtime validates inbound JWTs before the agent runs.** Bad tokens get 401 before any code executes.
3. **AgentCore Identity is a credential broker.** Your agent code never touches the Service App client secret or the OBO wire protocol.
4. **OBO preserves user identity while rotating the actor (and optionally the scope).** Same `sub`, new `cid`, possibly narrower `scp`. `aud` stays stable within one Okta auth server.
5. **Least-privilege downstream.** The agent can only do what the user consented to — demonstrated by the scope experiment in Chapter 5.
6. **Distributed tracing comes for free.** CloudWatch GenAI Observability ties the whole flow together.

## What's next

- Walk through [`../local/02_run_example.py`](../local/02_run_example.py) if you haven't — it's a single-script, chapter-by-chapter tour of the same Okta OBO mechanics.
- Read [`ARCHITECTURE.md`](./ARCHITECTURE.md) for a deeper dive on design decisions (BFF vs SPA, tool-scoped OBO vs handler-scoped, why the auth server audience rather than a client ID).
- Compare this guide with [`../../entra/real-world/LEARNING_GUIDE.md`](../../entra/real-world/LEARNING_GUIDE.md) side-by-side — the chapters are parallel, but each protocol's idiosyncrasies surface in Chapters 3 and 4.
