# OBO Python Examples

End-to-end working examples of OAuth 2.0 On-Behalf-Of (OBO) flows using AgentCore Identity, in Python.

Each use case has its own folder and is available in **two flavors** — one per identity provider:

- **`entra/`** — uses Microsoft Entra ID (RFC 7523 JWT bearer grant)
- **`okta/`** — uses Okta (RFC 8693 token exchange grant)

## Use cases

| # | Scenario | Entra | Okta |
|---|---|---|---|
| 1 | User → Frontend → Agent on Runtime → downstream (1 OBO hop) | [local](./01-agent-to-downstream/entra/local/) · [real-world](./01-agent-to-downstream/entra/real-world/) | [local](./01-agent-to-downstream/okta/local/) · [real-world](./01-agent-to-downstream/okta/real-world/) |
| 2 | User → Frontend → Agent → AgentCore Gateway → downstream (2 OBO hops) | [real-world](./02-agent-via-gateway/entra/real-world/) | [real-world](./02-agent-via-gateway/okta/real-world/) |

## Shared prerequisites

All examples expect:

- **Python 3.10+**
- **AWS credentials** configured locally with access to the target AWS account (env vars, `~/.aws/credentials`, or `AWS_PROFILE`).
- **Boto3** with AgentCore Identity + Gateway support: `boto3 >= 1.43.2`.
- **IdP setup** — app registrations, scopes, access policies. Each example folder has its own `IDP_SETUP.md` with the specific steps.
- **Region** — examples default to `us-west-2` but accept `AWS_REGION` env var.

Additional prerequisites for the `real-world/` mode (all use cases with `real-world/`):

- **Node.js 20+**, **AWS CDK 2.1129.0+**, **AgentCore CLI (`@aws/agentcore`) 0.21.1+**
- **Bedrock model access** in the target region (Claude Sonnet 4.5 by default)

## How the examples are structured

Each use case is available in one or two modes:

### `local/` — interactive single-machine walkthrough

Available for UC1 only.

```
<use-case>/<flavor>/local/
├── README.md
├── IDP_SETUP.md
├── config.example.env
├── requirements.txt
├── 01_create_providers.py    ← create AgentCore Identity resources
├── 02_run_example.py         ← end-to-end OBO flow in one process
└── callback_server.py        ← local OAuth callback receiver
```

To run: `cp config.example.env .env`, fill in your IdP values, `pip install -r requirements.txt`, then run `python 01_create_providers.py` once, then `python 02_run_example.py`.

### `real-world/` — production-shaped deployment

Available for UC1 and UC2. UC2 has **only** this mode — there is no meaningful way to simulate the Gateway hop locally.

```
<use-case>/<flavor>/real-world/
├── README.md                   ← 12–14 step quick start
├── ARCHITECTURE.md             ← design rationale + request lifecycle + tokens decoded
├── IDP_SETUP.md                ← IdP app registrations, scopes, policies (auto + manual paths)
├── LEARNING_GUIDE.md           ← 6-chapter hands-on tour, run after stack is up
├── config.example.env
├── requirements.txt
├── agent/
│   ├── agent.py                ← Strands agent (deploys to AgentCore Runtime)
│   ├── requirements.txt
│   └── README.md
├── frontend/
│   ├── app.py                  ← FastAPI BFF (MSAL for Entra, authlib for Okta)
│   ├── templates/{home,result}.html
│   └── README.md
├── gateway/                    ← UC2 only: Gateway target OpenAPI spec
│   ├── *_openapi.json
│   └── README.md
└── deploy/                     ← automation scripts around the AgentCore CLI
    ├── 00_create_{entra,okta}_apps.py    ← IdP automation
    ├── 00_delete_{entra,okta}_apps.py    ← IdP teardown
    ├── 01_create_providers.py            ← workload + credential providers
    ├── 02_create_gateway.py              ← UC2 only: Gateway + target + service role
    ├── 03_patch_agentcore_json.py        ← inbound JWT + env vars
    ├── 04_grant_agent_iam_permissions.py ← OBO IAM policy on runtime role
    ├── 05_enable_observability.py        ← log retention + CloudWatch pointers
    ├── show_obo_trace.py                 ← per-invocation OBO chain view
    ├── compare_obo_claims.py             ← decode T_user vs T_gateway vs T_downstream
    └── teardown.py                       ← AWS resource cleanup with verify_all_gone
```

To run: follow the example's `README.md` step-by-step. Involves the AgentCore CLI, CDK, and either Azure CLI (Entra) or an Okta admin API token for the automated IdP setup path.

## Current status

- [x] `01-agent-to-downstream/` — foundational case (1 OBO hop)
  - [x] `entra/local/` — single-script interactive walkthrough
  - [x] `entra/real-world/` — FastAPI BFF + Strands agent on Runtime (calls Microsoft Graph)
  - [x] `okta/local/` — single-script interactive walkthrough
  - [x] `okta/real-world/` — FastAPI BFF + Strands agent on Runtime (calls Okta `/v1/userinfo`)
- [x] `02-agent-via-gateway/` — two OBO hops (agent + Gateway)
  - [x] `entra/real-world/` — agent → Gateway → Microsoft Graph `/me` (RFC 7523 JWT-bearer)
  - [x] `okta/real-world/` — agent → Gateway → mock downstream via httpbin.org (RFC 8693 Token Exchange)
  - No `local/` variant — Gateway is a deployed AWS resource

## How the two flavors compare (short version)

| Aspect | Entra ID | Okta |
|---|---|---|
| OBO protocol | RFC 7523 JWT-bearer | RFC 8693 Token Exchange |
| Credential provider vendor | `MicrosoftOauth2` (or `CustomOauth2` for UC2) | `CustomOauth2` (always) |
| Exchange grant type | `JWT_AUTHORIZATION_GRANT` | `TOKEN_EXCHANGE` + `actorTokenContent: NONE` |
| Extra params at exchange | `requested_token_use=on_behalf_of` | `subject_token_type` + `audience` |
| Frontend OAuth library | MSAL | authlib (with PKCE required) |
| User identity claim | `oid` (stable object ID) | `sub` (login) + `uid` (Okta internal ID) |
| Actor claim (rotates per hop) | `azp` / `appid` | `cid` |
| Audience across hops | Rotates per app | Stays at auth server audience (differentiation is by scope) |

For a deeper protocol comparison, see [`../2-examples-overview.md`](../2-examples-overview.md).
