// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest";
import { toFriendlyError } from "../src/api/errors";

// Pins the SDK-error -> user-message mapping. The AccessDenied path is
// load-bearing: it is how the UI explains that IAM (not the UI) blocked an
// out-of-role action, which is the whole point of the persona demo.

describe("toFriendlyError", () => {
  it("maps AccessDeniedException to a role explanation", () => {
    const fe = toFriendlyError({ name: "AccessDeniedException" }, "Consumer");
    expect(fe.isAccessDenied).toBe(true);
    expect(fe.title).toBe("Not permitted");
    expect(fe.message).toContain("Consumer");
    expect(fe.message).toContain("IAM");
  });

  it("treats AccessDenied, NotAuthorizedException and HTTP 403 all as access-denied", () => {
    expect(toFriendlyError({ name: "AccessDenied" }).isAccessDenied).toBe(true);
    expect(
      toFriendlyError({ name: "NotAuthorizedException" }).isAccessDenied,
    ).toBe(true);
    expect(
      toFriendlyError({ $metadata: { httpStatusCode: 403 } }).isAccessDenied,
    ).toBe(true);
  });

  it("gives a generic access-denied message when no persona is supplied", () => {
    const fe = toFriendlyError({ name: "AccessDenied" });
    expect(fe.isAccessDenied).toBe(true);
    expect(fe.message).toBe("You are not permitted to perform this action.");
  });

  it("maps not-found (name or 404)", () => {
    expect(
      toFriendlyError({ name: "ResourceNotFoundException", message: "gone" }),
    ).toMatchObject({
      title: "Not found",
      message: "gone",
      isAccessDenied: false,
    });
    expect(toFriendlyError({ $metadata: { httpStatusCode: 404 } }).title).toBe(
      "Not found",
    );
  });

  it("maps validation (name or 400)", () => {
    expect(
      toFriendlyError({ name: "ValidationException", message: "bad" }).title,
    ).toBe("Invalid request");
    expect(toFriendlyError({ $metadata: { httpStatusCode: 400 } }).title).toBe(
      "Invalid request",
    );
  });

  it("maps throttling (name or 429) with a fixed message", () => {
    const fe = toFriendlyError({ name: "ThrottlingException" });
    expect(fe.title).toBe("Throttled");
    expect(fe.message).toBe("Too many requests. Try again shortly.");
    expect(toFriendlyError({ $metadata: { httpStatusCode: 429 } }).title).toBe(
      "Throttled",
    );
  });

  it("falls back to a generic error carrying the original message", () => {
    expect(toFriendlyError({ name: "Whatever", message: "boom" })).toEqual({
      title: "Error",
      message: "boom",
      isAccessDenied: false,
    });
    expect(toFriendlyError({}).message).toBe("An unexpected error occurred.");
  });

  it("maps an MCP auth failure to a token-specific message, still flagged access-denied", () => {
    const fe = toFriendlyError(
      {
        name: "McpError",
        message: "HTTP 403",
        status: 403,
        isAuthFailure: true,
      },
      "Consumer",
    );
    expect(fe.isAccessDenied).toBe(true);
    expect(fe.title).toMatch(/token/i);
    expect(fe.message).toMatch(/allowed clients|expired/i);
    // It must NOT blame the IAM role: the bearer token is what was rejected.
    expect(fe.message).not.toMatch(/IAM/);
  });

  it("maps a non-auth MCP failure to a discovery error, not access-denied", () => {
    const fe = toFriendlyError(
      {
        name: "McpError",
        message: "Could not reach the registry MCP endpoint",
        isAuthFailure: false,
      },
      "Consumer",
    );
    expect(fe.isAccessDenied).toBe(false);
    expect(fe.title).toBe("Discovery failed");
    expect(fe.message).toMatch(/MCP endpoint/);
  });
});
