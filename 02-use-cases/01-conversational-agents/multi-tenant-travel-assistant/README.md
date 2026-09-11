# Multi-tenant travel assistant

> **The code for this sample lives in its own repository:**
> **[aws-samples/sample-multi-tenant-travel-assistant](https://github.com/aws-samples/sample-multi-tenant-travel-assistant)**

A corporate travel assistant that serves two tenant companies from a single pooled serverless
deployment, demonstrating tenant isolation as a property of the architecture rather than a promise in
the prompt.

Two travelers ask the same agent the same question and get different, correct answers:

```
Priya (Globex)   "What's my hotel nightly cap?"   ->  $250.00 USD, 4-star maximum
Sam (Initech)    "What's my hotel nightly cap?"   ->  EUR 150.00 per night, 3-star maximum
```

Same agent, same tool, same arguments. Nothing in the question says which company, and nothing the
model can influence decides it: tenant identity arrives from a verified JWT claim injected
server-side, after the model has finished choosing what to call.

## What it demonstrates

Isolation is enforced at independent layers, so no single decision has to be correct for the property
to hold:

| Layer | What it refuses |
|-------|-----------------|
| Cognito claims | A token for one tenant does not carry another's `custom:tenant_id`. Immutable, so a traveler cannot edit their own tenancy |
| Cedar at the Gateway | Policy engine in `ENFORCE`; a call with no verified tenant tag matches no permit and is denied before the tool Lambda runs |
| Gateway interceptor | A forged `X-Tenant-Id` header is overwritten, not validated. Validation invites a bypass; overwriting has none |
| Tool schemas | No tenant field exists to supply. Identity arrives in the Lambda client context, unreachable from anything the model shapes |
| IAM row-scoping | `dynamodb:LeadingKeys` on a per-request role assumed with a tenant session tag. The read is impossible, not merely audited |
| Knowledge base | Per-tenant metadata filter built server-side, so retrieval cannot cross tenants |

The two tenants differ in behavior rather than in configuration: one confirms bookings in chat, the
other refuses at the tool and returns a checkout link instead. That difference is a real per-customer
capability boundary, not a feature flag.

## AgentCore features used

Runtime, Gateway, Memory, Policy (Cedar), Identity (Cognito), Guardrails, Observability, and
Evaluations. 14 tools across 9 Lambda gateway targets, retrieval-augmented generation with a
per-tenant knowledge base filter, short and long term memory, response streaming, per-turn cost
attribution by tenant, an evaluation suite behind a CI gate, and human escalation.

Built with Strands Agents and AWS CDK.

## Where the code lives

The full sample is in
[aws-samples/sample-multi-tenant-travel-assistant](https://github.com/aws-samples/sample-multi-tenant-travel-assistant),
published under MIT-0. That repository carries the CDK infrastructure, the agent, the tool Lambdas,
the React frontend, the test and evaluation suites, and a one-command deploy.

## Before you adopt it

It is sample code for learning, not a production-ready artifact, and every fixture record is
synthetic. Note in particular that all of the controls above defend the **application**, not the
service plane: the tenant session tag is asserted by the service, so code running with the service's
own permissions can assert any tenant's tag. That is a property of pooled tenancy rather than a gap in
the implementation. The repository's README covers this in
"[Pooled tenancy, and when to choose silos instead](https://github.com/aws-samples/sample-multi-tenant-travel-assistant#pooled-tenancy-and-when-to-choose-silos-instead)",
along with the compliance obligations an adopter inherits in
"[Handling real traveler data](https://github.com/aws-samples/sample-multi-tenant-travel-assistant#handling-real-traveler-data)".
