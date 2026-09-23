# Learning Guide — UC2 Entra Real-World

Six short chapters you walk through *after* the stack is running. Each chapter has an **objective**, an **action** (something you do), an **expected result**, and an **observation** (what to take away).

This is where the OBO concepts land. The `README.md` got it deployed; this guide explains what's actually happening and where to see it.

**Setup check before you start:** the BFF (`python frontend/app.py`) is running, you've signed in once, and "Ask agent" returns an answer that mentions you by name. If any of those don't work, fix that before continuing — the chapters all assume the happy path runs end-to-end.

**Where to run these commands.** The primary observation tool for Chapters 2–4 is a helper script that pulls just the OBO-trace log lines and prints them grouped by invocation:

```bash
# From real-world/
python deploy/show_obo_trace.py           # last 5 minutes
python deploy/show_obo_trace.py --since 30m   # widen the window
python deploy/show_obo_trace.py --raw     # show every matching line (skip dedupe)
```

The script cd's into the runtime folder for you, calls `agentcore logs`, deduplicates AgentCore's JSON+text log pair emissions, and formats a per-invocation view like:

```
─── Invocation 1 ─────────────────────────────────────────────────
  [1] T_user received. aud=… azp=… oid=… scp=… ver=…
  [2] OBO 1 start. …
  [3] OBO 1 complete. T_gateway minted. aud=… azp=… oid=… scp=… ver=…
  [4] MCP session opened to Gateway. …
  [5] Gateway MCP tools discovered: […] (count=1)
```

If you'd rather use `agentcore logs` directly, run it from inside `$AGENT_RUNTIME_NAME/` (e.g. `oboUc2EntraAgent/`) and grep for the marker prefix:

```bash
cd $AGENT_RUNTIME_NAME
agentcore logs --since 5m | grep "OBOTRACE:"
```

Note: `agentcore logs --query "…"` treats `#` and spaces as term separators and returns broad matches — `grep` on `OBOTRACE:` gives a cleaner, exact filter.

Helpful side windows to keep open:

- The frontend's terminal output (it logs every request).
- A terminal in `real-world/` for `python deploy/show_obo_trace.py`.
- Optionally a terminal in `$AGENT_RUNTIME_NAME/` for direct `agentcore logs`/`agentcore traces` commands.

---

## Chapter 1 — Where T_user comes from

**Objective.** See where the chain starts: the user's first token, minted by Entra during sign-in to the Frontend app.

**Action.** Sign out (`/auth/logout`), then sign back in. Watch the browser network tab during the redirect to `login.microsoftonline.com`.

**Expected result.** You complete the auth-code flow and land back on `http://localhost:8000/` with "Signed in as …" displayed. The frontend's terminal shows successful POST to `/auth/callback` and a 302 to `/`.

**Observation.** What the frontend stores at this point:

- An access token. `aud = AGENT_CLIENT_ID` (NOT FrontendApp!). This is T_user.
- An ID token, used to populate the welcome banner.
- An MSAL refresh token, in MSAL's in-memory cache.

The audience already points at AgentApp because that's the scope the frontend requested (`AGENT_SCOPE = api://AGENT_CLIENT_ID/access_as_user`). FrontendApp authenticated the user; AgentApp is the audience the user delegates to.

**Try this:** With the BFF running and the browser signed in, visit:

```
http://localhost:8000/debug/token
```

That page displays the current session's `T_user` in a copyable textarea. It's a learning-only route in `frontend/app.py`; not intended for production (mentioned inline on the page).

Copy the full token and paste it into <https://jwt.io>. You'll see (among others):

```
aud   : <AGENT_CLIENT_ID>             ← T_user is for AgentApp
azp   : <FRONTEND_CLIENT_ID>          ← actor is FrontendApp (it did the request)
oid   : <your stable user OID>        ← will appear unchanged in T_gateway and T_graph
sub   : <PPID-1>                      ← per-app pseudonymous identifier
scp   : access_as_user
ver   : 2.0
```

**Getting the token into your shell** (for Chapter 5's `compare_obo_claims.py`):

1. In the browser at `http://localhost:8000/debug/token`, **triple-click the textarea** to select the whole token.
2. Copy it (Cmd+C on macOS, Ctrl+C elsewhere).
3. In a terminal, paste it between the quotes of a shell variable assignment:

```bash
T_USER="<paste-the-token-here>"    # cursor between the quotes, then paste, then Enter
echo "sanity check — length should be > 1500, starts with eyJ:"
echo "  length=${#T_USER}  starts=${T_USER:0:8}"
```

If the length is under 500 or doesn't start with `eyJ`, the paste didn't take the whole token — try again. Some terminals convert long strings on paste; if that keeps happening, save the token to a file from the browser and use `--user-token-file` in Chapter 5 instead.

---

## Chapter 2 — Inbound auth at the Runtime

**Objective.** Understand what happens **before** your agent code runs.

**Action.** Click "Ask agent" in the browser once, then run the helper script from `real-world/`:

```bash
# From real-world/
python deploy/show_obo_trace.py
```

The helper runs `agentcore logs` under the hood, filters to the `OBOTRACE:` lines the agent emits at each hop, deduplicates the JSON/text pairs AgentCore produces for every log record, and groups the output by invocation.

**Expected result.** One block per invocation, five lines each. The first line is what Chapter 2 is about:

```
─── Invocation 1 ─────────────────────────────────────────────────
  [1] T_user received. aud=<AGENT_CLIENT_ID> azp=<FRONTEND_CLIENT_ID> oid=<user-oid> scp=access_as_user ver=2.0
  [2] OBO 1 start. Exchanging T_user for T_gateway via AgentCore Identity.
  [3] OBO 1 complete. T_gateway minted. aud=<GATEWAY_CLIENT_ID> azp=<AGENT_CLIENT_ID> oid=<user-oid> scp=access_as_user ver=2.0
  [4] MCP session opened to Gateway. About to list tools.
  [5] Gateway MCP tools discovered: ['microsoft-graph-obo___getMyProfile'] (count=1)
```

Focus on line [1] for this chapter: it's `T_user`, the JWT that just cleared Runtime's inbound `customJWTAuthorizer`.

Values you should be able to correlate:

- `aud` should equal your `AGENT_CLIENT_ID` from `.env`.
- `azp` should equal your `FRONTEND_CLIENT_ID` — the frontend was the OAuth actor for sign-in.
- `oid` is your user's stable Entra Object ID — it'll be **identical** across T_user, T_gateway, and T_graph in later chapters.
- `scp` and `ver` should match what you saw at jwt.io in Chapter 1.

Note there's no log line for the JWT validation itself — that happens inside the Runtime fabric, before your handler runs. The `T_user received` line proves the JWT was accepted (else the handler wouldn't run at all).

Prefer raw CloudWatch? Discover the actual log group name first — it includes the runtime endpoint qualifier:

```bash
aws logs describe-log-groups \
  --log-group-name-prefix "/aws/bedrock-agentcore/runtimes/${AGENT_RUNTIME_NAME}" \
  --region "$AWS_REGION" \
  --query 'logGroups[].logGroupName' --output table

# Then tail the one that matches your runtime:
aws logs tail "<log-group-name-from-above>" --follow --region "$AWS_REGION"
```

**Observation.** The Runtime is enforcing inbound auth via `customJWTAuthorizer` (configured in `agentcore.json` by step 8 of the README). Specifically:

- It fetches Entra's JWKS (signing keys) at startup and caches them.
- On every invocation, it validates `T_user`'s signature against those keys, checks `iss == https://login.microsoftonline.com/<tenant>/v2.0` (or the v1 issuer if appropriate), checks `aud ∈ allowedAudience`, and checks `exp`.
- Only after all that passes does the agent handler run.

If validation fails, the BFF would see a `401` and you'd never see your "Invoking agent…" log line. **Try it:** in `home.html`'s form action, briefly hardcode a wrong invoke URL, ask, and observe the 401 in the BFF terminal. (Then revert.)

**Why this matters.** The JWT in `context.request_headers["Authorization"]` inside your handler is **already validated**. You don't have to re-validate it. You can use it as the OBO subject token immediately.

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
  [3] OBO 1 complete. T_gateway minted. aud=<GATEWAY_CLIENT_ID> azp=<AGENT_CLIENT_ID> oid=<user-oid> scp=access_as_user ver=2.0
```

That's the whole OBO #1 hop, condensed to two lines. Compare against what you saw in Chapter 2 for T_user:

That's the whole OBO #1 hop, condensed to two lines. Compare against what you saw in Chapter 2 for T_user:

| Claim | T_user (Chapter 2) | T_gateway (this chapter) | Notes |
|---|---|---|---|
| `aud` | `<AGENT_CLIENT_ID>` | `<GATEWAY_CLIENT_ID>` | Audience rotated — this token is now for the Gateway |
| `azp` | `<FRONTEND_CLIENT_ID>` | `<AGENT_CLIENT_ID>` | Actor rotated — the agent is now the caller |
| `oid` | `<user-oid>` | **same `<user-oid>`** | User identity preserved through the hop |
| `scp` | `access_as_user` | `access_as_user` | Scope named the same on both apps in this example |
| `ver` | `2.0` | `2.0` | Both v2 tokens because both apps have `requestedAccessTokenVersion: 2` |

**Cross-check with CloudTrail.** AgentCore Identity read AgentApp's client secret from Secrets Manager to authenticate the exchange with Entra. You can see that read event:

```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=GetSecretValue \
  --max-results 5 --region "$AWS_REGION" \
  --query 'Events[?contains(Resources[0].ResourceName, `bedrock-agentcore-identity`)].[EventTime,Username,Resources[0].ResourceName]' \
  --output table
```

You should see one `GetSecretValue` for the agent-actor provider around the time of your invocation. Chapter 4 has the same check for OBO #2 (attributable to the Gateway service role instead of the agent's execution role).

**Observation.** What just happened during OBO #1:

1. The agent calls `GetWorkloadAccessTokenForJWT` with T_user. AgentCore Identity validates the JWT against the workload's configured trust (Entra discovery), wraps it as a workload token, and returns it.
2. The agent calls `GetResourceOauth2Token` with `oauth2Flow=ON_BEHALF_OF_TOKEN_EXCHANGE`, `scopes=[GATEWAY_SCOPE]`, AND `customParameters={"requested_token_use": "on_behalf_of"}`.
3. AgentCore Identity reads the agent-actor credential provider from Secrets Manager (THIS is the GetSecretValue you see). It pulls AgentApp's `client_id` + `client_secret`.
4. AgentCore Identity POSTs to Entra's `/oauth2/v2.0/token` endpoint:
   ```
   grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
   client_id=<AgentApp ID>
   client_secret=<AgentApp secret>
   assertion=T_user
   scope=api://<GatewayApp ID>/access_as_user
   requested_token_use=on_behalf_of
   ```
5. Entra mints T_gateway. Audience rotated to GatewayApp; actor (`azp`) rotated to AgentApp; `oid` unchanged.

The agent now holds T_gateway and uses it to talk to Gateway over MCP.

> **Gotcha worth internalizing.** The `customParameters={"requested_token_use": "on_behalf_of"}` argument on the `GetResourceOauth2Token` call is REQUIRED for Microsoft OBO but AgentCore Identity does NOT auto-add it for `CustomOauth2 + JWT_AUTHORIZATION_GRANT` providers. Without that argument Entra rejects the exchange with `HTTP 400: Token exchange failed`. Only the built-in `MicrosoftOauth2` vendor auto-adds this parameter. When using `CustomOauth2` (as we do — the built-in vendor doesn't expose OBO config knobs), the caller must pass it explicitly. The Gateway-target outbound OBO config has the same requirement, which is why `deploy/02_create_gateway.py` puts `customParameters: {"requested_token_use": "on_behalf_of"}` in the target's credential-provider config.

---

## Chapter 4 — OBO #2 inside the Gateway

**Objective.** See OBO #2 in action — the Gateway's transparent exchange that the agent code never participates in.

**Action.** OBO #2 happens *inside* the Gateway process, not in the agent. Two evidence sources:

**Source 1 — the agent's own log confirms it stopped at the Gateway boundary.** Click "Ask agent" once, then run the same helper:

```bash
# From real-world/
python deploy/show_obo_trace.py
```

Focus on lines [4] and [5]:

```
  [4] MCP session opened to Gateway. About to list tools.
  [5] Gateway MCP tools discovered: ['microsoft-graph-obo___getMyProfile'] (count=1)
```

Notice what's **not** logged: any Graph token, any Graph URL, any OBO exchange. The agent's log stops at "handed off to Gateway." Everything about T_graph and the Graph call happens inside the Gateway boundary — invisible from here.

**Source 2 — CloudTrail shows the Gateway's OBO exchange.** The Gateway's service role read GatewayApp's secret from Secrets Manager to authenticate the exchange with Entra. Search for that event:

```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=GetSecretValue \
  --max-results 10 --region "$AWS_REGION" \
  --query 'Events[?contains(Resources[0].ResourceName, `bedrock-agentcore-identity`)].[EventTime, Username, Resources[0].ResourceName]' \
  --output table
```

**Expected:** two `GetSecretValue` events near your invocation timestamp, on *different* IAM principals:

- One by the **agent's execution role** (`AgentCore-oboUc2EntraAgen-…ExecutionRole-…`) — that was OBO #1 reading the agent-actor secret. You saw this in Chapter 3.
- One by the **Gateway service role** (`AmazonBedrockAgentCoreGatewayRole-obo-uc2-entra-gateway`) — that's OBO #2 reading the gateway-actor secret. Same primitive, different actor.

The two-different-roles pattern is the artifact: it proves OBO #1 and OBO #2 are cryptographically independent even though both flow through AgentCore Identity.

**Observation.** What happened during OBO #2 — different actors but the same primitive as OBO #1:

1. The Gateway received `tools/call getMyProfile` over MCP, with T_gateway as Bearer.
2. It validated T_gateway against its own `customJWTAuthorizer` (audience must = GatewayApp).
3. It looked at the target's outbound `oauthCredentialProvider` config: `grantType=TOKEN_EXCHANGE` with `customParameters={"requested_token_use": "on_behalf_of"}` against the gateway-actor credential provider.
4. It called AgentCore Identity (using the **Gateway's** service role IAM identity — different role from the agent's) to do the exchange:
   - `GetWorkloadAccessTokenForJWT` with T_gateway as input.
   - `GetResourceOauth2Token` with the gateway-actor provider, `customParameters`, and `scopes=["https://graph.microsoft.com/.default"]`.
5. AgentCore Identity read the gateway-actor credential provider's secret (GatewayApp's client secret). POSTed to Entra:
   ```
   grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer
   client_id=<GatewayApp ID>
   client_secret=<GatewayApp secret>
   assertion=T_gateway
   scope=https://graph.microsoft.com/.default
   requested_token_use=on_behalf_of
   ```
6. Entra minted T_graph. Audience = `https://graph.microsoft.com`; actor = GatewayApp; `oid` unchanged.
7. The Gateway called `GET https://graph.microsoft.com/v1.0/me` with `Authorization: Bearer T_graph`. Returned the JSON to the agent as the MCP tool result.

**Crucially:** none of this happened in your agent code. Two OBO hops, identical primitive, different layer.

---

## Chapter 5 — `oid` is the seam

**Objective.** Confirm with your own eyes that user identity is preserved across all three tokens.

**Prep.** Capture `T_user` following Chapter 1's steps (`/debug/token` → triple-click textarea → copy → paste into a `T_USER="..."` shell variable). Sanity check: `echo ${#T_USER}` should print a number > 1500.

**Action.** Run the comparison script from `real-world/`:

```bash
# From real-world/
python deploy/compare_obo_claims.py --user-token "$T_USER"
```

Or if pasting a very long token is fiddly in your terminal, save it to a file and use `--user-token-file`:

```bash
# Save the token from the browser page into a file:
#   1. Open http://localhost:8000/debug/token
#   2. Copy the textarea contents
#   3. Paste into /tmp/t_user.txt and save
python deploy/compare_obo_claims.py --user-token-file /tmp/t_user.txt
```

The script:

- Decodes T_user (must be a valid JWT — if you see `_decode_error` in the first block, you pasted something other than a token; go back to Chapter 1 and re-capture).
- Performs OBO #1 (the agent's exchange) and decodes T_gateway.
- Performs OBO #2 (the Gateway's exchange — re-doing it from your laptop using the gateway-actor provider) and decodes T_graph.
- Prints the three side by side.

**Prerequisite for the OBO calls to succeed from your laptop:** your local AWS credentials need the same permissions the agent's execution role has (or broader). If you get `AccessDeniedException` on either OBO call, either widen your local role temporarily or accept that Chapter 5's proof-by-decoding of T_gateway/T_graph won't run locally — Chapter 3's `show_obo_trace.py` output already gives you T_user and T_gateway claims from the deployed agent's logs, which is the same evidence.

**Expected result.** Three blocks of claims. Compare them.

**Observation.** What you should see:

- `aud` rotates: `AgentApp → GatewayApp → https://graph.microsoft.com`. Each token is targeted at exactly one consumer.
- `azp` (or `appid` on v1 tokens) rotates: `FrontendApp → AgentApp → GatewayApp`. The actor walks down the chain.
- `oid` is **identical** in all three. This is your user, regardless of which app last touched the token. Auditing systems use this to attribute every action — anywhere in the chain — back to the originating user.
- `sub` is different in all three. Entra mints a new pairwise pseudonymous identifier (PPID) per audience. This is good for privacy: a service can identify "this user" without learning the user's identity in any other service. But don't use `sub` for cross-service correlation — use `oid`.
- `scp` shrinks: `access_as_user → access_as_user → User.Read`. The token's authority narrows as it walks toward the actual API.

Print one of these side-by-side diffs and put it next to the architecture diagram. That's the entire mental model of OBO in one picture.

---

## Chapter 6 — What the agent code DOESN'T do

**Objective.** Internalize the most important practical takeaway: how much token-handling logic the Gateway absorbed.

**Action.** Open `agent/agent.py` and `01-agent-to-downstream/entra/real-world/agent/agent.py` side by side.

**Expected result.** Comparing the two:

| | UC1 agent | UC2 agent |
|---|---|---|
| Imports `requests`? | yes | no |
| Calls `https://graph.microsoft.com`? | yes | no |
| Defines a `@tool` for `get_my_profile`? | yes | no — tool comes from Gateway |
| Reads/parses the Graph response? | yes | no — Gateway returns parsed result via MCP |
| Stores or forwards a Graph token? | yes (transient, in-memory) | no — Gateway never gives it to the agent |
| Does an OBO call? | yes (1) | yes (1) |
| LOC for the OBO/downstream logic | ~80 | ~30 |

**Observation.** **The Gateway is the OBO primitive applied to infrastructure.** The agent now has exactly one OBO hop in code (OBO #1) and the rest is "make an MCP tool call." For each additional downstream surface you'd add to the chain in UC1, you'd add a new OBO hop, a new credential provider, and a new `requests.get(...)`. In UC2, you'd add another Gateway target — same OBO primitive, in config, no agent code changes.

The trade is:

- **You give up:** fine control over per-call token caching, mixing OBO with M2M tokens for different services, custom Authorization header schemes.
- **You gain:** zero token handling in agent code, OpenAPI-driven tool surfaces, and a single MCP endpoint that can host many downstream targets, each with its own outbound auth.

For the "agent on Runtime expressing tools backed by external APIs" pattern — which is most agentic systems — Gateway is significantly less code per integration.

---

## What's next

- Try adding a second Gateway target (e.g. `/me/messages` for mail). Same OpenAPI shape; same outbound OBO config; the agent gets a new tool with no code change. Watch CloudWatch — you'll see two OBO #2 calls per request (one per tool invocation).
- Read the [`OBO Reference Guide.md`](../../../../OBO%20Reference%20Guide.md) at the top of the repo for the broader OBO taxonomy (`actorTokenContent` modes, RFC 8693 vs 7523, etc.).
- When the Okta variant lands, compare the two: same chain shape, different protocol on the wire (Token Exchange vs JWT Bearer), and Okta's nested `act` claim instead of Entra's `azp` chain.
