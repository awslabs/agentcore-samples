# Security and responsible-use guide

This sample demonstrates agent-to-agent discovery and delegation with AWS Agent Registry and Amazon Bedrock AgentCore. Read this before adapting it, because the autonomy posture it demonstrates is safe only under the conditions below. The design is mapped to the AWS Well-Architected Security pillar and the Well-Architected Agentic AI Lens.

## Required conditions for the push (auto-invoke) path

The push path auto-invokes a peer agent the moment its registry record reaches `APPROVED`, with no human at invoke time. That is a legitimate AWS Agentic AI Security Scoping Matrix **Scope 3 (supervised agency)** pattern, but only when both conditions hold. Copying this pattern without them teaches an unsafe default.

1. **The invoked capability must be read-only or reversible, and low-consequence.** Auto-invoking an agent that analyzes or enriches data is in the autonomous tier (AGENTREL02-BP05 classifies autonomous actions as low-risk and reversible). An agent that mutates external state, moves money, or takes irreversible action must NOT be auto-invoked. route it to a notify/approve tier with human review (AGENTREL02-BP05, AGENTSEC04-BP02). The sample's peer is a `greet` skill, trivially reversible, which is why auto-invoke is appropriate here.

2. **The `APPROVED` gate must be human-adjudicated.** Approval is the one human checkpoint in the push chain, which makes it the Scope 2 to Scope 3 boundary. If the registry uses auto-approval (records reach `APPROVED` with no curator), there is no human anywhere in the path, which collapses the supervised-agency posture regardless of how reversible the capability is. **Auto-approval is unsupported for the auto-invoke pattern.** Keep approval human-adjudicated, or scope auto-approval narrowly to capabilities you have pre-evaluated.

Before relying on a discovered peer in production, gate `APPROVED` on an evaluation attestation (AGENTOPS06-BP03, SME validation) so a record that passed governance approval was also behaviorally evaluated, and add coordination-anomaly monitoring on the invoke path (AGENTSEC06-BP04).

The pull path (an orchestrator searches and invokes on its own initiative) does not carry this risk, because invocation is initiated by the standing agent's own logic, not triggered automatically by a third party's approval.

## Defense in depth (three layers, "apply security at all layers")

No single layer carries the trust decision. Each defends a distinct failure:

1. **Registry approval gates discoverability.** The data plane returns only approved records, so an un-vetted agent is never surfaced. This is the admission control (change-governance gate), and the provenance check for the discovered card: a consumer only ever reads a card that passed governance.
2. **Account-scoped target check.** Every AgentCore runtime shares one endpoint host, so a host allowlist is vacuous. the meaningful check is the peer runtime ARN's account, read from the card, against an allowed set (AGENTSEC06-BP03 trust boundary, enforced at the application layer).
3. **IAM resource-based policy (the enforced gate).** Invocation is authorized by a resource-based policy on the callee (peer runtime and its DEFAULT endpoint) naming the consumer-group role, paired with an explicit deny for all other principals. The caller holds no standing `InvokeAgentRuntime` permission (SEC03-BP02 least privilege).

A2A card signing (a cryptographic-integrity layer over the card itself, verified against a distributed trust anchor) is intentionally omitted in this sample: the registry approval gate is the provenance control for the card, and signing would add key-management surface without changing the trust decision under this threat model. Add it (a2a-sdk `create_agent_card_signer` / `create_signature_verifier`, private key from KMS or Secrets Manager) when cards are sourced from a channel that is not approval-gated. See `agent_registry_sample/peer/card.py`.

## Invoke authorization: consumer-group resource policy

Same-account invocation uses identity-silent caller + resource-allow callee (AgentCore evaluates identity and resource policies together; either can allow). The peer's resource policy carries two statements: ALLOW the consumer group, and DENY every other principal via `ArnNotEquals aws:PrincipalArn`. Without the explicit deny, any other same-account principal holding an identity-based invoke allow could still reach the runtime (IAM-allow + resource-silent = allowed). the deny makes the boundary enforced rather than advisory. AgentCore authorizes `InvokeAgentRuntime` hierarchically against both the runtime and its endpoint, so both resources carry the policy.

The consumer group is a NAMED SET of principals, not a single role. Both the allow and the deny-exception cover the whole set, so only named members invoke and every other principal is denied. In production the set is exactly the orchestrator execution role (or roles, if several standing agents share the consumer group); invocation then always originates inside the orchestrator runtime as that role, for both the push and the pull path. This walkthrough also runs the pull path LOCALLY under the operator's own identity as a convenience, so the walkthrough adds the operator's principal to the set at deploy time. That operator entry is demo-only: in a real deployment pull runs in-runtime as the orchestrator role and the operator is not a group member. If the operator runs as an assumed role rather than an IAM user, add the role ARN to the set, because `aws:PrincipalArn` evaluates to the role ARN.

## Least privilege

Every registry and DynamoDB grant is scoped to a specific resource ARN with no wildcards. The submitter, approver, and orchestrator are separate roles (SEC03-BP02, AGENTSEC03-BP02), so no single identity holds create+approve+invoke, and audit attribution is unambiguous. Control-plane actions use the `agent-registry:` namespace scoped at the `registry` versus `registry/record` resource levels per the service-authorization reference.

## Transport and the arrival event

AgentCore invocation is the SigV4-gated `bedrock-agentcore:InvokeAgentRuntime` binding, not a public HTTP A2A host, so every invoke is an authenticated, IAM-authorized, CloudTrail-logged AWS API call (AGENTSEC06-BP01/BP03), with no public ingress to harden.

The push arrival event travels EventBridge to SQS to the adapter Lambda. The adapter is logic-free: it wraps the event's non-secret identifiers (`registryRecordId`, `registryId`) in an A2A DataPart and invokes the orchestrator, holding no card read, verification, or A2A session. The arrival payload is not signed, and that is acceptable here because it carries no secret and confers no trust: the orchestrator re-reads the record from the approval-gated data plane (which returns only approved records) before invoking. a tampered message can at most name a different record id, which is then either absent (fail-closed `batch_get`) or itself an approved record. The orchestrator's `ArrivalAwareExecutor` strict-validates the DataPart schema (exact `registryRecordId` + `registryId`, no extra keys) so a crafted DataPart cannot smuggle fields into the event path, and routes it deterministically with no LLM interpretation (AGENTSEC04 deterministic enforcement). For a workload where the arrival payload carries trusted or sensitive content, add message-level signing or an HMAC at the adapter and verify at consumption (AGENTSEC06-BP01).

## Reliability

EventBridge delivers at least once, so the orchestrator's event path is idempotent (AGENTREL06-BP04): a two-phase DynamoDB dedupe claims `(recordId, state)` before the handoff and marks it complete only after, so a duplicate `APPROVED` event does not cause a second invocation. The SQS consumer has a dead-letter queue with `maxReceiveCount` 3, so a poison or failing event is captured rather than retried forever or dropped.

## Observability

Every hop emits a structured JSON log line keyed on `registryRecordId`, which is the correlation key across the adapter and the orchestrator event path, and AgentCore emits an OpenTelemetry `trace_id` on each runtime log line (AGENTOPS05-BP01/BP03). For production, forward AgentCore Observability traces to CloudWatch so agent reasoning joins the infrastructure trace.

## Before production

- Keep approval human-adjudicated (see required conditions).
- Add an evaluation gate on `APPROVED` and coordination-anomaly monitoring (AGENTOPS06-BP03, AGENTSEC06-BP04).
- Add `aws:SourceArn` / `aws:SourceAccount` confused-deputy conditions to the AgentCore execution-role trust policy, and confirm the execution role holds equal or fewer privileges than the principals that can invoke it.
- Add a runtime toggle to disable auto-invoke without redeploy (AGENTREL06-BP05).
- Confirm the design with your security and legal teams.
