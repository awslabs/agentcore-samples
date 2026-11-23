// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Federate the Cognito ID token into temporary IAM credentials.
 *
 * Two calls against cognito-identity, both unsigned (they authenticate with the
 * ID token itself):
 *   GetId                       -> an identity ID for this user
 *   GetCredentialsForIdentity   -> short-lived access key, secret, session token
 *
 * The credentials are cached until shortly before expiry, because a long
 * assessment must not have them rotate out from under it mid-request.
 */

import type { RuntimeConfig } from "./config"
import type { AwsCredentials } from "./sigv4"

const TARGET_PREFIX = "AWSCognitoIdentityService"

/** Refresh this long before real expiry, so no request starts on a stale key. */
const EXPIRY_MARGIN_MS = 5 * 60 * 1000

interface CachedCredentials extends AwsCredentials {
  expiresAt: number
}

let cached: CachedCredentials | null = null
let inFlight: Promise<AwsCredentials> | null = null

export class CredentialsError extends Error {}

async function call<T>(
  region: string,
  action: string,
  body: unknown
): Promise<T> {
  const response = await fetch(`https://cognito-identity.${region}.amazonaws.com/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": `${TARGET_PREFIX}.${action}`,
    },
    body: JSON.stringify(body),
  })

  const parsed = await response.json().catch(() => ({}))
  if (!response.ok) {
    const code = String(parsed?.__type ?? "").split("#").pop()
    throw new CredentialsError(
      `${action} failed (${code ?? response.status}): ${parsed?.message ?? "unknown error"}`
    )
  }
  return parsed as T
}

/** Reset the cache — call on sign-out so the next user starts clean. */
export function clearCredentials(): void {
  cached = null
  inFlight = null
}

/**
 * Return valid signing credentials, fetching or refreshing as needed.
 *
 * Concurrent callers share one in-flight request rather than each triggering
 * their own federation round trip.
 */
export async function getCredentials(
  config: RuntimeConfig,
  idToken: string
): Promise<AwsCredentials> {
  if (cached && cached.expiresAt - Date.now() > EXPIRY_MARGIN_MS) {
    return cached
  }
  if (inFlight) return inFlight

  if (!config.identityPoolId) {
    throw new CredentialsError(
      "No identity pool configured — the console cannot sign API requests."
    )
  }

  const login = `cognito-idp.${config.region}.amazonaws.com/${config.userPoolId}`

  inFlight = (async () => {
    const { IdentityId } = await call<{ IdentityId: string }>(
      config.region,
      "GetId",
      {
        IdentityPoolId: config.identityPoolId,
        Logins: { [login]: idToken },
      }
    )

    const result = await call<{
      Credentials: {
        AccessKeyId: string
        SecretKey: string
        SessionToken: string
        Expiration: number
      }
    }>(config.region, "GetCredentialsForIdentity", {
      IdentityId,
      Logins: { [login]: idToken },
    })

    const raw = result.Credentials
    if (!raw?.AccessKeyId || !raw.SecretKey || !raw.SessionToken) {
      throw new CredentialsError("Identity pool returned incomplete credentials.")
    }

    cached = {
      accessKeyId: raw.AccessKeyId,
      secretAccessKey: raw.SecretKey,
      sessionToken: raw.SessionToken,
      // Expiration is epoch seconds.
      expiresAt: Number(raw.Expiration) * 1000,
    }
    return cached
  })()

  try {
    return await inFlight
  } finally {
    inFlight = null
  }
}
