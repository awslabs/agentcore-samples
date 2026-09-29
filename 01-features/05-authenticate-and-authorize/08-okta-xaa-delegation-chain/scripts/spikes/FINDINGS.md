# Spike findings

Run live against a real AWS account in `us-west-2`, boto3 1.43.104, on a
throwaway MCP gateway (`authorizerType: NONE`) with an echo API behind API Gateway.
Reproduce with `python scripts/spikes/run_spike.py`; remove with `--cleanup`.

`authorizerType: NONE` was deliberate: with no inbound `Authorization` at all, any
`Authorization` the upstream reports can only have come from the interceptor.

## 1. The interceptor's transformed `Authorization` header IS forwarded — PASS

The upstream echo reported exactly what the interceptor set:

```json
{"authorization_seen": "Bearer SPIKE-INJECTED-TOKEN", ...}
```

So the egress design works: a REQUEST interceptor can mint `T_tool` and inject it,
and the gateway forwards it to the API. This is the load-bearing assumption of the
whole sample, and it holds.

## 2. `JWT_PASSTHROUGH` is unusable on an MCP gateway — plan corrected

Every combination was rejected:

| Target | Result with `JWT_PASSTHROUGH` |
| :--- | :--- |
| `mcp.openApiSchema` | `Open api schema target does not support JWT_PASSTHROUGH credential provider type` |
| `mcp.mcpServer` | `MCP server target does not support JWT_PASSTHROUGH credential provider type` |
| `http.passthrough` | `HTTP target configuration is not supported for gateways with MCP protocol type` |

`JWT_PASSTHROUGH` is an `http.*` feature, and `http.*` targets require a gateway
with **no** protocol type. An MCP gateway — which the agent needs in order to call
tools — cannot use it.

**It turns out not to be needed.** `credentialProviderConfigurations` can be
**omitted entirely**, and the interceptor's `Authorization` is still forwarded
(finding 1 was produced with no outbound credential provider on the target). The
plan's "hop D uses `JWT_PASSTHROUGH`" is wrong and is corrected to "target with no
outbound credential provider; the interceptor supplies `Authorization`".

Also probed on `mcp.openApiSchema`: `GATEWAY_IAM_ROLE` needs an
`iamCredentialProvider` block, `OAUTH` needs an `oauthCredentialProvider` block,
and `CALLER_IAM_CREDENTIALS` is rejected outright.

## 3. Only `Authorization` is forwarded — custom headers are dropped

The interceptor set both `Authorization` and `X-Spike-Marker`. The upstream saw the
first and reported `x_spike_marker: <ABSENT>`; the header list it received was

```
accept-encoding, authorization, content-length, content-type, host,
user-agent, x-amzn-trace-id, x-forwarded-for, x-forwarded-port, x-forwarded-proto
```

**Consequence:** a correlation id cannot be passed to the upstream API in a custom
header. It must travel in the request body, or the API must correlate on
`X-Amzn-Trace-Id`, which *is* forwarded.

## 4. The interceptor event carries no caller identity — validates dropping the cache

Full event shape observed:

```
interceptorInputVersion
mcp.gatewayRequest.{path, httpMethod, headers, body}
mcp.gatewayRequest.context      <- present as a field, but null here
mcp.gatewayResponse             <- null on a REQUEST interception
mcp.rawGatewayRequest.body
```

No session id, no workload identity, no principal, no `sub`, no ARN. (A grep for
"identity" matches only `Accept-Encoding: identity`.)

Two consequences:

- **There is no AgentCore identity chain for an interceptor to read.** Had the
  design kept the token-blind agent, the egress interceptor would have had no way
  to learn the user except an out-of-band cache. This retroactively supports
  dropping the cache in favour of the agent forwarding a narrowly scoped token that
  the interceptor reads from `gatewayRequest.headers`.
- **`Mcp-Session-Id` is not in the event**, even though the client sent one. The
  usable correlation id at the interceptor is `X-Amzn-Trace-Id`.

### Follow-up, not blocking

`mcp.gatewayRequest.context` exists but was `null` under `authorizerType: NONE`.
It may be populated with validated JWT claims under `CUSTOM_JWT`. If it is, the
interceptor can read the user's `sub` from the gateway's own validation instead of
decoding the token itself — strictly better, since the gateway has already verified
the signature. Worth checking as soon as the real gateway is up with Okta inbound.

## 5. ID-JAG leg 1 accepts ONLY an ID token — the access token is rejected

Run live against the tenant with the registered AI Agent
(`scripts/spikes/spike2_idjag_subject.py`, interactive sign-in).

| leg 1 `subject_token` | Result |
| :--- | :--- |
| the **ID token** (`subject_token_type=id_token`) | ✅ **ACCEPTED** — ID-JAG minted |
| the **access token** (`subject_token_type=access_token`) | ❌ `400 invalid_request: 'subject_token' is invalid: no delegation policy authorizes this token.` |

The error names the cause exactly: the **User access** binding authorises the *ID
token issued by the linked app*, and nothing else. So the customer's design — stash
the invocation *access* token and exchange that — **cannot work with Okta XAA**. The
ID token is structurally required.

Leg 2 then succeeded from the ID-token ID-JAG, producing a real resource token.

### Observed claims, end to end

| Token | `iss` | `aud` | `cid` | `sub` | `scp` | TTL |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| ID token | AS 1 | the linked app | — | `00u15gg…` (user **id**) | — | 1h |
| access token (`T_user`) | AS 1 | `api://agentcore` | linked app | `alice@example.com` | `agent.access openid profile email` | 1h |
| **ID-JAG** | **org server** | AS 2 issuer | — | `00u15gg…` | `todos.read` | **299s** |
| **`T_tool`** | AS 2 | **`api://todo`** | linked app | `alice@example.com` | `todos.read` | 1h |

Three things to carry into the build:

- **`sub` is not one format.** ID token and ID-JAG carry the Okta **user id**;
  access tokens and `T_tool` carry the **email**. Cedar reads claims from the
  gateway's inbound access token, and the API authorises on `T_tool`, so both see
  the email and stay consistent — but do not assume the ID-JAG's `sub` matches.
- **The ID-JAG lives 299 seconds and is single-use.** Mint one per exchange; never
  cache it.
- **`T_tool` lives an hour, so cache it per user.** Each leg-1 call consumes one of
  the 250 ID-JAGs per user per resource per month allowed under plain SSO. Without a
  cache a chatty demo would exhaust that. Port the TTL cache from
  `06-okta-xaa/agent/xaa_client.py`.

## Consequence: how the ID token reaches the interceptor

Finding 3 said custom headers are dropped — but that applies to
**gateway → upstream**. The interceptor sees the *inbound* request, so a custom
header from the agent does reach it when the interceptor is configured with
`passRequestHeaders: true`.

So the agent sends two headers on the MCP call:

```
Authorization:      Bearer <T_gateway>   # the gateway's CUSTOM_JWT + Cedar read this
X-Okta-Id-Token:    <the ID token>       # the interceptor reads this for leg 1
```

The interceptor then replaces `Authorization` with `T_tool`, and only that reaches
the API. MCP sets headers per connection, which suits an ID token — it is constant
for the user's session, unlike the per-call `owner`/`repo` values that defeated the
GitHub sample.

**Not the request body**, which was the earlier plan: the body carries tool
arguments the model composes, so a credential there would sit in the model's
context. A connection header never does.

## 6. Interceptor credential injection works WITH Cedar ENFORCE

Run on a gateway with `CUSTOM_JWT` (AS 1), a policy engine, a REQUEST interceptor
with `passRequestHeaders: true`, and an echo target with no outbound credential
provider (`scripts/spikes/spike4_jwt_gateway.py`).

**What the interceptor injects is what matters** — not whether it replaces the
header:

| Policy engine | Injected `Authorization` | `tools/list` | `tools/call` |
| :--- | :--- | :--- | :--- |
| `ENFORCE` | **real `T_tool`** (valid JWT, resource-AS issuer) | ✅ | ✅ `isError:false`, Cedar **permitted** |
| `ENFORCE` | the same inbound JWT, echoed back | ✅ | ✅ Cedar **permitted** |
| `ENFORCE` | *nothing* — header left untouched | ✅ | ✅ Cedar **permitted** |
| `ENFORCE` | a **non-JWT** marker string | ❌ | ❌ `-32002 Policy Evaluation Internal Failure` |
| `LOG_ONLY` | a non-JWT marker string | ✅ | ✅ upstream saw the marker |

### Conclusion

**All three questions pass, and the design needs no compromise.**

- **Q1 ✅** A custom inbound header reaches the interceptor
  (`ID_TOKEN_HEADER_SEEN=True len=998`, `x-okta-id-token` present). The ID token has a
  delivery route that never enters the model's context.
- **Q2 ✅** The interceptor's injected `Authorization` reaches the upstream on a
  `CUSTOM_JWT` gateway, **including a token from a different issuer** than the one the
  gateway's authorizer trusts. The gateway does not re-validate what the interceptor
  puts there.
- **Q3 ✅** Cedar in `ENFORCE` sees the inbound JWT's claims as principal tags:
  `principal is AgentCore::OAuthUser` with `hasTag("sub")` permitted the call, *while*
  the interceptor was swapping the credential.

So a single gateway can do per-tool, per-user Cedar **and** inject the resource
credential. No ingress gateway is required, and the customer's two requirements are
not in tension.

### The one real constraint

The policy engine builds its principal from the request it sees, so the value in
`Authorization` must remain a **parseable JWT**. Put a non-JWT there and policy
evaluation errors out with `Policy Evaluation Internal Failure` — which is an error,
not a deny, and it also breaks `tools/list`. In this design the injected value is
always a real `T_tool`, so the constraint is satisfied naturally.

### Corrections to earlier notes in this file

Two things recorded here previously were wrong, both traceable to one unrepresentative
test that injected a placeholder string instead of a real token:

1. **`tools/list` is not blocked by Cedar.** It succeeded in every row with a valid
   JWT. Its denial was caused by the non-JWT marker.
2. **There is no interceptor-versus-Cedar conflict**, and no trade-off table between
   "credential outside the agent" and "per-tool per-user policy". Both hold at once.

### Bonus: the delegation trail is visible in `T_tool`

The minted resource token carries, alongside `sub = alice@example.com` and
`scp = [todos.read]`:

- `cid` = the AI Agent's `wlp…` client
- `act.sub` = the same `wlp…` — an RFC 8693 **actor** claim naming the agent that
  acted
- `sub_profile` = `ai_agent web_app`

So the resource API can see both *who* the user is and *which agent* acted for them,
without any extra plumbing. Worth surfacing in the sample's docs and trace output.

## 7. Built and run for real — what the live chain showed

Hop D deployed and exercised via `scripts/test_chain.py` (interactive sign-in, then the
gateway directly). `todo___whoami` returned:

```json
{ "user": "alice@example.com",
  "acting_agent": "wlp0EXAMPLE0AGENT0ID",
  "scopes": ["todos.read"] }
```

So the user's identity and the acting agent both survived to the resource API.

### `allowedClients` does not work with Okta

Verified by minting a machine token whose `cid` was **exactly** the listed client:

| Authorizer | Result |
| :--- | :--- |
| `allowedAudience` + `allowedScopes=[tools.access]` | ✅ 200 |
| `allowedAudience` + `allowedClients=[<that cid>]` | ❌ 403 `insufficient_scope` |
| `allowedAudience` only | ✅ 200 |

Okta puts the client id in `cid`; the gateway evidently compares a claim Okta does not
emit in access tokens (`client_id` / `azp`). Worse, the mismatch is reported as
**`insufficient_scope`**, which sends you looking at scopes.

**Pin on `allowedScopes` instead.** It gives the same protection here: only the OBO
exchange mints `tools.access`, so a replayed `T_user` (carrying `agent.access`) is
refused at the gateway.

### The interceptor must ship its dependencies

`pyjwt` and `cryptography` are not in the Lambda runtime. Shipping `handler.py` alone
fails at import with `No module named 'jwt'`, and the gateway surfaces that as a **500
from the tool call** — it reads like a gateway fault, not a packaging one. Bundle with
`--platform manylinux2014_x86_64` because `cryptography` has compiled extensions.

### Exchange only on `tools/call`

The interceptor is invoked for **every** MCP method. Left ungated it minted a resource
token during `initialize`, which burns an ID-JAG even if the client never calls a tool
— against a 250 per user, per resource, per month budget. Gating on
`method == "tools/call"` makes it one ID-JAG per user per hour (the `T_tool` lifetime)
rather than one per session.

### Observed latency

| Step | Time |
| :--- | :--- |
| ID-JAG legs 1 + 2, cold | **1664 ms** |
| subsequent calls (cache hit) | ~2 ms |
| resource API, cold start | 644 ms |
| resource API, warm | 4 ms |

So the exchange costs ~1.7 s once per user per hour, and nothing thereafter. Worth
stating in the README so the first call's latency is not mistaken for a fault.

## Still open

Nothing blocking. The remaining unknown is cosmetic: whether
`mcp.gatewayRequest.context` is populated under `CUSTOM_JWT` inbound (it was null
under `NONE`). If it is, the interceptor can read the verified `sub` from the
gateway instead of decoding a token itself.
