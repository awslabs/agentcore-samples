# Platform mechanics

The five AgentCore services, and what each one is doing that a slideware demo
would skip — plus the design decisions behind how they are wired together.

> **A note on freshness.** AgentCore is in preview and its services gain
> features quickly. This document goes deeper than the [README](../README.md)
> deliberately: the specifics here (API shapes, what a given Region accepts,
> which models a connector fronts) are true as observed while building, and are
> the parts most likely to change. Where behaviour surprised us, the
> [preview-API notes](preview-api-notes.md) record it in more detail.

## Contents

- [AgentCore Gateway — one endpoint, two planes](#agentcore-gateway--one-endpoint-two-planes)
- [The LLM-gateway pattern](#the-llm-gateway-pattern)
- [AgentCore Policy — enforceable authorization](#agentcore-policy--enforceable-authorization)
- [Binding the Bedrock Guardrail](#binding-the-bedrock-guardrail)
- [Agent Registry — governed catalog](#agent-registry--governed-catalog)
- [AgentCore Runtime — multi-agent orchestration](#agentcore-runtime--multi-agent-orchestration)
- [AgentCore Harness — the managed agent loop](#agentcore-harness--the-managed-agent-loop)
- [AgentCore Memory — cross-session history](#agentcore-memory--cross-session-history)
- [Design decisions](#design-decisions)

---

## AgentCore Gateway — one endpoint, two planes

The Gateway is usually described as the tool plane. Since it gained **inference
targets**, the same gateway can also front model invocation — which makes it the
natural control point for a platform serving multiple teams.

| Target | Kind | Serves |
|---|---|---|
| `kyc-tools` | MCP | Five KYC data tools over `tools/list` and `tools/call` |
| `bedrock-mantle` | inference | Foundation models on `/inference/v1`, discovered through the connector |

Inbound authorization is `AWS_IAM`, so the Runtime's own execution role
authorizes tool calls with SigV4 — no Cognito resource server, machine client,
or token vault. Signing happens **per request**, because MCP's streamable-HTTP
transport sends a different JSON-RPC body on every call and a pre-signed header
set returns 401.

## The LLM-gateway pattern

Setting `INFERENCE_ROUTE=gateway` (the default) points every agent's model
provider at the Gateway's `/inference` endpoint instead of Bedrock. What that
buys:

- **One endpoint, one credential scheme.** Teams SigV4-sign to the Gateway
  rather than holding IAM permission to invoke Bedrock. The Gateway brokers
  provider credentials outbound, so for third-party providers a team never sees
  an API key.
- **One place to enforce policy.** Every model call traverses the same
  authorization point as every tool call.
- **One place to attribute cost and audit**, across every provider the gateway
  fronts. This solution uses `bedrock-mantle`; the connector list also includes
  `openai` and `anthropic`.

Flip `INFERENCE_ROUTE=direct` to run the identical agent code against Bedrock
without the Gateway. That the code does not change is the point of the pattern.

The two routes are not interchangeable in their details, which is worth knowing
before adopting this: the connector's model ids are its own (no `us.` prefix, no
version suffix), Claude models serve only `/v1/messages` while others serve
`/v1/chat/completions`, and the newest Claude models reject `temperature`
outright. All of it is in [preview-api-notes.md](preview-api-notes.md).

> **Caveat for multi-team serving.** AgentCore Gateway sets no service-level cap
> on stream duration or response size. Without a per-target token-limit policy,
> one team's high-`max_tokens` requests can exhaust the shared provider TPM quota
> for everyone on that target — RPM throttling caps request count, not
> per-request cost.

## AgentCore Policy — enforceable authorization

A Policy Engine attached to the Gateway evaluates Cedar policies on every
request, in `ENFORCE` mode. This is the enforceable half of governance:
per-agent tool scoping in the orchestrator is *cooperative* — it holds because
the code honours the skill definition — whereas a Cedar `forbid` holds because
the Gateway refuses the call before the tool Lambda runs.

Proven in both directions against the deployed stack:

```
tools/call get_customer_profile(CUST001) -> ALLOW
tools/call get_customer_profile(CUST999) -> DENY
  "Tool Execution Denied: Tool call not allowed due to policy enforcement
   [Policy evaluation denied due to kyc_agentcore_forbid_unknown_customers]"
```

A full CUST003 assessment still completes under `ENFORCE` with all five tools
and `REJECT / 98`, so the rail blocks what it should and nothing else.

Two properties to design around:

- **`ENFORCE` is default-deny.** It blocks every action no policy explicitly
  permits, so attaching an engine without a baseline `permit` takes down all
  tool calls *and* the inference path at once. Cedar's `forbid` beats `permit`,
  which is what makes a broad baseline safe alongside targeted denials. Start in
  `LOG_ONLY`.
- **A Cedar guardrail condition does not reference a guardrail you created.**
  `BedrockGuardrails::PromptAttack` is a *built-in* that calls
  `bedrock:InvokeGuardrailChecks` with categories written inline in the policy.

## Binding the Bedrock Guardrail

A Bedrock Guardrail — PII anonymization, prompt-injection filtering, denied
topics — is deployed and versioned as a reviewable artifact, and applies to
callers that invoke Bedrock directly.

For **gateway** traffic, an AgentCore Policy is the *only* mechanism that binds a
guardrail: the inference target's configuration is a tagged union accepting only
`connector` or `provider` — there is no guardrail field — so the guardrail rides
in through a Cedar `when guardrails { BedrockGuardrails::… }` condition on the
policy engine. That statement is written and ready in `infra/policy.tf`, gated
behind `enable_guardrail_binding_policy` (default off).

It is gated because **the `when guardrails` Cedar extension is not yet live in
the public `CreatePolicy` parser.** Verified against fresh policy engines in four
Regions — `us-east-1`, `eu-west-2`, `ap-northeast-1`, `ap-southeast-2` — each of
which rejected it at the lexer with *"unexpected token `guardrails`"*, while a
plain Cedar statement on the same engine parsed through to semantic validation.
The AI authoring path (`start-policy-generation`, which `agentcore add policy`
drives) likewise returns *"cannot be expressed"*. So this is a service-level
preview rollout gap — not an account or Region entitlement, and not something
`validation_mode` can suppress, since the token is rejected before findings run.
The devguide marks these Regions available; the API surface has not caught up.

The engine, its IAM (`bedrock:InvokeGuardrailChecks`), and the gateway
attachment are all in place, so binding is a one-flag change once AWS ships the
extension. The console and diagram reflect this state rather than implying more
than is enforced today.

## Agent Registry — governed catalog

AWS Agent Registry (preview) is the governed catalog for AI resources; AWS is
migrating it from the `bedrock-agentcore` namespace to a dedicated
`agent-registry` one, so you may see it referred to either way.

Four records describe the deployed system: an A2A agent card for the
orchestrator, an MCP server record for the Gateway, and one `AGENT_SKILLS`
record per specialist. Auto-approval is left off so the demo can walk
`DRAFT → PENDING_APPROVAL → APPROVED`; semantic search covers approved records
only.

The records are **generated from the same objects the agents run on**, so the
catalog cannot describe a capability the system does not have. That single-
sourcing is the interesting part:

| Contract | Single source | Drives |
|---|---|---|
| Gateway tools | `backend/gateway/tool_spec.json` | The Terraform tool schemas *and* the MCP Registry record |
| Agent skills | each specialist's `SKILL` object | Its system prompt, the tools the orchestrator grants it, *and* its `AGENT_SKILLS` record |

Renaming a skill or rescoping its tools updates the Registry automatically — the
frontmatter name and the "Tools required" section are generated, not
hand-maintained.

## AgentCore Runtime — multi-agent orchestration

An ARM64 container running a Strands workflow. The orchestrator recalls history,
runs both specialists on a thread pool, synthesizes the verdict, and persists
it. A failed synthesis returns `ESCALATE`, never a silent `APPROVE` — an
unparsable model response must not read as approval.

### Seeing the skills take effect

A demo that only *claims* its agent skills matter is not worth much, so each
specialist panel reports its own tool scope as observed at runtime:

![Tool scope](assets/tool-scope.png)

Both specialists connect to the same Gateway with the same credentials. The only
reason the Credit Analyst never calls `sanctions_screen` is that its skill does
not list it, so the orchestrator never handed it over — shown struck through. A
green check means granted *and* invoked on this run.

None of those values are restated constants. The granted list is read back off
the tool objects actually passed to the agent, the withheld set is computed as
advertised-minus-granted, and the invoked set comes from the agent's own message
trace. If the skills were inert, every panel would show all five tools.

This mattered more than expected. When an unrelated change silently emptied the
gateway's tool list, the specialists still returned a confident, plausible,
*unsourced* verdict — and the only visible signal was this panel showing zero
granted tools. Observed evidence beats restated configuration.

Two further signals in the same output: the specialists return *different JSON
shapes* (`score`/`level`/`factors` versus `status`/`checks_failed`/`edd_required`),
from the response contracts in their prompts; and only the Compliance Officer
cites 31 CFR 1020.320 and FATF Recommendation 12, rules written into its prompt
and absent from the other.

## AgentCore Harness — the managed agent loop

Every agent has an orchestration loop — call the model, pick tools, feed results
back, manage context, handle failures — and in production that loop needs compute,
a sandbox, secure tool connections, memory, identity, and observability under it.
AgentCore **Harness** is that whole layer as a managed service: you declare the
model, tools, skills, and instructions, and AgentCore runs the loop in an isolated
microVM per session. Trying a different model or adding a tool is a config change,
not a code rewrite. (The harness is itself powered by Strands, the same framework
the Runtime uses.)

This solution runs **both**, deliberately, so the trade-off is visible:

| | AgentCore Runtime (this repo's `backend/agent`) | AgentCore Harness (`infra/harness.tf`) |
|---|---|---|
| The loop | Owned in code — a Strands multi-agent workflow (two specialists in parallel, then a supervisor) | Managed by AgentCore from a declaration |
| You provide | A container image and orchestration logic | Model + system prompt + tools, as config |
| Best when | You need custom orchestration (our supervisor/parallel pattern) | A straightforward tool-calling agent is enough |

AWS's own guidance is to **use the harness unless you need to own the loop** — and
this KYC assessment's parallel-specialists-then-synthesis shape is exactly a case
that needs to own it, which is why the Runtime is the primary path. The harness is
included as the declarative counterpart: the *same* KYC assistant, wired to the
*same* governed surfaces —

- the **same AgentCore Gateway** (its five KYC tools over MCP), reached with the
  harness's own IAM role and `aws_iam` outbound auth, so **AgentCore Policy
  authorizes the harness's tool calls exactly as it does the Runtime's** — the
  `forbid_unknown_customers` rail holds for both; and
- the **same Bedrock model**, with the loop capped (`max_iterations`,
  `max_tokens`, `timeout_seconds`) so a runaway assessment cannot spend unbounded
  tokens or time.

So the harness exercises four of its capabilities: **model**, **tools** (the
Gateway), **memory** (its own managed short/long-term store — a resource distinct
from the Runtime's shared KYC memory, which the service provisions per harness),
and **skills**.

### The skill, and a provider gap worth knowing

Agent **Skills** are [AgentSkills.io](https://agentskills.io) bundles — a
`SKILL.md` with YAML frontmatter plus optional scripts/references — that give the
agent domain method on demand through progressive disclosure (the metadata sits
in the system prompt; the full instructions load via a tool call only when
needed). The KYC onboarding skill (`backend/harness/skills/kyc-onboarding-assessment/SKILL.md`)
captures the assessment method — evidence to gather, sanctions/PEP/structuring
rules, and the compliance-dominates-credit decision logic.

It ships in **S3** (`infra/harness.tf` creates the bucket and uploads the bundle;
the harness role gets `s3:GetObject`/`s3:ListBucket`) and is attached **at invoke
time** — `invoke_harness(skills=[{s3: {uri}}])` — rather than persisted on the
harness resource. That is a provider limitation, not a preference: the AWS
provider's harness resource models only the `path` skill source, and attaching an
`s3` skill to the resource makes the provider's next *read* fail outright
(`reading Bedrock AgentCore Harness: Unsupported Type — skill flatten:
HarnessSkillMemberS3`), which breaks `plan`/`apply` for the whole stack —
`lifecycle ignore_changes` cannot help, because the failure is at refresh, not
diff. Passing the skill on the call loads the same bundle without mutating the
config Terraform reads. `scripts/manage_harness_skill.py` does this and is the
skill smoke test: it prints the tools the loop called (watch for `skills`, the
progressive-disclosure loader) and the verdict. Persist the skill on the resource
once the provider learns the `s3` source.

The harness resource itself is provider-native (`aws_bedrockagentcore_harness`)
and gated behind `var.enable_harness` (default on). The console surfaces its id
on the architecture tab; invoke it with the `InvokeHarness` API (which requires
both `bedrock-agentcore:InvokeHarness` and `bedrock-agentcore:InvokeAgentRuntime`
on the harness ARN, since a harness runs on AgentCore Runtime underneath).

## AgentCore Memory — cross-session history

Assessments are keyed to the corporate customer rather than the analyst, so any
reviewer looking at CUST003 sees the same history — which is what a bank
actually needs.

Two layers are read and merged: long-term records extracted by the semantic
strategy, and raw short-term events. Extraction is asynchronous and can lag
minutes, too slow for a live demo, so the raw events fill that window.

Recall is capped at `top_k`, which is a reporting trap worth naming: showing
only the recalled count made the console say "5 recalled from Memory" for a
customer with 27 assessments on record — read by an audience as "assessed 5
times." How many times an applicant has been reviewed is itself a
compliance-relevant fact, so the panel reports **"5 of 27 (most relevant)"**.

---

## Design decisions

- **AWS_IAM authorization everywhere**, so there is no Cognito resource server,
  machine client, or token vault between the agents and the Gateway. For a
  reference implementation this removes an entire subsystem while keeping the
  security story honest — the Runtime's execution role is the only credential.
- **Memory is scoped to the customer, not the analyst**, because that is what a
  reviewer needs.
- **Tools are scoped per specialist**, so the tool-call trace shows a real
  division of labour rather than both agents calling everything.
- **A failed synthesis escalates rather than approves.**
- **Model instances are built per agent, not shared.** The gateway route's
  provider SDK binds an async client to the event loop that created it, and the
  specialists run on separate threads.
- **The console reports observed evidence, not configuration** — the property
  that caught a silent tool-plane regression.
