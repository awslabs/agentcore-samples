// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Runtime configuration.
 *
 * Fetched from /config.json rather than baked in at build time, so the same
 * bundle can be redeployed against a different stack without a rebuild. The
 * file is written by scripts/deploy_frontend.py.
 *
 * Local development has no config.json; the defaults below point at the Vite
 * dev proxy, and an absent Cognito pool means auth is bypassed (matching the
 * API's AUTH_DISABLED mode).
 */

export interface RuntimeConfig {
  apiBase: string
  region: string
  userPoolId: string
  userPoolClientId: string
  /** Federates the ID token into IAM credentials for SigV4 request signing. */
  identityPoolId: string
}

const LOCAL_DEFAULTS: RuntimeConfig = {
  apiBase: "",
  region: "us-east-1",
  userPoolId: "",
  userPoolClientId: "",
  identityPoolId: "",
}

let cached: RuntimeConfig | null = null

export async function loadConfig(): Promise<RuntimeConfig> {
  if (cached) return cached

  try {
    const response = await fetch("/config.json", { cache: "no-store" })
    if (response.ok) {
      const loaded: RuntimeConfig = {
        ...LOCAL_DEFAULTS,
        ...(await response.json()),
      }
      cached = loaded
      return loaded
    }
  } catch {
    /* No config.json in local dev — fall through to the defaults. */
  }

  cached = LOCAL_DEFAULTS
  return LOCAL_DEFAULTS
}

/** True when Cognito is configured and the console must show a login screen. */
export function authRequired(config: RuntimeConfig): boolean {
  return Boolean(config.userPoolId && config.userPoolClientId)
}
