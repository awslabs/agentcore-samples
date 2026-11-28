# Design decisions

## Container runtime and separate tools

The HTTP Runtime keeps the existing Python/Strands workflow and locked dependency
graph. Container packaging handles native dependencies and OTel instrumentation.
Required Linux ARM64 execution must be tested separately from AMD64 development;
local imports or synthesis are not production parity.

The four tools stay in separate Lambda functions behind Gateway. That keeps
resource permissions and tool schemas separate from model code and preserves
per-tool observability. Tool roles limit reachable AWS resources; they do not
prove a model's proposed business action is appropriate.

## Event-driven processing

SNS decouples synthetic event publication from diagnosis. The trigger validates,
persists and dispatches; the Runtime claims the incident before actions. DynamoDB
holds user/process/ticket/change records and a requester-history GSI. Conditional
writes and claims reduce duplicate actions, but are not an exactly-once guarantee.
A stuck claim or ambiguous external action requires review rather than automatic
state deletion or re-execution. DLQ/alarms preserve operational visibility.

## Cross-session Memory

SUMMARIZATION fits incident-sized episodes. Each session is stored under
`incidents/{actorId}/{sessionId}`, while enrichment uses the requester parent
path. The IAM addition grants only that read on this Memory. Extraction can lag;
DynamoDB history is useful context but does not substitute for Memory evidence.

## Authentication is boundary-specific

IAM is the default for Runtime and Gateway. Optional JWT M2M changes only the
Gateway call: issuer, client, audience and scopes must agree with the actual
runtime token request. It is not a user-facing Runtime JWT upgrade.

Jira remains direct SSE with USER_FEDERATION. Its secret is external and
versioned; a retained ownership record distinguishes verified provider retries
from unrelated collisions. The configured synthetic user, approved Jira tool
schemas and consent are prerequisites, not values inferred from an arbitrary
SNS event. See [authentication](authentication-guide.md).

## Audit policy and guarded application

Both Cedar policies remain LOG_ONLY. Their qualified action/context matching
still needs live review before any enforcement experiment. The optional reason
field is not represented as a guaranteed Gateway denial.

The application fails closed on unavailable/blocked Guardrails and unavailable or
failed tools. It also guards recalled context, tool results and final output.
This does not make all storage/traces PII-safe: content capture for evaluation is
an explicit synthetic-only mode, and instrumentation can observe pre-redaction
input. Do not put real user data in this example.

## Model routing and evaluation

LOW priority uses the fast Haiku configuration; higher priority uses Sonnet.
Cost and quality depend on the actual workload, region and provider pricing;
there is no promised cost-reduction ratio.

The four online evaluators remain enabled in the full scenario. Transaction
Search prerequisites must be actually observed, not assumed from a successful
write. Shared settings are verified read-only by default and retained on Delete.
E2E requires current-run tool/Memory/evaluator evidence, not just a resolved flag.

## One custom stack, fresh only

The custom stack retains all scenario infrastructure and explicit bindings.
Using v1 `AgentCoreApplication` changes some old logical IDs, so this sample
rejects old/unproven deployments instead of claiming a migration mapping.

A new reservation created with CreateStack and nonce ownership tags allows the
native CLI to work against one known StackId. Cleanup verifies that identity and
keeps state until retained resources are reviewed. A single stack is easier to
reason about, but is not atomic across external providers, account settings,
Memory extraction or application side effects.

[Architecture](ARCHITECTURE.md) · [Lifecycle](cli-v1-migration.md)
