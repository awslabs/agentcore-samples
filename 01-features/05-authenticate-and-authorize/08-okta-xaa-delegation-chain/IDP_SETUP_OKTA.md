# Okta setup

Three identities, two custom authorization servers, and one manual console step.

| What | Created by | Used for |
| :--- | :--- | :--- |
| **AS 1** `https://xaa-agentcore.example.com` | `deploy/00_create_okta_apps.py` | mints `T_user` at sign-in and `T_gateway` via the OBO exchange |
| **AS 2** `api://todo` | `deploy/00_create_okta_apps.py` | redeems the ID-JAG (leg 2) and mints `T_tool` |
| **Login app** | `deploy/00_create_okta_apps.py` | the OIDC client the user signs into |
| **Agent app** (API Services) | `deploy/00_create_okta_apps.py` | AgentCore Identity's client for the OBO exchange |
| **AI Agent** (`wlp…`) | **you, in the Admin Console** | signs both ID-JAG legs inside the interceptor |

> **Prerequisites**
> - An Okta org with **API Access Management** (custom authorization servers).
> - **Cross App Access / AI Agents** enabled — often listed as *Agent to Agent
>   Connections* under **Settings → Features**. Without it, leg 1 fails with
>   `requested_token_type is invalid` and this sample cannot work on that tenant.
> - An admin API token: **Security → API → Tokens → Create Token** (Org or Super
>   Admin). Setup-only; never used at runtime.
> - A **Single Sign-On** subscription. Cross App Access requires it.
> - A test user you can sign in as.
>
> **ID-JAG quota.** Use of XAA as part of SSO is limited to **250 ID-JAG tokens per
> user, per resource app, per month**, and one is consumed per resource access. The
> "user" must be a licensed SSO user in an Active status, and the number of users using
> XAA cannot exceed the org's purchased SSO seats. Ample for a sample; real volume needs
> the **Okta for AI Agents** subscription, which is also what surfaces the **Machine
> access** tab that this sample does not use.
>
> Limits and licensing terms change, so confirm the current ones against Okta's own
> documentation rather than this page:
> [Cross App Access (agent to app)](https://developer.okta.com/docs/guides/xaa-agent-to-app/main/)
> and [Okta rate limits](https://developer.okta.com/docs/reference/rate-limits/).

## Automated part

```bash
cp config.example.env .env
# set OKTA_ORG_URL (app-facing host, NOT -admin, no /oauth2 path) and OKTA_API_TOKEN
python deploy/00_create_okta_apps.py
```

It creates both authorization servers, the three custom scopes
(`agent.access`, `tools.access`, `todos.read`), both apps, client secrets, and one
**ACTIVE** access policy per app. Re-runs are safe — everything is looked up by
name, and `.env` is written incrementally so a mid-run failure loses nothing.

Then generate the AI Agent's keypair:

```bash
python scripts/gen_keypair.py --kid xaa-agent
```

This writes `scripts/keys/okta_private_key.pem` (mode `0600`, gitignored) and
`scripts/keys/okta_public_jwk.json`. **Treat the keypair as immutable once
registered** — re-running this after registering the public key breaks every
signature with `invalid_client: client_assertion signature is invalid`, and Okta
will not let you deactivate an agent's only key or reuse a `kid`. To rotate, add a
new public key under a **new** `kid`, repoint `AI_AGENT_KEY_KID`, then deactivate
the old one.

---

## Manual part: register the AI Agent

No Management API exists for workload principals, so this is console-only. Do it
**after** `00_create_okta_apps.py`, because the resource connection needs AS 2.

The sections below follow Okta's current
[Add an AI agent manually](https://help.okta.com/oie/en-us/content/topics/ai-agents/ai-agent-add-manually.htm)
flow. The agent's page has six tabs — **Profile**, **Owners**, **Client
registration**, **User access**, **Machine access**, **Resource connections** —
and you need four of them.

> **Looking for "Delegations"? It no longer exists.** Okta renamed it: per the
> [agent-to-app XAA guide](https://developer.okta.com/docs/guides/xaa-agent-to-app/main/),
> "the **Delegations** tab has been renamed **User access** and **Machine
> access**." Older material — including the `06-okta-xaa` sample in this repo —
> tells you to use *Delegations → Add caller*; that instruction is stale. What was
> the delegation is now the **User access** binding in step 3.
>
> If an agent you inherited shows an app under **User access** with a warning that
> it "is using an outdated method for user sign-on", relink it via *Create a new
> OIDC app linked to this agent*, or delete and re-register the agent.

### Step 1 — Profile

**Directory → AI Agents → Register AI agent → Register manually**

| Field | Value |
| :--- | :--- |
| Name | `XAA Todo Agent` |
| Description | Performs the ID-JAG exchange for the AgentCore todo sample |
| Platform | **optional** — leave blank |
| External ID | **optional** — leave blank |

`Platform` and `External ID` matter when you import agents from a third-party
builder platform, where they keep profiles unique. For a hand-built agent both can
stay empty; the Profile tab still shows a green check.

**Next**, then choose the user-access binding (see step 3), then **Next**.

### Step 2 — Owners (optional)

**Owners → Edit** → assign yourself. Okta suggests at least two owners for real
deployments; one is fine for a sample.

### Step 3 — User access — sign-in, and leg 1 in `id_token` mode

Still required: this binding is how the user signs in, and it is **permanent** — to change
it you must delete and recreate the agent.

> It is also what authorises leg 1 *if* you run the interceptor in `XAA_LEG1_SUBJECT=id_token`
> mode. On the default `access_token` path, leg 1 is authorised by **Machine access**
> (step 6) instead. Configure both and the sample works either way.

Okta offers two options:

| Option | Use it? |
| :--- | :--- |
| **Create a new OIDC app linked to this AI agent** | ✅ **this one** |
| **Select an existing app** (SAML only) | ❌ — the picker lists SAML apps, so you cannot bind the OIDC app the deploy script made |

Okta then creates an OIDC app with the same name as the agent (`XAA Todo Agent`)
and links it. Per Okta: *the agent can act for a user only while that user is
signed in to the bound app.* That binding **is** the leg-1 delegation — the ID
token the agent exchanges must come from this app.

> **Consequence for this sample.** The user signs in to the agent's **linked app**,
> not to the separate `XAA Todo Login` app that `00_create_okta_apps.py` creates.
> After this step, run:
>
> ```bash
> python deploy/00_relink_login_app.py
> ```
>
> which repoints `LOGIN_CLIENT_ID` / `LOGIN_CLIENT_SECRET` at the linked app and
> moves the AS 1 sign-in policy onto it. `XAA Todo Login` is then unused and can be
> deleted.

Two things to finish on this tab, both reached by the links Okta shows there:

1. **Assign your test user.** The tab shows *Users and groups assigned to this
   agent*; if it reads **⚠ None assigned**, click **Application > Assignments** and
   assign your user. Okta issues no token to an unassigned user.
2. **Check the authentication requirement.** It often defaults to *Any two
   factors*. Either enrol a factor for your test user, or open **Application >
   Sign On** and relax the policy for the demo. Otherwise sign-in stalls at an MFA
   prompt the BFF cannot complete.

### Step 4 — Client registration — the signing key

The tab is headed **Okta-generated client ID** and offers three methods, of which
**only one is active at a time** (staged ones are kept, so you can switch later):

| Method | Okta's label | Use |
| :--- | :--- | :--- |
| Client secret | — | a shared secret for confidential server-side agents |
| **Public/private key** | *(Most secure)* | **use this** — a builder-managed key pair |
| Client ID only | *(Least secure)* | public clients that cannot store a secret |

**Client registration → Configure** beside **Public/private key**:

1. **Add public key** → paste the whole contents of
   `scripts/keys/okta_public_jwk.json`, braces included.
2. **Done**, then copy the **Client ID** shown on this tab.
3. Click **Activate**, then **Enable** in the dialog. Staging a method is not the
   same as activating it — only one method is live at a time, and an inactive one
   is ignored. When it worked, the **Public/private key** row shows a green
   **ACTIVE** badge.

Put the copied Client ID in `.env`:

```bash
AI_AGENT_CLIENT_ID=<the Client ID from this tab>
```

This value is both the OAuth `client_id` and the `iss`/`sub` of the client
assertion the interceptor signs, in **both** legs.

### Step 5 — Resource connections — *this is what authorises ID-JAG leg 2*

**Directory → AI Agents →** your agent **→ Resource connections → Add resource
connection**.

**Select a resource type → `Authorization server`** — the first radio button.
Okta describes it as: *"Select a custom authorization server. This allows an agent
to gain access to resources protected by a custom authorization server in your
tenant."* That is precisely this sample's resource.

| Field | Value |
| :--- | :--- |
| Resource type | **Authorization server** |
| Authorization server | **XAA Todo Resource** (`api://todo`) |
| Scopes | `todos.read` |

Then **Add**. Without this, leg 2 fails at the resource.

Afterwards the **Resource connections** table should read:

| Connected resource | Detail | Connection status |
| :--- | :--- | :--- |
| Authorization server — **XAA Todo Resource**, Audience: `api://todo` | `Only allow` · `todos.read` | **ACTIVE** |

`Only allow` is the scope mode. The alternatives are *Allow all* and *Disallow*;
`Only allow` with an explicit scope list is the least-privilege choice.

> **Do not pick `Application`.** Its *Application instance* dropdown lists only apps
> that already have a resource server configured for AI Agent access, so on a
> tenant without one it shows **No options** — which looks like a missing step but
> is just the wrong branch. Okta's
> [developer guide](https://developer.okta.com/docs/guides/xaa-agent-to-app/main/)
> documents only the `Application` path; the console offers more.

The full set of resource types, for orientation:

| Resource type | What it is for |
| :--- | :--- |
| **Authorization server** | a custom AS in your tenant — **use this** |
| Secret / Service account | stored credentials in Okta Privileged Access |
| Application | an app with a resource server configured for AI Agent access, or a *Custom resource server* for APIs not yet in Okta |
| MCP server | an MCP server the agent may call |
| Connect to another AI agent | a bilateral agent-to-agent connection |

### Step 6 — Machine access — *this is what lets leg 1 take an **access** token*

This is the step that removes a whole piece of plumbing, and it is easy to skip because
the tab's own description points the other way.

**What the UI says:** *"Callers access this agent as a resource with their own access
token. No user identity is required."* That sounds like inbound traffic — something calling
*into* the agent — which is the opposite of leg 1, where the agent reaches *out* carrying a
user's identity.

**What it actually does:** Machine access holds the **non-user delegation links** that used
to live on the old *Delegations* tab. Okta's own
[XAA guide](https://developer.okta.com/docs/guides/xaa-agent-to-app/main/) says so: *"You
can still have multiple non-user delegation links, which now appear in the Machine access
tab."* A delegation link is exactly what leg 1 looks for. Without one, an access token is
refused with:

```
'subject_token' is invalid: no delegation policy authorizes this token.
```

Configure it and leg 1 accepts an `access_token`, so the interceptor can exchange the
bearer the gateway **already validated** — no second token has to travel with the request.
Skip it and you must run the interceptor in `XAA_LEG1_SUBJECT=id_token` mode and forward an
ID token from the BFF through the runtime.

#### 6a. The audience constraint — read before you click

**Machine access requires an `https://` audience, and an Okta custom authorization server
accepts exactly one audience.** Adding a second returns:

```
audiences: 'audiences' must be an array with exactly one value.
```

So AS 1 cannot keep `api://agentcore` *and* gain an https audience — the https URL has to
*be* its audience. That is why this sample's `AGENTCORE_AUDIENCE` is
`https://xaa-agentcore.example.com` rather than `api://agentcore`.

> **If you are upgrading an existing deployment**, changing the audience touches four
> places: AS 1, the gateway authorizer, the runtime authorizer, and the OBO provider's
> requested audience. Set `AGENTCORE_AUDIENCE` in `.env`, then re-run
> `00_create_okta_apps.py`, `02_create_gateway.py`, `05_patch_agentcore_json.py` and
> redeploy the runtime. A mismatch surfaces as an opaque 403 at whichever authorizer
> disagrees.
>
> `example.com` is reserved by RFC 2606, so it can never collide with a real endpoint. The
> value is only an identifier — Okta never fetches it.

#### 6b. Configure the custom authorization server

**Machine access → Configure**:

| Field | Value | Why |
| :--- | :--- | :--- |
| Authorization server | **XAA AgentCore** (AS 1) | the server that issues the token the interceptor will exchange |
| Audience/resource URL | `https://xaa-agentcore.example.com` | must equal that token's `aud`, character for character |

> **The audience cannot be changed after saving.** The authorization server can, but only
> after removing every caller. Get the audience right the first time: mint a token and
> check its `aud` with `scripts/show_token_claims.py` before you save.

#### 6c. Add caller

**Add caller** offers two kinds. Choose **Application or service**, and select the
**Agent app** (`XAA Todo Agent App`, the `AGENT_APP_CLIENT_ID`).

That is the `cid` of `T_gateway` — the token AgentCore Identity mints at the OBO hop, and
the one the interceptor receives. The other option, *AI agent*, is for *another* agent, so
you cannot select this agent: **an agent cannot be its own caller.** That rules out using
`T_user`, whose `cid` is the agent itself — it fails leg 1 with `no delegation policy
authorizes this token` no matter what you configure.

The resulting row reads: caller `XAA Todo Agent App`, on behalf of **Application
(service)**, on the custom AS.

#### 6d. Assign the user to the caller app — the trap

Leg 1 will still fail, with a *different* message:

```
'subject_token' is invalid: the user is not assigned to the client application.
```

The **Agent app needs the user assigned to it**, not just the sign-in app. `00_create_okta_apps.py`
creates that app with no assignments, because until now nothing needed them.

```bash
# Applications -> XAA Todo Agent App -> Assignments -> Assign to People
# or via the API:
#   POST /api/v1/apps/<AGENT_APP_CLIENT_ID>/users  {"id": "<userId>", "scope": "USER"}
```

> Okta may return `200` on that assignment while the app still reports zero assigned
> users. Do not trust either signal — run `scripts/test_chain.py`; a successful leg 1 is
> the only proof that matters.

#### 6e. What success looks like

The ID-JAG comes back with a **nested** `act` chain, which the ID-token path does not
produce:

```json
"sub": "00u1…",                                  // the human
"act": { "sub": "wlp1…",                         // the AI Agent
         "sub_profile": "ai_agent web_app",
         "act": { "sub": "0oa1…",                // the Agent app
                  "sub_profile": "service" } }
```

Read outwards: the Agent app, acting as the AI Agent, acting for the user. That chain
carries through to `T_tool`, so the resource API can see every hop.

#### Which mode should you run?

| | `access_token` (default) | `id_token` |
| :--- | :--- | :--- |
| Okta requirement | **Machine access** + user assigned to the caller app | **User access** binding only |
| Audience | must be `https://…` | any, including `api://…` |
| Tokens on the wire to the gateway | one | two (`Authorization` + `X-Okta-Id-Token`) |
| BFF must forward an ID token | no | yes |
| `act` chain | nested, records the Agent app too | single level |
| Set | nothing (default) | `XAA_LEG1_SUBJECT=id_token`, `SEND_ID_TOKEN=true` |

Both are verified end to end in this sample. Prefer `access_token`: fewer credentials in
flight, and the agent never handles an ID token at all.

### Step 7 — Activate

**Actions → Activate → Confirm.** The header's **Managed status** should stop
reading `STAGED`.

> A staged agent fails *every* call with a bare `invalid_client`, which looks
> exactly like a key problem and is not.

### Step 8 — Authorize the agent on the resource server

Put the agent's Client ID in `.env`, then run:

```bash
python deploy/00_authorize_agent.py
```

`00_create_okta_apps.py` had to create the AS 2 `jwt-bearer` policy **before** the
AI Agent existed, and Okta requires a policy to name at least one client — so it is
created pointing at the Agent app as a placeholder. That placeholder is wrong twice
over: the Agent app exchanges at AS 1 and has no business at the resource server,
and the client that actually redeems the ID-JAG is the agent's `wlp…`. Left unfixed,
leg 2 fails with `access_denied: Policy evaluation failed`.

The script repoints the policy at the agent (replacing the placeholder, not adding
to it) and reports the rule's grants and scopes. It is idempotent.

To do it by hand instead: **Security → API → Authorization Servers → XAA Todo
Resource → Access Policies → XAA sample - Resource jwt-bearer → Edit** and set
*Assigned to clients* to the agent's Client ID.

> This is the policy evaluated at leg 2, because the resource connection in step 5
> targets the custom authorization server directly.

---

## Verify before moving on

```bash
.venv/bin/python scripts/verify_ai_agent.py
```

Use the venv interpreter: the key-pair check needs `cryptography`, and on a system
python without it the check is skipped with a warning rather than failing loudly.

It checks over the Management API that the client exists, is **ACTIVE**, carries a
key whose `kid` matches `.env`, that your local private key is genuinely the pair
of the registered public key, and that the AS 2 policy lists it.

**User access** and **Resource connections** are not exposed to the Management API,
so steps 3 and 5 are only provable by running the flow, which exercises both ID-JAG
legs:

```bash
.venv/bin/python scripts/test_chain.py
```

## Troubleshooting

| Symptom | Cause | Fix |
| :--- | :--- | :--- |
| No **AI Agents** item under Directory | Cross App Access not enabled on the tenant | Settings → Features → *Agent to Agent Connections* |
| `requested_token_type is invalid` (leg 1) | same | as above |
| `invalid_client` on every call, key is correct | agent is **STAGED**, or the key is registered but not **ACTIVE** | Actions → Activate (step 7); confirm the ACTIVE badge on Public/private key (step 4) |
| `invalid_client: client_assertion signature is invalid` | the local private key is not the pair of the registered public key | re-register the current `okta_public_jwk.json`; never re-run `gen_keypair.py` after registering |
| `'subject_token' is invalid: … not registered for delegation` (leg 1) | the ID token did not come from the app bound under **User access** | sign in through the agent's linked app and run `deploy/00_relink_login_app.py` (step 3) |
| Cannot find a **Delegations** tab | renamed by Okta to **User access** / **Machine access** | use **User access** (step 3); older docs including `06-okta-xaa` are stale |
| The agent shows "outdated method for user sign-on" | a legacy delegation link | relink via *Create a new OIDC app linked to this agent*, or re-register the agent |
| **Application instance** shows **No options** | wrong resource type chosen — that list only holds apps with an AI-Agent resource server | pick resource type **Authorization server** instead (step 5) |
| Sign-in stalls on an MFA prompt | the linked app's Sign On policy defaults to *Any two factors* | enrol a factor, or relax the policy (step 3) |
| `429` / quota errors on leg 1 | the 250 ID-JAG per user/resource/month SSO cap | wait for the reset, use another user, or subscribe to Okta for AI Agents |
| `access_denied: Policy evaluation failed` (leg 2) | the AS 2 policy still names the placeholder client, or the Resource connection is missing | run `deploy/00_authorize_agent.py` (step 8); check step 5 |
| `invalid_grant: id-jag already used` | ID-JAGs are single-use | mint a fresh one per attempt; run the legs back to back |
| `User is not assigned to the client application` | test user not assigned | step 8 |
| `E0000011 Invalid token provided` on the deploy scripts | `OKTA_API_TOKEN` expired (Okta expires tokens after 30 days of inactivity) | mint a new one, update `.env` |
| `404 … (AppInstance)` when assigning a user | the linked app is still inactive | activate the agent first (step 7), then assign |
| `verify_ai_agent.py` skips the key-pair check | run with a python lacking `cryptography` | use `.venv/bin/python` |
