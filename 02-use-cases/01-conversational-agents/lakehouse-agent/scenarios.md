# Lakehouse Agent: Access-Control Scenarios

> **Applies to both identity providers.** These RBAC scenarios are
> **IdP-agnostic** — the same personas (policyholders / adjusters /
> administrators), the same tools, and the same `lakehouse_tenant_role_map`
> lookup apply whether `IDP_PROVIDER` is `cognito` or `okta`. The **only**
> difference is the name of the group claim in the access token —
> `cognito:groups` (Cognito) vs `groups` (Okta) — which the seeder
> (`interceptor-request/setup_dynamodb_tenant_role_maps.py`) writes as the
> table's `claim_name`, branched once on `IDP_PROVIDER`. Everything below is
> shown with Cognito claim names for concreteness.

---

## Two isolation axes (the lesson)

This sample isolates data on **two architecturally different paths**, and the
scenarios below are organized around that contrast. Read this section first — it
is the lesson the rest of the document illustrates.

| Axis | Gateway | Data store | What scopes a result | How it is enforced |
|---|---|---|---|---|
| **Per-role** | **Claims Gateway (GW1)** | Athena / S3 Tables (Iceberg) | The caller's **group** (policyholders vs adjusters vs administrators) | **Columns** by Lake Formation (per-role grants / wildcard exclusions) plus the tenant-role **table grants**; **rows** at the tool layer — the claims tools bind the caller into a `WHERE user_id = ?` predicate. Same on both IdPs. |
| **Per-user** | **Notes Gateway (GW2)** | OpenSearch Serverless | The caller's **individual identity** (`sub`) | Per-document `owner_user_sub` field with a query-time `term` filter. How the caller's `sub` arrives is IdP-branched: **`[OKTA]`** the OBO-exchanged bearer (RFC 8693 `TOKEN_EXCHANGE`, no interceptor Lambda); **`[COGNITO]`** a thin notes REQUEST interceptor injecting it on the body-context channel (`params.arguments.context.user_id`, DR-9). |

Both paths start from the **same** inbound `Authorization: Bearer <user_jwt>`.
They diverge in *what* the result is scoped to, and *where* enforcement lands:

- **GW1 is a per-role story.** A custom REQUEST interceptor validates the JWT,
  maps the group claim to a tenant IAM role, and hands the Claims MCP server
  that role's temporary credentials. Lake Formation governs **which columns**
  the role may read; the tool layer governs **which rows**. Two policyholders
  see the same *shape* of data, each scoped to their own rows.
- **GW2 is a per-user story.** Scoping keys on the individual `sub`, not the
  group, so two users in the *same* group see disjoint note sets. Only the
  identity-propagation mechanism differs between IdPs; the filter is identical.

> Each store keeps its **native** scoping mechanism. This sample deliberately
> does not unify them — the contrast *is* the teaching point. Note in particular
> that Lake Formation is doing **column** work here, not row work: LF data-cell
> filters are not used, because a filter's row expression takes constants only and
> so cannot express per-caller scope (full reasoning and AWS references in
> `deployment/3-s3tables-setup/setup_lakeformation_permissions.py`). The tenant
> roles are per-**group** with no per-user session tags, and per-user row scope
> comes from the bound predicate alone — which is enforced and tested, not merely
> asserted.

The document has three parts:

- **Part 1 — Per-role** (GW1 → Athena): the policyholder, adjuster, and
  administrator personas.
- **Part 2 — Per-user** (GW2 → OpenSearch): free-text claim-notes search scoped
  to the individual caller.
- **Part 3 — Two-user isolation test** (notebook
  `07-optional-multi-user-isolation-test`): the proof that neither path leaks
  across users, even inside one shared agent session.

---

# Part 1 — Per-role access control (Claims Gateway GW1 → Athena)

All three personas call the **same** claims tools against the **same** `claims`
table. What differs is the **columns** each role's Lake Formation grant exposes
and the **rows** the tool layer scopes to. Identity is propagated by the REQUEST
interceptor: it validates the JWT, extracts the group claim, looks the group up
in the `lakehouse_tenant_role_map` DynamoDB table, assumes the mapped tenant IAM
role, and passes those credentials to the Claims MCP server.

## Scenario 1: Policy Holder Inquiry
**Pattern: Row-Level Security + Column Filtering**

**User Story**: Sarah, a policy holder (`policyholder001@example.com`), logs into the claims portal to check the status of her recent hospital claim.

**What Sarah Can See**:
| claim_id | policyholder_name | policyholder_dob | claim_amount | claim_status | provider_name |
|----------|--------------|-------------|--------------|--------------|---------------|
| CLM-2024-001 | Sarah Chen | 1985-03-15 | $1,250.00 | approved | City Medical |
| CLM-2024-003 | Sarah Chen | 1985-03-15 | $3,500.00 | in_review | General Hospital |

> **Note what the header row does *not* contain.** There is no `adjuster_user_id` column — not a blanked one. The policyholder grant names **15 columns** and that is not among them, so Lake Formation filters it out of the **table definition** and `SELECT *` expands to exactly those 15. Nothing is redacted or starred out; the column does not exist in Sarah's view. She *does* see `policyholder_dob` — it is her own, and policyholders are granted it (adjusters are not; see Scenario 2).

**What Sarah Cannot See**:
- Claims belonging to other policy holders
- The `adjuster_user_id` column — **absent** from her view of the table (Lake Formation column filtering)

**How**: rows are scoped by a query predicate bound to the caller's interceptor-propagated identity (`WHERE user_id = ?`); columns are scoped by the Lake Formation grant on the assumed tenant role — `lakehouse-policyholders-role` is granted 15 named columns of `claims`, which is what makes `adjuster_user_id` absent. Both controls are live in the shipped sample (`deployment/3-s3tables-setup/setup_lakeformation_permissions.py`).

```
┌────────┐      ┌──────┐        ┌───────┐       ┌────────┐      ┌─────────┐      ┌────────┐      ┌──────────┐      ┌────────┐
│ Sarah  │      │  UI  │        │ Agent │       │Gateway │      │Intercept│      │  MCP   │      │   Lake   │      │ Athena │
│Patient │      │      │        │       │       │        │      │         │      │        │      │Formation │      │        │
└───┬────┘      └───┬──┘        └───┬───┘       └───┬────┘      └────┬────┘      └───┬────┘      └─────┬────┘      └───┬────┘
    │               │               │               │                │               │                 │               │
    │"Show my claims"               │               │                │               │                 │               │
    │──────────────>│               │               │                │               │                 │               │
    │               │ Query         │               │                │               │                 │               │
    │               │──────────────>│               │                │               │                 │               │
    │               │               │JWT Bearer     │                │               │                 │               │
    │               │               │──────────────>│                │               │                 │               │
    │               │               │               │ Forward JWT    │               │                 │               │
    │               │               │               │───────────────>│               │                 │               │
    │               │               │               │                │╔═══════════════════════════╗    │               │
    │               │               │               │                │║ JWT Validation            ║    │               │
    │               │               │               │                │║ • Decode & validate       ║    │               │
    │               │               │               │                │║ • Extract email & grp     ║    │               │
    │               │               │               │                │║ email=policyholder001@    ║    │               │
    │               │               │               │                │║ groups=[policyholders]    ║    │               │
    │               │               │               │                │╚═══════════════════════════╝    │               │
    │               │               │               │                │                                 │               │
    │               │               │               │   X-User-Identity: policyholder001@              │               │
    │               │               │               │   X-User-Scopes: policyholders                   │               │
    │               │               │               │                │──────────────>│                 │               │
    │               │               │               │                │               │ AssumeRole      │               │
    │               │               │               │                │               │(per-group role) │               │
    │               │               │               │                │               │────────────────>│               │
    │               │               │               │                │               │                 │ Query w/      │
    │               │               │               │                │               │                 │ WHERE user_id │
    │               │               │               │                │               │                 │──────────────>│
    │               │               │               │                │               │                 │               │
    │               │               │               │                │               │                 │ note: Row filter applied
    │               │               │               │                │               │                 │ note: adjuster_user_id filtered out
    │               │               │               │                │               │                 │               │
    │               │               │               │                │               │                 │<──────────────│
    │               │               │               │                │               │<────────────────│               │
    │               │               │               │                │<──────────────│                 │               │
    │<───────────────────────────────────────────────────────────────────────────────│Claims (own only, adjuster hidden)
    │               │               │               │                │               │                 │               │
```

_(Diagram simplifies the interceptor as forwarded headers; the real mechanism is group→role STS + tool-list filtering — see below.)_

**Security Controls**:
- **Row-Level**: `WHERE user_id = '{authenticated_user}'` (application-level predicate, bound as an Athena execution parameter)
- **Column-Level**: Lake Formation filters `adjuster_user_id` out of the table definition for `lakehouse-policyholders-role` (the `policyholders` group)
- **Tool Parameters**: Interceptor ensures `user_id` parameter matches authenticated user

---

## Scenario 2: Adjuster Dashboard
**Pattern: Tool-Based Access Control + Column Filtering**

**User Story**: Michael, a claims adjuster (`adjuster001@example.com`), logs in to review claims assigned to him.

**What Michael Can See**:
| claim_id | policyholder_name | claim_amount | claim_status | adjuster_user_id |
|----------|--------------|--------------|--------------|-------------|
| CLM-2024-001 | Sarah Chen | $1,250.00 | approved | adjuster001 |
| CLM-2024-005 | Jane Smith | $850.00 | approved | adjuster001 |
| CLM-2024-006 | Jane Smith | $125.00 | approved | adjuster001 |

> **Note what the header row does *not* contain.** There is no `policyholder_dob` column — not a blanked one. Lake Formation filters the column out of the **table definition** for `lakehouse-adjusters-role`, so `SELECT *` expands to 20 columns instead of 21 and `glue:GetTable` never reports it. Nothing is redacted, masked or starred out; the column simply does not exist in Michael's view. Compare Scenario 1, where `policyholder_dob` **is** present because policyholders are granted their own.

**What Michael Cannot See**:
- Claims assigned to other adjusters, and claims not assigned to any adjuster — **unless he is the policyholder on them** (see the row-scope note below)
- The `policyholder_dob` column — **absent from his view of the table**, not blanked (Lake Formation column filtering)

```
┌─────────┐       ┌──────┐        ┌───────┐       ┌────────┐      ┌─────────┐      ┌────────┐      ┌──────────┐      ┌────────┐
│ Michael │       │  UI  │        │ Agent │       │Gateway │      │Intercept│      │  MCP   │      │   Lake   │      │ Athena │
│Adjuster │       │      │        │       │       │        │      │         │      │        │      │Formation │      │        │
└────┬────┘       └───┬──┘        └───┬───┘       └───┬────┘      └────┬────┘      └───┬────┘      └─────┬────┘      └───┬────┘
     │                │               │               │                │               │                 │               │
     │"Show claims assigned to me"    │               │                │               │                 │               │
     │───────────────>│               │               │                │               │                 │               │
     │                │ Query         │               │                │               │                 │               │
     │                │──────────────>│               │                │               │                 │               │
     │                │               │JWT Bearer     │                │               │                 │               │
     │                │               │──────────────>│                │               │                 │               │
     │                │               │               │ Forward JWT    │               │                 │               │
     │                │               │               │───────────────>│               │                 │               │
     │                │               │               │                │╔═══════════════════════════╗    │               │
     │                │               │               │                │║ JWT Validation            ║    │               │
     │                │               │               │                │║ • Decode & validate       ║    │               │
     │                │               │               │                │║ • Extract email & grp     ║    │               │
     │                │               │               │                │║ email=adjuster001@        ║    │               │
     │                │               │               │                │║ groups=[adjusters]        ║    │               │
     │                │               │               │                │╚═══════════════════════════╝    │               │
     │                │               │               │                │                                 │               │
     │                │               │               │   X-User-Identity: adjuster001@                  │               │
     │                │               │               │   X-User-Scopes: adjusters                       │               │
     │                │               │               │                │──────────────>│                 │               │
     │                │               │               │                │               │ AssumeRole      │               │
     │                │               │               │                │               │(per-group role) │               │
     │                │               │               │                │               │────────────────>│               │
     │                │               │               │                │               │                 │ Query w/      │
     │                │               │               │                │               │                 │ WHERE         │
     │                │               │               │                │               │                 │ adjuster_user_│
     │                │               │               │                │               │                 │ id            │
     │                │               │               │                │               │                 │──────────────>│
     │                │               │               │                │               │                 │               │
     │                │               │               │                │               │                 │ note: Row filter applied
     │                │               │               │                │               │                 │ note: policyholder_dob filtered out
     │                │               │               │                │               │                 │               │
     │                │               │               │                │               │                 │<──────────────│
     │                │               │               │                │               │<────────────────│               │
     │                │               │               │                │<──────────────│                 │               │
     │<────────────────────────────────────────────────────────────────────────────────│  Assigned claims (DOB filtered) │
     │                │               │               │                │               │                 │               │
```

_(Diagram simplifies the interceptor as forwarded headers; the real mechanism is group→role STS + tool-list filtering — see below.)_

**Security Controls**:
- **Tool-Based**: adjusters are mapped to `get_claims_summary`, `get_claim_details`, `query_claims` (the same tool set as policyholders — row-scoping is enforced by the bound identity predicate inside those tools, not by a distinct tool)
- **Row-Level**: a **disjunction**, not assignment alone — `WHERE (c.user_id = ? OR ('adjuster' IN (SELECT user_role FROM role_exp) AND c.adjuster_user_id = ?))`, with every caller-derived value bound as an Athena execution parameter (`query_claims` in `deployment/4a-mcp-lakehouse-server/athena_tools_secure.py`).

  **Why both halves.** The first disjunct, `c.user_id = ?`, is the *same* row-scope predicate every caller gets, including policyholders. It is kept for adjusters deliberately: an adjuster who is also a customer of the insurer must still see their own claims, and dropping it would make that impossible. The second disjunct adds the claims **assigned** to them, and is guarded by a `role_exp` CTE so it only activates when the `users` table records the caller as an adjuster.

  **Why the branch exists at all** is a *column*-grant matter rather than a row-scope one: policyholders are not Lake Formation-granted `adjuster_user_id`, so a query referencing that column fails for them with `COLUMN_NOT_FOUND`. Policyholders therefore run a query that names only caller-visible columns. `get_claim_details` mirrors the same branch.

  With the shipped sample data all nine claims are owned by the two policyholders, so the first disjunct never adds a row for `adjuster001` — the effective result is the assigned set shown above. The predicate is still what governs, and a tenant whose adjusters are also policyholders will see both.
- **Column-Level**: Lake Formation filters `policyholder_dob` out of the table definition for `lakehouse-adjusters-role` (the `adjusters` group) — the column is **absent** from that role's view, not masked

---

## Scenario 3: IT Admin Analytics
**Pattern: Tool Restriction + DynamoDB Session Logs**

**User Story**: Admin (`admin@example.com`) wants to review user login activity and engagement metrics. Session details are captured via a Cognito post-authentication Lambda trigger and logged into DynamoDB. A dedicated MCP tool (`query_login_audit`) reads from DynamoDB and is only available to the admin role.

### How the Interceptor Works (Validated Against Current Code)

The incoming request does NOT contain a role or email in the headers. It contains only a JWT bearer token in the `Authorization` header. The claims REQUEST interceptor Lambda (`deployment/5a-gateway-setup/interceptor-request/lambda_function.py`):

1. Extracts the `Authorization: Bearer <token>` from the MCP gateway request
2. Validates the JWT against the active IdP's JWKS public keys (Cognito user pool **or** Okta authorization server)
3. Decodes the JWT claims to extract user identity — **`[COGNITO]`** priority `email` → `username` → `cognito:username` → `sub`; **`[OKTA]`** the `sub`/`email` claim
4. Extracts the **group claim** — **`[COGNITO]`** `cognito:groups`, **`[OKTA]`** `groups` (e.g., `["administrators"]`)
5. Looks up that group in `lakehouse_tenant_role_map` (partition key `claim_name` = the active IdP's group-claim name) and **assumes the mapped tenant IAM role via STS** — the assumed role scopes **column**-/table-level access via Lake Formation grants (there are **no** per-user session tags); per-user **row** scope is the bound identity SQL predicate (see the Security Controls in each scenario, e.g. `WHERE user_id = ?`). A separate RESPONSE interceptor then **filters the returned tool list** to the group's `allowed_tools`.

On authorization failure the interceptors fail **CLOSED** — the RESPONSE interceptor returns an empty tool catalog (deny-all) and the REQUEST interceptor returns 403 if the tenant-role exchange fails; they never fall open to all-tools or to the runtime's default credentials.

> The claims path carries identity via this group→role STS exchange (not a forwarded header). The **notes** path (GW2 / OpenSearch) differs: on Cognito a thin notes REQUEST interceptor injects the caller `sub` on the body-context channel (`params.arguments.context.user_id`, DR-9); on Okta the OBO-exchanged bearer carries it.

### Role-to-Tool Mapping (DynamoDB: `lakehouse_tenant_role_map`)

Tool access is controlled by mapping the caller's group claim to allowed tools. `claim_name` is `cognito:groups` on Cognito and `groups` on Okta (shown below with Cognito names):

| claim_name | claim_value | allowed_tools | description | role_type | role_value |
|------------|-------------|---------------|-------------|-----------|------------|
| cognito:groups | ["adjusters"] | get_claims_summary, get_claim_details, query_claims | Adjusters group mapping | iam_role | arn:aws:iam::XXXXXXXXXXXX:role/lakehouse-adjusters-role |
| cognito:groups | ["administrators"] | query_login_audit, text_to_sql | Administrators group mapping with audit access and text-to-SQL | iam_role | arn:aws:iam::XXXXXXXXXXXX:role/lakehouse-administrators-role |
| cognito:groups | ["policyholders"] | get_claims_summary, get_claim_details, query_claims | Policyholders group mapping | iam_role | arn:aws:iam::XXXXXXXXXXXX:role/lakehouse-policyholders-role |

### DynamoDB Table: `lakehouse_user_login_audit`

Session data captured by Cognito post-authentication Lambda trigger:

| user_id | login_timestamp | client_id | cognito_username | email | email_verified | event_type | groups | source_ip | ttl | user_agent | user_pool_id |
|---------|-----------------|-----------|------------------|-------|----------------|------------|--------|-----------|-----|------------|--------------|
| adjuster001@example.com | 2026-02-14T00:50:15 | ... | adjuster001@example.com | adjuster001@example.com | FALSE | post_authentication | [] | ... | 1778806215 | ... | us-east-1_KguWtaDjS |
| adjuster001@example.com | 2026-02-14T00:54:09 | ... | adjuster001@example.com | adjuster001@example.com | FALSE | post_authentication | [] | ... | 1778806449 | ... | us-east-1_KguWtaDjS |
| policyholder001@example.com | 2026-02-14T00:47:43 | ... | policyholder001@example.com | policyholder001@example.com | TRUE | post_authentication | [] | ... | 1778806063 | ... | us-east-1_KguWtaDjS |
| policyholder001@example.com | 2026-02-14T00:53:10 | ... | policyholder001@example.com | policyholder001@example.com | TRUE | post_authentication | [] | ... | 1778806390 | ... | us-east-1_KguWtaDjS |
| admin@example.com | 2026-02-14T00:28:04 | ... | admin@example.com | admin@example.com | TRUE | post_authentication | [] | ... | 1778804884 | ... | us-east-1_KguWtaDjS |
| admin@example.com | 2026-02-14T00:28:10 | ... | admin@example.com | admin@example.com | TRUE | post_authentication | [] | ... | 1778804890 | ... | us-east-1_KguWtaDjS |

**What Admin Can Query**:

- How many times each user/policyholder logged in
- Login timestamps and frequency patterns
- Source IP addresses
- Email verification status
- User pool and client information

```
┌───────┐      ┌──────┐        ┌───────┐       ┌────────┐      ┌─────────┐      ┌────────┐       ┌─────────┐     ┌─────────┐
│ Admin │      │  UI  │        │ Agent │       │Gateway │      │Intercept│      │  MCP   │       │ DynamoDB│     │ DynamoDB│
│       │      │      │        │       │       │        │      │         │      │        │       │ RoleMap │     │ Sessions│
└───┬───┘      └───┬──┘        └───┬───┘       └───┬────┘      └────┬────┘      └───┬────┘       └────┬────┘     └────┬────┘
    │              │               │               │                │               │                 │               │
    │"Show user login activity"    │               │                │               │                 │               │
    │─────────────>│               │               │                │               │                 │               │
    │              │ Query         │               │                │               │                 │               │
    │              │──────────────>│               │                │               │                 │               │
    │              │               │JWT Bearer     │                │               │                 │               │
    │              │               │──────────────>│                │               │                 │               │
    │              │               │               │ Forward JWT    │               │                 │               │
    │              │               │               │───────────────>│               │                 │               │
    │              │               │               │                │╔═══════════════════════════╗    │               │
    │              │               │               │                │║ JWT Validation            ║    │               │
    │              │               │               │                │║ • Decode & validate       ║    │               │
    │              │               │               │                │║ • Extract email & grp     ║    │               │
    │              │               │               │                │║ email=admin@              ║    │               │
    │              │               │               │                │║ groups=[administrators]   ║    │               │
    │              │               │               │                │╚═══════════════════════════╝    │               │
    │              │               │               │                │                                 │               │
    │              │               │               │   X-User-Identity: admin@                        │               │
    │              │               │               │   X-User-Scopes: administrators                  │               │
    │              │               │               │                │──────────────>│                 │               │
    │              │               │               │                │               │ Lookup          │               │
    │              │               │               │                │               │"admins"         │               │
    │              │               │               │                │               │────────────────>│               │
    │              │               │               │                │               │                 │               │
    │              │               │               │                │               │tools:           │               │
    │              │               │               │                │               │[query_login_    │               │
    │              │               │               │                │               │audit]           │               │
    │              │               │               │                │               │<────────────────│               │
    │              │               │               │                │               │                                 │
    │              │               │               │                │               │ query_login_audit()             │
    │              │               │               │                │               │────────────────────────────────>│
    │              │               │               │                │               │                                 │
    │              │               │               │                │               │ note: Login records per user    │
    │              │               │               │                │               │ note: timestamps, source_ip,    │
    │              │               │               │                │               │       event_type                │
    │              │               │               │                │               │                                 │
    │              │               │               │                │               │<────────────────────────────────│
    │              │               │               │                │<──────────────│                 │               │
    │<──────────────────────────────────────────────────────────────────────────────│ Login activity dashboard        │
    │              │               │               │                │               │                 │               │
```

_(Diagram simplifies the interceptor as forwarded headers; the real mechanism is group→role STS + tool-list filtering — see below.)_

**Security Controls**:

- **Tool-Based**: `query_login_audit` and `text_to_sql` only available to the `administrators` group (via the role-mapping table)
- **IAM Policies**: DynamoDB table access restricted to `lakehouse-administrators-role` ARN
- **Interceptor**: extracts the group claim (`[COGNITO]` `cognito:groups` / `[OKTA]` `groups`) from the JWT and assumes the mapped tenant role via STS
- **MCP Server**: allowed tools come from the RESPONSE interceptor filtering the tool list against the group's `allowed_tools` in the `lakehouse_tenant_role_map` DynamoDB table

**Lake Formation + DynamoDB Note**:
> ⚠️ Lake Formation does NOT support DynamoDB. DynamoDB security is enforced via:
>
> 1. **IAM Policies** — Restrict DynamoDB table access by IAM role ARN
> 2. **DynamoDB Fine-Grained Access Control (FGAC)** — IAM conditions on partition/sort keys
> 3. **Tool-Level Restriction** — `query_login_audit` tool only exposed to admin role via role-mapping table lookup

---

## Part 1 summary: per-role differences (same tool shape, different data)

| Role | Group | Rows (tool layer) | Columns (Lake Formation) | Write | Tools Available | Data Source |
|------|-------|-------------------|--------------------------|-------|-----------------|-------------|
| **Policyholder** | `policyholders` | Own claims only (`user_id`) | 15/21 — excludes `adjuster_user_id`, `created_by`, `last_modified_by`, `last_modified_date`, `notes`, `denial_reason` | No (SELECT) | `query_claims`, `get_claim_details`, `get_claims_summary` | Athena |
| **Adjuster** | `adjusters` | Assigned claims only (`adjuster_user_id`) | 20/21 — excludes `policyholder_dob` | No (SELECT) | `query_claims`, `get_claim_details`, `get_claims_summary` | Athena |
| **Admin** | `administrators` | Session logs (all users); full claims book via `text_to_sql` | 21/21 (both PII columns) | Yes (INSERT/ALTER/DELETE) | `query_login_audit`, `text_to_sql` | DynamoDB + Athena |

Column counts are the grants issued by
`deployment/3-s3tables-setup/setup_lakeformation_permissions.py` against the
21-column `claims` schema in `setup_s3tables.py`.

Note the division of labour once more: the **Columns** column is Lake Formation's
contribution, the **Rows** column is the tool layer's. Neither is doing the
other's job.

---

# Part 2 — Per-user access control (Notes Gateway GW2 → OpenSearch)

Where Part 1 scopes by **role** against structured claims, Part 2 scopes by
**individual user** against a qualitatively different data type: **free-text
claim notes** (adjuster notes, policyholder statements, correspondence) held in
an Amazon OpenSearch Serverless collection. There is no tabular column model
here — the unit of access is the **document**.

Notes live in the `claim-notes` index, carry an `owner_user_sub` field, and are
isolated **per user**: each caller sees only their own notes (∩ across users = ∅).

### The data

Each note is a minimal document carrying an owner identifier:

```json
{
  "claim_id": "CLM-...",
  "owner_user_sub": "<sub-of-owner>",
  "note_text": "Initial damage assessment ...",
  "note_type": "adjuster-note" | "policyholder-statement" | "...",
  "created_at": "2024-..."
}
```

### How identity propagates — and where the filter lands

The tool the agent calls is `search_claim_notes(query, limit=10)` — full-text
search over claim notes, **scoped to the caller's `sub`**. The OpenSearch MCP
server applies `term: { owner_user_sub: <sub> }` at query time. This is
**per-user** isolation: two users in the *same* group (e.g. two policyholders)
see disjoint note sets, because the filter keys on the individual identity rather
than the group.

The owner identity is resolved by IdP:

- **`[OKTA]`** — derived from the OBO-exchanged bearer token's `sub` (the user's
  email/login). AgentCore Identity performs the RFC 8693 `TOKEN_EXCHANGE`
  natively, per request — there is **no** interceptor Lambda on this path.
- **`[COGNITO]`** — injected by the thin notes REQUEST interceptor on the
  body-context channel (`params.arguments.context.user_id`), per DR-9.

The group→tool RBAC from Part 1 is unchanged for notes tools; only the per-user
owner scoping is notes-specific. See `deployment/README.md` **Step 7 (Notes
Gateway GW2 + OpenSearch)** and notebook `05b-deploy-notes-gateway` for the
deployment path.

### Security controls

- **Per-document filter** — `term: { owner_user_sub: <sub> }`, applied at query
  time inside the OpenSearch MCP server.
- **No header → no query** — with no `Authorization` header the tool returns an
  error rather than running an unscoped query. It fails closed.
- **`[OKTA]` no caller IAM required** — the gateway's own role performs the token
  exchange; identity propagation is native AgentCore Identity OBO.

> **Seed form must match extracted form.** On the Okta tenant the `sub` claim
> resolves to the user's email, not the `00u…` Okta user id. If documents are
> seeded with one form while the runtime extracts the other, the `term` filter
> matches nothing, every query returns empty, and an isolation test passes
> **vacuously**. See the `owner_user_sub` seeding warning in `README.md` for the
> SSM keys involved. The own-data baselines in Part 3 (scenarios 1–2 returning
> non-empty) are what give the isolation assertion teeth.

---

# Part 3 — Two-user isolation test (notebook `07-optional-multi-user-isolation-test`)

Parts 1 and 2 describe what a single caller sees. Part 3 is the proof that **no
per-user data leaks across users**, on **either** gateway, **even within a single
shared agent session**.

### The invariant

> For any two distinct authenticated users A and B with distinct subject
> identifiers, a `tools/call` result returned to user B SHALL contain ONLY data
> scoped to user B's identity, with NO data scoped to user A's identity,
> regardless of which gateway processed the call.

The invariant is IdP-neutral: it holds on the Cognito path and the Okta path
alike, because both re-derive the caller per request.

### Test data (disjoint by construction)

Two distinct `policyholders`-group users with disjoint data on **both** stores, so
any cross-user leak is detectable by simple set-difference:

- **Athena claims** — `policyholder001@example.com` owns `CLM-2024-001..004`;
  `policyholder002@example.com` owns `CLM-2024-005..009`.
- **OpenSearch notes** — six documents split disjointly between the two users'
  `owner_user_sub` values.

### The six scenarios

The first two establish per-user baselines (each user's own data is reachable
through each gateway). Scenarios 3–6 are the load-bearing **same-session
ordering** stressors — two users hitting the **same** gateway in close succession
inside one agent session.

| # | Scenario | Sequence | Gateway | Asserts |
|---|----------|----------|---------|---------|
| 1 | User A alone, both gateways | A → A | GW2, then GW1 | A's call returns ONLY A's data on each |
| 2 | User B alone, both gateways | B → B | GW2, then GW1 | B's call returns ONLY B's data on each |
| 3 | A → B same session, notes | A then B | GW2 | B's call returns ONLY B's data; no A-residue |
| 4 | B → A same session, notes | B then A | GW2 | A's call returns ONLY A's data; no B-residue |
| 5 | A → B same session, claims | A then B | GW1 | B's call returns ONLY B's data; no A-residue |
| 6 | B → A same session, claims | B then A | GW1 | A's call returns ONLY A's data; no B-residue |

Each scenario asserts via **set-difference** — `result ∩ other_user_record_set`
must be empty, and the disjoint fixtures make any non-empty intersection a
definitive violation — plus a **negative leakage scan** confirming no field
contains the other user's `sub`. The notebook prints a single explicit signal:

```
ISOLATION TEST PASSED
```

### Why same-session ordering is the real test

The AgentCore runtime **session-id is not principal-bound**. The isolation test
empirically accepts a single `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` across
two *different* user tokens — the platform does not reject the principal switch on
a reused session. Isolation therefore does **not** rest on session binding. It
rests entirely on **per-request identity re-derivation**:

- every tool call re-extracts the caller's principal from the freshly validated
  inbound JWT, and
- **`[OKTA]`** the OBO leg re-exchanges the token per call,

combined with the `owner_user_sub` filter (notes) and the bound identity
predicate plus tenant-role grants (claims). Scenarios 3–6 run two users through
the *same* agent session precisely to guard this: **any future change that caches
a principal, token, or vault handle at session granularity — instead of
re-deriving per request — would silently break cross-user isolation while a
session-id check still appeared to work.**

---

## Screenshots

The Part 1 per-role scenarios are illustrated with screenshots in the sample
`README.md`, under **User-Specific Data Access Demo** — policyholder PII access,
adjuster detail filtered out, cross-policyholder denial, adjuster DOB filtered out, and the
two admin views. They are not duplicated here.

## Implementation Priority

1. **Scenario 1** (Easiest): Add `adjuster_user_id` column + Lake Formation column filter
2. **Scenario 2** (Medium): Add new tool + role-based tool filtering in interceptor
3. **Scenario 3** (Complex): DynamoDB login-audit table + Cognito post-auth trigger + role-mapping table + `query_login_audit` MCP tool
