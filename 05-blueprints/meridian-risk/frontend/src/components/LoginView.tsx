// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/** Sign-in screen for the demo console. */

import { useState } from "react"
import { AuthError, signIn, type Session } from "../lib/auth"
import type { RuntimeConfig } from "../lib/config"
import type { Theme } from "../lib/theme"
import { ThemeToggle } from "./ThemeToggle"

interface Props {
  config: RuntimeConfig
  onSignedIn: (session: Session) => void
  theme: Theme
  onThemeChange: (theme: Theme) => void
}

export function LoginView({ config, onSignedIn, theme, onThemeChange }: Props) {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!email.trim() || !password) {
      setError("Enter your email and password.")
      return
    }

    setBusy(true)
    setError(null)
    try {
      onSignedIn(await signIn(config, email.trim(), password))
    } catch (exc) {
      setError(
        exc instanceof AuthError
          ? exc.message
          : `Could not reach the identity provider: ${(exc as Error).message}`
      )
      setPassword("")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login">
      {/* Left: the brand plate. Establishes the institutional tone before
          anyone has signed in. */}
      <aside className="login-plate">
        <div className="login-plate-inner">
          <div className="wordmark login-wordmark">
            Meridian <em>Risk</em>
          </div>
          <div className="login-rule" />
          <h1 className="login-headline">
            Corporate onboarding,
            <br />
            <em>underwritten by agents.</em>
          </h1>
          <p className="login-blurb">
            Credit analysis and AML compliance screening run in parallel against
            a governed tool catalog, then synthesize a single, auditable
            onboarding decision.
          </p>

          <dl className="login-stack">
            <div>
              <dt>Runtime</dt>
              <dd>Multi-agent KYC orchestration</dd>
            </div>
            <div>
              <dt>Harness</dt>
              <dd>Managed agent loop, as config</dd>
            </div>
            <div>
              <dt>Gateway</dt>
              <dd>Five KYC data tools over MCP</dd>
            </div>
            <div>
              <dt>Policy</dt>
              <dd>Cedar authorization on every request</dd>
            </div>
            <div>
              <dt>Guardrail</dt>
              <dd>PII + prompt-injection rail (deployed)</dd>
            </div>
            <div>
              <dt>Registry</dt>
              <dd>Governed catalog and discovery</dd>
            </div>
            <div>
              <dt>Memory</dt>
              <dd>Assessment history per customer</dd>
            </div>
            <div>
              <dt>Observability</dt>
              <dd>Unified traces in CloudWatch</dd>
            </div>
          </dl>

          <div className="login-foot">
            Amazon Bedrock AgentCore · {config.region}
          </div>
        </div>
      </aside>

      {/* Right: the form. */}
      <main className="login-form-side">
        <div className="login-toggle">
          <ThemeToggle theme={theme} onChange={onThemeChange} />
        </div>

        <form className="login-form" onSubmit={submit}>
          <div className="eyebrow">Risk desk access</div>
          <h2 className="login-title">Sign in</h2>
          <p className="login-sub">
            Authenticate with your Amazon Cognito credentials to reach the
            onboarding desk.
          </p>

          <label className="field">
            <span className="field-label">Email</span>
            <input
              type="email"
              autoComplete="username"
              autoFocus
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              disabled={busy}
              placeholder="you@example.com"
            />
          </label>

          <label className="field">
            <span className="field-label">Password</span>
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={busy}
              placeholder="••••••••••••"
            />
          </label>

          {error && (
            <div className="error" role="alert" style={{ marginBottom: 14 }}>
              {error}
            </div>
          )}

          <button className="btn" type="submit" disabled={busy}>
            {busy ? "Authenticating…" : "Sign in"}
          </button>

          <div className="login-note">
            Sessions last 8 hours and are cleared when this tab closes.
            Synthetic data only — no real customer records.
          </div>
        </form>
      </main>
    </div>
  )
}
