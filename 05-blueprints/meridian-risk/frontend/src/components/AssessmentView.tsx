// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** AgentCore Runtime — run a KYC assessment and show the multi-agent result. */

import { useRef, useState } from "react"
import {
  runAssessment,
  type Assessment,
  type DemoCustomer,
  type SpecialistFinding,
  type StreamEvent,
} from "../lib/api"

interface Props {
  customers: DemoCustomer[]
}

interface LogLine {
  stage: string
  message: string
}

/** Strip the Gateway's "gateway_<target>___" prefix for display. */
function bareToolName(name: string): string {
  return name.split("___").pop() ?? name
}

function FindingList({ items, risk }: { items?: string[]; risk?: boolean }) {
  if (!items?.length) return null
  return (
    <ul className={risk ? "finding-list risk" : "finding-list"}>
      {items.map((item, index) => (
        <li key={index}>{item}</li>
      ))}
    </ul>
  )
}

function SpecialistPanel({
  title,
  role,
  finding,
}: {
  title: string
  role: string
  finding?: SpecialistFinding | null
}) {
  if (!finding) return null

  if (finding.error) {
    return (
      <div className="panel">
        <div className="panel-head">
          <span className="panel-title">{title}</span>
        </div>
        <div className="panel-body">
          <div className="error">{finding.error}</div>
        </div>
      </div>
    )
  }

  const isCredit = finding.score !== undefined

  return (
    <div className="panel">
      <div className="panel-head">
        <span className="panel-title">{title}</span>
        {isCredit ? (
          <span className="spec-score mono">{finding.score}</span>
        ) : (
          <span className={`chip status-${finding.status === "compliant" ? "APPROVED" : "REJECTED"}`}>
            {finding.status?.replace(/_/g, " ")}
          </span>
        )}
      </div>
      <div className="panel-body stack">
        <div className="muted">{role}</div>
        {finding.narrative && <p style={{ fontSize: 13 }}>{finding.narrative}</p>}

        {isCredit ? (
          <>
            <Labelled label="Contributing factors">
              <FindingList items={finding.factors} />
            </Labelled>
            <Labelled label="Mitigations">
              <FindingList items={finding.recommendations} />
            </Labelled>
          </>
        ) : (
          <>
            {finding.edd_required && (
              <span className="chip status-PENDING_APPROVAL">
                Enhanced due diligence required
              </span>
            )}
            <Labelled label="Checks failed">
              <FindingList items={finding.checks_failed} risk />
            </Labelled>
            <Labelled label="Checks passed">
              <FindingList items={finding.checks_passed} />
            </Labelled>
            <Labelled label="Regulatory notes">
              <FindingList items={finding.regulatory_notes} />
            </Labelled>
          </>
        )}

        {/*
          Scoping evidence. This is the demo's proof that the Registry agent
          skill is not decorative: the skill definition names the tools, the
          orchestrator grants exactly those, and the withheld ones are shown
          struck through. If skills were inert, both specialists would be
          granted all of the Gateway's tools.
        */}
        {!!finding._tools_granted?.length && (
          <Labelled
            label={
              finding._tools_available
                ? `Tool scope — ${finding._tools_granted.length} of ${finding._tools_available} Gateway tools granted`
                : "Tool scope"
            }
          >
            {finding._skill && (
              <div className="scope-skill mono">
                skill: {finding._skill}
              </div>
            )}
            <div className="chip-row">
              {finding._tools_granted.map((tool) => {
                const bare = bareToolName(tool)
                const called = (finding._tool_calls ?? []).some(
                  (c) => bareToolName(c) === bare
                )
                return (
                  <span
                    key={tool}
                    className={called ? "chip chip-tool" : "chip"}
                    title={
                      called
                        ? "granted by the skill, and invoked on this run"
                        : "granted by the skill, but not needed on this run"
                    }
                  >
                    {bare}
                    {called && " ✓"}
                  </span>
                )
              })}
              {(finding._withheld ?? []).map((tool) => (
                <span
                  key={tool}
                  className="chip chip-withheld"
                  title="not in this skill's tool list — the agent never received it"
                >
                  {tool}
                </span>
              ))}
            </div>
          </Labelled>
        )}
      </div>
    </div>
  )
}

function Labelled({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  // Render nothing when the child list is empty, so panels stay tight.
  if (
    children == null ||
    (Array.isArray(children) && children.length === 0) ||
    children === false
  )
    return null
  return (
    <div>
      <span className="field-label">{label}</span>
      {children}
    </div>
  )
}

export function AssessmentView({ customers }: Props) {
  const [customerId, setCustomerId] = useState(customers[0]?.id ?? "CUST001")
  const [assessmentType, setAssessmentType] = useState("full")
  const [analystContext, setAnalystContext] = useState("")
  const [running, setRunning] = useState(false)
  const [log, setLog] = useState<LogLine[]>([])
  const [result, setResult] = useState<Assessment | null>(null)
  const [error, setError] = useState<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  const start = async () => {
    setRunning(true)
    setLog([])
    setResult(null)
    setError(null)

    const controller = new AbortController()
    abortRef.current = controller

    const onEvent = (event: StreamEvent) => {
      if (event.type === "result" && event.assessment) {
        setResult(event.assessment)
      } else if (event.type === "error") {
        setError(event.message ?? "Assessment failed")
      } else if (event.message) {
        setLog((prior) => [
          ...prior,
          { stage: event.stage ?? event.type, message: event.message! },
        ])
      }
    }

    try {
      await runAssessment(
        {
          customer_id: customerId,
          assessment_type: assessmentType,
          context: analystContext.trim() || undefined,
        },
        onEvent,
        controller.signal
      )
    } catch (exc) {
      if ((exc as Error).name !== "AbortError") {
        setError((exc as Error).message)
      }
    } finally {
      setRunning(false)
      abortRef.current = null
    }
  }

  const selected = customers.find((c) => c.id === customerId)

  return (
    <>
      <div className="section-head">
        <div className="eyebrow">AgentCore Runtime</div>
        <h2>Corporate onboarding assessment</h2>
        <p>
          Two specialist agents — a Credit Analyst and a Compliance Officer — run
          concurrently against the Gateway's KYC tools, then a supervisor
          synthesizes a single onboarding decision. Prior assessments are
          recalled from Memory before the review begins.
        </p>
      </div>

      <div className="grid-2">
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">Prospective customer</span>
          </div>
          <div className="panel-body">
            {customers.map((customer) => (
              <button
                key={customer.id}
                className="customer"
                aria-pressed={customer.id === customerId}
                onClick={() => setCustomerId(customer.id)}
                disabled={running}
              >
                <span className="customer-id">{customer.id}</span>
                <div className="customer-name">{customer.name}</div>
                <div className="customer-note">{customer.note}</div>
              </button>
            ))}

            <div style={{ marginTop: 18 }}>
              <label className="field">
                <span className="field-label">Scope</span>
                <select
                  value={assessmentType}
                  onChange={(event) => setAssessmentType(event.target.value)}
                  disabled={running}
                >
                  <option value="full">Full — credit and compliance</option>
                  <option value="credit_only">Credit only</option>
                  <option value="compliance_only">Compliance only</option>
                </select>
              </label>

              <label className="field">
                <span className="field-label">Analyst notes (optional)</span>
                <textarea
                  value={analystContext}
                  onChange={(event) => setAnalystContext(event.target.value)}
                  placeholder="e.g. Applicant requests a $10M revolving facility."
                  disabled={running}
                />
              </label>

              <button className="btn" onClick={start} disabled={running}>
                {running ? "Assessment running…" : "Run assessment"}
              </button>
            </div>
          </div>
        </div>

        <div className="stack">
          {(running || log.length > 0) && (
            <div className="panel">
              <div className="panel-head">
                <span className="panel-title">Execution trace</span>
                {running && <span className="spinner" />}
              </div>
              <div className="panel-body">
                <div className="log">
                  {log.map((line, index) => (
                    <div className="log-line" key={index}>
                      <span className="log-stage">{line.stage}</span>
                      <span>{line.message}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {error && <div className="error">{error}</div>}

          {!result && !running && !error && (
            <div className="panel">
              <div className="empty">
                <div className="empty-mark">§</div>
                <div>
                  Select a prospective customer and run an assessment.
                  {selected && (
                    <>
                      <br />
                      <span className="muted">
                        {selected.name} — {selected.industry}
                      </span>
                    </>
                  )}
                </div>
              </div>
            </div>
          )}

          {result && (
            <>
              <div className="verdict" data-v={result.recommendation}>
                <div className="score-dial">
                  <div className="score-value">
                    {result.overall_risk_score ?? "—"}
                  </div>
                  <div className="score-scale">risk / 100</div>
                </div>
                <div>
                  <div className="verdict-label">{result.recommendation}</div>
                  <p className="verdict-summary">{result.summary}</p>
                </div>
              </div>

              <div className="panel">
                <div className="panel-head">
                  <span className="panel-title">Service evidence</span>
                </div>
                <div className="panel-body">
                  <dl className="kv">
                    <dt>Runtime session</dt>
                    <dd>{result.session_id}</dd>
                    <dt>Gateway tools</dt>
                    <dd>
                      {result.tools_invoked?.length ?? 0} invoked —{" "}
                      {(result.tools_invoked ?? []).map(bareToolName).join(", ")}
                    </dd>
                    {/*
                      Model-plane scope. "gateway" means every model call on
                      this run traversed the Gateway's /inference target rather
                      than Bedrock direct — the single ingress at which policy
                      and credentials are enforced for every caller.
                    */}
                    {result.inference && (
                      <>
                        <dt>Model plane</dt>
                        <dd>
                          {result.inference.route === "gateway" ? (
                            <>
                              <span
                                className="chip chip-tool"
                                title="LLM call went through AgentCore Gateway /inference"
                              >
                                gateway
                              </span>{" "}
                              →{" "}
                              <span className="mono">
                                {result.inference.model_id ?? "?"}
                              </span>
                            </>
                          ) : (
                            <>
                              <span
                                className="chip"
                                title="LLM call went straight to Bedrock InvokeModel (no gateway rail)"
                              >
                                direct
                              </span>{" "}
                              →{" "}
                              <span className="mono">
                                {result.inference.model_id ?? "?"}
                              </span>
                            </>
                          )}
                        </dd>
                        <dt>Bedrock Guardrail</dt>
                        <dd>
                          {result.inference.guardrail_id ? (
                            <>
                              <span className="mono">
                                {result.inference.guardrail_id}
                                {result.inference.guardrail_version
                                  ? ` v${result.inference.guardrail_version}`
                                  : ""}
                              </span>{" "}
                              <span
                                className="muted"
                                title="Deployed and versioned, and enforced for callers that invoke Bedrock directly. Binding it to gateway traffic needs Cedar's `when guardrails` condition, which the public CreatePolicy parser does not yet accept in any Region tested — so authorization, not content filtering, is what the gateway enforces today."
                              >
                                (deployed; not bound to gateway traffic)
                              </span>
                            </>
                          ) : (
                            <span className="muted">none configured</span>
                          )}
                        </dd>
                      </>
                    )}
                    {/*
                      Policy plane — the enforceable control. Per-agent tool
                      scoping above is cooperative: it holds because the
                      orchestrator honours the skill definition. A Cedar forbid
                      holds because the Gateway refuses the call before the tool
                      Lambda runs, so under ENFORCE every call counted here is
                      one that passed authorization.
                    */}
                    {result.policy?.mode && (
                      <>
                        <dt>Policy plane</dt>
                        <dd>
                          <span
                            className={
                              result.policy.mode === "ENFORCE"
                                ? "chip chip-tool"
                                : "chip chip-amber"
                            }
                            title={
                              result.policy.mode === "ENFORCE"
                                ? "Cedar policies evaluated on every gateway request; violations are denied"
                                : "Cedar policies evaluated and logged, but violations are allowed through"
                            }
                          >
                            {result.policy.mode}
                          </span>{" "}
                          {result.policy.authorized_calls !== undefined && (
                            <>
                              {result.policy.authorized_calls} tool call
                              {result.policy.authorized_calls === 1 ? "" : "s"}{" "}
                              authorized
                            </>
                          )}
                          {result.policy.engine_id && (
                            <>
                              {" · "}
                              <span className="mono" style={{ fontSize: 12 }}>
                                {result.policy.engine_id}
                              </span>
                            </>
                          )}
                        </dd>
                      </>
                    )}
                    <dt>Memory event</dt>
                    <dd>{result.memory_event_id ?? "not recorded"}</dd>
                    {/*
                      Recall is capped at top_k, so the recalled count is not a
                      history size. Showing "N of M" keeps the panel from
                      implying a customer has been assessed far fewer times than
                      they actually have — which for KYC is a compliance-relevant
                      claim, not a cosmetic one.
                    */}
                    <dt>Prior assessments</dt>
                    <dd>
                      {result.prior_assessment_total != null &&
                      result.prior_assessment_total >
                        (result.prior_assessment_count ?? 0) ? (
                        <>
                          {result.prior_assessment_count ?? 0} of{" "}
                          {result.prior_assessment_total} recalled from Memory{" "}
                          <span
                            className="muted"
                            title="Recall is capped so the prompt stays focused; the full history remains in Memory and is visible on the Memory tab."
                          >
                            (most relevant)
                          </span>
                        </>
                      ) : (
                        <>
                          {result.prior_assessment_count ?? 0} recalled from
                          Memory
                        </>
                      )}
                    </dd>
                  </dl>
                </div>
              </div>

              {!!result.key_risks?.length && (
                <div className="panel">
                  <div className="panel-head">
                    <span className="panel-title">Key risks</span>
                  </div>
                  <div className="panel-body">
                    <FindingList items={result.key_risks} risk />
                  </div>
                </div>
              )}

              {!!result.regulatory_actions?.length && (
                <div className="panel">
                  <div className="panel-head">
                    <span className="panel-title">Required regulatory actions</span>
                  </div>
                  <div className="panel-body">
                    <FindingList items={result.regulatory_actions} />
                  </div>
                </div>
              )}

              {!!result.conditions?.length && (
                <div className="panel">
                  <div className="panel-head">
                    <span className="panel-title">Conditions</span>
                  </div>
                  <div className="panel-body">
                    <FindingList items={result.conditions} />
                  </div>
                </div>
              )}

              <div className="specialists">
                <SpecialistPanel
                  title="Credit Analyst"
                  role="Assessed repayment capacity, leverage, and payment discipline."
                  finding={result.credit_risk}
                />
                <SpecialistPanel
                  title="Compliance Officer"
                  role="Screened sanctions, PEP exposure, transaction patterns, and adverse media."
                  finding={result.compliance}
                />
              </div>
            </>
          )}
        </div>
      </div>
    </>
  )
}
