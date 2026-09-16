// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** AgentCore Memory — assessment history per corporate customer. */

import { useEffect, useState } from "react"
import { api, type DemoCustomer, type MemoryEvent, type MemoryView as View } from "../lib/api"

interface Props {
  customers: DemoCustomer[]
}

/** Extract the readable text from a Memory event's conversational payload. */
function eventText(event: MemoryEvent): { role: string; text: string }[] {
  const turns: { role: string; text: string }[] = []
  for (const item of event.payload ?? []) {
    const conversational = item?.conversational
    if (!conversational) continue
    turns.push({
      role: conversational.role ?? "?",
      text: conversational.content?.text ?? "",
    })
  }
  return turns
}

function formatTime(value?: string): string {
  if (!value) return "—"
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleString()
}

export function MemoryView({ customers }: Props) {
  const [customerId, setCustomerId] = useState(customers[0]?.id ?? "CUST001")
  const [view, setView] = useState<View | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async (id: string) => {
    setBusy(true)
    setError(null)
    try {
      setView(await api.memory(id))
    } catch (exc) {
      setError((exc as Error).message)
      setView(null)
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    void load(customerId)
  }, [customerId])

  return (
    <>
      <div className="section-head">
        <div className="eyebrow">AgentCore Memory</div>
        <h2>Assessment history per customer</h2>
        <p>
          Memory is scoped to the corporate customer rather than the analyst, so
          any reviewer sees the same history. On a re-assessment the agent
          recalls what the bank concluded last time and reports what changed.
        </p>
      </div>

      <div className="panel" style={{ marginBottom: 20 }}>
        <div className="panel-body">
          <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
            <label className="field" style={{ flex: 1, marginBottom: 0 }}>
              <span className="field-label">Customer (memory actor)</span>
              <select
                value={customerId}
                onChange={(event) => setCustomerId(event.target.value)}
              >
                {customers.map((customer) => (
                  <option key={customer.id} value={customer.id}>
                    {customer.id} — {customer.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="btn btn-ghost"
              onClick={() => void load(customerId)}
              disabled={busy}
            >
              Refresh
            </button>
          </div>

          {view && (
            <dl className="kv" style={{ marginTop: 16 }}>
              <dt>Memory ID</dt>
              <dd>{view.memory_id}</dd>
              <dt>Sessions</dt>
              <dd>{view.session_count}</dd>
              <dt>Events</dt>
              <dd>{view.event_count}</dd>
              <dt>Extracted records</dt>
              <dd>{view.records.length}</dd>
            </dl>
          )}
        </div>
      </div>

      {error && <div className="error" style={{ marginBottom: 14 }}>{error}</div>}

      {busy && !view && <div className="panel"><div className="empty"><span className="spinner" /></div></div>}

      {view && view.events.length === 0 && !busy && (
        <div className="panel">
          <div className="empty">
            <div className="empty-mark">∅</div>
            <div>
              No assessment history for {customerId} yet.
              <br />
              <span className="muted">
                Run an assessment on the Assessment tab, then return here.
              </span>
            </div>
          </div>
        </div>
      )}

      {view && view.events.length > 0 && (
        <div className="grid-2" style={{ gridTemplateColumns: "1fr 1fr" }}>
          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">
                Short-term memory — assessment timeline
              </span>
            </div>
            <div className="panel-body">
              <div className="timeline">
                {view.events.map((event, index) => {
                  const turns = eventText(event)
                  return (
                    <div className="tl-item" key={event.eventId ?? index}>
                      <div className="tl-time">
                        {formatTime(event.eventTimestamp)} ·{" "}
                        {event.sessionId?.slice(0, 18)}
                      </div>
                      {turns.map((turn, turnIndex) => (
                        <div className="tl-body" key={turnIndex}>
                          <span className="chip" style={{ marginRight: 8 }}>
                            {turn.role}
                          </span>
                          {turn.text}
                        </div>
                      ))}
                    </div>
                  )
                })}
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">
                Long-term memory — extracted records
              </span>
            </div>
            <div className="panel-body">
              {view.records.length === 0 ? (
                <div className="muted">
                  No records extracted yet. The semantic and summary strategies
                  run asynchronously after an assessment — records typically
                  appear within a few minutes, and are what the agent retrieves
                  on the next review.
                </div>
              ) : (
                <div className="stack">
                  {view.records.map((record: any, index: number) => (
                    <div key={record.memoryRecordId ?? index}>
                      <span className="field-label">
                        {record.namespaces?.[0] ?? record.memoryStrategyId ?? "record"}
                      </span>
                      <p style={{ fontSize: 13, color: "var(--text-mid)" }}>
                        {typeof record.content === "string"
                          ? record.content
                          : record.content?.text ?? JSON.stringify(record.content)}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
