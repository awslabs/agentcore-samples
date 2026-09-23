# Learning Guide — Real-World OBO (Entra)

A companion to the [README](./README.md). Once your stack is deployed and the frontend is running, work through these five chapters to **see** OBO happening across the real system.

Each chapter has:
- **Objective** — what concept you'll observe.
- **Action** — what to do in the browser or terminal.
- **Expected result** — what you should see.
- **Key observation** — the teaching moment.

Keep two terminals open:
- **Terminal 1**: `python frontend/app.py` running (from `real-world/`).
- **Terminal 2**: `agentcore logs` running (from inside `real-world/oboUc1EntraAgent/` — the CLI needs to be next to its `agentcore/agentcore.json`).

For streaming logs, use whichever of these your CLI version supports:

```bash
# From inside real-world/oboUc1EntraAgent/
agentcore logs --help   # check which streaming flag your version exposes
```

Common forms across CLI versions:

```bash
agentcore logs --tail        # some versions
agentcore logs -f            # some versions ("follow")
agentcore logs --follow      # some versions
```

If your version doesn't have a streaming flag, poll every few seconds instead:

```bash
watch -n 3 'agentcore logs --since 1m'
```

…or just re-run `agentcore logs --since 2m` after each UI interaction. That's the pattern we used during development — it works everywhere.

And one browser window at `http://localhost:8000`.

---

## Chapter 1 — Sign the user in (observe the frontend's OAuth dance)

**Objective:** See the user establishing an authenticated session, backed by an Entra JWT, and understand where it's stored.

**Action:**
1. In the browser, navigate to `http://localhost:8000`.
2. Click **Sign in with Microsoft**.
3. Sign in with a test user from your tenant.
4. After the redirect, you land back at `http://localhost:8000` with a signed-in banner.

**Expected result:**
- The home page shows your display name, preferred username, and Entra `oid`.
- In Terminal 1 (frontend logs), you should see:
  ```
  GET /auth/login HTTP/1.1 302 Found
  GET /auth/callback?code=... HTTP/1.1 302 Found
  GET / HTTP/1.1 200 OK
  ```

**Key observation — where does the token live?**
The user JWT lives in an **HTTP-only signed session cookie** on the BFF — never in JavaScript, never in `localStorage`. The browser holds a tiny session cookie; the FastAPI process holds the actual access token server-side. If an attacker injected JS into a page, they couldn't exfiltrate this token. **This is the BFF pattern, and it's why you keep OAuth on the backend.**

**Inspect it yourself:**
```python
# In a Python shell with the frontend process running, dump the session cookie contents
import base64, json
from itsdangerous import TimestampSigner  # what starlette uses for sessions
# (You won't easily decode the cookie without the secret. The point is: it's opaque to the browser.)
```

Open your browser's DevTools → Application → Cookies → `localhost:8000` → `session`. You'll see a long signed value — no JSON, no obvious JWT, just a signed opaque blob.

---

## Chapter 2 — Invoke the agent (observe JWT propagation)

**Objective:** See the user JWT traveling from the browser → BFF → Runtime → agent handler.

**Action:**
1. In the UI, type a prompt: *"What is my display name?"*
2. Click **Ask agent about me**.
3. Watch Terminal 1 (frontend) and Terminal 2 (agent logs).

**Expected result:**

In the **frontend log** (Terminal 1):
```
POST /ask HTTP/1.1 200 OK
```

In the **agent log** (Terminal 2):
```
Returning streaming response (generator) (0.000s)
Invoking agent for OBO use case 1
```
(These are emitted from the `@app.entrypoint` in `agent/agent.py` after the Runtime has validated the inbound JWT.)

The response eventually renders on the result page with the user's display name.

**Key observation — Runtime validated the JWT before the handler ran.**
Between your `POST /ask` and the agent's `Invoking agent for OBO use case 1` log line, AgentCore Runtime validated:
- JWT signature (against Entra's JWKS).
- Issuer (`iss`) matches the tenant's Entra authority.
- Audience (`aud`) matches your `AGENT_CLIENT_ID`.
- Not expired.

If any of that had failed, the Runtime would have returned **401** to the BFF *before your agent code ran* — your handler would never be reached.

**Prove it fails correctly:**
Try `curl` without a token:
```bash
curl -X POST "$AGENT_RUNTIME_INVOKE_URL" -H 'Content-Type: application/json' -d '{"prompt":"hi"}'
# → HTTP 401: Missing Authorization header (or similar from Runtime)
```

The 401 comes from the Runtime itself — your handler is untouched.

---

## Chapter 3 — Perform the OBO exchange (observe the token transformation)

**Objective:** See a fresh Graph-scoped token being minted on demand, with the user's identity preserved and the audience rotated.

**Action:**
1. Still watching Terminal 2, note the log sequence for a single invocation.
2. Grep for the OBO-specific steps:
   ```bash
   agentcore logs --since 1m | grep -E "GetWorkloadAccessToken|GetResourceOauth2Token|OBO"
   ```

**Expected result:**
You should see something like:
```
botocore.credentials Found credentials from IAM Role: execution_role
Tool #1: get_my_profile
```

And no error lines. (If there are errors about `AccessDeniedException` on `GetWorkloadAccessTokenForJWT` or `secretsmanager:GetSecretValue`, you missed step 10 of the README — re-run `python ../deploy/03_grant_agent_iam_permissions.py`.)

**Key observation — AgentCore Identity is the broker.**
Look at the [`agent/agent.py`](./agent/agent.py) source. The `_obo_exchange()` function makes two `boto3` calls to AgentCore Identity:
```python
workload_token = _ac_identity.get_workload_access_token_for_jwt(...)["workloadAccessToken"]
graph_token    = _ac_identity.get_resource_oauth2_token(..., oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE")["accessToken"]
```

Nowhere in your code is the Entra client secret referenced. Nowhere is Entra's token endpoint called directly. **AgentCore Identity is the credential broker** — it stores the Entra client secret in Secrets Manager, constructs the RFC 7523 `jwt-bearer` POST body, hits Entra's token endpoint, and returns the downstream-audienced token. Your agent code remains free of secret-handling logic.

**Inspect the exchange request format:**
The request AgentCore Identity sends to Entra (under the hood):
```
POST https://login.microsoftonline.com/<tenant>/oauth2/v2.0/token
grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
client_id=<agent-app-client-id>
client_secret=<retrieved from Secrets Manager>
assertion=<your inbound user JWT>
scope=https://graph.microsoft.com/User.Read
requested_token_use=on_behalf_of
```

You never wrote this code. AgentCore Identity did.

#### How to verify this exchange actually happened

The raw POST leaves your account's network boundary inside the AgentCore Identity service — you won't see it in CloudWatch directly. But you can prove the exchange happened and inspect its shape **three different ways**, each giving you different evidence:

**Option A — Inspect the returned Graph token (strongest evidence).**
Every field of the request shapes the token you get back. Decode both tokens and compare — if the outbound token's `iss` is Entra, its `aud` is Graph, and its `appid` is your agent app, then RFC 7523 exchange is the only way those claims could appear together. See Chapter 4 for the decode instructions.

**Option B — Entra's own sign-in logs (auditable, tenant-admin only).**
Entra logs every token request, including OBO exchanges, in its sign-in logs.

1. Open the [Entra admin center](https://entra.microsoft.com) as a user with **Security Reader** or higher.
2. Navigate to **Monitoring & health → Sign-in logs**.
3. Filter by **Application** = your agent app.
4. Click on a recent sign-in entry from the time you invoked the UI.
5. Expand **Authentication Details**. You'll see:
   - `grantType: urn:ietf:params:oauth:grant-type:jwt-bearer` (confirms RFC 7523)
   - `requestedTokenUse: on_behalf_of` (confirms OBO semantics)
   - `userAgent`: something like `"AWS-SDK-..."` (confirms the caller is AWS / AgentCore Identity)
6. Under **Assigned Resources**, you'll see `Microsoft Graph` with the `User.Read` scope — the downstream audience the exchange was for.

This is the most authoritative evidence because it comes from Entra itself. If you have tenant-admin access, this is what you'd use in an audit or incident review.

**Option C — CloudTrail event for the agent-side call (AWS-side evidence).**
CloudTrail records the AWS API call that *triggered* the exchange — you'll see `GetResourceOauth2Token` events from your agent's execution role. You can't see what AgentCore Identity did next, but you can confirm who called it and when.

```bash
# Events in the last 30 min where your agent called the OBO flow
aws cloudtrail lookup-events \
  --region us-west-2 \
  --lookup-attributes AttributeKey=EventName,AttributeValue=GetResourceOauth2Token \
  --max-results 10 \
  --query 'Events[].[EventTime,Username,Resources[0].ResourceName]' \
  --output table
```

You should see entries with:
- `EventTime` matching your UI interactions.
- `Username` = the assumed-role session from your agent's execution role (`AgentCore-oboUc1EntraAgen-...`).
- A resource that references your credential provider.

No CloudTrail entry, no invocation. Empty output means the agent never called the OBO flow — usually a missing IAM permission (Chapter 3's error scenario).

**Putting it together.** Options A + B + C triangulate the same event from three vantage points:
- **A** — the cryptographic evidence (Entra signed a token that could only come from an OBO exchange).
- **B** — Entra's audit trail of the request it received.
- **C** — AWS's audit trail of the API call your agent made to initiate it.

If all three line up, you have end-to-end proof that a user JWT was exchanged for a Graph token via RFC 7523 OBO — with your agent as the actor and AgentCore Identity as the broker. That's what an auditor would want to see.

---

## Chapter 4 — Compare the inbound and outbound tokens

**Objective:** See exactly what changed in the token (audience, scope, actor) and what stayed the same (user identity).

**Action:**

1. In the frontend's **result** page, click **"Show raw response"** (the `<details>` section under the answer) — we'll use this trick in a moment.

2. Add a quick debug block to the agent to dump both tokens. Edit `agent/agent.py` to temporarily log the tokens (never leave this in production).

   **Two edits, in this order:**

   **Edit 2a.** Near the top of the file, after the existing imports, add a small helper that decodes JWT claims without verifying the signature (debug-only — signature verification was already done by the Runtime on inbound, and we trust AgentCore Identity for outbound):

   ```python
   # TEMPORARY DEBUG HELPER — remove before committing.
   import base64 as _b64
   import json as _json

   def _decode_jwt(token: str) -> dict:
       payload = token.split(".")[1]
       payload += "=" * (-len(payload) % 4)
       return _json.loads(_b64.urlsafe_b64decode(payload))
   ```

   **Edit 2b.** Replace the body of `_obo_exchange` so it captures both tokens and logs their decoded claims before returning. Find the existing function (around line 54 in `agent/agent.py`):

   ```python
   def _obo_exchange(user_token: str) -> str:
       """Swap an inbound user JWT for a Graph-scoped OBO access token."""
       workload_token = _ac_identity.get_workload_access_token_for_jwt(
           workloadName=WORKLOAD_NAME, userToken=user_token,
       )["workloadAccessToken"]

       return _ac_identity.get_resource_oauth2_token(
           workloadIdentityToken=workload_token,
           resourceCredentialProviderName=ACTOR_PROVIDER_NAME,
           scopes=[GRAPH_SCOPE],
           oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
       )["accessToken"]
   ```

   And change it to:

   ```python
   def _obo_exchange(user_token: str) -> str:
       """Swap an inbound user JWT for a Graph-scoped OBO access token."""
       workload_token = _ac_identity.get_workload_access_token_for_jwt(
           workloadName=WORKLOAD_NAME, userToken=user_token,
       )["workloadAccessToken"]

       graph_token = _ac_identity.get_resource_oauth2_token(
           workloadIdentityToken=workload_token,
           resourceCredentialProviderName=ACTOR_PROVIDER_NAME,
           scopes=[GRAPH_SCOPE],
           oauth2Flow="ON_BEHALF_OF_TOKEN_EXCHANGE",
       )["accessToken"]

       # ─── TEMPORARY DEBUG LOGGING — remove before committing ──────────────
       log.info("CLAIMS_INBOUND: %s", _json.dumps(_decode_jwt(user_token)))
       log.info("CLAIMS_OUTBOUND: %s", _json.dumps(_decode_jwt(graph_token)))
       # ──────────────────────────────────────────────────────────────────────

       return graph_token
   ```

   Changes: capture the result in `graph_token`, log both decoded payloads, then `return graph_token` at the end. Everything else is unchanged.

3. Redeploy:
   ```bash
   # from inside oboUc1EntraAgent/
   cp ../agent/agent.py app/oboUc1EntraAgent/main.py
   agentcore deploy -y -v
   ```

4. Restart the frontend so it reconnects to the redeployed Runtime and clears any cached request state.

   The frontend doesn't strictly require a restart — your `AGENT_RUNTIME_INVOKE_URL` is the same Runtime, so it'll keep working. But restarting gives you a clean session and ensures you see *only* logs from the new code.

   In the terminal that's running `python frontend/app.py` (Terminal 1):
   ```bash
   # Ctrl+C to stop

   # From real-world/
   python frontend/app.py
   ```

   If Ctrl+C doesn't release the port (uvicorn has a known quirk where the worker process sometimes lingers):
   ```bash
   lsof -ti:8000 | xargs kill -9 2>/dev/null
   python frontend/app.py
   ```

   Open `http://localhost:8000` in an incognito window (or hard-refresh with Cmd+Shift+R to clear any stale session cookie), sign in again, and you're ready for the next step.

5. Ask another question in the UI.

6. Compare in Terminal 2.

   **Raw view** — just dump the two JSON blobs and look:
   ```bash
   agentcore logs --since 2m | grep -E "CLAIMS_INBOUND|CLAIMS_OUTBOUND"
   ```

   **Pretty-printed (each token on its own):**
   ```bash
   agentcore logs --since 2m | grep -E "CLAIMS_INBOUND|CLAIMS_OUTBOUND" \
     | sed -E 's/.*CLAIMS_(INBOUND|OUTBOUND): (.*)/\1: \2/' \
     | while IFS= read -r line; do
         label="${line%%:*}"
         json="${line#*: }"
         echo "── $label ──"
         echo "$json" | python3 -m json.tool
       done
   ```

   **Side-by-side of the teaching-relevant claims** — this is what makes the OBO transformation click. Requires `jq` (`brew install jq` on macOS):
   ```bash
   logs=$(agentcore logs --since 2m)
   inb=$(echo "$logs" | grep "CLAIMS_INBOUND:"  | tail -1 | sed -E 's/.*CLAIMS_INBOUND: //')
   out=$(echo "$logs" | grep "CLAIMS_OUTBOUND:" | tail -1 | sed -E 's/.*CLAIMS_OUTBOUND: //')

   {
     printf "claim\tinbound (user → agent)\toutbound (agent → Graph)\n"
     printf "─────\t──────────────────────\t────────────────────────\n"
     for claim in iss aud oid scp appid azp tid exp iat; do
       iv=$(echo "$inb" | jq -r --arg k "$claim" '.[$k] // "—"')
       ov=$(echo "$out" | jq -r --arg k "$claim" '.[$k] // "—"')
       printf "%s\t%s\t%s\n" "$claim" "$iv" "$ov"
     done
   } | column -t -s $'\t'
   ```

   You should see a table whose rows match the shape of the "Expected result" table below. A few things to notice in your own output:
   - `iss` and `tid` are **identical** on both sides — same tenant, same IdP.
   - `oid` is **identical** — the user never changed.
   - `aud` **rotated** — inbound is your agent's client ID (a UUID); outbound is Microsoft Graph, shown either as `https://graph.microsoft.com` or as Graph's well-known app ID `00000003-0000-0000-c000-000000000000`.
   - `appid` (or `azp`, depending on Entra token version) **rotated** — inbound is the frontend's client ID; outbound is the agent's. That's the OBO breadcrumb: the token records who *acted* on the user's behalf.
   - `scp` **narrowed** from `access_as_user` to `User.Read` — the agent can only reach Graph for what the user consented to.
   - `exp` on the outbound token is typically **later** than the inbound's — Entra mints a fresh token rather than reusing the user's.

   > **Revert before moving on.** The debug edits log raw JWT payloads. Never leave them in a deployed agent. When you're done comparing:
   > ```bash
   > git checkout -- agent/agent.py     # if agent.py is under version control
   > # or manually remove edits 2a and 2b
   > cp ../agent/agent.py app/oboUc1EntraAgent/main.py
   > agentcore deploy -y -v
   > ```

**Expected result — side-by-side:**

| Claim | Inbound (user → agent) | Outbound (agent → Graph) |
|---|---|---|
| `iss` | `https://sts.windows.net/<tenant>/` | `https://sts.windows.net/<tenant>/` (same tenant) |
| `aud` | `<AGENT_CLIENT_ID>` (your agent app) | `https://graph.microsoft.com` or Graph resource ID |
| `oid` | Alice's Entra `oid` | **Same Alice `oid`** |
| `scp` | `access_as_user` | `User.Read` |
| `appid` / `azp` | `<FRONTEND_CLIENT_ID>` | `<AGENT_CLIENT_ID>` (the actor) |

**Key observation — the OBO guarantee in three claims:**

- `oid` **stays the same** → the user's identity is preserved. Graph will run `/me` as this exact user. **No privilege escalation.**
- `aud` **rotates** from the agent app ID to Microsoft Graph → the new token cannot be replayed against your agent, and vice versa.
- `appid`/`azp` **rotates** from the frontend to the agent → the token records who *acted* on the user's behalf. This is the OBO breadcrumb (Entra's flavor — stored in `azp`, not a nested `act` claim).

**Don't forget to remove the debug logging** when you're done. Real apps must not log token values.

---

## Chapter 5 — Call Graph, and verify least privilege

**Objective:** Prove the agent can only see data the signed-in user consented to — not more.

**Action — try to exceed your scope:**

1. In `agent/agent.py`, temporarily try to hit an endpoint beyond `User.Read` — e.g., all users:

   ```python
   r = requests.get(
       "https://graph.microsoft.com/v1.0/users",   # needs User.Read.All, not granted
       headers={"Authorization": f"Bearer {graph_token}"},
       timeout=30,
   )
   ```

2. Redeploy, invoke, and check the log.

**Expected result:**
```json
{
  "error": {
    "code": "Authorization_RequestDenied",
    "message": "Insufficient privileges to complete the operation."
  }
}
```

**Key observation — the agent cannot over-reach.**
Even though the agent has its own Entra app registration with *its own* identity, the OBO'd token is **strictly scoped to what the user consented to** — in this case `User.Read`. If an attacker compromised the agent and tried to enumerate every user's profile, Graph would refuse. The blast radius of a compromised agent is limited to *the permissions the user granted, for the current user only*. That is the end-game of OBO.

Revert `main.py` back to `/me` and redeploy before you move on.

---

## Chapter 6 (bonus) — Trace the full chain in CloudWatch

**Objective:** See the OBO flow as a distributed trace in AWS console, which is how you'd debug this in production.

**Action:**
1. Wait 5–10 minutes after your last invocation (CloudWatch trace indexing takes time).
2. Open the [CloudWatch GenAI Observability console](https://console.aws.amazon.com/cloudwatch/home#gen-ai/agents).
3. Navigate to **Bedrock AgentCore → Agents → `oboUc1EntraAgent_oboUc1EntraAgent` → Traces**.
4. Click into the latest trace.

**Expected result:**
A visual trace showing:
- The inbound HTTP invocation.
- The Strands agent's LLM call to Bedrock.
- The `get_my_profile` tool invocation.
- The two AgentCore Identity calls (workload token + resource OAuth2 token).
- The Graph API call (instrumented if botocore + httpx auto-instrumentation is on).

**Key observation — production-grade observability is free.**
AgentCore Runtime auto-instruments your agent with OpenTelemetry. You didn't add any tracing code, yet you get distributed traces across the whole OBO flow including timing for each hop. In an incident, this is how you'd pinpoint whether the user's request is slow because Bedrock is slow, or AgentCore Identity is slow, or the IdP is slow, or Graph is slow.

---

## Summary — what you've learned

By running and observing this real-world example, you've confirmed firsthand that:

1. **The browser never holds a JWT.** BFF pattern keeps tokens off the client.
2. **The Runtime validates inbound JWTs before the agent runs.** Bad tokens get 401 before any code executes.
3. **AgentCore Identity is a credential broker.** Your agent code never touches the Entra client secret or the OBO wire protocol.
4. **OBO preserves user identity while rotating audience and actor.** Same `oid`, new `aud`, new `appid`.
5. **Least-privilege downstream.** The agent can only do what the user consented to.
6. **Distributed tracing comes for free.** CloudWatch GenAI Observability ties the whole flow together.

## What's next

- Walk through [`../local/02_run_example.py`](../local/02_run_example.py) if you haven't — it's a single-script, chapter-by-chapter tour of the same OBO mechanics, with side-by-side claim comparisons.
- Read [`ARCHITECTURE.md`](./ARCHITECTURE.md) for a deeper dive on design decisions (BFF vs SPA, tool-scoped OBO vs handler-scoped, why not nested `act`).
- When Use Case 2 lands, you'll see the Gateway pattern where the Gateway (not your agent) does the OBO exchange — zero token-handling code in the agent.
