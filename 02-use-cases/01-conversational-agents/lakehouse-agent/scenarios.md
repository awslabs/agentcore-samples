# Lakehouse Agent: Role-Based Access Control Scenarios

---

## Scenario 1: Policy Holder Inquiry
**Pattern: Row-Level Security + Column Masking**

**User Story**: Sarah, a policy holder (`policyholder001@example.com`), logs into the claims portal to check the status of her recent hospital claim.

**What Sarah Can See**:
| claim_id | patient_name | patient_dob | claim_amount | claim_status | provider_name | adjuster_id |
|----------|--------------|-------------|--------------|--------------|---------------|-------------|
| CLM-2024-001 | Sarah Chen | 1985-03-15 | $1,250.00 | approved | City Medical | ████████ |
| CLM-2024-003 | Sarah Chen | 1985-03-15 | $3,500.00 | in_review | General Hospital | ████████ |

**What Sarah Cannot See**:
- Claims belonging to other policy holders
- The `adjuster_id` column (masked by Lake Formation column-level security)

**How**: Control access to the data through query conditions controlled by tools and parameters curated by Interceptor. The `adjuster_id` column is protected by Lake Formation column masking (to be added to Claims table).

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
    │               │               │               │                │               │(tag:user_id)    │               │
    │               │               │               │                │               │────────────────>│               │
    │               │               │               │                │               │                 │ Query w/      │
    │               │               │               │                │               │                 │ WHERE user_id │
    │               │               │               │                │               │                 │──────────────>│
    │               │               │               │                │               │                 │               │
    │               │               │               │                │               │                 │ note: Row filter applied
    │               │               │               │                │               │                 │ note: adjuster_id masked
    │               │               │               │                │               │                 │               │
    │               │               │               │                │               │                 │<──────────────│
    │               │               │               │                │               │<────────────────│               │
    │               │               │               │                │<──────────────│                 │               │
    │<───────────────────────────────────────────────────────────────────────────────│Claims (own only, adjuster hidden)
    │               │               │               │                │               │                 │               │
```

**Security Controls**:
- **Row-Level**: `WHERE user_id = '{authenticated_user}'` (application-level via tool parameters)
- **Column-Level**: Lake Formation masks `adjuster_id` for `patient` role
- **Tool Parameters**: Interceptor ensures `user_id` parameter matches authenticated user

---

## Scenario 2: Adjuster Dashboard
**Pattern: Tool-Based Access Control + Column Masking**

**User Story**: Michael, a claims adjuster (`adjuster001@example.com`), logs in to review claims assigned to him.

**What Michael Can See**:
| claim_id | patient_name | patient_dob | claim_amount | claim_status | adjuster_id |
|----------|--------------|-------------|--------------|--------------|-------------|
| CLM-2024-001 | Sarah Chen | ██████████ | $1,250.00 | approved | adjuster001 |
| CLM-2024-005 | Jane Smith | ██████████ | $850.00 | approved | adjuster001 |
| CLM-2024-006 | Jane Smith | ██████████ | $125.00 | approved | adjuster001 |

**What Michael Cannot See**:
- Claims assigned to other adjusters
- `patient_dob` column (masked for HIPAA compliance — adjusters don't need DOB)
- Claims not assigned to any adjuster

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
     │                │               │               │                │               │(tag:adjuster_id)│               │
     │                │               │               │                │               │────────────────>│               │
     │                │               │               │                │               │                 │ Query w/      │
     │                │               │               │                │               │                 │ WHERE         │
     │                │               │               │                │               │                 │ adjuster_id   │
     │                │               │               │                │               │                 │──────────────>│
     │                │               │               │                │               │                 │               │
     │                │               │               │                │               │                 │ note: Row filter applied
     │                │               │               │                │               │                 │ note: patient_dob masked
     │                │               │               │                │               │                 │               │
     │                │               │               │                │               │                 │<──────────────│
     │                │               │               │                │               │<────────────────│               │
     │                │               │               │                │<──────────────│                 │               │
     │<────────────────────────────────────────────────────────────────────────────────│  Assigned claims (DOB masked)   │
     │                │               │               │                │               │                 │               │
```

**Security Controls**:
- **Tool-Based**: `query_assigned_claims` only available to `adjuster` role
- **Row-Level**: `WHERE adjuster_id = '{authenticated_adjuster}'`
- **Column-Level**: Lake Formation masks `patient_dob` for `adjuster` role

---

## Scenario 3: IT Admin Analytics
**Pattern: Tool Restriction + DynamoDB Session Logs**

**User Story**: Admin (`admin@example.com`) wants to review user login activity and engagement metrics. Session details are captured via a Cognito post-authentication Lambda trigger and logged into DynamoDB. A dedicated MCP tool (`query_login_audit`) reads from DynamoDB and is only available to the admin role.

### How the Interceptor Works (Validated Against Current Code)

The incoming request does NOT contain a role or email in the headers. It contains only a JWT bearer token in the `Authorization` header. The Lambda interceptor (`gateway-setup/interceptor/lambda_function.py`):

1. Extracts the `Authorization: Bearer <token>` from the MCP gateway request
2. Validates the JWT against Cognito JWKS public keys
3. Decodes the JWT claims to extract user identity (priority: `email` → `username` → `cognito:username` → `sub`)
4. Extracts `cognito:groups` from the JWT claims (e.g., `["administrators"]`)
5. Passes `X-User-Identity` and `X-User-Scopes` (which includes groups) as headers to the downstream MCP server

### Role-to-Tool Mapping (DynamoDB: `lakehouse-role-mappings`)

Tool access is controlled by mapping Cognito groups to allowed tools:

| claim_name | claim_value | allowed_tools | description | role_type | role_value |
|------------|-------------|---------------|-------------|-----------|------------|
| cognito:groups | ["adjusters"] | get_claims_summary, get_claim_details, query_claims | Adjusters group mapping | iam_role | arn:aws:iam::XXXXXXXXXXXX:role/lakehouse-adjusters-role |
| cognito:groups | ["administrators"] | query_login_audit | Administrators group mapping with audit access | iam_role | arn:aws:iam::XXXXXXXXXXXX:role/lakehouse-administrators-role |
| cognito:groups | ["policyholders"] | get_claims_summary, get_claim_details, query_claims | Policyholders group mapping | iam_role | arn:aws:iam::XXXXXXXXXXXX:role/lakehouse-policyholders-role |

### DynamoDB Table: `lakehouse-session-logs`

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
    │              │               │               │                │║ groups=[admins]           ║    │               │
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

**Security Controls**:

- **Tool-Based**: `query_login_audit` tool only available to `administrators` group (via role-mapping table)
- **IAM Policies**: DynamoDB table access restricted to `lakehouse-administrators-role` ARN
- **Interceptor**: Extracts `cognito:groups` from JWT, passes as `X-User-Scopes` to MCP server
- **MCP Server**: Looks up allowed tools from `lakehouse-role-mappings` DynamoDB table based on user's group

**Lake Formation + DynamoDB Note**:
> ⚠️ Lake Formation does NOT support DynamoDB. DynamoDB security is enforced via:
>
> 1. **IAM Policies** — Restrict DynamoDB table access by IAM role ARN
> 2. **DynamoDB Fine-Grained Access Control (FGAC)** — IAM conditions on partition/sort keys
> 3. **Tool-Level Restriction** — `query_login_audit` tool only exposed to admin role via role-mapping table lookup

---

## Summary: Security Patterns by Role

| Role | Cognito Group | Row Access | Column Access | Tools Available | Data Source |
|------|---------------|------------|---------------|-----------------|-------------|
| **Patient** | policyholders | Own claims only | All except `adjuster_id` | `query_claims`, `get_claim_details`, `get_claims_summary` | Athena |
| **Adjuster** | adjusters | Assigned claims only | All except `patient_dob` | `query_claims`, `get_claim_details`, `get_claims_summary` | Athena |
| **Admin** | administrators | Session logs (all users) | All columns | `query_login_audit` | DynamoDB |

## Implementation Priority

1. **Scenario 1** (Easiest): Add `adjuster_id` column + Lake Formation column mask
2. **Scenario 2** (Medium): Add new tool + role-based tool filtering in interceptor
3. **Scenario 3** (Complex): DynamoDB session-logs table + Cognito post-auth trigger + role-mapping table + `query_login_audit` MCP tool
