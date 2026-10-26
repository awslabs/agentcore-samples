# OBO Examples - Overview and Protocol Comparison

This section introduces the end-to-end Python examples and provides a side-by-side protocol reference for the two identity providers covered: Microsoft Entra ID (RFC 7523) and Okta (RFC 8693).

For setup instructions and how to run the examples, see [`3-examples/README.md`](./3-examples/README.md).

---

## What the examples demonstrate

AgentCore Identity supports OBO token exchange with any OAuth 2.0 compliant identity provider. The examples show how AgentCore can be configured across two different RFC implementations:

- **`entra/`** - uses Microsoft Entra ID (RFC 7523 JWT bearer grant)
- **`okta/`** - uses Okta (RFC 8693 token exchange grant)

Each use case is available in both flavors and in two modes:

- **`local/`** - single-script interactive walkthrough you can run from your laptop
- **`real-world/`** - FastAPI BFF + Strands agent deployed on AgentCore Runtime

---

## OBO token exchange - Entra ID and Okta protocol reference

The underlying OAuth 2.0 protocols differ between providers, but AgentCore abstracts the wire-level details through a consistent `onBehalfOfTokenExchangeConfig` configuration. The tables below cover what differs between the two providers.

**Standard used**

| | Entra ID | Okta |
|---|---|---|
| Underlying RFC | RFC 7523 (JWT Bearer Assertion) | RFC 8693 (Token Exchange) |
| `grant_type` on the wire | `urn:ietf:params:oauth:grant-type:jwt-bearer` | `urn:ietf:params:oauth:grant-type:token-exchange` |
| Inbound token parameter name | `assertion` | `subject_token` |
| Extras | `requested_token_use=on_behalf_of` required | - |

**AgentCore `onBehalfOfTokenExchangeConfig`**

| | Entra ID | Okta |
|---|---|---|
| `credentialProviderVendor` | `MicrosoftOauth2` (built-in, recommended) or `CustomOauth2` | `CustomOauth2` only |
| `grantType` | `JWT_AUTHORIZATION_GRANT` | `TOKEN_EXCHANGE` |
| `clientAuthenticationMethod` | `CLIENT_SECRET_POST` (Custom only) | `CLIENT_SECRET_BASIC` |
| `actorTokenContent` | Not used in RFC 7523 mode | `NONE` (safest), `M2M`, or `AWS_IAM_ID_TOKEN_JWT` |
| Extra exchange-time params | None | `subject_token_type` + `--audience` required |

**Token claims**

| Claim | Entra ID | Okta |
|---|---|---|
| `sub` | Pairwise pseudonymous identifier - different per app | Stable user identifier (usually email) across apps |
| `act` | Not present - delegation chain via `azp` instead | Present when configured; nested for multi-hop |
| `azp` | The authorized party that performed the exchange | `cid` plays a similar role |
| `oid` | Stable cross-tenant user ID | - |

**Setup artifacts**

| | Entra ID | Okta |
|---|---|---|
| Where you register apps | App registrations | Applications |
| Where you define scopes | Per app ("Expose an API") | Per authorization server |
| Where you define access policies | Per app + admin consent | Per authorization server (access policies + rules) |
| Cross-scope consent mechanism | `.default` scope + `knownClientApplications` | Access policy rules per app |
| Multi-server exchange | N/A (single Entra tenant boundary) | Trusted Servers feature |
| Preferred agent credential | Managed Identity as FIC | Client secret (Basic auth) |

**Common gotchas**

| | Entra ID | Okta |
|---|---|---|
| Built-in provider supports OBO | Yes - `MicrosoftOauth2` auto-configures OBO | No - use `CustomOauth2` |
| Token version issues | v1.0 vs v2.0 discovery mismatch (CustomOauth2 only) | - |
| Conditional Access / MFA step-up | `interaction_required` error with claims challenge | - |
| Default scopes required | - | Yes - else "ClientCredentials response parse error" |
| Subject token type parameter | Not required | Required via `--custom-parameters` |
| Audience parameter | Not required | Required via `--audience` |
| Refresh tokens on exchanged tokens | Supported via `offline_access` | Not supported |
| ID tokens on exchanged tokens | Supported via `openid` | Not supported |
