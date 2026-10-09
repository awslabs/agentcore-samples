// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Typed client for the demo console API. */

import { currentToken } from "./auth"
import type { RuntimeConfig } from "./config"
import { getCredentials } from "./credentials"
import { signRequest } from "./sigv4"

export interface DemoCustomer {
  id: string
  name: string
  industry: string
  expected: string
  note: string
}

export interface Config {
  region: string
  runtime_arn: string
  registry_id: string
  gateway_id: string
  gateway_url: string
  /** OpenAI-compatible inference endpoint — same gateway, /inference path. */
  gateway_inference_url: string
  memory_id: string
  user_pool_id: string
  /** Bedrock guardrail deployed and versioned; binding to gateway traffic
   *  (via AgentCore Policy) awaits a preview Cedar feature — see policy.tf. */
  guardrail_id: string
  guardrail_version: string
  /** "gateway" when the runtime routes through the LLM gateway, else "direct". */
  inference_route: "gateway" | "direct" | string
  /** AgentCore Policy Engine evaluating Cedar policies on gateway traffic. */
  policy_engine_id: string
  /** "ENFORCE" — violations denied; "LOG_ONLY" — evaluated and logged only. */
  policy_mode: "ENFORCE" | "LOG_ONLY" | string
  /** AgentCore Harness — the managed agent loop. Empty when not deployed. */
  harness_id?: string
  configured: boolean
  demo_customers: DemoCustomer[]
}

export interface SpecialistFinding {
  score?: number
  level?: string
  factors?: string[]
  recommendations?: string[]
  status?: string
  checks_passed?: string[]
  checks_failed?: string[]
  regulatory_notes?: string[]
  edd_required?: boolean
  narrative?: string
  /** Registry AGENT_SKILLS record this specialist corresponds to. */
  _skill?: string
  /** Tools the orchestrator actually handed this agent, read off the objects. */
  _tools_granted?: string[]
  /** How many tools the Gateway advertised — the scoping denominator. */
  _tools_available?: number
  /** Advertised minus granted: tools this skill deliberately does not get. */
  _withheld?: string[]
  _tool_calls?: string[]
  error?: string
}

/**
 * Model-plane scope actually used on the run, echoed off the orchestrator.
 * "gateway" means every LLM call went through AgentCore Gateway's /inference
 * endpoint; "direct" means the runtime called Bedrock InvokeModel with its own
 * role. The named guardrail is deployed and versioned, but not yet bound to
 * gateway traffic (that binding is a preview Cedar feature — see policy.tf).
 * The demo surfaces this so an operator can prove the LLM-gateway path is not
 * decorative.
 */
export interface InferenceEvidence {
  route: "gateway" | "direct" | string
  model_id?: string
  guardrail_id?: string | null
  guardrail_version?: string | null
}

/**
 * Policy-plane scope for the run. Under ENFORCE the Gateway authorizes every
 * tool call against Cedar policies before invoking the tool, so
 * `authorized_calls` is a count of requests that actually passed — not a
 * restatement of configuration.
 */
export interface PolicyEvidence {
  mode?: "ENFORCE" | "LOG_ONLY" | string | null
  engine_id?: string | null
  authorized_calls?: number
}

export interface Assessment {
  customer_id: string
  session_id: string
  assessment_type: string
  overall_risk_score: number | null
  risk_level: string
  recommendation: "APPROVE" | "REJECT" | "ESCALATE" | string
  summary: string
  key_risks?: string[]
  conditions?: string[]
  regulatory_actions?: string[]
  credit_risk?: SpecialistFinding | null
  compliance?: SpecialistFinding | null
  tools_invoked?: string[]
  gateway_tools_available?: number
  memory_event_id?: string | null
  /** How many priors recall fed the model — capped at top_k, not a history size. */
  prior_assessment_count?: number
  /** Total assessments on record; null when Memory could not be counted. */
  prior_assessment_total?: number | null
  inference?: InferenceEvidence
  policy?: PolicyEvidence
}

export interface StreamEvent {
  type: "status" | "result" | "error" | "log"
  stage?: string
  message?: string
  assessment?: Assessment
  tools_invoked?: string[]
}

export interface RegistryRecord {
  recordId: string
  recordArn?: string
  name: string
  description?: string
  descriptorType: "MCP" | "A2A" | "AGENT_SKILLS" | "CUSTOM" | string
  status: string
  statusReason?: string
  createdAt?: string
  updatedAt?: string
  descriptors?: Record<string, any>
}

export interface GatewayTool {
  name: string
  description: string
  inputSchema?: { properties?: Record<string, any>; required?: string[] }
}

export interface GatewayTarget {
  target_id: string
  name: string
  status: string
  /** Tool target vs model target — an inference target has no tools by design. */
  kind?: "mcp" | "inference" | "http" | "unknown" | string
  tools: GatewayTool[]
}

export interface GatewayInfo {
  gateway_id: string
  gateway_url: string
  targets: GatewayTarget[]
  tool_count: number
}

export interface MemoryEvent {
  eventId?: string
  sessionId?: string
  eventTimestamp?: string
  payload?: any[]
}

export interface MemoryView {
  customer_id: string
  memory_id: string
  session_count: number
  event_count: number
  events: MemoryEvent[]
  records: any[]
}

/** Raised on 401 so the shell can drop back to the login screen. */
export class UnauthorizedError extends Error {}

let apiBase = ""
let signingConfig: RuntimeConfig | null = null

/**
 * Point the client at the API.
 *
 * Pass the runtime config to enable SigV4 signing. When `identityPoolId` is set,
 * every request is signed with federated IAM credentials — required because the
 * Function URL uses AWS_IAM authorization. Locally (no config.json) the client
 * talks to the Vite proxy unsigned.
 */
export function configureApi(config: RuntimeConfig): void {
  apiBase = config.apiBase.replace(/\/$/, "")
  signingConfig = config.identityPoolId ? config : null
}

function url(path: string): string {
  return `${apiBase}${path}`
}

/**
 * Build the headers for one request.
 *
 * The Cognito ID token identifies the operator to the API; the SigV4 signature
 * satisfies the Function URL's IAM authorization. The ID token rides in
 * `X-Id-Token` rather than `Authorization`, because SigV4 needs `Authorization`
 * for the signature itself.
 */
async function buildHeaders(
  method: string,
  path: string,
  body: string
): Promise<Record<string, string>> {
  const token = currentToken()

  if (!signingConfig) {
    return token ? { Authorization: `Bearer ${token}` } : {}
  }
  if (!token) throw new UnauthorizedError("Not signed in")

  const credentials = await getCredentials(signingConfig, token)
  const signed = await signRequest(
    method,
    url(path),
    body,
    credentials,
    signingConfig.region
  )
  return { ...signed, "X-Id-Token": token }
}

async function failure(response: Response): Promise<Error> {
  // The API returns FastAPI's {detail: ...} shape on error.
  let detail = `HTTP ${response.status}`
  try {
    detail = (await response.json())?.detail ?? detail
  } catch {
    /* non-JSON error body; keep the status line */
  }
  return response.status === 401
    ? new UnauthorizedError(detail)
    : new Error(detail)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "GET"
  const body = typeof init?.body === "string" ? init.body : ""

  const response = await fetch(url(path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(await buildHeaders(method, path, body)),
      ...(init?.headers ?? {}),
    },
  })
  if (!response.ok) throw await failure(response)
  return response.json() as Promise<T>
}

export const api = {
  config: () => request<Config>("/api/config"),

  registryRecords: () =>
    request<{ registry_id: string; count: number; records: RegistryRecord[] }>(
      "/api/registry/records"
    ),

  registrySearch: (query: string) =>
    request<{ query: string; count: number; records: RegistryRecord[] }>(
      "/api/registry/search",
      { method: "POST", body: JSON.stringify({ query, max_results: 10 }) }
    ),

  setRecordStatus: (recordId: string, status: string, reason: string) =>
    request<RegistryRecord>(`/api/registry/records/${recordId}/status`, {
      method: "POST",
      body: JSON.stringify({ status, reason }),
    }),

  gatewayTools: () => request<GatewayInfo>("/api/gateway/tools"),

  invokeTool: (toolName: string, customerId: string) =>
    request<{ tool: string; customer_id: string; result: any }>(
      "/api/gateway/invoke",
      {
        method: "POST",
        body: JSON.stringify({ tool_name: toolName, customer_id: customerId }),
      }
    ),

  memory: (customerId: string) =>
    request<MemoryView>(`/api/memory/${customerId}`),
}

/**
 * Run an assessment, invoking `onEvent` for each streamed event.
 *
 * The API relays the Runtime's progress stream as SSE. Events are newline-framed
 * `data:` records, so partial frames are buffered across chunk boundaries.
 */
export async function runAssessment(
  body: { customer_id: string; assessment_type: string; context?: string },
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const payload = JSON.stringify(body)
  const response = await fetch(url("/api/assess"), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(await buildHeaders("POST", "/api/assess", payload)),
    },
    body: payload,
    signal,
  })

  if (!response.ok) throw await failure(response)
  if (!response.body) throw new Error("Response carried no body to stream")

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  const flush = (frame: string) => {
    const line = frame.trim()
    if (!line.startsWith("data:")) return
    const raw = line.slice(5).trim()
    if (!raw) return
    try {
      // The Runtime double-encodes: the SSE payload is a JSON string whose
      // contents are themselves the JSON event.
      let parsed = JSON.parse(raw)
      if (typeof parsed === "string") parsed = JSON.parse(parsed)
      onEvent(parsed as StreamEvent)
    } catch {
      /* ignore malformed frames rather than aborting the stream */
    }
  }

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let index: number
    while ((index = buffer.indexOf("\n\n")) !== -1) {
      flush(buffer.slice(0, index))
      buffer = buffer.slice(index + 2)
    }
  }
  if (buffer.trim()) flush(buffer)
}
