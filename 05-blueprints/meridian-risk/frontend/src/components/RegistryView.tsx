// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** AgentCore Registry — browse the governed catalog, search it, approve records. */

import { useEffect, useState } from "react"
import { api, type RegistryRecord } from "../lib/api"

const TYPE_BLURB: Record<string, string> = {
  MCP: "MCP server",
  A2A: "A2A agent card",
  AGENT_SKILLS: "Agent skill",
  CUSTOM: "Custom resource",
}

/** Next status in the governance workflow, or null if terminal. */
function nextStatus(status: string): { status: string; label: string } | null {
  if (status === "DRAFT")
    return { status: "PENDING_APPROVAL", label: "Submit for approval" }
  if (status === "PENDING_APPROVAL")
    return { status: "APPROVED", label: "Approve" }
  return null
}

function descriptorContent(record: RegistryRecord): string | null {
  const d = record.descriptors ?? {}
  const raw =
    d.mcp?.server?.inlineContent ??
    d.a2a?.agentCard?.inlineContent ??
    d.agentSkills?.skillMd?.inlineContent ??
    d.custom?.inlineContent
  if (!raw) return null
  // Agent skills are markdown; the rest are JSON worth pretty-printing.
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}

function toolsOf(record: RegistryRecord): string[] {
  const raw = record.descriptors?.mcp?.tools?.inlineContent
  if (!raw) return []
  try {
    return (JSON.parse(raw).tools ?? []).map((t: any) => t.name)
  } catch {
    return []
  }
}

export function RegistryView() {
  const [records, setRecords] = useState<RegistryRecord[]>([])
  const [registryId, setRegistryId] = useState("")
  const [query, setQuery] = useState("")
  const [searchResults, setSearchResults] = useState<RegistryRecord[] | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = async () => {
    setError(null)
    try {
      const data = await api.registryRecords()
      setRecords(data.records)
      setRegistryId(data.registry_id)
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const search = async () => {
    if (!query.trim()) {
      setSearchResults(null)
      return
    }
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const data = await api.registrySearch(query)
      setSearchResults(data.records)
      if (data.records.length === 0) {
        setNotice(
          "No matches. Search covers APPROVED records only, and newly approved records take about 30 seconds to index."
        )
      }
    } catch (exc) {
      setError((exc as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const advance = async (record: RegistryRecord) => {
    const next = nextStatus(record.status)
    if (!next) return
    setBusy(true)
    setError(null)
    try {
      await api.setRecordStatus(
        record.recordId,
        next.status,
        "Advanced from the demo console by the platform governance team."
      )
      await load()
    } catch (exc) {
      setError((exc as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const shown = searchResults ?? records
  const approvedCount = records.filter((r) => r.status === "APPROVED").length

  return (
    <>
      <div className="section-head">
        <div className="eyebrow">AgentCore Registry — preview</div>
        <h2>Governed AI resource catalog</h2>
        <p>
          Every agent, skill, and MCP server this platform exposes is catalogued
          here, so teams discover what already exists instead of rebuilding it.
          Records pass through an approval workflow before they become
          discoverable, and CloudTrail audits every access.
        </p>
      </div>

      <div className="panel" style={{ marginBottom: 20 }}>
        <div className="panel-head">
          <span className="panel-title">Semantic discovery</span>
          <span className="muted mono">
            {records.length} records · {approvedCount} approved ·{" "}
            {registryId || "—"}
          </span>
        </div>
        <div className="panel-body">
          <div style={{ display: "flex", gap: 8 }}>
            <input
              type="text"
              value={query}
              placeholder="Ask in plain language — e.g. which agent can screen for sanctions exposure?"
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => event.key === "Enter" && void search()}
            />
            <button
              className="btn"
              style={{ width: "auto", whiteSpace: "nowrap" }}
              onClick={() => void search()}
              disabled={busy}
            >
              Search
            </button>
            {searchResults && (
              <button
                className="btn btn-ghost"
                onClick={() => {
                  setSearchResults(null)
                  setQuery("")
                  setNotice(null)
                }}
              >
                Clear
              </button>
            )}
          </div>
          {searchResults && (
            <div className="muted" style={{ marginTop: 10 }}>
              {searchResults.length} approved record(s) matched — ranked by
              semantic relevance.
            </div>
          )}
        </div>
      </div>

      {error && <div className="error" style={{ marginBottom: 14 }}>{error}</div>}
      {notice && <div className="panel" style={{ marginBottom: 14 }}>
        <div className="panel-body muted">{notice}</div>
      </div>}

      {shown.length === 0 && !error ? (
        <div className="panel">
          <div className="empty">
            <div className="empty-mark">◇</div>
            <div>No records to show.</div>
          </div>
        </div>
      ) : (
        shown.map((record) => {
          const next = nextStatus(record.status)
          const tools = toolsOf(record)
          const content = descriptorContent(record)
          const isOpen = expanded === record.recordId

          return (
            <div className="record" key={record.recordId}>
              <div className="record-head">
                <span className="record-name">{record.name}</span>
                <span className="chip chip-amber">
                  {TYPE_BLURB[record.descriptorType] ?? record.descriptorType}
                </span>
                <span className={`chip status-${record.status}`}>
                  {record.status.replace(/_/g, " ")}
                </span>
                <span style={{ flex: 1 }} />
                <div className="btn-row">
                  {content && (
                    <button
                      className="btn btn-ghost"
                      onClick={() => setExpanded(isOpen ? null : record.recordId)}
                    >
                      {isOpen ? "Hide descriptor" : "View descriptor"}
                    </button>
                  )}
                  {next && (
                    <button
                      className="btn btn-ghost"
                      onClick={() => void advance(record)}
                      disabled={busy}
                    >
                      {next.label}
                    </button>
                  )}
                </div>
              </div>

              {record.description && (
                <p className="record-desc">{record.description}</p>
              )}

              {tools.length > 0 && (
                <div className="chip-row" style={{ marginTop: 9 }}>
                  {tools.map((tool) => (
                    <span key={tool} className="chip chip-tool">
                      {tool}
                    </span>
                  ))}
                </div>
              )}

              {record.statusReason && (
                <div className="muted" style={{ marginTop: 7 }}>
                  {record.statusReason}
                </div>
              )}

              {isOpen && content && (
                <pre className="json" style={{ marginTop: 12 }}>
                  {content}
                </pre>
              )}
            </div>
          )
        })
      )}
    </>
  )
}
