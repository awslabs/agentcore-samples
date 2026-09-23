# Learning Guide — UC2 Okta Real-World

Six short chapters you walk through *after* the stack is running. Each chapter has an **objective**, an **action** (something you do), an **expected result**, and an **observation** (what to take away).

This is where the OBO concepts land. The `README.md` got it deployed; this guide explains what's actually happening and where to see it.

**Setup check before you start:** the BFF (`python frontend/app.py`) is running, you've signed in once, and "Ask agent" returns an answer that confirms the downstream API responded. If any of those don't work, fix that before continuing.

**Where to run these commands.** The primary observation tool for Chapters 2–4 is a helper script that pulls just the OBO-trace log lines and prints them grouped by invocation:

```bash
# From real-world/
python deploy/show_obo_trace.py            # last 5 minutes
python deploy/show_obo_trace.py --since 30m
python deploy/show_obo_trace.py --raw      # show every matching line (skip dedupe)
```

The script `cd`s into the runtime folder for you, calls `agentcore logs`, deduplicates AgentCore's JSON+text log pair emissions, and formats a per-invocation view like:

```
─── Invocation 1 ─────────────────────────────────────────────────
  [1] T_user received. aud=api://default cid=<Frontend> sub=alice@... scp=[...] uid=00u...
  [2] OBO 1 start. Exchanging T_user for T_gateway via AgentCore Identity.
  [3] OBO 1 complete. T_gateway minted. aud=api://default cid=<Agent> sub=alice@... scp=[gateway.access] uid=00u...
  [4] MCP session opened to Gateway. About to list tools.
  [5] Gateway MCP tools discovered: ['downstream-echo-obo___callDownstreamApi'] (count=1)
```

Client IDs are annotated with FrontendApp/AgentApp/GatewayApp labels sourced from your `.env`.

Note: `agentcore logs --query "..."` treats `#` and spaces as term separators and returns broad matches — the helper's exact-prefix filter (`OBOTRACE:`) is cleaner.

Helpful side windows:
- The frontend's terminal output (it logs every request).
- A terminal in `real-world/` for `python deploy/show_obo_trace.py`.
- Optionally a terminal in `$AGENT_RUNTIME_NAME/` for direct `agentcore logs`/`agentcore traces`.

---

## Chapter 1 — Where T_user comes from

**Objective.** See where the chain starts: the user's first token, minted by Okta during sign-in to the Frontend Web App.

**Action.** Sign out (`/auth/logout`), then sign back in. Watch the browser network tab during the redirect to `<OKTA_DOMAIN>/oauth2/...`.

**Expected result.** You complete the auth-code flow and land back on `http://localhost:8000/` with "Signed in as ..." displayed. The frontend's terminal shows successful POST to `/auth/callback` and a 302 to `/`.

**Observation.** What the frontend stores at this point:

- An access token. `aud = OKTA_AUDIENCE` (typically `api://default`). `cid = FRONTEND_CLIENT_ID`. `scp` includes `openid profile email agent.access`. This is T_user.
- An ID token, used to populate the welcome banner.
- A refresh token, held by authlib for silent renewal.

**Try this:** With the BFF running and the browser signed in, visit:

```
http://localhost:8000/debug/token
```

That page displays the current session's `T_user` in a copyable textarea. It's a learning-only route in `frontend/app.py`; not for production.

Copy the full token and paste it into <https://jwt.io>. You'll see (among others):

```
aud   : api://default             ← the auth server's audience
cid   : <FRONTEND_CLIENT_ID>      ← actor: the frontend
sub   : alice@example.com         ← your user login
uid   : 00u...                    ← your Okta internal user ID
scp   : ["openid", "profile", "email", "agent.access"]
exp   : <unix timestamp>
```

**Getting the token into your shell** (for Chapter 5's `compare_obo_claims.py`):

1. In the browser at `/debug/token`, **triple-click the textarea** to select the whole token.
2. Copy it.
3. In a terminal, paste it between quotes of a shell variable:

```bash
T_USER="<paste-the-token-here>"
echo "sanity check — length should be > 800, starts with eyJ:"
echo "  length=${#T_USER}  starts=${T_USER:0:8}"
```

If the length is under 500 or doesn't start with `eyJ`, the paste didn't take the whole token — try again, or use `curl -sb "session=<cookie>" http://localhost:8000/debug/token/raw > /tmp/t_user.txt` and pass `--user-token-file`.

---

## Chapter 2 — Inbound auth at the Runtime

**Objective.** Understand what happens **before** your agent code runs.

**Action.** Click "Ask agent" in the browser once, then run:

```bash
# From real-world/
python deploy/show_obo_trace.py
```

**Expected result.** One block per invocation, five lines each. The first line is what Chapter 2 is about:

```
─── Invocation 1 ─────────────────────────────────────────────────
  [1] T_user received. aud=api://default cid=FrontendApp (<FRONTEND_CLIENT_ID>) sub=alice@example.com scp=[...agent.access] uid=00u...
  [2] OBO 1 start. Exchanging T_user for T_gateway via AgentCore Identity.
  [3] OBO 1 complete. T_gateway minted. aud=api://default cid=AgentApp (<AGENT_CLIENT_ID>) sub=alice@example.com scp=[gateway.access] uid=00u...
  [4] MCP session opened to Gateway. About to list tools.
  [5] Gateway MCP tools discovered: ['downstream-echo-obo___callDownstreamApi'] (count=1)
```

Focus on line **[1]** for this chapter: `T_user`, the JWT that just cleared Runtime's inbound `customJWTAuthorizer`.

Values you should be able to correlate:

- `aud` should equal your `OKTA_AUDIENCE` from `.env` (typically `api://default`).
- `cid` should equal your `FRONTEND_CLIENT_ID` — the frontend was the OAuth actor for sign-in.
- `sub` is your user's login (email).
- `uid` is your Okta user's internal ID.
- Both `sub` and `uid` will be **identical** across T_user and T_gateway in the next chapter.
- `scp` includes `agent.access` — the custom scope that authorized this call.

There's no log line for the JWT validation itself — that happens inside the Runtime fabric, before your handler runs. The `T_user received` line proves the JWT was accepted (else the handler wouldn't run at all).

**Observation.** The Runtime is enforcing inbound auth via `customJWTAuthorizer` (configured in `agentcore.json` by step 8 of the README):

- It fetches Okta's JWKS (signing keys) at startup and caches them.
- On every invocation, it validates T_user's signature, checks `iss == https://<OKTA_DOMAIN>/oauth2/<auth-server-id>`, checks `aud ∈ [OKTA_AUDIENCE]`, and checks `exp`.
- Only after all that passes does the agent handler run.

If validation fails, the BFF would see a 401 and you'd never see your "Invoking agent…" log line.

**Why this matters.** The JWT in `context.request_headers["Authorization"]` inside your handler is **already validated**. You don't have to re-validate it. You can use it as the OBO subject token immediately.

**Important note about audience:** Okta's default auth server mints every token with the same `aud`. This means the Runtime's `allowedAudience` check alone does NOT prove the token was minted for the agent specifically — a T_gateway (which also has `aud = api://default`) would also pass this check. In practice this doesn't matter because:
1. A user's browser will only ever have T_user (via the Frontend app's scope).
2. The scope (`agent.access` vs `gateway.access`) is what carries the authorization signal.
3. In production you'd add scope validation at the resource layer to defend against replay.

---

## Chapter 3 — OBO #1 inside the agent

**Objective.** See the agent's OBO #1 call and the resulting T_gateway claims.

**Action.** Click "Ask agent" once, then run the same helper:

```bash
# From real-world/
python deploy/show_obo_trace.py
```

**Expected result.** In the same block from Chapter 2, focus on lines [2] and [3]:

```
  [2] OBO 1 start. Exchanging T_user for T_gateway via AgentCore Identity.
  [3] OBO 1 complete. T_gateway minted. aud=api://default cid=AgentApp (<AGENT_CLIENT_ID>) sub=alice@example.com scp=[gateway.access] uid=00u...
```

That's the whole OBO #1 hop, condensed to two lines. Compare against Chapter 2's T_user:

| Claim | T_user (Chapter 2) | T_gateway (this chapter) | Notes |
|---|---|---|---|
| `aud` | `api://default` | `api://default` | **Unchanged** — Okta's default server always mints the same audience |
| `cid` | `<FRONTEND_CLIENT_ID>` | `<AGENT_CLIENT_ID>` | Actor rotated — the agent is now the caller |
| `sub` | `alice@example.com` | `alice@example.com` | User identity preserved through the hop |
| `uid` | `00u...` | `00u...` (same) | Also unchanged |
| `scp` | `[openid, profile, email, agent.access]` | `[gateway.access]` | Scope narrowed to what the next hop needs |
| `exp` | (~1 hr from sign-in) | (fresh, ~1 hr from exchange) | New token, new expiry |

**Cross-check with CloudTrail.** AgentCore Identity read AgentApp's client secret from Secrets Manager to authenticate the exchange with Okta. You can see that read event:

```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=GetSecretValue \
  --max-results 5 --region "$AWS_REGION" \
  --query 'Events[?contains(Resources[0].ResourceName, `bedrock-agentcore-identity`)].[EventTime,Username,Resources[0].ResourceName]' \
  --output table
```

You should see one `GetSecretValue` for the agent-actor provider around the time of your invocation. Chapter 4 does the same check for OBO #2 (attributable to the Gateway service role instead of the agent's execution role).

**Observation.** What just happened during OBO #1:

1. The agent calls `GetWorkloadAccessTokenForJWT` with T_user. AgentCore Identity validates the JWT against the workload's configured trust (Okta discovery), wraps it as a workload token, and returns it.
2. The agent calls `GetResourceOauth2Token` with `oauth2Flow=ON_BEHALF_OF_TOKEN_EXCHANGE`, `scopes=["gateway.access"]`, `customParameters={"subject_token_type": "..."}`, AND `audiences=[OKTA_AUDIENCE]`.
3. AgentCore Identity reads the agent-actor credential provider from Secrets Manager (this is the `GetSecretValue` you see). It pulls AgentApp's `client_id` + `client_secret`.
4. AgentCore Identity POSTs to Okta's `/v1/token` endpoint:
   ```
   grant_type=urn:ietf:params:oauth:grant-type:token-exchange
   client_id=<AgentApp ID>
   client_secret=<AgentApp secret>
   subject_token=<T_user>
   subject_token_type=urn:ietf:params:oauth:token-type:access_token
   scope=gateway.access
   audience=api://default
   ```
5. Okta's access policy for AgentApp matches (Token Exchange + gateway.access) → Okta mints T_gateway with rotated `cid` and unchanged `sub`/`uid`.

The agent now holds T_gateway and uses it to talk to Gateway over MCP.

> **Gotcha worth internalizing.** The `customParameters={"subject_token_type": ...}` argument on the `GetResourceOauth2Token` call is REQUIRED for Okta Token Exchange and AgentCore Identity does NOT auto-add it for `CustomOauth2 + TOKEN_EXCHANGE` providers. Without it, Okta returns HTTP 400 (invalid_request). The Gateway-target outbound OBO config has the same requirement, which is why `deploy/02_create_gateway.py` also puts `subject_token_type` in the target's credential-provider config.

---

## Chapter 4 — OBO #2 inside the Gateway

**Objective.** See OBO #2 in action — the Gateway's transparent exchange that the agent code never participates in.

**Action.** OBO #2 happens *inside* the Gateway process, not in the agent. Two evidence sources:

**Source 1 — the agent's own log confirms it stopped at the Gateway boundary.** Click "Ask agent" once, then run the helper:

```bash
# From real-world/
python deploy/show_obo_trace.py
```

Focus on lines [4] and [5]:

```
  [4] MCP session opened to Gateway. About to list tools.
  [5] Gateway MCP tools discovered: ['downstream-echo-obo___callDownstreamApi'] (count=1)
```

Notice what's **not** logged: any downstream token, any downstream URL, any OBO exchange. The agent's log stops at "handed off to Gateway." Everything about T_downstream and the downstream call happens inside the Gateway boundary — invisible from here.

**Source 2 — CloudTrail shows the Gateway's OBO exchange.** The Gateway's service role read GatewayApp's secret from Secrets Manager to authenticate the exchange with Okta. Search for that event:

```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=GetSecretValue \
  --max-results 10 --region "$AWS_REGION" \
  --query 'Events[?contains(Resources[0].ResourceName, `bedrock-agentcore-identity`)].[EventTime, Username, Resources[0].ResourceName]' \
  --output table
```

**Expected:** two `GetSecretValue` events near your invocation timestamp, on *different* IAM principals:

- One by the **agent's execution role** (`AgentCore-oboUc2OktaAgent-…ExecutionRole-…`) — that was OBO #1 reading the agent-actor secret. You saw this in Chapter 3.
- One by the **Gateway service role** (`AmazonBedrockAgentCoreGatewayRole-obo-uc2-okta-gateway`) — that's OBO #2 reading the gateway-actor secret. Same primitive, different actor.

The two-different-roles pattern is the artifact: it proves OBO #1 and OBO #2 are cryptographically independent even though both flow through AgentCore Identity and both hit Okta's `/v1/token` endpoint.

**Source 3 — the tool response echoes T_downstream.** httpbin.org/anything echoes the request back including the Authorization header. Sign in via the frontend, ask a question, then look at the raw response (the result page has a "Show raw response" details block):

The tool result includes something like:
```json
{
  "headers": {
    "Authorization": "Bearer eyJhbGci...T_downstream...",
    "Host": "httpbin.org",
    ...
  },
  "method": "GET",
  "url": "https://httpbin.org/anything"
}
```

Copy that Bearer token into jwt.io. You'll see:
- `aud = api://default` (unchanged — same auth server)
- `cid = <GATEWAY_CLIENT_ID>` (actor rotated to Gateway)
- `sub = alice@example.com` (**still the same user**)
- `uid = 00u...` (**still the same user**)
- `scp = ["downstream.access"]` (narrowed again)

**Observation.** What happened during OBO #2 — different actors but the same primitive as OBO #1:

1. The Gateway received `tools/call callDownstreamApi` over MCP, with T_gateway as Bearer.
2. It validated T_gateway against its own `customJWTAuthorizer` (audience = OKTA_AUDIENCE).
3. It looked at the target's outbound `oauthCredentialProvider` config: `grantType=TOKEN_EXCHANGE`, `customParameters={"subject_token_type": ...}`, against the gateway-actor credential provider.
4. It called AgentCore Identity (using the **Gateway's** service role IAM identity — different role from the agent's) to do the exchange.
5. AgentCore Identity read the gateway-actor credential provider's secret (GatewayApp's client secret). POSTed to Okta:
   ```
   grant_type=urn:ietf:params:oauth:grant-type:token-exchange
   client_id=<GatewayApp ID>
   client_secret=<GatewayApp secret>
   subject_token=<T_gateway>
   subject_token_type=urn:ietf:params:oauth:token-type:access_token
   scope=downstream.access
   audience=api://default
   ```
6. Okta's Access Policy for GatewayApp matches (Token Exchange + downstream.access) → mints T_downstream.
7. The Gateway called `GET https://httpbin.org/anything` with `Authorization: Bearer T_downstream`. Returned the JSON echo to the agent as the MCP tool result.

**Crucially:** none of this happened in your agent code. Two OBO hops, identical primitive, different layer.

---

## Chapter 5 — `sub` and `uid` are the seams

**Objective.** Confirm with your own eyes that user identity is preserved across all three tokens.

**Prep.** Capture `T_user` following Chapter 1's steps (`/debug/token` → triple-click textarea → copy → paste into a `T_USER="..."` shell variable). Sanity check: `echo ${#T_USER}` should print a number > 800.

**Action.** Run the comparison script from `real-world/`:

```bash
# From real-world/
python deploy/compare_obo_claims.py --user-token "$T_USER"
```

Or if pasting a very long token is fiddly, save it to a file first and use `--user-token-file`:

```bash
# Save via curl (grab session cookie from the browser first):
curl -sb "session=<cookie>" http://localhost:8000/debug/token/raw > /tmp/t_user.txt
python deploy/compare_obo_claims.py --user-token-file /tmp/t_user.txt
```

The script:
- Decodes T_user (fails fast if you pasted something other than a JWT).
- Performs OBO #1 (the agent's exchange) and decodes T_gateway.
- Performs OBO #2 (the Gateway's exchange — re-doing it from your laptop using the gateway-actor provider) and decodes T_downstream.
- Prints the three side by side.

**Prerequisite for the OBO calls to succeed from your laptop:** your local AWS credentials need the same permissions the agent's execution role has (or broader). If you get `AccessDeniedException` on either OBO call, either widen your local role temporarily or accept that Chapter 5's proof-by-decoding won't run locally — Chapter 3's `show_obo_trace.py` output already gives you T_user and T_gateway claims from the deployed agent's logs.

**Expected result.** Three blocks of claims. Compare them.

**Observation.** What you should see:

- `aud` **stays constant** at `api://default` (or whatever `OKTA_AUDIENCE` you configured). Okta's default auth server mints every token with the same audience.
- `cid` rotates: `<FRONTEND_CLIENT_ID> → <AGENT_CLIENT_ID> → <GATEWAY_CLIENT_ID>`. The actor walks down the chain.
- `sub` is **identical** in all three: `alice@example.com`. This is your user login regardless of which app last touched the token.
- `uid` is **identical** in all three: `00u...`. This is Okta's internal user ID. Auditing systems can use either `sub` or `uid` to attribute every action — anywhere in the chain — back to Alice. **These two claims are the seams that hold the chain together.**
- `scp` narrows: `[openid profile email agent.access] → [gateway.access] → [downstream.access]`. The token's authority narrows as it walks toward the actual API.

Print one of these side-by-side diffs and put it next to the architecture diagram. That's the entire mental model of OBO in one picture, Okta flavor.

**Comparison with Entra.** If you also ran UC2 Entra, notice the difference: in Entra the audience `aud` rotates (AgentApp → GatewayApp → Graph) because each Entra app has its own audience URI. In Okta the audience stays constant because Okta's default auth server has a single audience — the differentiation is entirely by `scp`. Both approaches are valid; they solve the same problem with different mechanics.

---

## Chapter 6 — What the agent code DOESN'T do

**Objective.** Internalize the most important practical takeaway: how much token-handling logic the Gateway absorbed.

**Action.** Open `agent/agent.py` and `01-agent-to-downstream/okta/real-world/agent/agent.py` side by side.

**Expected result.** Comparing the two:

| | UC1 agent (Okta) | UC2 agent (Okta) |
|---|---|---|
| Imports `requests`? | yes | no |
| Calls the downstream URL directly? | yes (`/v1/userinfo`) | no |
| Defines a `@tool` for `callDownstreamApi`? | yes (`get_my_profile`) | no — tool comes from Gateway |
| Reads/parses the downstream response? | yes | no — Gateway returns parsed result via MCP |
| Stores or forwards a downstream token? | yes (transient, in-memory) | no — Gateway never gives it to the agent |
| Does an OBO call? | yes (1) | yes (1) |
| LOC for the OBO/downstream logic | ~80 | ~30 |

**Observation.** **The Gateway is the OBO primitive applied to infrastructure.** The agent now has exactly one OBO hop in code (OBO #1) and the rest is "make an MCP tool call." For each additional downstream surface you'd add to the chain in UC1, you'd add a new OBO hop, a new credential provider, and a new `requests.get(...)`. In UC2, you'd add another Gateway target — same OBO primitive, in config, no agent code changes.

The trade:

- **You give up:** fine control over per-call token caching, mixing OBO with M2M tokens for different services, custom Authorization header schemes.
- **You gain:** zero token handling in agent code, OpenAPI-driven tool surfaces, and a single MCP endpoint that can host many downstream targets, each with its own outbound auth.

For the "agent on Runtime expressing tools backed by external APIs" pattern — which is most agentic systems — Gateway is significantly less code per integration.

---

## What's next

- **Try adding a second Gateway target.** Something like `https://httpbin.org/get` (a different echo endpoint) or `https://reqres.in/api/users/2` (a mock user API). Same OpenAPI shape; same outbound OBO config; the agent gets a new tool with no code change. Watch CloudWatch — you'll see two OBO #2 calls per request (one per tool invocation).
- **Compare UC2 Okta with UC2 Entra.** Same chain shape, different protocol on the wire (Token Exchange vs JWT-Bearer). Same OBOTRACE format so you can side-by-side diff. Key difference: in Okta `aud` stays constant while `cid` walks; in Entra `aud` rotates while `azp` walks. Same result, different mechanics.
- **Read the [`OBO Reference Guide`](../../../../OBO%20Reference%20Guide.md)** at the top of the repo for the broader OBO taxonomy (`actorTokenContent` modes, when to use custom vs default auth servers, RFC 8693 vs 7523, etc.).
- **Swap in your own downstream API.** Point `gateway/downstream_openapi.json` at your resource server, validate `sub`/`uid`/`scp` on incoming tokens, and you have a real OBO-based delegation pattern.
