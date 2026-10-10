<!-- Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved. -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# AWS Agent Registry UI — Design Spec

Zero-backend single-page app that gives AWS Agent Registry (GA, `agent-registry`
namespace, API version `2025-12-01`) a password-protected console-style UI.
The browser authenticates against Amazon Cognito, exchanges the resulting token
for short-lived scoped IAM credentials, and calls the Agent Registry SDK v3
clients **directly** — there is no application server.

- **Service availability:** both `agent-registry-control` and `agent-registry`
  CLI/SDK clients present; on a clean account `list-registries` returns
  `{registries: []}` (nothing to collide with).
- **UI:** Cloudscape Design System (`@cloudscape-design/components`).
- **Stack:** Vite + React + TypeScript static SPA, deployed to S3 + CloudFront.

---

## 1. Architecture — zero backend

```
Browser (Cloudscape SPA)
  │
  │ 1. USER_PASSWORD_AUTH / SRP  ──────────────►  Cognito User Pool
  │    (amazon-cognito-identity-js)                (password auth, groups)
  │ ◄── ID + Access + Refresh JWT ────────────────┘
  │
  │ 2. GetId + GetCredentialsForIdentity ──────►  Cognito Identity Pool
  │    (login = User Pool ID token)                (group → IAM role mapping)
  │ ◄── temporary scoped IAM credentials ─────────┘
  │
  │ 3. SigV4-signed API calls (SDK v3) ────────►  agent-registry-control  (control plane)
  │    using the temp creds                       publish / approve / tag — ALWAYS IAM
  │ ◄── registries / records ─────────────────────┘
  │
  │ 4. Bearer JWT (Cognito access token) ──────►  registry MCP endpoint   (data plane)
  │    JSON-RPC over streamable HTTP              agent-registry.<region>.api.aws
  │ ◄── discoverable (approved) records ──────────┘
```

No secret ever lives in the bundle — only **public identifiers** (User Pool ID,
App Client ID, Identity Pool ID, region). All authorization is enforced by AWS: IAM
on the persona role for the control plane, and the registry's JWT authorizer for
discovery. The UI's show/hide is a convenience layer over real enforcement.

### Why this shape
- "Minimum backend, if any" → **none**. Cognito Identity Pool is the credential
  broker AWS provides exactly for untrusted browser clients.
- The registry is created with `authorizerType = CUSTOM_JWT` whose `discoveryUrl` is
  **this sample's own user pool**. So the token the SPA already holds is a valid
  registry credential, and discovery can go over the registry's documented MCP
  endpoint — the same endpoint + token pair the app tells you to paste into Kiro or
  Amazon Quick. The instructions are therefore exercised by the app, not just
  documented.
- Control-plane calls are unaffected by the authorizer choice and keep using SigV4
  with the persona-scoped role, which is what makes the persona model real.
- Set `REGISTRY_AUTH_MODE=AWS_IAM` at seed time for the SigV4 variant; the SPA then
  uses `@aws-sdk/client-agent-registry` for discovery instead. `authorizerType` is
  immutable after creation, so this is a create-time decision, not a toggle.

---

## 2. Auth flow (detail)

1. **Login** — `amazon-cognito-identity-js` `CognitoUser.authenticateUser` with
   `AuthenticationDetails` (USER_PASSWORD_AUTH). On `NEW_PASSWORD_REQUIRED`
   (seeded users' first login) we complete the challenge in-UI. Yields
   ID/Access/Refresh JWTs; ID token carries `cognito:groups`.
2. **Credential exchange** — `@aws-sdk/credential-providers` `fromCognitoIdentityPool`
   with `logins["cognito-idp.<region>.amazonaws.com/<userPoolId>"] = idToken`.
   The Identity Pool is configured for **role-based access control** using the
   User-Pool-group → IAM-role mapping (`cognito:preferred_role` claim), so the
   temp creds carry exactly the persona's role.
3. **SDK calls** — the credential provider is passed to
   `AgentRegistryControlClient` / `AgentRegistryClient`. Tokens auto-refresh via
   the refresh token; creds re-vend on expiry.
4. **Session persistence** — `amazon-cognito-identity-js` stores the session in
   `localStorage` by default; restored on reload. Sign-out clears it.
5. **Route guard** — unauthenticated users only ever see the login screen.

---

## 3. Personas → Cognito groups → scoped IAM roles

**Three** personas — the day-to-day registry roles. Each is a Cognito User-Pool
**group** mapped to a distinct **IAM role** on the Identity Pool. The role policy is
scoped to only the `agent-registry:` actions that persona may call, so IAM physically
blocks out-of-role actions (a Consumer's `CreateRegistryRecord` returns
`AccessDenied` at the AWS layer).

**Deliberately no admin persona.** Creating a registry chooses an *immutable*
authorization model and asynchronously provisions an AgentCore workload identity —
an infrastructure action, not day-to-day registry work. It is done out-of-band by an
operator (console / CLI / IaC); in this sample `deploy/setup/03-registry.sh` does it
with the deployer's own credentials. Consequently there is no `CreateRegistry` /
`DeleteRegistry` code path in the frontend, no admin role in the stack, and no role
in the app needs workload-identity or `iam:PassRole` permissions.

**Single IAM prefix for everything:** `agent-registry:` (both planes).

| Persona group | Purpose | Allowed `agent-registry:` actions |
|---|---|---|
| `AgentRegistryConsumer` | Discover & read only | `SearchDiscoverableRegistryRecords`, `ListDiscoverableRegistryRecords`, `GetDiscoverableRegistryRecord`, `InvokeRegistryMcp`, `ListRegistries`, `GetRegistry`, `ListTagsForResource` |
| `AgentRegistryPublisher` | Author records, submit for approval, tag | Consumer set **+** `CreateRegistryRecord`, `UpdateRegistryRecord`, `GetRegistryRecord`, `ListRegistryRecords`, `SubmitRegistryRecordForApproval`, `TagResource`, `UntagResource` |
| `AgentRegistryApprover` | Review & gate quality | Read set **+** `UpdateRegistryRecordStatus` (approve/reject/deprecate), `GetRegistryRecord`, `ListRegistryRecords` |

Notes baked into the policies:
- The four data-plane actions (`Search*`, `List*Discoverable*`,
  `GetDiscoverableRegistryRecord`, `InvokeRegistryMcp`) are only exercised when a
  registry uses `AWS_IAM`. Under `CUSTOM_JWT` the bearer token authorizes those calls
  instead, so the grants are inert — they are kept so both models work unchanged.
- Tag-on-create is authorized by `TagResource`, which is why the Publisher role has it
  even though the wizard passes `tags` to `CreateRegistryRecord` in a single call.
  `ListTagsForResource` is granted to every persona so the record page can display
  tags (`GetRegistryRecord` does not return them).
- `batch-get-discoverable-registry-record` authorizes under the permission-only
  action `agent-registry:GetDiscoverableRegistryRecord` (singular, no "Batch").
- Resource ARNs:
  - registry: `arn:aws:agent-registry:us-east-1:111122223333:registry/<RegistryId>`
  - record:   `arn:aws:agent-registry:us-east-1:111122223333:registry/<RegistryId>/record/<RecordId>`
  - `ListRegistries` / `Search*` are account-wide (Resource `*`).

The **deploying operator** (not any app role) needs the registry-creation
permissions: the AWS-managed `AgentRegistryFullAccess` policy is the ready-made
superset. Scoping a registry-creating principal to only `agent-registry:*` makes
`CreateRegistry` reach **`CREATE_FAILED`** with *"Unable to create workload identity
because access was denied."*

The UI reads `cognito:groups` from the ID token to compute capability flags
(`canPublish`, `canApprove`); IAM is the real gate.

---

## 4. Data model (verified against live CLI)

### Registry
- `create-registry`: `--name` (req), `--description`, `--discovery-configuration`
  (`{authorizerType: CUSTOM_JWT, authorizerConfiguration: {customJWTAuthorizer:
  {discoveryUrl, allowedClients}}}` for us), `--approval-configuration`
  (`{autoApprovalRules: []}` = manual approval), `--tags`. Returns **only**
  `registryArn` → poll `get-registry` until `status = READY`.
- `discoveryConfiguration.authorizerType` (`AWS_IAM | CUSTOM_JWT`) controls **only**
  the data plane + MCP endpoint. `authorizerType` and the JWT `discoveryUrl` are
  **immutable**; `update-registry` can change only allowed clients / audiences /
  scopes / custom claims.
- Registry `status`: `CREATING | READY | UPDATING | CREATE_FAILED | UPDATE_FAILED | DELETING | DELETE_FAILED`.

### Record
- `recordType`: `MCP | AGENT | SKILL | CUSTOM` → descriptor branch
  `mcpServer | a2aAgentCard | agentSkillsDefinition | custom`.
- `descriptors` is a **tagged union**; set exactly the branch matching recordType.
  Each leaf `data` is a JSON/text string (≤1,024,000 chars); `custom` is `data` only.
- Record `status` (lifecycle): `DRAFT → PENDING_APPROVAL → APPROVED`
  (or `REJECTED`), `APPROVED → DEPRECATED`; plus `CREATING/UPDATING/*_FAILED`.
- **Approval workflow ops:**
  - `submit-registry-record-for-approval` → DRAFT into the flow
    (auto-approve if registry configured, else `PENDING_APPROVAL`).
  - `update-registry-record-status --status APPROVED|REJECTED|DEPRECATED
    --status-reason "<required>"` → approver action.

### Tags
- `create-registry-record --tags` applies tags **at creation** — one call, no
  follow-up. `update-registry-record` has **no** tags field, and
  `get-registry-record` does **not** return tags.
- After creation: `tag-resource` / `untag-resource` / `list-tags-for-resource`
  against the **record ARN** (`TagResource` supports registries and records).
- Limits: 50 tags; key 1–128; value 0–256; pattern `[a-zA-Z0-9\s._:/=+@-]*`;
  keys unique; `aws:` reserved.
- Tags are **not** filterable. `RegistryRecordFilterName` is `name | recordType |
  status` only, and the discoverable-record shape carries no tags — so tags are
  governance/cost metadata, not a discovery axis.

### Descriptor source (inline vs synchronized)
- A descriptor carries EITHER inline `data` (+ `dataSchemaVersion`) OR
  `source.fromUrl.url`. They are alternatives, not complements: with a source the
  registry **invokes the URL**, introspects the server/tool definitions and populates
  the descriptor itself (and may overwrite name/description/version), so this app sends
  `{ source }` alone and never both.
- The URL is the **live endpoint**, not a link to a static `server.json`. For an
  AgentCore Gateway or Runtime, it is the MCP endpoint.
- Only `mcpServer` and `a2aAgentCard` expose a settable `source`;
  `agentSkillsDefinition` and `custom` have no such field, so the wizard offers
  synchronization for MCP and AGENT records only.
- Lifecycle: `CREATING` while the registry fetches → `DRAFT` on success, or
  `CREATE_FAILED` with `statusReason` on failure. Re-fetch with
  `update-registry-record --trigger-synchronization` (which on an APPROVED record
  produces a new DRAFT revision while the approved one stays discoverable).
- Outbound credential (`credentialProviderConfigurations`, exactly one entry here):
  - `OAUTH` → `oauthCredentialProvider { providerArn, grantType: CLIENT_CREDENTIALS, scopes }`
    where `providerArn` is an **AgentCore Identity** OAuth2 credential provider. The
    caller needs `bedrock-agentcore:GetWorkloadAccessToken` +
    `GetResourceOauth2Token`; the provider must be in the same account.
  - `IAM` → `iamCredentialProvider { roleArn, service, region? }`, signing service
    `bedrock-agentcore` for an AgentCore Gateway/Runtime with `AWS_IAM` inbound auth
    (signing as `agent-registry` is rejected by the gateway with HTTP 401). The caller
    needs `iam:PassRole` with `iam:PassedToService = agent-registry.amazonaws.com`, and
    the role must trust `agent-registry.amazonaws.com`: the registry service itself
    assumes the role (CloudTrail `AssumeRole`, `invokedBy: agent-registry.amazonaws.com`).
    Verified live; `bedrock-agentcore.amazonaws.com` as `PassedToService` is denied.
  - omitted → the source is public.
- `autoDetectionConfiguration { scope: ORGANIZATION, enabled }` is the hands-off
  alternative: the registry auto-creates records for AgentCore Gateways/Runtimes it
  discovers (flagged `createdByAutoDetection`, with `provenance.relation = DETECTED_FROM`).
  Not used here — it is org-scoped and precondition-gated, whereas this sample targets a
  single account.

### Discovery (data plane, approved records only)
- `search-discoverable-registry-records` — **natural-language**, `--search-query`
  (≤256), `--registry-ids` (exactly 1), `--max-results` 1–20, relevance-ranked,
  returns full records incl. descriptors, no pagination.
- `list-discoverable-registry-records` — keyword/attribute list, `--registry-id`,
  `--filters` (`recordType`/`descriptorType`), paginated (`nextToken`), summaries
  **without** descriptors.
- `batch-get-discoverable-registry-record` — `--entries` (1 entry:
  `{registryId, recordIds[1–100]}`), full records + partial `errors[]`.

### Registry MCP endpoint (how the SPA discovers under CUSTOM_JWT)
```
POST https://agent-registry.<region>.api.aws/registry/<registryId>/mcp
Authorization: Bearer <Cognito access token>
Content-Type: application/json
Accept: application/json, text/event-stream
```
- MCP spec `2025-11-25` over streamable HTTP. Stateless for `tools/call`, so a single
  JSON-RPC POST suffices — no `initialize` handshake.
- Tools: `search_discoverable_registry_records`,
  `list_discoverable_registry_records`, `batch_get_discoverable_registry_record`.
- The registry is implicit in the URL path, so the tools take no `registryId(s)`
  argument. The search tool takes a singular `filter` object where the REST API takes
  `filters`.
- Underlying IAM action (for an `AWS_IAM` registry, signed with SigV4 instead):
  `agent-registry:InvokeRegistryMcp`.
- Protected-resource metadata (RFC 9728) for clients that discover auth dynamically:
  `https://agent-registry.<region>.api.aws/.well-known/oauth-protected-resource/registry/<registryId>/mcp`.
- This is the GA `agent-registry` namespace. The deprecated preview
  `bedrock-agentcore` namespace exposed only a single `search_registry_records` tool
  and is not used anywhere in this sample.

---

## 5. Cloudscape screen inventory

**Shell:** `AppLayout` + `TopNavigation` (product title, region badge, current
persona + user email, sign-out) + `SideNavigation`. Global light/dark via
`applyMode`. `Flashbar` for success/error toasts (AccessDenied → friendly
"Your role (<persona>) can't perform this action").

| Route | Screen | Cloudscape components | Persona visibility |
|---|---|---|---|
| `/login` | Login | `ContentLayout`, `Form`, `FormField`, `Input`, `Button`, `Alert` | all (unauthenticated) |
| `/` | Registries list | `Cards`, `Header`, `Button` (refresh) | all (read-only — no create) |
| `/registries/:id` | Registry detail: records / discover / connect | `SegmentedControl` (Manage · Discover · Connect your tools), `Table` (records: name/type/status/version, filtering), `Header`, `StatusIndicator` (lifecycle) | all read; actions per persona |
| `/registries/:id` → Connect | Connect your tools | `Container`, `Tabs` (token · Kiro · Amazon Quick · Claude/generic · curl), `CopyToClipboard`, `KeyValuePairs` | all |
| `/registries/:id/records/:rid` | Record detail | `Container`, `KeyValuePairs` (incl. Tags), `<pre>` for descriptor JSON, action `Button`s | all read; actions per persona |
| `/registries/:id/records/new` | Author record | `Wizard` (1: type+metadata, 2: source & authentication via `Tiles`/`RadioGroup`, 3: descriptor editor per type, 4: tags via `AttributeEditor`, 5: review) | Publisher |
| record actions | Submit / Approve / Reject / Deprecate | `Modal` + `Textarea` (status reason), `Button` | Submit: Publisher · status change: Approver |

Status → `StatusIndicator` mapping: `APPROVED`→success, `PENDING_APPROVAL`→pending,
`REJECTED`/`*_FAILED`→error, `DRAFT`/`DEPRECATED`→info/stopped, `CREATING`/`UPDATING`→in-progress.

---

## 6. Live AWS foundation (CloudFormation)

One stack creates:
- **User Pool** + app client (USER_PASSWORD_AUTH enabled, no client secret — public SPA client).
  This pool doubles as the registry's OIDC provider under `CUSTOM_JWT`.
- 3 **groups** (`AgentRegistry{Consumer,Publisher,Approver}`), each with `RoleArn`.
- **Identity Pool** (`AllowUnauthenticatedIdentities: false`), Cognito UP as auth provider,
  RBAC set to **"Choose role from token"** (`cognito:preferred_role`) with the 3 group roles.
- 3 **IAM roles** with `sts:AssumeRoleWithWebIdentity` trust on the Identity Pool
  (`cognito-identity.amazonaws.com:aud = <identityPoolId>`,
  `amr = authenticated`) + the scoped `agent-registry:` policy from §3.
- A `CognitoDiscoveryUrl` output — the `discoveryUrl` the registry's JWT authorizer
  is created with.

Plus (CLI, post-stack, with the **operator's** credentials): one **Agent Registry**
(`CUSTOM_JWT` pointed at the pool above, manual approval) and 9 **tagged seed
records** (MCP + AGENT + SKILL + CUSTOM; 5 APPROVED, 2 PENDING_APPROVAL, 2 DRAFT) so
every persona journey has real data. One **user per persona** seeded with a known
password and added to the matching group.

---

## 7. Verification strategy

- **Logic:** a Vitest suite pins the persona → capability matrix, descriptor
  schema construction, error mapping (IAM *and* MCP failures), PATCH-wrapper update
  semantics, tag validation, config + endpoint builders, the API command shapes each
  helper sends (both the SigV4 and the MCP bearer discovery path), and the router
  contract.
- **IAM enforcement (the real test):** log in as each of the 3 personas and
  confirm enforcement — e.g. a Consumer attempting `CreateRegistryRecord`
  gets `AccessDenied`, a Publisher can create+submit+tag but a Publisher's
  `UpdateRegistryRecordStatus` is denied, an Approver can approve/reject/deprecate.
  No persona can create or delete a registry, because no role grants it and the app
  has no code path for it.
- **JWT enforcement:** with a `CUSTOM_JWT` registry, discovery must succeed with the
  session's Cognito access token and fail (401/403 → "Registry rejected the token")
  with an expired or foreign token.
- The full `DRAFT → PENDING_APPROVAL → APPROVED` lifecycle can be walked
  end-to-end against a live Agent Registry.
- `npm run typecheck && lint && test && build` clean; no long-lived secret in the bundle.
- **Teardown script** removes every created resource (records → registry → Cognito → IAM roles).

---

## 8. Repo layout

```
agent-registry-ui/
  README.md                      overview, architecture, quick start
  DESIGN.md                      (this file)
  deploy/
    cognito-stack.yaml           CFN: User Pool, groups, Identity Pool, 3 roles
    seed-registry.sh             seed helper invoked by setup/03-registry.sh up
    sample-mcp-server/           the sample MCP server's Lambda target
      lambda_function.py         two tools; runnable standalone for a local smoke test
      tool-schema.json           inline toolSchema advertised by the gateway target
    setup/                       one up|down script per layer (see setup/README.md)
      _lib.sh                    shared verbose logging + config
      00-preflight.sh            read-only environment check
      01-foundation.sh  up|down  CFN stack: Cognito + Identity Pool + 3 IAM roles
      02-config.sh      up|down  write / reset outputs.env + frontend/.env
      03-registry.sh    up|down  registry (CUSTOM_JWT) + 9 tagged records + 3 persona users
      04-run-local.sh            build + preview the SPA locally
      05-site.sh        up|down  S3 + CloudFront + OAC (+ bucket policy)
      06-sample-mcp-gateway.sh up|down  Lambda + AgentCore Gateway + AgentCore Identity
                                 credential provider + a synchronized registry record
      99-all.sh         up|down  every layer in order / reverse (SKIP_SAMPLE=1 omits 06)
    state/                       (gitignored) stack outputs + generated password
  docs/
    architecture.png             architecture diagram
    screenshots/                 UI screenshots for the README
  frontend/
    index.html
    package.json, tsconfig.json, vite.config.ts, vitest.config.ts
    .env.example                 VITE_AWS_REGION, VITE_USER_POOL_ID, VITE_USER_POOL_CLIENT_ID, VITE_IDENTITY_POOL_ID, VITE_REGISTRY_ID, VITE_REGISTRY_AUTH_MODE
    src/
      main.tsx, App.tsx
      auth/                      cognito.ts, credentials.ts, AuthContext.tsx
      api/                       client.ts (control SDK + data-plane mode), registry.ts, records.ts, discovery.ts, mcp.ts (bearer JSON-RPC), errors.ts, useClients.ts
      personas/                  capabilities.ts (group → flags)
      pages/                     Login, Registries, RegistryDetail, RecordDetail, RecordCreate, RecordEdit
      components/                AppShell, StatusBadge, Breadcrumbs, ConnectRegistry, ConsumptionGuide, LifecycleInfo, TagsEditor, tags.ts, SourceEditor, source.ts, descriptors.ts, ...
      config.ts                  reads Vite env: pool IDs, identity pool, region, registry auth mode, credential provider ARN, endpoint builders
    tests/                       Vitest behaviour suite (personas, descriptors, errors, API command shapes incl. MCP, tags, source/credential providers, config, routing)
```
