# OBO Cheatsheet — Entra ID vs Okta (with AgentCore Identity)

A one-page side-by-side for quick reference.

## Standard used

| | Entra ID | Okta |
|---|---|---|
| Underlying RFC | RFC 7523 (JWT Bearer Assertion) | RFC 8693 (Token Exchange) |
| `grant_type` on the wire | `urn:ietf:params:oauth:grant-type:jwt-bearer` | `urn:ietf:params:oauth:grant-type:token-exchange` |
| Inbound token parameter name | `assertion` | `subject_token` |
| MS-specific extras | `requested_token_use=on_behalf_of` required | — |

## AgentCore `onBehalfOfTokenExchangeConfig`

| | Entra ID | Okta |
|---|---|---|
| `credentialProviderVendor` | `MicrosoftOauth2` (built-in, recommended) or `CustomOauth2` (for knobs) | `CustomOauth2` only |
| `grantType` | `JWT_AUTHORIZATION_GRANT` (auto on built-in, explicit on Custom) | `TOKEN_EXCHANGE` |
| `clientAuthenticationMethod` | `CLIENT_SECRET_POST` (Custom only) | `CLIENT_SECRET_BASIC` |
| `actorTokenContent` | (not used in RFC 7523 mode) | `NONE` (safest), `M2M`, or `AWS_IAM_ID_TOKEN_JWT` |
| Extra exchange-time params | none | `--custom-parameters '{"subject_token_type":"urn:ietf:params:oauth:token-type:access_token"}'` + `--audience <auth-server-audience>` |

## Token claims

| Claim | Entra ID | Okta |
|---|---|---|
| `sub` | Pairwise pseudonymous identifier — **different per app** | Stable user identifier (usually email) across apps |
| `act` | **Not present** — delegation chain via `azp` | Present when configured; nested for multi-hop |
| `azp` | The authorized party (the client that did the exchange) | `cid` plays a similar role |
| `oid` | Stable cross-tenant user id | — |

## Setup artifacts

| | Entra ID | Okta |
|---|---|---|
| Where you register apps | App registrations | Applications |
| Where you define scopes | Per app ("Expose an API") | Per authorization server |
| Where you define access policies | Per app + admin consent / knownClientApplications | Per authorization server (access policies + rules) |
| Cross-scope consent mechanism | `.default` scope + `knownClientApplications` + `preAuthorizedApplications` | Access policy rules per app |
| Multi-server exchange | N/A (single Entra tenant boundary) | **Trusted Servers** feature |
| Preferred agent credential | Managed Identity as FIC | Client secret (Basic auth) |

## Common gotchas

| | Entra ID | Okta |
|---|---|---|
| Built-in provider supports OBO? | **Yes** (`MicrosoftOauth2` auto-configures OBO) — use Custom only for extra knobs | **No** — use Custom |
| Token version issues | v1.0 vs v2.0 discovery mismatch (CustomOauth2 only) | — |
| Conditional Access / MFA step-up | `interaction_required` error with claims challenge | — |
| Default scopes required | — | Yes — else "ClientCredentials response parse error" |
| Claim emission issues | — | `groups` claim causes `system_claim_evaluation_failure` if emitted broadly |
| Subject token type parameter | Not required | **Required** at exchange time via `--custom-parameters` |
| Audience parameter | Not required | **Required** at exchange time via `--audience` |
| Refresh tokens on exchanged tokens | Supported via `offline_access` | Not supported on service-app-initiated exchange |
| ID tokens on exchanged tokens | Supported via `openid` | Not supported on service-app-initiated exchange |

## One-minute setup summary

### Entra ID (agent → Graph on user's behalf)

1. Register two Entra apps: **Client App** (user-facing) and **Actor App** (middle-tier that does OBO).
2. On the Actor App, expose a scope like `access_as_user` under "Expose an API."
3. Grant the Actor App the target API's delegated permissions (e.g., `User.Read` for Graph). Admin-consent.
4. Create client secrets on both apps (or use managed identity as FIC for production).
5. Create two AgentCore credential providers using built-in `MicrosoftOauth2` — one per app. No `onBehalfOfTokenExchangeConfig` needed.
6. Agent calls `GetWorkloadAccessTokenForJWT` + `GetResourceOauth2Token` with `ON_BEHALF_OF_TOKEN_EXCHANGE` on the Actor App's provider, gets a target-scoped token.

### Okta (native app → API1 → API2)

1. Create **Native App** integration for the user-facing client (Auth Code + PKCE, client secret enabled).
2. Create **API Services** integration for API1 — enable `Token Exchange` grant type, disable DPoP. Copy client ID and secret.
3. Add custom scopes (e.g., `oboe2e.apiC.read`) on the authorization server.
4. Create access policy "Access API1" assigned to the native app, rule allowing `openid`.
5. Create access policy "Access API2" assigned to API1's service app, rule applicable to the `Token Exchange` grant type allowing the custom scope.
6. Create two AgentCore credential providers using `CustomOauth2`:
   - Client provider for API1: `CLIENT_SECRET_BASIC`, no OBO config.
   - Actor provider for API2: `CLIENT_SECRET_BASIC`, `TOKEN_EXCHANGE`, `actorTokenContent: NONE`.
7. Agent calls `GetResourceOauth2Token` with `ON_BEHALF_OF_TOKEN_EXCHANGE` + `--custom-parameters '{"subject_token_type":"urn:ietf:params:oauth:token-type:access_token"}'` + `--audience "api://default"`.

## Choose based on

- **Your company's IdP.** The IdP dictates the protocol.
- **Do you need a human-readable audit trail in tokens?** Okta gives you nested `act`. Entra gives you `azp` and PPIDs (harder to trace at a glance but more privacy-preserving).
- **Do you need refresh / ID tokens on the exchanged token?** Entra supports it, Okta doesn't.
- **Privacy**: Entra's PPIDs hide user identity from downstream services by default; Okta exposes it.
