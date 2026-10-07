// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import type { Persona } from "../personas/capabilities";

export interface FriendlyError {
  title: string;
  message: string;
  isAccessDenied: boolean;
}

/**
 * Map an SDK or MCP-endpoint error to a user-facing message.
 *
 * Two failure vocabularies reach this function, because the app talks to the
 * service two ways:
 *   - SDK (SigV4): AWS exception names + `$metadata.httpStatusCode`.
 *   - MCP endpoint (bearer token, CUSTOM_JWT registries): an `McpError` carrying a
 *     bare HTTP `status`. A 401/403 there means the TOKEN was rejected by the
 *     registry's JWT authorizer, which is a different fix from an IAM denial, so it
 *     gets its own message.
 *
 * AccessDenied is the expected signal when a persona attempts an action outside its
 * scoped IAM role -- surface it as a role explanation rather than a raw stack.
 */
export function toFriendlyError(
  err: unknown,
  persona?: Persona,
): FriendlyError {
  const e = err as {
    name?: string;
    message?: string;
    status?: number;
    isAuthFailure?: boolean;
    $metadata?: { httpStatusCode?: number };
  };
  const name = e?.name ?? "";
  const status = e?.$metadata?.httpStatusCode;

  // The registry's JWT authorizer rejected the bearer token.
  if (name === "McpError" && e?.isAuthFailure) {
    return {
      title: "Registry rejected the token",
      message:
        "The registry's JWT authorizer rejected this session's Cognito token. The token may " +
        "have expired (sign out and back in), or this app client is not in the registry's " +
        "allowed clients list.",
      isAccessDenied: true,
    };
  }
  // Any other MCP transport failure (unreachable endpoint, non-JSON body, tool error).
  if (name === "McpError") {
    return {
      title: "Discovery failed",
      message: e?.message ?? "The registry MCP endpoint could not be reached.",
      isAccessDenied: false,
    };
  }

  const isAccessDenied =
    name === "AccessDeniedException" ||
    name === "AccessDenied" ||
    name === "NotAuthorizedException" ||
    status === 403;

  if (isAccessDenied) {
    return {
      title: "Not permitted",
      message: persona
        ? `Your role (${persona}) is not allowed to perform this action. This is enforced by IAM, not just the UI.`
        : "You are not permitted to perform this action.",
      isAccessDenied: true,
    };
  }

  if (name === "ResourceNotFoundException" || status === 404) {
    return {
      title: "Not found",
      message: e?.message ?? "The resource was not found.",
      isAccessDenied: false,
    };
  }
  if (name === "ValidationException" || status === 400) {
    return {
      title: "Invalid request",
      message: e?.message ?? "The request failed validation.",
      isAccessDenied: false,
    };
  }
  if (name === "ThrottlingException" || status === 429) {
    return {
      title: "Throttled",
      message: "Too many requests. Try again shortly.",
      isAccessDenied: false,
    };
  }

  return {
    title: "Error",
    message: e?.message ?? "An unexpected error occurred.",
    isAccessDenied: false,
  };
}
