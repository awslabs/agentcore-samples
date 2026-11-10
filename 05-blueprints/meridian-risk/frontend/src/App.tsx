// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { useEffect, useState } from "react"
import { api, configureApi, UnauthorizedError, type Config } from "./lib/api"
import { authRequired, loadConfig, type RuntimeConfig } from "./lib/config"
import { restoreSession, signOut, type Session } from "./lib/auth"
import { clearCredentials } from "./lib/credentials"
import {
  applyTheme,
  initialTheme,
  persistTheme,
  watchSystemTheme,
  type Theme,
} from "./lib/theme"
import { ThemeToggle } from "./components/ThemeToggle"
import { LoginView } from "./components/LoginView"
import { AssessmentView } from "./components/AssessmentView"
import { RegistryView } from "./components/RegistryView"
import { GatewayView } from "./components/GatewayView"
import { MemoryView } from "./components/MemoryView"
import { ArchitectureView } from "./components/ArchitectureView"

type Tab = "assessment" | "registry" | "gateway" | "memory" | "architecture"

const TABS: { id: Tab; label: string; service: string }[] = [
  { id: "assessment", label: "Assessment", service: "Runtime" },
  { id: "registry", label: "Catalog", service: "Registry" },
  { id: "gateway", label: "Tools", service: "Gateway" },
  { id: "memory", label: "History", service: "Memory" },
  { id: "architecture", label: "How it works", service: "Architecture" },
]

export default function App() {
  const [runtime, setRuntime] = useState<RuntimeConfig | null>(null)
  const [session, setSession] = useState<Session | null>(null)
  const [config, setConfig] = useState<Config | null>(null)
  const [tab, setTab] = useState<Tab>("assessment")
  const [error, setError] = useState<string | null>(null)
  const [booted, setBooted] = useState(false)
  const [theme, setTheme] = useState<Theme>(initialTheme)

  // Reflect the theme on <html>, where styles.css reads it.
  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  // Follow the OS preference until the user picks a theme explicitly.
  useEffect(() => watchSystemTheme(setTheme), [])

  // Load runtime config, point the API client at it, then restore any session.
  useEffect(() => {
    loadConfig()
      .then((loaded) => {
        setRuntime(loaded)
        configureApi(loaded)
        setSession(restoreSession())
      })
      .catch((exc) => setError((exc as Error).message))
      .finally(() => setBooted(true))
  }, [])

  const needsLogin = runtime ? authRequired(runtime) && !session : false

  // Fetch server config once authenticated (or when auth is not required).
  useEffect(() => {
    if (!booted || !runtime || needsLogin) return
    api
      .config()
      .then(setConfig)
      .catch((exc) => {
        if (exc instanceof UnauthorizedError) {
          // Token expired or was revoked — fall back to the login screen.
          signOut()
          clearCredentials()
          setSession(null)
          return
        }
        setError((exc as Error).message)
      })
  }, [booted, runtime, needsLogin])

  const chooseTheme = (next: Theme) => {
    persistTheme(next)
    setTheme(next)
  }

  const logout = () => {
    signOut()
    clearCredentials()
    setSession(null)
    setConfig(null)
    setTab("assessment")
  }

  if (!booted) {
    return (
      <div className="boot">
        <span className="spinner" />
      </div>
    )
  }

  if (needsLogin && runtime) {
    return (
      <LoginView
        config={runtime}
        onSignedIn={setSession}
        theme={theme}
        onThemeChange={chooseTheme}
      />
    )
  }

  const customers = config?.demo_customers ?? []

  return (
    <div className="app">
      <header className="masthead">
        <div className="wordmark">
          Meridian <em>Risk</em>
        </div>
        <div className="masthead-rule" />
        <div className="masthead-meta">
          <div>
            Desk
            <b>KYC Onboarding</b>
          </div>
          <div>
            Region
            <b className="mono">{config?.region ?? runtime?.region ?? "—"}</b>
          </div>
          <div>
            Platform
            <b>Bedrock AgentCore</b>
          </div>
          {session && (
            <div>
              Signed in
              <b>{session.email}</b>
            </div>
          )}
        </div>
        <ThemeToggle theme={theme} onChange={chooseTheme} />

        {session && (
          <button className="btn btn-ghost" onClick={logout}>
            Sign out
          </button>
        )}
      </header>

      <nav className="tabs" role="tablist">
        {TABS.map((entry, index) => (
          <button
            key={entry.id}
            className="tab"
            role="tab"
            aria-selected={tab === entry.id}
            onClick={() => setTab(entry.id)}
          >
            <span className="tab-index mono">0{index + 1}</span>
            {entry.label}
            <span className="tab-index">/ {entry.service}</span>
          </button>
        ))}
      </nav>

      <main>
        {error && (
          <div className="error">
            Cannot reach the console API: {error}
            <br />
            <span className="muted">
              Locally, start it with ./scripts/dev.sh. If the stack was just
              deployed, run scripts/write_env.py and restart the API.
            </span>
          </div>
        )}

        {config && !config.configured && (
          <div className="error" style={{ marginBottom: 16 }}>
            Some services are not configured. Run{" "}
            <code>python3 scripts/write_env.py</code> after{" "}
            <code>terraform apply</code>, then restart the API.
          </div>
        )}

        {config && (
          <>
            {tab === "assessment" && <AssessmentView customers={customers} />}
            {tab === "registry" && <RegistryView />}
            {tab === "gateway" && <GatewayView customers={customers} />}
            {tab === "memory" && <MemoryView customers={customers} />}
            {tab === "architecture" && <ArchitectureView config={config} />}
          </>
        )}
      </main>
    </div>
  )
}
