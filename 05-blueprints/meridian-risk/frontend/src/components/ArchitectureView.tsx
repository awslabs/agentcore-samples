// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Architecture view — how a single request flows end to end.
 *
 * Two linked halves:
 *   1. A clickable topology. Selecting a node reveals what it is, which AWS
 *      APIs it calls, which source file implements it, and the design decision
 *      behind it. Node subtitles show live resource IDs from /api/config, so the
 *      diagram describes the deployment rather than an idealized drawing.
 *   2. A numbered request lifecycle, so a viewer can follow one assessment from
 *      the click to the persisted verdict.
 */

import { useState } from "react"
import type { Config } from "../lib/api"

interface Props {
  config: Config
}

type Layer = "client" | "auth" | "agent" | "data"

interface Node {
  id: string
  label: string
  service: string
  layer: Layer
  /** Resolves the live identifier for this node, when it has one. */
  detail?: (config: Config) => string
  what: string
  calls: string[]
  source: string
  decision?: string
}

const NODES: Node[] = [
  {
    id: "console",
    label: "Demo console",
    service: "Amplify Hosting",
    layer: "client",
    what: "React SPA on Amplify. Holds the Cognito ID token in sessionStorage and streams assessment progress over SSE.",
    calls: ["fetch → Lambda Function URL"],
    source: "frontend/src/App.tsx",
    decision:
      "Runtime config is fetched from /config.json rather than baked in, so one build artifact can target any stack.",
  },
  {
    id: "cognito",
    label: "Cognito user pool",
    service: "Amazon Cognito",
    layer: "auth",
    detail: (config) => config.user_pool_id || "not configured",
    what: "Authenticates the operator and issues an 8-hour ID token, then federates it into temporary IAM credentials scoped to invoking this one API.",
    calls: ["InitiateAuth", "GetId", "GetCredentialsForIdentity"],
    source: "frontend/src/lib/auth.ts · frontend/src/lib/credentials.ts · infra/cognito.tf",
    decision:
      "USER_PASSWORD_AUTH against the API directly, not the Hosted UI, so the console keeps its own branded login. The identity pool exists because this account blocks unauthenticated Function URLs — the browser must SigV4-sign, which needs real IAM credentials.",
  },
  {
    id: "api",
    label: "Console API",
    service: "Lambda Function URL",
    layer: "auth",
    what: "FastAPI on Lambda. IAM authorizes the request at the URL; the app then verifies the ID token against the pool's JWKS and calls AWS with its own execution role.",
    calls: ["JWKS verify", "SigV4 to AgentCore"],
    source: "backend/api/main.py · backend/api/auth.py",
    decision:
      "A Function URL with RESPONSE_STREAM, not API Gateway: assessments run 25–60s and API Gateway caps integrations at 30s. Python needs the Lambda Web Adapter to stream at all.",
  },
  {
    id: "runtime",
    label: "KYC orchestrator",
    service: "AgentCore Runtime",
    layer: "agent",
    detail: (config) => config.runtime_arn.split("/").pop() ?? "",
    what: "ARM64 container running a Strands multi-agent workflow. Recalls history, runs both specialists concurrently, synthesizes the verdict, and persists it.",
    calls: ["InvokeAgentRuntime"],
    source: "backend/agent/kyc_agent.py · backend/agent/orchestrator.py",
    decision:
      "A failed synthesis returns ESCALATE, never a silent APPROVE — an unparsable model response must not read as approval.",
  },
  {
    id: "harness",
    label: "Managed agent loop",
    service: "AgentCore Harness",
    layer: "agent",
    detail: (config) => config.harness_id || "not deployed",
    what: "The same KYC assistant expressed as configuration instead of code: a managed agent loop declaring a model, a system prompt, the shared Gateway as its tool, managed memory, and an S3 agent skill. AgentCore runs the orchestration, tool execution, memory, and observability in an isolated microVM per session.",
    calls: ["InvokeHarness"],
    source: "infra/harness.tf · backend/harness/skills/kyc-onboarding-assessment/SKILL.md",
    decision:
      "Runtime owns its loop in code because the multi-agent supervisor pattern needs custom orchestration; the Harness shows the declarative alternative on the same governed surfaces. Its Gateway tool calls go out under the harness IAM role, so AgentCore Policy authorizes them exactly as it does the Runtime's. The KYC skill is attached at invoke time (the Terraform provider can't yet read back a non-path skill source), which loads the assessment method via progressive disclosure.",
  },
  {
    id: "credit",
    label: "Credit Analyst",
    service: "Specialist agent",
    layer: "agent",
    what: "Assesses repayment capacity, leverage, payment discipline, and facility utilization. Returns a 0–100 credit risk score.",
    calls: ["get_customer_profile", "credit_bureau_report"],
    source: "backend/agent/agents/credit_analyst.py",
    decision:
      "Its Registry skill (credit-risk-analysis) names the two tools it may call, and the orchestrator grants exactly those — the Assessment tab shows the other three struck through as withheld.",
  },
  {
    id: "compliance",
    label: "Compliance Officer",
    service: "Specialist agent",
    layer: "agent",
    what: "Screens sanctions and PEP lists, reviews transaction patterns for structuring, and scans adverse media.",
    calls: [
      "get_customer_profile",
      "sanctions_screen",
      "transaction_history",
      "adverse_media_scan",
    ],
    source: "backend/agent/agents/compliance_officer.py",
    decision:
      "Hard regulatory rules live in the prompt: any sanctions match blocks a compliant status, and structuring must be escalated as potentially SAR-reportable. Its Registry skill (aml-compliance-screening) grants four of the five Gateway tools.",
  },
  {
    id: "gateway",
    label: "KYC tool gateway",
    service: "AgentCore Gateway",
    layer: "data",
    detail: (config) => config.gateway_id || "",
    what: "Exposes five KYC data tools to the agents as an MCP server, backed by one Lambda target.",
    calls: ["MCP tools/list", "MCP tools/call"],
    source: "backend/agent/lib/gateway.py · infra/gateway.tf",
    decision:
      "AWS_IAM inbound auth, so the Runtime's execution role authorizes tool calls with SigV4 — no Cognito machine client or token vault. Signing happens per request, because MCP sends a different body on every call.",
  },
  {
    id: "tools",
    label: "KYC tools",
    service: "AWS Lambda",
    layer: "data",
    what: "Five tools over the synthetic corporate dataset: profile, credit bureau, sanctions and PEP, transactions, adverse media.",
    calls: ["Invoke (via Gateway)"],
    source: "backend/gateway/kyc_tools_lambda.py",
    decision:
      "tool_spec.json is the single source of truth — it drives both the Terraform tool schemas and the Registry record, so they cannot drift.",
  },
  {
    id: "memory",
    label: "Assessment history",
    service: "AgentCore Memory",
    layer: "data",
    detail: (config) => config.memory_id || "",
    what: "Stores each verdict and extracts long-term facts. Scoped per corporate customer, so any reviewer sees the same history.",
    calls: ["CreateEvent", "RetrieveMemoryRecords", "ListEvents"],
    source: "backend/agent/lib/memory.py · infra/memory.tf",
    decision:
      "actorId is the customer, not the analyst. Recall merges extracted records with raw events, because extraction is asynchronous and lags minutes — too slow for a live demo.",
  },
  {
    id: "registry",
    label: "Resource catalog",
    service: "AgentCore Registry",
    layer: "data",
    detail: (config) => config.registry_id || "",
    what: "Governed catalog of this platform's MCP server, agent card, and two agent skills. Records must be APPROVED before they are discoverable.",
    calls: ["SearchRegistryRecords", "ListRegistryRecords", "UpdateRegistryRecordStatus"],
    source: "scripts/seed_registry.py · infra/registry.tf",
    decision:
      "Auto-approval is left off so the demo can walk DRAFT → PENDING_APPROVAL → APPROVED. The two agent-skill records are generated from the same Skill objects the orchestrator runs on, so the catalog cannot describe a tool set the agents do not actually have.",
  },
  {
    id: "inference",
    label: "LLM inference gateway",
    service: "AgentCore Gateway · inference target",
    layer: "data",
    detail: (config) =>
      config.inference_route === "gateway"
        ? `${config.gateway_id || ""} · /inference/v1`
        : `direct (route=${config.inference_route})`,
    what: "Same Gateway, second target: a Bedrock Mantle inference connector. The runtime uses the OpenAI SDK against /inference/v1 — the Gateway SigV4-forwards to Bedrock under its own role, so the runtime never holds Bedrock credentials.",
    calls: ["POST /inference/v1/chat/completions", "Bedrock InvokeModel (server-side)"],
    source: "backend/agent/lib/inference.py · infra/gateway.tf · scripts/manage_inference_target.py",
    decision:
      "Two targets under one gateway (tools + inference) means one governed ingress, one credential broker, and one audit trail across every LLM call — regardless of upstream provider. A guardrail rail over the same path is the intended next step, bound through AgentCore Policy (see the guardrail node). Flip INFERENCE_ROUTE to 'direct' to prove the same code path also works against Bedrock directly.",
  },
  {
    id: "policy",
    label: "Cedar authorization",
    service: "AgentCore Policy",
    layer: "data",
    detail: (config) =>
      config.policy_engine_id
        ? `${config.policy_mode} · ${config.policy_engine_id}`
        : "not configured",
    what: "Evaluates every request the Gateway receives — tool calls and inference alike — against Cedar policies, server-side. A baseline permit allows normal traffic; a forbid policy denies KYC tool calls for any customer outside this deployment's book.",
    calls: ["AuthorizeAction", "PartiallyAuthorizeActions"],
    source: "infra/policy.tf",
    decision:
      "This is the enforceable half of governance. Per-agent tool scoping in the orchestrator is cooperative — it holds because our code honours the skill definition. A Cedar forbid holds because the Gateway refuses the call before the Lambda runs: tools/call for CUST999 returns 'Tool Execution Denied … [Policy evaluation denied due to forbid_unknown_customers]'. ENFORCE is default-deny, which is why the baseline permit is mandatory rather than tidy.",
  },
  {
    id: "guardrail",
    label: "PII + prompt-injection rail",
    service: "Bedrock Guardrails",
    layer: "data",
    detail: (config) =>
      config.guardrail_id
        ? `${config.guardrail_id} v${config.guardrail_version}`
        : "not configured",
    what: "Anonymizes SSN, bank account, and card numbers on output; blocks prompt-injection on input; denies unsolicited financial-advice topics. Deployed and versioned as a reviewable artifact, and enforceable on callers that invoke Bedrock directly.",
    calls: ["bedrock:InvokeGuardrailChecks (via Policy, once bound)"],
    source: "infra/guardrail.tf · infra/policy.tf",
    decision:
      "Not yet bound to gateway traffic. For the /inference target the only binding mechanism is an AgentCore Policy with Cedar's `when guardrails { BedrockGuardrails::… }` condition — and that Cedar extension is not yet live in the public CreatePolicy parser: fresh policy engines in four Regions (us-east-1, eu-west-2, ap-northeast-1, ap-southeast-2) all reject it with 'unexpected token guardrails', while a plain policy on the same engine parses. So it is a service-level preview gap, not our statement and not Region-specific. The binding policy is written and gated off in policy.tf; the engine, its IAM, and the attachment are all in place, so this becomes a one-flag change when the extension ships.",
  },
]

const LAYERS: { id: Layer; title: string; caption: string }[] = [
  { id: "client", title: "Client", caption: "Browser" },
  { id: "auth", title: "Identity & edge", caption: "Token issued, then verified" },
  { id: "agent", title: "Reasoning", caption: "Multi-agent orchestration" },
  { id: "data", title: "Tools, memory & governance", caption: "AgentCore services" },
]

const LIFECYCLE: { step: string; title: string; body: string }[] = [
  {
    step: "01",
    title: "Authenticate",
    body: "The operator signs in against the Cognito user pool. The resulting ID token is then federated through an identity pool into short-lived IAM credentials.",
  },
  {
    step: "02",
    title: "Request an assessment",
    body: "POST /api/assess is SigV4-signed with those credentials, satisfying the Function URL's IAM authorization, and carries the ID token so the API can verify which operator is calling.",
  },
  {
    step: "03",
    title: "Recall prior history",
    body: "The Runtime queries Memory for earlier verdicts on this customer and injects them into both specialists' prompts.",
  },
  {
    step: "04",
    title: "Discover tools",
    body: "The agent opens an MCP session to the Gateway, SigV4-signed with its execution role, and lists the available tools.",
  },
  {
    step: "05",
    title: "Analyse in parallel",
    body: "Credit Analyst and Compliance Officer run concurrently, each calling only its own scoped tools. Progress streams to the console as it happens.",
  },
  {
    step: "06",
    title: "Synthesize",
    body: "A supervisor weighs both findings — compliance failures dominate credit ones — and returns a score with APPROVE, REJECT, or ESCALATE.",
  },
  {
    step: "07",
    title: "Reason through the LLM gateway",
    body: "Every model call from both specialists and the supervisor flows through the Gateway's /inference target. The Gateway SigV4-forwards to Bedrock under its own role, so the runtime never invokes InvokeModel directly.",
  },
  {
    step: "08",
    title: "Persist",
    body: "The verdict — plus the route, model, and guardrail on record for the run — is written back to Memory, so the next assessment of this customer can report what changed and prove which model plane produced the answer.",
  },
]

export function ArchitectureView({ config }: Props) {
  const [selectedId, setSelectedId] = useState("runtime")
  const selected = NODES.find((node) => node.id === selectedId) ?? NODES[0]

  return (
    <>
      <div className="section-head">
        <div className="eyebrow">How it works</div>
        <h2>Request lifecycle, end to end</h2>
        <p>
          Every box below is a real deployed resource. Select one to see what it
          does, which AWS APIs it calls, where it is implemented, and why it was
          built that way.
        </p>
      </div>

      <div className="arch-grid">
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">Topology</span>
            <span className="muted mono">{config.region}</span>
          </div>
          <div className="panel-body">
            {LAYERS.map((layer, index) => (
              <div className="arch-layer" key={layer.id}>
                <div className="arch-layer-head">
                  <span className="arch-layer-title">{layer.title}</span>
                  <span className="arch-layer-caption">{layer.caption}</span>
                </div>
                <div className="arch-row">
                  {NODES.filter((node) => node.layer === layer.id).map((node) => (
                    <button
                      key={node.id}
                      className="arch-node"
                      aria-pressed={node.id === selectedId}
                      onClick={() => setSelectedId(node.id)}
                    >
                      <span className="arch-node-service">{node.service}</span>
                      <span className="arch-node-label">{node.label}</span>
                      {node.detail && (
                        <span className="arch-node-detail mono">
                          {node.detail(config)}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
                {index < LAYERS.length - 1 && (
                  <div className="arch-connector" aria-hidden="true">
                    <span />
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="stack">
          <div className="panel arch-detail">
            <div className="panel-head">
              <span className="panel-title">{selected.label}</span>
              <span className="chip chip-amber">{selected.service}</span>
            </div>
            <div className="panel-body stack">
              <p style={{ fontSize: 13.5 }}>{selected.what}</p>

              <div>
                <span className="field-label">Calls</span>
                <div className="chip-row">
                  {selected.calls.map((call) => (
                    <span key={call} className="chip chip-tool mono">
                      {call}
                    </span>
                  ))}
                </div>
              </div>

              <div>
                <span className="field-label">Implemented in</span>
                <div className="mono" style={{ fontSize: 12, color: "var(--text-mid)" }}>
                  {selected.source}
                </div>
              </div>

              {selected.decision && (
                <div>
                  <span className="field-label">Why it is built this way</span>
                  <p className="arch-decision">{selected.decision}</p>
                </div>
              )}
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">One assessment, step by step</span>
            </div>
            <div className="panel-body">
              <ol className="lifecycle">
                {LIFECYCLE.map((entry) => (
                  <li key={entry.step}>
                    <span className="lifecycle-step mono">{entry.step}</span>
                    <div>
                      <div className="lifecycle-title">{entry.title}</div>
                      <div className="lifecycle-body">{entry.body}</div>
                    </div>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}
