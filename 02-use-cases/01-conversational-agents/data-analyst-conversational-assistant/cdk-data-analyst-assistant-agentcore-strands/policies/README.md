# AgentCore Policy: Dogwood policies

These files are the AgentCore Policy Engine policies for the data analyst Gateway, written in
[Dogwood](https://dogwood-policy.github.io/dogwood/). Dogwood is an open-source superset of Cedar that adds
`when temporal { }` conditions over the session's tool-call history. Every valid Cedar policy is also a valid
Dogwood policy.

| File | Effect | Kind | What it enforces |
|------|--------|------|------------------|
| `01_permit_get_tables_information.dw` | `permit` | Authorization | Schema lookup is always allowed |
| `02_permit_sql_after_schema_lookup.dw` | `permit` | Temporal (`formerly`) | Queries need a successful schema lookup in the last 30 min |
| `03_forbid_sensitive_columns.dw` | `forbid` | Authorization (`like`) | No SQL that references PII or internal cost columns |
| `04_forbid_query_rate_limit.dw` | `forbid` | Temporal (`count`) | At most 20 queries per session in 5 min |
| `05_forbid_requery_until_schema_refresh.dw` | `forbid` | Temporal (`!L since R`) | After a denied query, re-read the schema before querying again |
| `06_guardrail_prompt_attack.dw` | `forbid` | Guardrail | Prompt injection / jailbreak in tool arguments |
| `07_guardrail_harmful_content.dw` | `forbid` | Guardrail | Violence / hate in the query description |
| `08_suppress_pii_output.dw` | `suppressOutput` | Guardrail | Withhold results that contain SSN, card, email, or phone data |

## How they are deployed

The CDK stack (`cdklib/cdk-data-analyst-assistant-agentcore-strands-stack.ts`, `dogwoodPolicies`) reads
each file. It removes full-line `//` comments, replaces `{{GATEWAY_ARN}}` with the stack's Gateway ARN,
and creates an `AWS::BedrockAgentCore::Policy` with `Definition.Policy.Statement`. The policies are created
one at a time, in file order, after the Gateway (with the engine attached) and its target exist.

To add a policy, drop a new `.dw` file here and add an entry to `dogwoodPolicies` in the stack.

## Authoring notes

These rules come from the AgentCore Policy service and from testing against it while building this sample.

- **Action names** for an MCP Lambda target are `<TargetName>___<tool_name>`, e.g.
  `DatabaseToolsTarget___execute_sql_query`. Tool arguments are `context.input.<arg>`.
- **Name the gateway.** A policy that names a specific action must use
  `resource == AgentCore::Gateway::"<arn>"`, not `resource is AgentCore::Gateway`.
- **Deny-by-default and the dependency trap.** A tool without a matching `permit` is denied, and a denied
  call is recorded only as `::error`. A temporal condition on `get_tables_information::response` would
  therefore never match without policy 01.
- **Every temporal predicate needs `eventResource: resource`.**
- **`count` and `sum` include the current request**, so `n > 20` allows 20 calls and blocks the 21st.
- **No `like` or `||` inside `when temporal { }`.** Field patterns in predicates are equality/binding only.
  This is why policy 05 is keyed on "any denied query", not "a query that touched a sensitive column".
- **`suppressOutput` must be the first token** of the statement the service receives. That's why the stack
  strips comments before sending (`permit`/`forbid` accept leading comments).
- **Output paths.** For this Lambda target, the policy sees the Lambda's raw JSON result
  (`{"result": [...]}`), not the MCP content envelope. Policy 08 uses the JSONPath `"$.*"`. A JSONPath
  is not validated at creation, and if none of a guardrail's paths resolve the call fails closed
  (`request missing field(s) required for guardrail evaluation`). Test output paths against real traffic.
- **Policy sessions.** Callers must send `x-amzn-bedrock-agentcore-policy-session-id`. Changing a temporal
  policy invalidates open sessions, and the next tool call on one fails with JSON-RPC error `-32005`.
- **Validation mode.** Policy validation can't interpret temporal or guardrail conditions and reports
  findings like "Overly Restrictive". The stack uses `ValidationMode: IGNORE_ALL_FINDINGS`.

## Validating locally

The open-source `dogwood` CLI can parse and type-check a policy against a Cedar schema and replay it over an
event trace. See [The command line](https://dogwood-policy.github.io/dogwood/guide/12-cli.html):

```bash
dogwood validate 02_permit_sql_after_schema_lookup.dw --policy-schema schema.cedarschema
```

The AgentCore-specific guardrail functions (`BedrockGuardrails::*`) are evaluated by the AgentCore service.
The definitive check is `CreatePolicy` reaching `ACTIVE`.
