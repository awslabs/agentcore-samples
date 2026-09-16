# Gateway Response Interceptor

An AgentCore Gateway **response** interceptor (AWS Lambda) that filters the tool list the
claims gateway (GW1) returns to the agent, so each persona only *sees* the tools its group is
allowed to use. It is the **visibility** half of the gateway's fine-grained access control.

Deployed on **both** IdP paths (`IDP_PROVIDER` = `cognito` | `okta`). Only the JWT-validation
and group-claim details differ; the filtering logic is identical.

## Overview

GW1 wires **two** interceptor Lambdas, both driven by the same `allowed_tools` mapping in the
`lakehouse_tenant_role_map` DynamoDB table:

| Interceptor | Point | Role | Effect |
|---|---|---|---|
| **Request** (`../interceptor-request/`) | `REQUEST` | enforcement (call-gate) | a disallowed `tools/call` is rejected with **HTTP 403** |
| **Response** (this Lambda) | `RESPONSE` | visibility (list filter) | a disallowed tool is **removed from `tools/list`** so it never appears in the persona's toolset |

The two are complementary defense-in-depth: the request interceptor stops an unauthorized *call*,
and the response interceptor stops the tool from being *advertised* in the first place. Without the
response interceptor, every persona would see the full tool list (and could try a call that then
403s); with it, `tools/list` is already scoped per group. Both derive their decision from the same
DynamoDB `allowed_tools` list, keyed on the caller's group claim.

> The request interceptor also exchanges the caller's group claim for a tenant IAM role and
> forwards the caller identity to the downstream Claims MCP server. This response interceptor does
> neither — it only reads the validated JWT from the forwarded `Authorization` header (see
> `passRequestHeaders` below) and filters the tool list.

## Architecture / flow

```
Claims MCP Server ──tools/list──▶ Claims Gateway (GW1) ──▶ RESPONSE interceptor ──▶ Agent / Client
                                                              (this Lambda)
                                                                   │
                                                                   ▼
                                                         DynamoDB lakehouse_tenant_role_map
                                                         (allowed_tools for the caller's group)
```

The interceptor only transforms `tools/list` responses. All other MCP methods (e.g. `tools/call`,
`initialize`) pass through unchanged.

## Behavior

On a `tools/list` response the Lambda:

1. Reads the `Authorization: Bearer <jwt>` header (forwarded by the gateway).
2. Validates the JWT against the active IdP:
   - **`[COGNITO]`** issuer `https://cognito-idp.<region>.amazonaws.com/<user-pool-id>`, JWKS at
     `<issuer>/.well-known/jwks.json`.
   - **`[OKTA]`** RS256, issuer `https://<okta-org-url>/oauth2/<okta-auth-server-id>`, JWKS at
     `<issuer>/v1/keys`, audience `api://lakehouse-api`.
3. Derives the authorization key from the group claim:
   - **`[COGNITO]`** `cognito:groups`, keyed as the **whole array** — e.g. `["policyholders"]`.
   - **`[OKTA]`** `groups`, keyed as the **first non-built-in group** (the Okta built-in `Everyone`
     is skipped) — e.g. `["policyholders"]`.
   Both forms are JSON-encoded, so the DynamoDB key is identical on the two paths.
4. Looks up `allowed_tools` for that key in `lakehouse_tenant_role_map`.
5. Filters the returned tools to the intersection of `allowed_tools` and the advertised tools, and
   **always strips the system tool** `x_amz_bedrock_agentcore_search` (gateway-provisioned semantic
   meta-tool — noise for this demo). Gateway tool names are prefixed `target___tool`; the interceptor
   compares on the un-prefixed tool name.

### Fail semantics — fail-CLOSED (DR-14)

This interceptor **fails closed**: every authorization-failure path returns an **empty tool list**
(deny-all), never the unfiltered catalog. This is a deliberate divergence from the upstream tutorial,
which failed open.

| Condition | Result |
|---|---|
| Method ≠ `tools/list` | pass response through unchanged |
| No tools in the response | pass through unchanged |
| No / non-Bearer `Authorization` header | **fail-closed**: empty tool list |
| JWT invalid or expired | **fail-closed**: empty tool list |
| No usable claim (`groups`/`cognito:groups`/`email`/`sub`) | **fail-closed**: empty tool list |
| Claim resolved but **no `allowed_tools` mapping** in DynamoDB | **fail-closed**: empty tool list |
| Any unexpected exception | **fail-closed**: empty tool list, rebuilt defensively from the event |

Because the list is empty on those paths, the system tool is excluded by consequence rather than by
the explicit strip in step 5.

> **Two of these branches are unreachable through the gateway.** GW1 is created with
> `authorizerType: CUSTOM_JWT`, so a request with no bearer token or an invalid JWT is rejected with
> **401 at the gateway** before any interceptor runs. The no-bearer / invalid-JWT rows above are
> defense-in-depth for a direct or misconfigured invocation, not the normal path.

> **`passRequestHeaders: true` is required.** The response interceptor can only read the caller's JWT
> if the gateway forwards request headers. If it is omitted, the interceptor sees no bearer token and
> — being fail-closed — returns an **empty** tool list to *every* persona, so the agent appears to
> have no claims tools at all. `create_gateway.py` sets it on the RESPONSE entry.

## Tool-gating specifics (as-built)

`allowed_tools` per group, seeded by `../interceptor-request/setup_dynamodb_tenant_role_maps.py`
(`get_seed_data()` is the authoritative source):

| Group (`claim_value`) | Allowed tools | Count |
|---|---|---|
| `["administrators"]` | `query_login_audit`, `text_to_sql` | 2 |
| `["adjusters"]` | `get_claims_summary`, `get_claim_details`, `query_claims` | 3 |
| `["policyholders"]` | `get_claims_summary`, `get_claim_details`, `query_claims` | 3 |

The seeder writes `claim_name` per the active IdP — `cognito:groups` on Cognito, `groups` on Okta —
so the key the interceptor builds always matches what was seeded.

`x_amz_bedrock_agentcore_search` is stripped from **all** personas. So a policyholder/adjuster sees a
3-tool set and an administrator sees a disjoint 2-tool set — the tool-gating story, visible directly
in `tools/list`.

## Deploy

**Prerequisites**

1. The **request interceptor must be deployed first** — it creates the shared IAM role
   (`InsuranceClaimsGatewayInterceptorRole`) and seeds the `lakehouse_tenant_role_map` table.
2. `IDP_PROVIDER` set in SSM (`/app/lakehouse-agent/idp-provider`) — `deploy.sh` fails fast if it is
   missing or not one of `cognito` | `okta`.
3. The active IdP's config present in SSM:
   - **`[COGNITO]`** `cognito-user-pool-id`, `cognito-app-client-id`
   - **`[OKTA]`** `okta-org-url`, `okta-auth-server-id`, `okta-resource-server-audience`

**Run**

```bash
cd deployment/5a-gateway-setup/interceptor-response
./deploy.sh
```

`deploy.sh`:

1. Packages `lambda_function.py` + deps (`python-jose[cryptography]`, `cryptography`) into
   `response-interceptor-lambda.zip`.
2. Reads `IDP_PROVIDER` from SSM (fail-fast, no default) and loads that IdP's config.
3. Resolves the shared Lambda role from SSM `interceptor-lambda-role-arn` (falls back to the IAM role
   `InsuranceClaimsGatewayInterceptorRole`).
4. Creates (or updates) the Lambda:
   - runtime **`python3.11`**, handler `lambda_function.lambda_handler`
   - environment, branched on the flag:
     ```
     # [COGNITO]
     Variables={
       COGNITO_REGION=<region>,
       COGNITO_USER_POOL_ID=<cognito-user-pool-id>,
       COGNITO_APP_CLIENT_ID=<cognito-app-client-id>,
       IDP_PROVIDER=cognito,
       TENANT_ROLE_MAPPING_TABLE=lakehouse_tenant_role_map
     }

     # [OKTA]
     Variables={
       OKTA_ORG_URL=<okta-org-url>,
       OKTA_AUTH_SERVER_ID=<okta-auth-server-id>,
       OKTA_RESOURCE_SERVER_AUDIENCE=api://lakehouse-api,
       IDP_PROVIDER=okta,
       TENANT_ROLE_MAPPING_TABLE=lakehouse_tenant_role_map
     }
     ```
5. Writes the function ARN to SSM `/app/lakehouse-agent/response-interceptor-lambda-arn`.

> Re-runs update the function **code** in place (`update-function-code`) as well as its environment.
> If you switch `IDP_PROVIDER`, re-run `deploy.sh` so the Lambda's environment matches the new flag —
> the interceptor reads it (or falls back to SSM) on cold start.

## Gateway wiring

The gateway attaches this Lambda as a `RESPONSE` interceptor alongside the `REQUEST` interceptor.
`create_gateway.py` builds both entries; the RESPONSE entry must set `passRequestHeaders: True`:

```python
interceptor_configurations = [
    {   # REQUEST — call-gate (403)
        "interceptor": {"lambda": {"arn": "<interceptor-lambda-arn>"}},
        "interceptionPoints": ["REQUEST"],
        "inputConfiguration": {"passRequestHeaders": True},
    },
    {   # RESPONSE — tools/list visibility filter (this Lambda)
        "interceptor": {"lambda": {"arn": "<response-interceptor-lambda-arn>"}},
        "interceptionPoints": ["RESPONSE"],
        "inputConfiguration": {"passRequestHeaders": True},  # required to read the JWT
    },
]
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| **Every** persona sees an empty tool list | `passRequestHeaders` not set on the RESPONSE entry, so the interceptor never sees the JWT and fails closed; or the JWT is invalid |
| **One** persona sees an empty tool list | no `allowed_tools` mapping for its group in `lakehouse_tenant_role_map` — check the seeded `claim_name` matches the active IdP (`cognito:groups` vs `groups`) |
| Every persona sees the **full** tool list | the response interceptor isn't attached to the gateway |
| `x_amz_bedrock_agentcore_search` still appears | the response interceptor isn't attached to the gateway |
| Tool names don't match | gateway prefixes names `target___tool`; the interceptor matches on the un-prefixed name |

Logs: `aws logs tail /aws/lambda/lakehouse-gateway-response-interceptor --follow`.
Every deny path logs a `🚫` line naming the reason, and each run logs `🔢 Tool count: <n> → <m>`.

## References

- [AgentCore Gateway interceptor types](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-interceptors-types.html)
- [Fine-grained access control tutorial](https://github.com/awslabs/amazon-bedrock-agentcore-samples/tree/main/01-tutorials/02-AgentCore-gateway/09-fine-grained-access-control)
