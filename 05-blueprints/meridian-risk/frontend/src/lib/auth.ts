// Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

/**
 * Cognito authentication.
 *
 * Calls the Cognito Identity Provider API directly with USER_PASSWORD_AUTH
 * rather than pulling in Amplify or amazon-cognito-identity-js: the console
 * needs exactly one flow, and a ~40-line fetch avoids a large dependency and
 * lets the login screen keep its own design.
 *
 * The ID token is held in sessionStorage — cleared when the tab closes, and not
 * shared with other tabs. A production console would prefer a refresh-token
 * rotation in an httpOnly cookie; sessionStorage is the pragmatic choice for a
 * demo that must survive a page reload mid-presentation.
 */

import type { RuntimeConfig } from "./config"

const TOKEN_KEY = "meridian.idToken"
const EMAIL_KEY = "meridian.email"

const IDP_TARGET = "AWSCognitoIdentityProviderService.InitiateAuth"

export interface Session {
  idToken: string
  email: string
  expiresAt: number
}

/** Decode a JWT payload without verifying it — the API does the verification. */
function decodePayload(token: string): Record<string, unknown> {
  const segment = token.split(".")[1]
  if (!segment) throw new Error("Malformed token")
  const json = atob(segment.replace(/-/g, "+").replace(/_/g, "/"))
  return JSON.parse(json)
}

export class AuthError extends Error {}

/**
 * Sign in with email and password.
 *
 * @throws AuthError with a human-readable message on bad credentials or when
 *   Cognito requires a challenge this console does not implement.
 */
export async function signIn(
  config: RuntimeConfig,
  email: string,
  password: string
): Promise<Session> {
  const endpoint = `https://cognito-idp.${config.region}.amazonaws.com/`

  const response = await fetch(endpoint, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-amz-json-1.1",
      "X-Amz-Target": IDP_TARGET,
    },
    body: JSON.stringify({
      AuthFlow: "USER_PASSWORD_AUTH",
      ClientId: config.userPoolClientId,
      AuthParameters: { USERNAME: email, PASSWORD: password },
    }),
  })

  const body = await response.json().catch(() => ({}))

  if (!response.ok) {
    const code = String(body?.__type ?? "").split("#").pop()
    // Cognito's raw messages leak internals; map the common ones.
    const message =
      code === "NotAuthorizedException"
        ? "Incorrect email or password."
        : code === "UserNotFoundException"
          ? "Incorrect email or password."
          : code === "TooManyRequestsException"
            ? "Too many attempts. Wait a moment and try again."
            : (body?.message ?? `Sign-in failed (HTTP ${response.status})`)
    throw new AuthError(message)
  }

  // A challenge means the account needs setup this console cannot complete
  // (e.g. FORCE_CHANGE_PASSWORD). Terraform sets a permanent password to avoid
  // this, so surface it plainly rather than silently failing.
  if (body?.ChallengeName) {
    throw new AuthError(
      `This account requires an additional step (${body.ChallengeName}) that the console does not support. Reset the password with the AWS CLI.`
    )
  }

  const idToken: string | undefined = body?.AuthenticationResult?.IdToken
  if (!idToken) throw new AuthError("Cognito returned no ID token.")

  const claims = decodePayload(idToken)
  const session: Session = {
    idToken,
    email: String(claims.email ?? email),
    expiresAt: Number(claims.exp ?? 0) * 1000,
  }

  sessionStorage.setItem(TOKEN_KEY, idToken)
  sessionStorage.setItem(EMAIL_KEY, session.email)
  return session
}

/** Restore a session from sessionStorage, discarding it if expired. */
export function restoreSession(): Session | null {
  const idToken = sessionStorage.getItem(TOKEN_KEY)
  if (!idToken) return null

  try {
    const claims = decodePayload(idToken)
    const expiresAt = Number(claims.exp ?? 0) * 1000
    // Treat a token expiring within a minute as already gone, so a long
    // assessment does not fail partway through on a stale token.
    if (expiresAt - Date.now() < 60_000) {
      signOut()
      return null
    }
    return {
      idToken,
      email: sessionStorage.getItem(EMAIL_KEY) ?? String(claims.email ?? ""),
      expiresAt,
    }
  } catch {
    signOut()
    return null
  }
}

export function signOut(): void {
  sessionStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(EMAIL_KEY)
}

export function currentToken(): string | null {
  return sessionStorage.getItem(TOKEN_KEY)
}
