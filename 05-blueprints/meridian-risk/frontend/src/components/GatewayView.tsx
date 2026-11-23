// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** AgentCore Gateway — inspect the MCP tool catalog and invoke a tool directly. */

import { useEffect, useState } from "react"
import { api, type DemoCustomer, type GatewayInfo, type GatewayTool } from "../lib/api"

interface Props {
  customers: DemoCustomer[]
}

export function GatewayView({ customers }: Props) {
  const [info, setInfo] = useState<GatewayInfo | null>(null)
  const [selected, setSelected] = useState<GatewayTool | null>(null)
  const [customerId, setCustomerId] = useState(customers[0]?.id ?? "CUST001")
  const [output, setOutput] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .gatewayTools()
      .then((data) => {
        setInfo(data)
        const first = data.targets.flatMap((t) => t.tools)[0]
        if (first) setSelected(first)
      })
      .catch((exc) => setError((exc as Error).message))
  }, [])

  const invoke = async () => {
    if (!selected) return
    setBusy(true)
    setError(null)
    setOutput(null)
    try {
      const data = await api.invokeTool(selected.name, customerId)
      setOutput(data.result)
    } catch (exc) {
      setError((exc as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const tools = info?.targets.flatMap((t) => t.tools) ?? []

  return (
    <>
      <div className="section-head">
        <div className="eyebrow">AgentCore Gateway</div>
        <h2>KYC tool catalog over MCP</h2>
        <p>
          One Lambda target, five tools, exposed to the agents as an MCP server.
          Inbound authorization is AWS IAM, so the Runtime's execution role
          signs each call with SigV4 — no bearer tokens or secrets to manage.
        </p>
      </div>

      {error && <div className="error" style={{ marginBottom: 14 }}>{error}</div>}

      {info && (
        <div className="panel" style={{ marginBottom: 20 }}>
          <div className="panel-body">
            <dl className="kv">
              <dt>Endpoint</dt>
              <dd>{info.gateway_url}</dd>
              <dt>Protocol</dt>
              <dd>MCP streamable-http · SigV4 (AWS_IAM)</dd>
              {/*
                One gateway, two planes. Tool targets answer tools/list and
                tools/call over MCP; the inference target answers OpenAI- and
                Anthropic-shaped requests on /inference. Labelling the kind
                keeps a model target from reading as a broken tool target,
                since it has no tools by design.
              */}
              <dt>Targets</dt>
              <dd>
                {info.targets.length === 0
                  ? "—"
                  : info.targets.map((target, index) => (
                      <span key={target.target_id}>
                        {index > 0 && ", "}
                        {target.name}
                        {target.kind && (
                          <span
                            className={
                              target.kind === "inference"
                                ? "chip chip-amber"
                                : "chip chip-tool"
                            }
                            style={{ marginLeft: 6 }}
                            title={
                              target.kind === "inference"
                                ? "Model plane — routes LLM traffic to Bedrock"
                                : "Tool plane — exposes MCP tools"
                            }
                          >
                            {target.kind === "inference" ? "model" : target.kind}
                          </span>
                        )}{" "}
                        <span className="muted">({target.status})</span>
                      </span>
                    ))}
              </dd>
              <dt>Tools</dt>
              <dd>{info.tool_count}</dd>
            </dl>
          </div>
        </div>
      )}

      <div className="grid-2">
        <div className="panel">
          <div className="panel-head">
            <span className="panel-title">Available tools</span>
          </div>
          <div className="panel-body">
            {tools.length === 0 ? (
              <div className="muted">Loading tool catalog…</div>
            ) : (
              tools.map((tool) => (
                <button
                  key={tool.name}
                  className="tool"
                  aria-pressed={selected?.name === tool.name}
                  onClick={() => {
                    setSelected(tool)
                    setOutput(null)
                  }}
                  style={{ display: "block", width: "100%", textAlign: "left" }}
                >
                  <div className="tool-name">{tool.name}</div>
                  <div className="tool-desc">{tool.description}</div>
                </button>
              ))
            )}
          </div>
        </div>

        <div className="stack">
          <div className="panel">
            <div className="panel-head">
              <span className="panel-title">
                {selected ? `Invoke ${selected.name}` : "Invoke a tool"}
              </span>
            </div>
            <div className="panel-body">
              <label className="field">
                <span className="field-label">customer_id</span>
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
                className="btn"
                onClick={() => void invoke()}
                disabled={busy || !selected}
              >
                {busy ? "Invoking…" : "Invoke tool"}
              </button>
            </div>
          </div>

          {selected?.inputSchema && (
            <div className="panel">
              <div className="panel-head">
                <span className="panel-title">Input schema</span>
              </div>
              <div className="panel-body">
                <pre className="json">
                  {JSON.stringify(selected.inputSchema, null, 2)}
                </pre>
              </div>
            </div>
          )}

          {output != null && (
            <div className="panel">
              <div className="panel-head">
                <span className="panel-title">Response</span>
              </div>
              <div className="panel-body">
                <pre className="json">{JSON.stringify(output, null, 2)}</pre>
              </div>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
