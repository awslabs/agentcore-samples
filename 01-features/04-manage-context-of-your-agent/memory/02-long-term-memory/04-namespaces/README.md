# Namespaces

A namespace is the path a long-term record is written to. You set it per strategy as a _template_; the service resolves it per event. It is both the retrieval scope and the IAM boundary, and existing records are never re-pathed — so the shape is a day-one decision.

| Variable                                | Resolves from                                       | Notes                                             |
| --------------------------------------- | --------------------------------------------------- | ------------------------------------------------- |
| `{actorId}`                             | `CreateEvent` `actorId`                             |                                                   |
| `{sessionId}`                           | `CreateEvent` `sessionId`                           | required in a summary strategy's template         |
| `{memoryStrategyId}`                    | the strategy itself                                 |                                                   |
| your own — `{tenantid}`, `{agentid}`, … | `CreateEvent` `extractionConfig.namespaceVariables` | must be declared in `namespaceKeys` on the memory |

Templates start **and** end with `/`. The trailing slash is what stops `/users/alice/` from also matching `/users/alice2/`, and a retrieval argument without it is rejected.

## Organising with the built-in variables

`namespaces-and-organization.py` gives three strategies one actor-first tree:

| Strategy        | Template                                 |
| --------------- | ---------------------------------------- |
| semantic        | `/users/{actorId}/facts/`                |
| user preference | `/users/{actorId}/preferences/`          |
| summary         | `/users/{actorId}/sessions/{sessionId}/` |

Retrieval then reads that tree at whatever depth the question needs:

| Query                             | Returns                            |
| --------------------------------- | ---------------------------------- |
| `namespace="/users/alice/facts/"` | exact — one user, one strategy     |
| `namespacePath="/users/alice/"`   | prefix — everything about one user |
| `namespacePath="/users/"`         | prefix — every user                |

The leading segment decides what a prefix query can group. Actor-first (above) makes "everything about one user" cheap; type-first (`/facts/{actorId}/`) makes "all users' facts" cheap. Pick the one that matches your dominant query.

## Custom variables: flexible namespaces

Dimensions that are neither the actor nor the session — tenant, agent, region, tier — get their own variables. Two API pieces:

| Where                           | Field                                 | Does                                                                      |
| ------------------------------- | ------------------------------------- | ------------------------------------------------------------------------- |
| `CreateMemory` / `UpdateMemory` | `namespaceKeys`                       | declares the variables templates may use, each with optional `validation` |
| `CreateEvent`                   | `extractionConfig.namespaceVariables` | supplies this event's values                                              |

`flexible-namespaces.py` runs a trip-planning crew — one tenant, one traveler, one session, three specialist agents — on two custom keys:

```jsonc
"namespaceKeys": [
  {"key": "tenantid", "validation": {"allowedValues": ["acme", "globex"]}},
  {"key": "agentid",  "validation": {"regexPattern": "^(flight|hotel|activity|agent-[a-z0-9-]+)$"}}
]
```

| Strategy        | Template                                                               | Audience    |
| --------------- | ---------------------------------------------------------------------- | ----------- |
| user preference | `/tenants/{tenantid}/travelers/{actorId}/shared/preferences/`          | every agent |
| summary         | `/tenants/{tenantid}/travelers/{actorId}/shared/sessions/{sessionId}/` | every agent |
| semantic        | `/tenants/{tenantid}/travelers/{actorId}/agents/{agentid}/findings/`   | one agent   |

```jsonc
// the flight agent's turn -> /tenants/acme/travelers/traveler-priya/agents/flight/findings/
"extractionConfig": {"namespaceVariables": {"tenantid": "acme", "agentid": "flight"}}
```

There is no wildcard: a strategy is crew-wide precisely because its template doesn't name `{agentid}`. One prefix query then serves each audience — `/tenants/acme/` for the tenant, `…/shared/` for the crew, `…/agents/hotel/` for one agent. Scope summaries by session and semantic facts by agent, not the reverse: summarisation aggregates the whole session, so a per-agent summary path writes near-identical content under every agent.

### Limits

|              |                                                                                                                                                                     |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| key name     | `[a-z][a-z0-9]*`, max 32 — no hyphens or underscores, and never `actorId` / `sessionId` / `memoryStrategyId`                                                        |
| value        | `[a-z0-9][a-z0-9-_]*`, max 64 — hyphens fine here, uppercase never                                                                                                  |
| counts       | max 5 `namespaceKeys` per memory, max 5 `namespaceVariables` per event, max 10 `allowedValues`, `regexPattern` max 64 chars                                         |
| `validation` | AND semantics — with both rules a value must be in `allowedValues` _and_ match `regexPattern`. Rules only narrow, so to widen, use one rule that admits everything. |

### Gotchas

- **A missing variable is not an error — it's silent data loss.** `CreateEvent` succeeds and templates that don't use the variable still resolve, but the strategy whose template can't be resolved extracts nothing, and nothing reports it. Validate at your call site.
- **A rejected value drops the whole event**, short-term turns included — not just that strategy's records.
- **An undeclared variable in `namespaceVariables` is silently ignored.** Only a _template_ referencing an undeclared variable is rejected, and that happens on `CreateMemory` / `UpdateMemory`.
- **`namespaceKeys` is a full-replacement list, minimum 1 entry.** Omit it on `UpdateMemory` to keep the current set; there is no "clear every key" call.
- **You can't drop a key any strategy still references** — the error names it, and `UpdateMemory` is atomic. Retiring one takes two calls: release it from every template, then drop it. Deleting a strategy doesn't release its keys; they stay behind as dangling ones.
- **When `modifyMemoryStrategies` sets a template that uses custom variables, resend `namespaceKeys` in the same call.** Validation reads the request, not the stored set, so an otherwise-valid template is rejected with `namespace variables […] missing in namespaceKeys`.
- **`namespaceVariables: {}` is rejected** (minimum 1 entry). An entirely untagged event must omit `extractionConfig`.
- **Extraction reads the template as of the time it runs**, so retemplate between traffic — do it with events still pending and one session splits across the old and new shapes.

## Run

```bash
pip install "boto3>=1.43.75" "bedrock-agentcore>=1.14"   # namespaceKeys first modelled in boto3 1.43.75

python namespaces-and-organization.py boto3   # default — direct service calls
python namespaces-and-organization.py sdk     # AgentCore MemorySessionManager
python flexible-namespaces.py                 # boto3 only — see below
```

Add `--cleanup` to delete the memory resource at the end.

`flexible-namespaces.py` has no `sdk` mode: the AgentCore SDK models neither `namespaceKeys` nor `namespaceVariables`. On boto3 older than 1.43.75 it fails locally with `ParamValidationError` — botocore validates against its bundled model before sending, and an older model also strips the unmodelled _response_ member, so `namespaceKeys` reads back empty even when the write succeeded.

## Best practices

- **Lead with the dimension you scope by.** Whatever you'll query broadly and write IAM conditions against has to be a real path segment, and you get one chance to place it.
- **Model real dimensions as custom variables**, not as string concatenation inside `actorId`. An `actorId` may contain `/`, so `"tenantA/user1"` does resolve into path segments — but it overloads identity with tenancy, and `validation` can't guard it.
- **Put the shared/private boundary in the path, not in application code.** A template naming `{agentid}` is private to one agent; one that doesn't is crew-wide — which makes the boundary IAM-enforceable instead of conventional.
- **Constrain every variable that comes from user input or an upstream system** with `validation`, and check at your call site that you're sending all of them. It's the cheapest guard against unbounded fan-out and against the silent drop above.
- **Declare keys before you need them.** Dangling keys cost nothing; adding one mid-rollout is a control-plane update you'd rather not make.
- **Prefer metadata over a deeper tree** for orthogonal attributes you only filter on ([`../06-record-metadata/`](../06-record-metadata/)). Namespace = ownership and scope; metadata = attributes.
- **Pair with IAM.** Once the shape is fixed, scope runtime roles with `bedrock-agentcore:namespace` / `namespacePath` conditions — see [`../../05-security/01-iam-scoped-access/`](../../05-security/01-iam-scoped-access/).

## AWS CLI walkthrough

The built-in-variable flow. `aws-cli` 2.34 exposes only `--namespace` on `retrieve-memory-records`, and no `--namespace-keys` / `--extraction-config`; prefix queries and flexible namespaces need a newer CLI, or boto3 today.

```bash
# 1. Create memory
MEMORY_ID=$(aws bedrock-agentcore-control create-memory \
  --region "$AWS_REGION" --name "NamespacesCli_$(date +%s)" \
  --event-expiry-duration 30 --client-token "$(uuidgen)" \
  --memory-strategies '[{"semanticMemoryStrategy":{"name":"Facts","namespaceTemplates":["/facts/{actorId}/"]}}]' \
  --query 'memory.id' --output text)


# Wait until ACTIVE. CreateEvent is rejected while the memory is still CREATING,
# and creation takes a couple of minutes. This also exits on FAILED, so it cannot hang.
while [ "$(aws bedrock-agentcore-control get-memory --region "$AWS_REGION" \
    --memory-id "$MEMORY_ID" --query 'memory.status' --output text)" = CREATING ]; do
  sleep 10
done

# 2. Wait for ACTIVE — CreateEvent fails while the memory is still CREATING
until [ "$(aws bedrock-agentcore-control get-memory --region "$AWS_REGION" \
  --memory-id "$MEMORY_ID" --query 'memory.status' --output text)" = "ACTIVE" ]; do sleep 5; done

# 3. An event — actorId and sessionId are what the templates substitute
aws bedrock-agentcore create-event \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --actor-id alice --session-id sess-1 \
  --event-timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --payload '[{"conversational":{"role":"USER","content":{"text":"I lead the data platform team in Berlin."}}}]'
sleep 90

# 4. Retrieve from the resolved path — templates are never queryable
aws bedrock-agentcore retrieve-memory-records \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" \
  --namespace "/users/alice/facts/" \
  --search-criteria '{"searchQuery":"what does alice work on","topK":5}'

# 5. Teardown
aws bedrock-agentcore-control delete-memory \
  --region "$AWS_REGION" --memory-id "$MEMORY_ID" --client-token "$(uuidgen)"
```
