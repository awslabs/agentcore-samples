# Databricks per-user identity with native AgentCore Gateway token exchange

Per-user (on-behalf-of) access from an Amazon Bedrock AgentCore agent to a Databricks Managed MCP
server, using the Gateway's **native `TOKEN_EXCHANGE` grant type**. Unity Catalog enforces each end
user's own permissions, and the Databricks audit trail attributes activity to the human who asked.

No Lambda interceptor is required.

## Relationship to the interceptor sample

[`databricks-dbsql-per-user-delegation`](../databricks-dbsql-per-user-delegation) achieves the same
outcome with a Gateway REQUEST interceptor, because it was written when Gateway supported only
`CLIENT_CREDENTIALS` and `AUTHORIZATION_CODE` for outbound auth. Gateway now supports `TOKEN_EXCHANGE`
natively, so the interceptor is no longer needed where the native path is available to your account.

Keep using the interceptor sample if the native path is not yet enabled for your account — see
[Prerequisites](#prerequisites).

## How it works

```
End user → agent → Gateway (validates inbound JWT)
                     │
                     ├─ RFC 8693 token exchange: inbound user JWT as subject_token
                     │  → Databricks /oidc/v1/token
                     │  → Databricks returns a token whose subject is the end user
                     │
                     └─ calls Databricks Managed MCP with that user-scoped token
                             → Unity Catalog enforces the end user's own grants
```

Three objects and no code: an inbound `CUSTOM_JWT` authorizer, a credential provider carrying
`onBehalfOfTokenExchangeConfig`, and an MCP target. See
[`working-config.json`](working-config.json) for the exact shape.

## Prerequisites

### AWS

1. **The public-client token exchange must be enabled for your AWS account and Region.** Databricks
   issues a per-user token only when the exchange presents *no* client authentication, and that mode is
   currently enabled per account and Region rather than being self-serve. See
   [Requesting access](#requesting-access). This is the single most common reason a correct
   configuration still fails.
2. **Gateway execution role.** Secrets Manager read on the token vault secret, KMS decrypt through
   Secrets Manager, and the AgentCore Identity token operations. Do **not** pin these to one Region —
   a Region-pinned policy produces `AccessDenied`, which Gateway reports as
   `insufficient permissions for token exchange`, and that reads like an access problem with the
   feature when it is your own role.

### Databricks

3. **An account-level OAuth federation policy** whose issuer and audience match your identity provider,
   with `subject_claim` set to the claim that carries the user's identity (commonly `email`). Create it at
   the **account** level — that is what this configuration was verified against.
4. **Each end user must be a member of the target workspace.** Without membership the exchange resolves
   the identity correctly and then fails with
   `user '<identity>' is not a member of workspace <id>`. Identity mapping is working at that point;
   user provisioning is the gap.
5. **The service principal** referenced by the credential provider needs the `workspace-access` and
   `databricks-sql-access` entitlements. Re-mint its OAuth secret token after granting them — an
   already-issued token carries the entitlements it had when minted.

## Why no client authentication is required

Databricks resolves the two cases differently. Against the same workspace, with the same subject token:

| Exchange | Result |
|---|---|
| **With** client authentication (client id and secret) | `400 invalid_grant` — Databricks looks for a *service principal* federation policy rather than the account user policy |
| **Without** client authentication | `200` — the returned token's subject is the end user |

Databricks advertises this in its OpenID configuration: `token_endpoint_auth_methods_supported`
includes `none`.

## Preflight check

Nothing in the API or the console tells you which identity will actually arrive at Unity Catalog, and a
misconfigured on-behalf-of target can succeed while running as the shared service principal. Run the
check before you trust the path:

```bash
python check_obo_identity.py \
  --gateway-url https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp \
  --target-name <your-target> \
  --token "$END_USER_JWT" \
  --subject-claim email
```

It creates and changes nothing. The identity query runs on a SQL warehouse that may be cold, so it
gets a 180s budget rather than the handshake's 60s — a cold start that timed out would otherwise be
reported as `UNKNOWN` for a target that is configured correctly. Override with `OBO_QUERY_TIMEOUT`.

The check also reads the token you pass and compares the claim your federation policy maps
(`--subject-claim`, default `email`) with the principal Unity Catalog reports. The shape of the principal
is weak evidence in both directions — a shared service account whose username is an email address looks
exactly like a human, and a workspace whose usernames are not email-shaped makes a real human look
unclassifiable — so an exact match against the claim outranks the shape either way. Two different
email-shaped identities are `IDENTITY_MISMATCH`.

A comparison that could not be made is `PER_USER_UNVERIFIED`, which exits non-zero on purpose: exit 0 on
an unverified check is the same green as a verified one, and that is the hole this exists to close. Two
common cases: a Cognito access token carries no `email` claim at all, and `sub` is an opaque GUID on both
Entra and Cognito, so it cannot be a Databricks username and its disagreement proves nothing. Point
`--subject-claim` at the claim your federation policy actually maps.

Verdicts and exit codes:

| Verdict | Exit | Meaning |
|---|---|---|
| `PER_USER` | 0 | Unity Catalog saw the end user. Working. |
| `SERVICE_PRINCIPAL` | 1 | Ran as the shared identity. Check `grantType` is `TOKEN_EXCHANGE` on both the provider and the target. |
| `EXCHANGE_REFUSED` | 2 | The exchange was refused. Work through the prerequisites, then ask whether the account and Region are enabled. |
| `CALLER_PERMISSIONS` | 3 | Your own execution role. Check CloudTrail for `AccessDenied`. |
| `WORKSPACE_MEMBERSHIP` | 4 | Identity resolved; the user is not a workspace member. |
| `GATEWAY_UNREACHABLE` | 4 | The gateway URL itself was not reachable, so nothing was tested. Transport, not identity: check the URL, the Region in the hostname, and egress. |
| `UNKNOWN` | 4 | Could not determine; the report prints what was seen. |
| `INBOUND_TOKEN` | 5 | The token you presented was rejected before the exchange: expired, or an issuer or audience the authorizer does not accept. |
| `PROVIDER_NOT_FOUND` | 6 | The credential provider named on the target does not exist where the gateway looked. Check `providerArn`, and that the provider is in the gateway's Region. |
| `TRANSIENT` | 7 | Rate limited, or a service error. Nothing is misconfigured on this evidence; retry. |
| `TARGET_REJECTED_TOKEN` | 8 | The exchange succeeded and Databricks rejected the delivered token. Check `all-apis` in the target's scopes, that the provider's discovery host matches the workspace host the target points at, and the caller's own permission on the warehouse, Genie space or function. |
| `TARGET_NOT_FOUND` | 9 | No tool matched `--target-name`, or the target contributed no tools — see the `listingMode` note below. |
| `IDENTITY_MISMATCH` | 10 | Unity Catalog saw a human, but not the human in the token presented. Commonly a shared account with an email-shaped username. |
| `PER_USER_UNVERIFIED` | 11 | A human-shaped principal, but the claim could not be compared: absent from the token, or opaque. Not a pass. |

The `insufficient permissions for token exchange`, `is not a member of workspace`, `scopes, audience, or
IdP configuration` and `Authorization error when sending message` mappings were observed against a live
gateway. `INBOUND_TOKEN`, `PROVIDER_NOT_FOUND`, `TRANSIENT` and `TARGET_NOT_FOUND` come from the
service-side message list supplied in review of this sample, and are not from a run of our own.

## Notes that save time

- **Set `mcp.mcpServer.listingMode` to `DYNAMIC`** on Managed MCP targets. Without it, target creation
  may fail with `Authorization error when sending message`. That failure reproduced consistently against
  some workspaces and not others, so set `DYNAMIC` rather than relying on eager listing. The same message
  also appears when the service
  principal lacks the entitlements in prerequisite 5, so confirm a direct call to the MCP endpoint works
  before concluding the gateway is at fault.
- **Send the MCP protocol version the gateway returns from `initialize`.** It pins the session to that
  version and rejects a different one with `-32600`. The preflight script does this for you.
- **With a Cognito ID token, use `allowedAudience` rather than `allowedClients`** on the authorizer. ID
  tokens carry `aud` but no `client_id`, so `allowedClients` yields `403 insufficient_scope`.
- **When an exchange fails on a `DYNAMIC` target, that target contributes no tools and no error** to
  `tools/list`. A caller sees a short tool list with no explanation.
- **`exceptionLevel: DEBUG` populates `_meta.debug` in responses; it does not create log delivery.**
  Delivery needs a delivery source, a destination and a delivery.
- **`x-amzn-requestid` on an MCP response is the Gateway request id, not an AgentCore Identity one.**
  Use CloudTrail for the Identity side.
- **A serverless Databricks workspace** needs no credentials configuration, storage configuration, IAM
  role, bucket or VPC, and is the quickest way to stand up a test workspace.

## Requesting access

If the preflight reports `EXCHANGE_REFUSED` and the prerequisites above are all satisfied, ask AWS to
enable the public-client token exchange for your account, supplying:

```
AWS account ID : <account>
Region(s)      : <region>
Capability     : public-client (no client authentication) OAuth2 token exchange for
                 Bedrock AgentCore Gateway outbound auth, per RFC 8693
Rationale      : Databricks Unity Catalog issues a per-user token only on an exchange with no
                 client authentication, so per-user authorization and per-user audit depend on it.
```

## Tests

```bash
python -m unittest discover -v
```
