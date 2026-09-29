# Okta Cross App Access delegation chain + Amazon Bedrock AgentCore

A working sample where an AI agent reaches another application's API **on behalf of a
signed-in user**, and the **gateway** — not the agent — performs the Okta
[Cross App Access](https://www.okta.com/solutions/cross-app-access/) exchange
(Identity Assertion JWT Authorization Grant, **ID-JAG**,
[draft-ietf-oauth-identity-assertion-authz-grant](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-identity-assertion-authz-grant)).

- **Requesting app** — a [Strands](https://strandsagents.com) agent on **AgentCore
  Runtime**, behind an inbound JWT authorizer. It calls an **AgentCore Gateway** over
  MCP and never holds a credential that can reach the API.
- **Exchange point** — a **gateway REQUEST interceptor** runs both ID-JAG legs and
  injects the resulting token. The AI Agent's signing key lives only here.
- **Resource app** — a FastAPI "todo" API fronted by an Okta **custom authorization
  server**. It only *validates*.
- **IdP** — your Okta tenant with **Cross App Access / AI Agents** enabled.

Two exchanges, deliberately different: a **standard OBO** exchange gets the agent from
the user's token to a gateway-scoped token, then **ID-JAG** crosses into the resource's
authorization server.

```mermaid
flowchart LR
    U(["👤 User"])
    subgraph AWS["AWS · Amazon Bedrock AgentCore"]
        AG["Strands agent<br/>on Runtime"]
        GW["Gateway + interceptor<br/><i>runs the ID-JAG legs</i>"]
        API["Todo API"]
    end
    OKTA["🔐 Okta<br/>Cross App Access"]

    U -- "1 · sign in (OIDC + PKCE)" --> OKTA
    U -- "2 · ask, with the user's token" --> AG
    AG -- "3 · OBO exchange" --> OKTA
    AG -- "4 · MCP tools/call" --> GW
    GW -- "5 · ID-JAG, as the AI Agent" --> OKTA
    GW -- "6 · call as the user" --> API

    classDef okta fill:#eef,stroke:#66f
    class OKTA okta
```

*The API receives a token whose `sub` is the **human** and whose `act.sub` is the
**agent** — no static API keys, and no tool credential inside the agent.*

## How it works

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant BFF as BFF
    participant AS1 as Okta AS 1<br/>api://agentcore
    participant RT as Runtime agent
    participant ID as AgentCore Identity
    participant GW as Gateway<br/>CUSTOM_JWT + Cedar
    participant EI as REQUEST interceptor
    participant ORG as Okta ORG server
    participant AS2 as Okta AS 2<br/>api://todo
    participant API as Todo API

    Note over U,AS1: A · sign-in
    U->>BFF: GET /
    BFF->>AS1: authorization code + PKCE<br/>client_assertion = AI Agent key
    AS1-->>BFF: T_id + T_user
    BFF-->>U: session cookie — tokens stay server-side

    Note over BFF,RT: B · invoke
    U->>BFF: "what is on my todo list?"
    BFF->>RT: Bearer T_user, payload carries id_token
    RT->>RT: CUSTOM_JWT validates aud + scp=agent.access

    Note over RT,GW: C · OBO — the platform's native exchange
    RT->>ID: GetWorkloadAccessTokenForJWT(T_user)
    ID-->>RT: workload access token
    RT->>ID: GetResourceOauth2Token(ON_BEHALF_OF_TOKEN_EXCHANGE,<br/>scopes=tools.access)
    ID->>AS1: RFC 8693 exchange as the Agent app
    AS1-->>ID: T_gateway
    ID-->>RT: T_gateway
    RT->>GW: MCP tools/call<br/>Authorization: Bearer T_gateway<br/>X-Okta-Id-Token: T_id

    Note over GW,API: D · Cross App Access, outside the agent
    GW->>GW: CUSTOM_JWT validates T_gateway (scp=tools.access)
    GW->>EI: intercept (passRequestHeaders) — BEFORE Cedar
    EI->>ORG: leg 1 · token-exchange → ID-JAG
    ORG-->>EI: ID-JAG (aud=AS 2, 299 s, single use)
    EI->>AS2: leg 2 · jwt-bearer, assertion=ID-JAG
    AS2-->>EI: T_tool (sub=user, act=agent)
    EI-->>GW: Authorization = Bearer T_tool
    GW->>GW: Cedar ENFORCE — principal is AgentCore::OAuthUser
    GW->>API: Bearer T_tool
    API->>AS2: JWKS (cached)
    API-->>GW: the user's todos
    GW-->>RT: MCP result
    RT-->>BFF: answer
    BFF-->>U: answer
```

### The two ID-JAG legs in detail

```mermaid
flowchart LR
    TID["<b>T_id</b><br/>ID token<br/>aud = the wlp… app"]
    JAG["<b>ID-JAG</b><br/><i>a grant, not a token</i><br/>aud = AS 2<br/><b>299 s · single use</b>"]
    TOOL["<b>T_tool</b><br/>access token<br/>aud = api://todo<br/>sub = user · act = agent"]
    API(["Todo API<br/>validates AS 2 JWKS"])

    TID -->|"<b>leg 1</b> · ORG server<br/>grant_type = token-exchange<br/>requested_token_type = id-jag<br/>authorised by <b>User access</b>"| JAG
    JAG -->|"<b>leg 2</b> · RESOURCE AS<br/>grant_type = jwt-bearer<br/>assertion = the ID-JAG<br/>authorised by <b>Resource connection</b>"| TOOL
    TOOL --> API

    classDef grant fill:#fef9c3,stroke:#ca8a04
    classDef token fill:#dcfce7,stroke:#16a34a
    class JAG grant
    class TID,TOOL token
```

The IdP knows *who the user is*; the resource's authorization server owns *that API's*
audience, scopes and signing keys. Neither can do the other's job, so leg 1 produces a
portable **attestation** and leg 2 redeems it for a real token — leaving the resource
owner a veto. The AI Agent's `private_key_jwt` authenticates **both** legs.

A single exchange suffices only when one server both knows the user and owns the
resource, which is exactly why hop **C** needs just one call.

### Five identities to keep straight

| Identity | `.env` | What it is |
| --- | --- | --- |
| **AI Agent + linked app** | `AI_AGENT_CLIENT_ID` = `LOGIN_CLIENT_ID` (`wlp…`) | One client, three jobs: the user **signs in** to it, and it authenticates **both ID-JAG legs**. Okta's *User access* binding makes it the only app whose ID token leg 1 accepts. |
| **Agent app** | `AGENT_APP_CLIENT_ID` (`0oa…`) | API Services app with the Token Exchange grant, used by AgentCore Identity for the OBO exchange. Holds a secret the agent never sees. |
| **AS 1** | `AGENTCORE_AS_ISSUER`, `api://agentcore` | Issues `T_id`/`T_user` at sign-in and `T_gateway` via OBO. Runtime and Gateway both trust it. |
| **AS 2** | `RESOURCE_AS_ISSUER`, `api://todo` | Redeems the ID-JAG and mints `T_tool`. Separate on purpose: the API trusts **only** this issuer, which is what makes the agent's own tokens useless against it. |
| **Org server** | `OKTA_ORG_URL` | Mints the ID-JAG. Only the org server can. |

## Tokens: what each one is, and where it travels

Five credentials appear in this sample and they are not interchangeable. Most of the
confusion around Cross App Access comes from treating "a token" as one thing, so this
section names each one, says who mints it, who may hold it, and what it proves.

### Three different kinds of artefact

| Kind | Answers | Presented to | In this sample |
| --- | --- | --- | --- |
| **Identity token** (OIDC ID token) | *who signed in* | a token endpoint, as the **subject of an exchange** | `T_id` |
| **Access token** (OAuth 2.0) | *what the bearer may do* | a **resource**, as `Authorization` | `T_user`, `T_gateway`, `T_tool` |
| **Authorization grant** | *that an exchange is permitted* | a token endpoint, to be **redeemed** | the ID-JAG |

An ID token is not a credential for calling an API — its audience is a *client*, not a
resource. An ID-JAG is not an access token either: it is a signed statement that an
exchange may happen, audience-locked to one authorization server, single use, 299
seconds. Only the three access tokens can appear in an `Authorization` header, and only
one of them opens the todo API.

### The five tokens

| Token | Kind | Minted by | `aud` | `sub` | `scp` | TTL | Held by |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `T_id` | ID token | AS 1 at sign-in | the `wlp…` app | user **id** | — | 1 h | BFF → agent → interceptor |
| `T_user` | access | AS 1 at sign-in | `api://agentcore` | **email** | `agent.access` | 1 h | BFF, Runtime |
| `T_gateway` | access | AS 1 via **OBO** | `api://agentcore` | email | `tools.access` | 1 h | agent, Gateway |
| **ID-JAG** | grant | **org server**, leg 1 | AS 2 | user id | `todos.read` | **299 s**, single use | interceptor only |
| **`T_tool`** | access | **AS 2**, leg 2 | **`api://todo`** | **email** | `todos.read` | 1 h | interceptor → API |

`T_tool` additionally carries `cid` and **`act.sub`** naming the AI Agent, plus
`sub_profile = ai_agent web_app`. That is the delegation trail: the API sees *who* the
user is **and** *which agent* acted for them, and can require both.

### Which token is on the wire at each hop

| Hop | On the wire | Validated by | What the receiver learns |
| --- | --- | --- | --- |
| Browser → BFF | *nothing* — a signed session cookie | the BFF | which session this is; tokens never leave the server |
| BFF → Runtime | `Authorization: T_user`, plus `T_id` **in the payload** | Runtime `CUSTOM_JWT`: `aud`, `scp=agent.access` | a real user asked, through a client we trust |
| Runtime → AgentCore Identity | `T_user` as the exchange subject | Okta, as the Agent app | this user consents to the agent acting |
| Agent → Gateway | `Authorization: T_gateway` + `X-Okta-Id-Token: T_id` | Gateway `CUSTOM_JWT`: `aud`, `scp=tools.access`; then **Cedar** on the claims | the agent is acting, for this specific user |
| Interceptor → org server | `T_id` as `subject_token` | Okta, via the **User access** binding | this app may act for this user |
| Interceptor → AS 2 | the ID-JAG as `assertion` | Okta, via the **Resource connection** | this agent may reach this resource |
| Gateway → API | `Authorization: T_tool` | the API: `iss`, `aud`, `scp`, then `sub` | the human, and the agent that acted |

Note what is *not* on any wire: the agent never receives `T_tool`, and no token is ever
placed in the model's prompt.

### Why the agent holding two tokens is safe

The agent handles `T_user` and `T_gateway`. Neither can call the todo API, because the
API trusts **only** AS 2 as issuer and requires `aud=api://todo`, and both of those
tokens come from AS 1 with `aud=api://agentcore`. A fully compromised agent can
therefore do what it was already authorised to do — call the tools Cedar permits, as the
user it was already acting for — and no more.

The credential that *does* open the API, `T_tool`, exists only inside the interceptor and
on the gateway's forwarded request. This is what makes the failure mode
`wrong issuer: this API only trusts …` a **feature** of the sample: try replaying an
agent-held token against the API and it is refused by design.

### Using the wrong token, and the error you get

Each of these was observed while building the sample, which is why they are listed with
their exact text rather than paraphrased.

| Mistake | Error |
| --- | --- |
| access token as ID-JAG leg 1 subject | `'subject_token' is invalid: no delegation policy authorizes this token` (System Log: `invalid_subject_token_no_delegation_link`) |
| replaying `T_user` at the gateway | `insufficient_scope` — it carries `agent.access`, the gateway requires `tools.access` |
| `T_user` or `T_gateway` sent to the API | `403 wrong issuer: this API only trusts <AS 2>` |
| ID token sent to the API | `403 wrong audience` — its `aud` is a client, not a resource |
| reusing an ID-JAG | `invalid_grant: id-jag already used` — single use, so mint one per exchange |
| caching the ID-JAG instead of `T_tool` | works for 299 s then fails; cache the access token, never the grant |
| assuming one `sub` format | `T_id`/ID-JAG carry the Okta **user id**; access tokens carry the **email** |


## Repository layout

```
08-okta-xaa-delegation-chain/
├─ resource-app/          Todo API (FastAPI) — validates the AS 2 token, issues nothing
│  ├─ main.py             /todos, /whoami; checks iss, aud, scp, and optionally act.sub
│  ├─ lambda_handler.py   Mangum wrapper for Lambda
│  └─ requirements.txt · .env.example
├─ interceptors/
│  ├─ request_interceptor.py   both ID-JAG legs; injects Authorization; caches T_tool
│  └─ requirements.txt
├─ agent/                 Strands agent for AgentCore Runtime
│  ├─ agent.py            OBO exchange, then MCP to the gateway
│  └─ requirements.txt
├─ frontend/              FastAPI BFF: Okta sign-in, /ask
├─ gateway/todo-tools.json    OpenAPI for the todo target
├─ policies/*.cedar       per-user, per-tool authorization
├─ deploy/                numbered, idempotent; each writes state back to .env
└─ scripts/               keypair, verification, the chain test, tracing
```

## Prerequisites

- Python 3.11+, Node 20+ and `npm install -g @aws/agentcore aws-cdk`
- AWS credentials, a bootstrapped account, and Bedrock model access
- An Okta org with **API Access Management** *and* **Cross App Access / AI Agents**
  (often listed as *Agent to Agent Connections* under Settings → Features), plus a
  **Single Sign-On** subscription
- An Okta admin API token, and a test user you can sign in as

> **ID-JAG quota.** Under plain SSO, Okta allows **250 ID-JAGs per user, per resource
> app, per month**. The interceptor caches `T_tool` for its full hour and only
> exchanges on `tools/call`, so normal use is nowhere near the cap.

## Set up a virtual environment

```bash
cd 08-okta-xaa-delegation-chain
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.env .env      # then set OKTA_ORG_URL and OKTA_API_TOKEN
```

## Okta setup

```bash
python deploy/00_create_okta_apps.py    # two authorization servers, scopes, apps, policies
python scripts/gen_keypair.py           # the AI Agent's private_key_jwt keypair
```

Then register the **AI Agent** in the Admin Console — the one step with no API — and
run the two follow-ups. Full walkthrough with every field and its failure mode:
**[IDP_SETUP_OKTA.md](IDP_SETUP_OKTA.md)**.

```bash
python deploy/00_relink_login_app.py    # sign-in moves to the agent's linked app
python deploy/00_authorize_agent.py     # authorise the agent on the resource AS
python scripts/verify_ai_agent.py       # confirm everything the API can see
```

## Deploy

```bash
python deploy/01_deploy_resource.py                     # todo API → Lambda + HTTP API
python deploy/02_create_gateway.py --allow-user-scope    # gateway, interceptor, policy engine, target
python deploy/03_create_policies.py                      # Cedar
python deploy/04_create_obo_provider.py                  # AgentCore Identity provider for hop C
```

### Milestone: prove hop D without the agent

```bash
python scripts/test_chain.py
```

Signs you in and calls the gateway directly with the two headers the agent will send.
A pass means the interceptor, both ID-JAG legs, the injection, Cedar and the API all
work. `whoami` should return your email as `user` and the AI Agent as `acting_agent`.

### Deploy the agent and the BFF

```bash
agentcore create --project-name xaatodoagent --name xaatodoagent \
  --framework Strands --model-provider Bedrock --memory none \
  --build CodeZip --language Python --defaults
cp agent/agent.py xaatodoagent/app/xaatodoagent/main.py
python deploy/05_patch_agentcore_json.py     # Okta inbound auth + env vars
( cd xaatodoagent && agentcore validate && agentcore deploy -y )
python deploy/06_grant_iam.py                # OBO permissions for the execution role
python deploy/07_enable_observability.py     # transaction search + log retention
python deploy/02_create_gateway.py           # re-run WITHOUT --allow-user-scope to tighten
python frontend/app.py                       # http://localhost:8000
```

Ask *"what is on my todo list?"* and you should get your own items.

## Sample prompts

Ask these at <http://localhost:8000> once signed in. The target exposes four tools, and
the model picks which one answers the question.

| Prompt | Exercises |
| :--- | :--- |
| `What is on my todo list?` | `list_todos` — the default, and the lightest read |
| `Who am I according to the todo API?` | `whoami` — names **you** and the **agent that acted for you** |
| `Add "buy milk" to my list.` | `add_todo` — a write |
| `Mark item 1 as done.` | `complete_todo` — a write on a specific row |

`Who am I according to the todo API?` is the one to demo. It returns your email as the
user and the AI Agent as `acting_agent`, from a token minted by Okta for
`api://todo` — proving the identity survived three hops while the agent never held that
credential.

### Proving Cedar allows *and* denies

A policy that only ever permits proves very little. To see enforcement, name a user in
`.env` and redeploy the policies:

```bash
# the EMAIL as it appears in the access token's `sub`
echo 'CEDAR_READONLY_USER=you@example.com' >> .env
python deploy/03_create_policies.py --replace
```

That deploys [`policies/forbid_writes_for_readers.cedar`](policies/forbid_writes_for_readers.cedar).
Then, signed in as that user:

| Prompt | Expected | Why |
| :--- | :--- | :--- |
| `What is on my todo list?` | **works** | `allow_reads` permits the read tools |
| `Add "buy milk" to my list.` | **denied** | `forbid` beats `permit` in Cedar, so the write tools are refused for this `sub` |
| `Mark item 1 as done.` | **denied** | same policy, other write tool |

Reads continuing to work while writes fail is the proof that the policy engine is
reading **your** identity out of the inbound token — at the same time as the interceptor
is swapping the credential on its way to the API. Remove `CEDAR_READONLY_USER` and
re-run with `--replace` to restore writes.

Inspect what is deployed at any point with:

```bash
python deploy/03_create_policies.py --list
```

## Does ID-JAG take an access token or an ID token?

Asked often enough to deserve its own heading: **an ID token. An access token is
refused.**

| `subject_token` at leg 1 | Result |
| --- | --- |
| the **ID token** from the agent's linked app | ✅ ID-JAG minted |
| an **access token** for the same user | ❌ `400 invalid_request: 'subject_token' is invalid: no delegation policy authorizes this token` |

Okta records the failure in its System Log as
`invalid_subject_token_no_delegation_link`.

The reason is Okta's **User access** binding: it authorises *the ID token issued by the
app bound to the AI Agent*, and nothing else. An access token — even a valid one, for the
same user, from the same authorization server — carries no delegation link, so leg 1 has
nothing to act on. This is not a quirk of configuration; it is what the binding means.

Two consequences for anything built this way:

- **Whoever signs the user in must pass the ID token through.** The OBO exchange at hop C
  yields an *access* token, so the chain cannot derive an ID token from what the agent
  holds. The BFF is the only component that has one, and this sample's agent fails with
  an explicit message if it is missing.
- **It travels as a connection header** (`X-Okta-Id-Token`), never in the tool arguments,
  because those are composed by the model.

See [Tokens: what each one is, and where it travels](#tokens-what-each-one-is-and-where-it-travels)
for the full inventory, including which token is on the wire at every hop and the error
each mix-up produces.

## Tracing a request

```bash
python scripts/show_trace.py            # stitch one request across all four log groups
python scripts/show_okta_events.py      # the matching Okta System Log token grants
```

The interceptor logs one structured line per step with `trace_id`, and token **claims**
only — never token material. First call for a user costs ~1.7 s for the two legs; after
that it is a cache hit of a few milliseconds.

## Troubleshooting (error ladder)

| Symptom | Cause | Fix |
| --- | --- | --- |
| `insufficient_scope` at the gateway | **`allowedClients` does not work with Okta** — it compares a claim Okta does not emit (`cid` is not matched), and reports the mismatch as a scope error | pin on `allowedScopes` instead; `02_create_gateway.py` does |
| `No module named 'jwt'` as a 500 from a tool call | interceptor shipped without dependencies | rebuild: the bundle needs `pyjwt`+`cryptography` with `--platform manylinux2014_x86_64` |
| `Policy Evaluation Internal Failure` | something non-JWT ended up in `Authorization`; Cedar cannot build a principal | the interceptor must inject a real JWT, and must pass the request through untouched on failure |
| `'subject_token' is invalid: no delegation policy authorizes this token` | leg 1 got an **access** token, or an ID token from the wrong app | leg 1 needs the ID token from the agent's **linked** app |
| `access_denied: Policy evaluation failed` | the agent is not in the AS 2 policy | `python deploy/00_authorize_agent.py` |
| `invalid_client` on every call | the AI Agent is **STAGED**, or the key is staged not ACTIVE | Actions → Activate; check the ACTIVE badge on Public/private key |
| `invalid_grant: id-jag already used` | ID-JAGs are single-use | mint one per exchange; never cache the ID-JAG (only `T_tool`) |
| `Workload Identity does not belong to caller account` | the workload identity named in `AGENT_WORKLOAD_NAME` does not exist — the AgentCore CLI does not create it, and the message reads like a cross-account problem | `python deploy/04_create_obo_provider.py` creates it |
| `not authorized to perform GetResourceOauth2Token on resource: …/token-vault/default` | that action is authorized against **four** resources; naming only the credential provider is not enough, even though its ARN contains the vault as a prefix | `python deploy/06_grant_iam.py` lists all four |
| the agent starts but has no configuration | `agentcore.json` uses **`envVars`**, an ARRAY of `{name, value}`. An `environment` map is **silently ignored** — validate passes, deploy succeeds, the runtime comes up with no variables | `python deploy/05_patch_agentcore_json.py` writes the right shape |
| `authorizerConfiguration with customJwtAuthorizer is required` | the CLI schema spells it **`customJwtAuthorizer`**; boto3 uses `customJWTAuthorizer` | same script handles the casing |
| `404 UnknownOperationException` invoking the runtime | the invoke URL must end with `?qualifier=DEFAULT` | re-read `AGENT_RUNTIME_INVOKE_URL` from `.env` |
| `wrong issuer: this API only trusts …` | a token from AS 1 reached the API | expected — this is the check that makes the agent's tokens useless |
| Cedar denies everything | a policy names one tool action, or is still `CREATING` | `python deploy/03_create_policies.py --list` |
| Policy engine name rejected | names allow **no hyphens** (`^[A-Za-z][A-Za-z0-9_]*$`) | unlike gateway/target names, which do |
| Sign-in stalls on MFA | the linked app's policy defaults to *Any two factors* | enrol a factor or repoint its Sign On policy |

## Cleanup

```bash
python deploy/teardown.py               # preview
python deploy/teardown.py --yes         # delete AWS resources
python deploy/teardown.py --yes --include-runtime   # also the AgentCore stack
python deploy/00_delete_okta_apps.py --yes          # the Okta apps and servers
```

The **AI Agent** has no delete API — remove it in the Admin Console
(Directory → AI Agents).

## Security notes

- **The agent holds no credential that reaches the API.** It handles `T_user` and
  `T_gateway`, both audienced at `api://agentcore`; the API trusts only AS 2 and
  `api://todo`, so those tokens fail there. `T_tool` exists solely in the interceptor
  and on the gateway's forwarded request.

  The control worth enforcing is therefore *the agent never holds a token that can
  reach the resource, and no credential enters the model's context* — not "the agent
  never sees a token at all". Removing tokens from the agent entirely would mean a
  side-channel cache and an agent-supplied user reference, which adds an impersonation
  surface this design does not have. Prefer the stricter variant if the agent runs
  untrusted code, the token is long-lived or broadly scoped, or a rule forbids
  credentials transiting that component.
- **No credential enters the model's context.** Tokens live in headers and variables,
  never in the prompt, and the agent's output is buffered before being yielded.
- **The AI Agent's private key** is in Secrets Manager, read only by the interceptor.
  Treat the keypair as immutable once registered; rotate by adding a new `kid`.
- **`.env` holds plaintext secrets**, including an Okta admin token. It is gitignored
  (`.env` and `.env.*`), but treat it as a sandbox, not a pattern for production.
- **The BFF session cookie** is signed, `HttpOnly` and `SameSite=Lax`, but deliberately
  not `Secure`, because that would break `http://localhost`. Add `https_only=True`
  before serving it anywhere else.
- **Scope the resource API further** by setting `EXPECTED_ACT_SUB`, which requires the
  acting agent to be *your* agent rather than any agent Okta vouches for.
