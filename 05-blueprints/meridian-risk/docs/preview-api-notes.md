# Notes from building against the preview APIs

Every item here cost real debugging time, and most are not in the documentation.
They are grouped by the service they concern. If you are building on AgentCore
today, this is the most useful page in the repository.

A theme runs through it: **this stack fails quietly.** A wrong response key, a
missing IAM action, or an unfollowed pagination cursor produces an empty list
rather than an error, and an agent with no tools still returns a confident
answer. Several of the entries below were found only because the console
surfaces observed evidence — counts read back off the objects that were really
used — rather than restating configuration.

## Registry

- **Registry records have no Terraform resource.** The registry itself does
  (`aws_bedrockagentcore_registry`), but records are seeded by
  `scripts/seed_registry.py` via a Terraform provisioner.
- **The MCP server descriptor is validated strictly.** Only the MCP
  server-definition schema is accepted — `name`, `description`, `version`, and a
  `remotes` array. Extra top-level keys (`url`, `transport`, `capabilities`) are
  rejected as *"does not match any supported version"*.
- **That descriptor's `description` is capped at 100 characters**, undocumented,
  and an over-long value produces the same generic schema error. Asserted
  explicitly in the seed script.
- **The MCP tools descriptor must be `{"tools": [...]}`**, not a bare array.
- **`UpdateRegistryRecord` wraps nullable fields in `{"optionalValue": ...}`**
  recursively, and inconsistently across branches — `mcp` wraps `server`/`tools`,
  but `a2a` does *not* wrap `agentCard`. The seed script derives the wrapping from
  the botocore shape rather than hard-coding it.
- **`ListRegistryRecords` omits descriptor content**, so the console hydrates each
  record with `GetRegistryRecord`.
- **Semantic search covers APPROVED records only** and lags roughly 30 seconds
  behind approval.
- **`aws_bedrockagentcore_registry` is flagged deprecated** (sunset 2026-09-17)
  with no replacement resource yet, since the service is still in preview.

## Registry consumer — discover then invoke an agent via OAuth

The gated `enable_registry_oauth_demo` slice (infra/registry_oauth_demo.tf,
scripts/discover_and_invoke_via_oauth.py) proves the consumer flow: search the
registry, read the agent's endpoint + OAuth scheme from its A2A card, mint a
machine-to-machine token via AgentCore Identity, and call the agent with a bearer
token. Everything below was found by running it against the live service.

- **A JWT-authorized Runtime is called with a raw OAuth bearer token, no SigV4.**
  `POST https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<url-encoded-arn>/invocations?qualifier=DEFAULT`
  with `Authorization: Bearer <token>` returns 200; the same call with no header
  returns 401. So a runtime with `authorizer_configuration.custom_jwt_authorizer`
  is a first-class OAuth-callable endpoint, distinct from the SigV4 `InvokeAgentRuntime`
  data-plane API. A single runtime has one authorizer, so the OAuth demo adds a
  *twin* runtime rather than converting the console-facing (SigV4) one.
- **`GetResourceOauth2Token` (M2M) authorizes against a CHAIN of resources, one
  revealed per attempt** — exactly like the policy-engine actions. In order:
  `workload-identity-directory/default` (+ `/workload-identity/*`), then
  `token-vault/default`, then
  `token-vault/default/oauth2credentialprovider/<providerName>`. The public
  "synchronize records" doc only shows the last one, which is enough for the
  registry's *sync* role but denies the *consumer* path at the first resource.
- **`GetResourceOauth2Token` reads the provider's client secret from Secrets
  Manager using the CALLER's identity** (a forward-access session), so the caller
  also needs `secretsmanager:GetSecretValue`. AgentCore Identity names the secret
  `bedrock-agentcore-identity!default/oauth2/<providerName>-<id>-<smSuffix>`; a
  policy resource of `…:secret:bedrock-agentcore-identity!default/oauth2/<providerName>-*`
  covers both suffixes. This is undocumented and surfaces only as a generic
  Secrets Manager AccessDenied *after* the bedrock-agentcore authorization passes.
- **Cognito M2M works as a `CustomOauth2` credential provider with `oauth_discovery`
  pointed at the pool's `.well-known/openid-configuration`.** The runtime authorizer
  only needs `allowed_clients = [<m2m client id>]`: Cognito client-credentials access
  tokens carry `client_id` + `scope` and **no `aud`**, so `allowed_audience` is
  unnecessary (and setting it to the client id would reject every token).
- **client_credentials needs a Cognito user-pool *domain*.** The grant runs against
  `https://<domain>.auth.<region>.amazoncognito.com/oauth2/token`; without a
  `aws_cognito_user_pool_domain` there is no token endpoint. The client must be
  confidential (`generate_secret = true`) with a resource-server scope.

## Registry — OAuth discovery (JWT-authorized registry)

The gated `infra/registry_oauth_discovery.tf` + `scripts/discover_via_oauth_registry.py`
prove searching a registry over OAuth instead of IAM.

- **A registry's inbound auth type is immutable and single-valued** — `AWS_IAM` or
  `CUSTOM_JWT`, never both, and it cannot be changed after create. So OAuth discovery
  means a *second* registry; you cannot convert the IAM one the console/seed use.
- **The AWS SDK/CLI cannot search a JWT registry** (they always SigV4-sign). Call the
  data-plane API directly over HTTPS with a bearer token:
  `POST https://bedrock-agentcore.<region>.amazonaws.com/registry-records/search`
  (rest-json body `{"searchQuery": …, "registryIds": ["<jwt-registry-arn>"]}`),
  `Authorization: Bearer <token>`. Verified: 200 with a valid token, **403 without**.
- **Admin CRUDL stays IAM regardless of the registry's inbound auth.** Records are
  still created/approved with boto3; only *discovery* (search/list/MCP) is gated by
  the JWT authorizer. So seeding a JWT registry needs no special handling.
- The token from the M2M credential provider is accepted because the registry's
  `custom_jwt_authorizer.allowed_clients` matches the token's `client_id` claim —
  the same token that invokes the agent also searches the registry.

## Registry — OAuth auto-populate (URL sync, the "Discover & Govern" video flow)

`infra/registry_oauth_sync.tf` + `scripts/seed_sync_record.py`.

- **`CreateRegistryRecord` with `synchronizationType=URL`** and
  `synchronizationConfiguration.fromUrl.credentialProviderConfigurations[].oauthCredentialProvider`
  (`grantType=CLIENT_CREDENTIALS`) makes the *Registry* call the endpoint over OAuth and
  auto-populate the record — omit `descriptors`, they are filled by the sync. Verified:
  5 KYC tools auto-discovered from the gateway with no hand-authored tool list.
- **The sync source must be an OAuth-authorized MCP endpoint.** The IAM Gateway can't be
  a sync source over OAuth, so this adds a second `CUSTOM_JWT` gateway fronting the same
  tool Lambda. The auto-populated tools land in `descriptors.mcp.tools.inlineContent`.
- **A `CUSTOM_JWT` gateway target has no `metadata_configuration` quirk.** That create-time
  inconsistency (see Gateway — tools) is policy-engine-driven; a gateway with no policy
  engine attached creates its target cleanly on the first apply.

## Identity — bring-your-own credential-provider secret (EXTERNAL)

`scripts/manage_oauth_provider.py`.

- **The AWS provider (6.58) can't express `clientSecretSource=EXTERNAL` for the CustomOauth2
  vendor** — `aws_bedrockagentcore_oauth2_credential_provider` only exposes inline
  `client_secret` / `client_secret_wo`. To reference a Secrets Manager secret you own,
  create the provider via the API with `clientSecretSource=EXTERNAL` +
  `clientSecretConfig={secretId, jsonKey}` (a null_resource + script, like Registry records).
- **With EXTERNAL, `GetResourceOauth2Token` reads YOUR secret**, not the
  `bedrock-agentcore-identity!…` managed one — so the caller's `secretsmanager:GetSecretValue`
  must target your secret ARN. (Verified: token mint + agent invoke succeed against the
  customer-owned secret.)
- **Give the EXTERNAL provider a distinct name** from any prior inline-secret provider, or a
  single `terraform apply` will delete the old (TF-managed) one while the script creates the
  new one — same name, racy conflict.

## Gateway — tools

- **Gateway SigV4 must be signed per request.** MCP's streamable-HTTP transport
  sends a different JSON-RPC body on every call, so a set of headers pre-signed at
  connection time returns 401. `lib/gateway.py` signs inside an `httpx.Auth` hook.
- **Attaching an inference target silently emptied the MCP tool list — the
  nastiest bug in this build.** `tools/list` paginates *per target*, and once a
  non-MCP target is attached the **first page comes back empty with a
  `nextCursor` set**, because that target contributes no MCP tools. Strands'
  `list_tools_sync()` fetches one page, so the agents saw zero tools. Nothing
  errored: the specialists ran, answered from the model's priors plus recalled
  Memory, and returned a confident, plausible, *unsourced* verdict —
  `APPROVE / 15` with `tools_called: []`. A guard against exactly this already
  existed (`_filter_tools` logs a warning when nothing matches) and it fired, but
  a warning in CloudWatch is not a failure. `lib/gateway.py`'s `list_all_tools()`
  now follows the cursor; the scoping evidence in the UI is what makes the
  regression visible at a glance.

## Gateway — inference targets

- **Gateway inference targets have no Terraform resource.** The AWS provider's
  `aws_bedrockagentcore_gateway_target` exposes only `mcp` and `http` under
  `target_configuration`; boto3 already knows `inference`. Created here by
  `scripts/manage_inference_target.py` through a provisioner, like Registry records.
- **`bedrock-mantle` is a separate IAM service from `bedrock`.** The connector's
  first act is a `ListModels` call for model discovery, so a gateway role holding
  only `bedrock:InvokeModel*` produces a target that goes `CREATING → FAILED`
  with a 401. It needs `bedrock-mantle:Get*`, `List*`, `CreateInference` on
  `project/*` plus `CallWithBearerToken` on `*` — mirroring the AWS-managed
  `AmazonBedrockMantleInferenceAccess` policy.
- **A FAILED inference target never recovers via update.** Discovery runs once, at
  creation. Fixing the IAM policy afterwards does nothing until the target is
  deleted and recreated, so the provisioner keys on a hash of the gateway policy
  document and deletes-then-creates any target it finds in `FAILED`.
- **`gateway_url` already ends in `/mcp`.** The inference endpoint is a sibling
  path, so build it by trimming that suffix and appending `/inference/v1` rather
  than concatenating — otherwise you get `/mcp/inference/v1`, which 404s.
- **The connector's model ids are its own, not Bedrock's.** `GET
  /inference/v1/models` returns 55 entries named like
  `bedrock-mantle/anthropic.claude-sonnet-5` — no `us.` region prefix, no
  `-20250929-v1:0` suffix, no inference-profile ARNs. A Bedrock model id that
  works for `InvokeModel` 404s here, so the `direct` and `gateway` routes need
  separate identifiers for the same intent (`MODEL_ID` vs `GATEWAY_MODEL_ID`).
- **Which wire format a model speaks is a property of the model.** Claude models
  serve only `/v1/messages` (Anthropic Messages) and return
  *400 "does not support the '/v1/chat/completions' API"* on the OpenAI path;
  DeepSeek/Mistral/Llama/Nova serve `/v1/chat/completions`. So "point the OpenAI
  SDK at the gateway" is not universally true — `lib/inference.py` picks the
  Strands provider from the model id.
- **The `/v1` segment belongs to a different party in each SDK.** The Anthropic
  client appends `/v1/messages` to `base_url`, so its base must end at
  `/inference`; the OpenAI client appends `/chat/completions`, so its base must
  end at `/inference/v1`. Using one convention for both yields
  *400 "Unsupported inference path: /v1/v1/messages"*. `inference_base_url()`
  takes an `include_v1` flag for exactly this reason.
- **The Messages API requires `anthropic_version`, and it must travel as a
  header.** Omitting it entirely is a bare *400 "anthropic_version: Field
  required"*. Supplying it through Strands' `params` instead fails differently
  and later, at runtime, inside the SDK: Strands splats `params` into
  `messages.stream()`, which rejects it as *"unexpected keyword argument
  'anthropic_version'"*. The working form is
  `client_args={"default_headers": {"anthropic-version": "2023-06-01"}}`.
- **The provider SDK's own auth header has to be removed before signing.** Both
  SDKs require *an* `api_key` and turn it into a header — `x-api-key` for
  Anthropic, `Authorization` for OpenAI. Since the Gateway authorizes with
  SigV4, that key is a placeholder, but leaving its header on the request is a
  hard *401 "request must not include both 'authorization' and 'x-api-key'
  headers"*. The signing hook pops `x-api-key` first. This only reproduces
  through the real SDK — hand-rolled `httpx` calls never set it.
- **A gateway-route model instance cannot be shared across threads.** Strands'
  `AnthropicModel` holds an `AsyncAnthropic` client bound to the event loop that
  built it. The orchestrator runs its specialists on a `ThreadPoolExecutor`, so
  the second thread to touch a shared instance dies with *"Event loop is
  closed"*. The orchestrator therefore calls `build_model()` per agent rather
  than once in `__init__` — cheap, since the constructors do no I/O. The direct
  `BedrockModel` route never showed this, so it only surfaced after the tool
  plane started working again and both specialists ran concurrently.
- **The newest Claude models reject `temperature`.** `claude-sonnet-5` returns
  *400 "`temperature` is deprecated for this model"*, so the gateway route sends
  no sampling parameters and relies on the prompts (which already demand strict
  JSON) for determinism. The `direct` route still sets `temperature=0.1`,
  because the older inference-profile model it targets accepts it — another way
  the two routes are not interchangeable in their details even though the agent
  code above them is identical.
- **Both surfaces stream.** `"stream": true` yields SSE on each: OpenAI-shape
  `choices[].delta.content` chunks, Anthropic-shape `content_block_delta`
  events. Token usage is reported on both (Anthropic's includes cache-read and
  thinking-token breakdowns).

## Policy and guardrails

- **Guardrails do not attach to an inference target.**
  `targetConfiguration.inference` is a tagged union accepting only `connector` or
  `provider`; passing a `guardrail` key fails botocore's *client-side* validation
  (a `ParamValidationError`, so it never reaches the service and is not catchable
  as a `ClientError`). Guardrails bind to the *gateway* through
  `policyEngineConfiguration` — an AgentCore Policy Engine whose Cedar policies
  carry `when guardrails { BedrockGuardrails::… }` conditions.
- **`when guardrails { … }` is documented but not yet available in us-east-1.**
  `CreatePolicy` rejects it with *"unexpected token `guardrails`"*. Verified by
  isolation: the identical statement without the guardrails block parses and
  creates successfully, so it is the construct rather than the policy. The docs
  list guardrails-in-policy as available in five regions including us-east-1, so
  treat that table as aspirational and probe before designing around it. What
  *does* work is ordinary Cedar authorization — `permit`/`forbid` on
  `AgentCore::Action::"<target>___<tool>"` with `when`/`unless` conditions over
  `context.input.<field>`, which the service derives from the gateway's tool
  input schemas.
- **A Cedar guardrail condition does not reference a guardrail you created.**
  `BedrockGuardrails::ContentFilter([...], [...])` is a *built-in* function: the
  Policy data plane calls `bedrock:InvokeGuardrailChecks` with the categories and
  thresholds written inline in the policy. So an `aws_bedrock_guardrail` resource
  is **not** what the Policy Engine enforces — the categories are declared twice,
  once in the guardrail (as a reviewable artifact, and for direct `InvokeModel`
  callers) and once in Cedar (as the gateway's enforcement point).
- **A wildcard resource is rejected outright.** `CreatePolicy` requires the
  resource to be constrained either to a specific `AgentCore::Gateway` or to the
  `AgentCore::Gateway` resource type.
- **A policy engine in `ENFORCE` mode is default-deny.** It blocks every action no
  policy explicitly permits, so attaching one without a baseline `permit` takes
  down all tool calls *and* the inference path at once. Cedar's `forbid` beats
  `permit`, which is what makes a broad baseline permit safe next to targeted
  forbid policies. Start in `LOG_ONLY`, confirm the action names against real
  traffic, then flip.
- **Attaching a policy engine needs four IAM actions, and the first error names the
  wrong one.** `UpdateGateway` fails with *"Access denied while calling
  GetPolicyEngine … Confirm this role has bedrock-agentcore:GetPolicyEngine
  permissions"*. Granting exactly that changes nothing. Re-issuing the call
  **directly on the CLI** reveals the real chain, one action per attempt:
  `AuthorizeAction` on the policy-engine ARN → then on the *gateway* ARN → then
  `PartiallyAuthorizeActions`. `iam simulate-principal-policy` says "allowed" for
  the action the original error names, which compounds the misdirection. The
  working policy is documented at
  [policy-permissions.html](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/policy-permissions.html)
  — find it before iterating. Two of these are **permission-only actions** with no
  API operation, so they cannot be discovered from the botocore service model,
  only from the service-authorization reference.
- **`bedrock:InvokeGuardrailChecks` must be on the *gateway execution role*, with
  `Resource: "*"`.** The Policy data plane uses Forward Access Session credentials
  derived from that role, and the built-in checker is not scoped to a guardrail
  resource, so this permission cannot be narrowed to your own guardrail ARN.
- **`PartiallyAuthorizeActions` is what authorizes `tools/list`**, not just
  `tools/call` — it is described as "partial evaluation … to authorize a caller to
  list tools they are allowed to call". Omit it and tool *discovery* breaks under a
  policy engine, which looks like the pagination bug rather than a permissions one.
- **`ListPolicies` returns its results under `policies`, not `items`** — unlike
  `ListGatewayTargets` and `ListRegistryRecords`, which both use `items`. Reading
  the wrong key yields an empty list rather than an error, which made created
  policies look absent and made a cleanup script silently no-op. Five stray probe
  policies stayed live and ACTIVE that way, and one of them denied
  `sanctions_screen` for a whole assessment — the verdict still came back REJECT,
  so nothing looked broken. Check the response shape per operation in this API;
  it is not uniform.
- **Terraform and the API race on IAM propagation here.** When the same apply both
  updates the gateway role and attaches the engine, the attach runs against stale
  IAM and fails. Applying `-target=aws_iam_role_policy.gateway` first, waiting,
  then attaching is what actually converges.

## Memory

- **SUMMARIZATION memory strategies require `{sessionId}` in the namespace**;
  SEMANTIC ones aggregate per actor and do not.
- **Memory's semantic extraction favours USER turns.** Writing the verdict as an
  assistant reply yielded the useless fact *"the user requested an assessment"*, so
  `record_assessment` states the outcome on the USER turn.
- **Extraction is asynchronous** and can lag minutes — too slow for a live demo.
  `recall_prior_assessments` merges extracted records with raw events, which are
  durable immediately.
- **A recall count is not a history count.** `recall_prior_assessments` caps at
  `top_k=5`, so reporting only its length made the console say "5 recalled from
  Memory" for a customer with 27 assessments on record — read by a demo audience
  as "assessed 5 times." How many times an applicant has been reviewed is itself
  a compliance-relevant fact, so the panel shows "5 of 27 (most relevant)" and
  `count_assessment_sessions` supplies the denominator. Any capped retrieval
  surfaced in a UI has this trap.

## Tooling

- **`boto3` ≥ 1.42 is required** — older versions do not know the
  `bedrock-agentcore` service models at all.
