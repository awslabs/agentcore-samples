// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

// Pins config resolution + the required-var assertion. We stub import.meta.env
// via vi.stubEnv so the test is independent of whatever .env values exist, which
// is exactly what we want across a scrub that changes the placeholder IDs.

describe("config", () => {
  beforeEach(() => {
    vi.resetModules();
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("assertConfig reports every missing required var and never requires registryId", async () => {
    vi.stubEnv("VITE_AWS_REGION", "");
    vi.stubEnv("VITE_USER_POOL_ID", "");
    vi.stubEnv("VITE_USER_POOL_CLIENT_ID", "");
    vi.stubEnv("VITE_IDENTITY_POOL_ID", "");
    const { assertConfig } = await import("../src/config");
    expect(assertConfig()).toEqual([
      "VITE_AWS_REGION",
      "VITE_USER_POOL_ID",
      "VITE_USER_POOL_CLIENT_ID",
      "VITE_IDENTITY_POOL_ID",
    ]);
  });

  it("assertConfig passes when the four required vars are present (registryId optional)", async () => {
    vi.stubEnv("VITE_AWS_REGION", "us-east-1");
    vi.stubEnv("VITE_USER_POOL_ID", "us-east-1_example");
    vi.stubEnv("VITE_USER_POOL_CLIENT_ID", "client123");
    vi.stubEnv("VITE_IDENTITY_POOL_ID", "us-east-1:guid");
    vi.stubEnv("VITE_REGISTRY_ID", "");
    const { assertConfig } = await import("../src/config");
    expect(assertConfig()).toEqual([]);
  });

  it("cognitoLoginKey is built from region + user pool id", async () => {
    vi.stubEnv("VITE_AWS_REGION", "eu-west-1");
    vi.stubEnv("VITE_USER_POOL_ID", "eu-west-1_pool");
    const { cognitoLoginKey } = await import("../src/config");
    expect(cognitoLoginKey).toBe(
      "cognito-idp.eu-west-1.amazonaws.com/eu-west-1_pool",
    );
  });

  describe("registryAuthMode", () => {
    it("defaults to CUSTOM_JWT when unset or unrecognised", async () => {
      vi.stubEnv("VITE_REGISTRY_AUTH_MODE", "");
      let mod = await import("../src/config");
      expect(mod.config.registryAuthMode).toBe("CUSTOM_JWT");

      vi.resetModules();
      vi.stubEnv("VITE_REGISTRY_AUTH_MODE", "nonsense");
      mod = await import("../src/config");
      expect(mod.config.registryAuthMode).toBe("CUSTOM_JWT");
    });

    it("honours an explicit AWS_IAM", async () => {
      vi.stubEnv("VITE_REGISTRY_AUTH_MODE", "AWS_IAM");
      const { config } = await import("../src/config");
      expect(config.registryAuthMode).toBe("AWS_IAM");
    });
  });

  describe("endpoint builders", () => {
    it("builds the registry MCP endpoint on the GA agent-registry namespace", async () => {
      vi.stubEnv("VITE_AWS_REGION", "us-west-2");
      const { registryMcpEndpoint } = await import("../src/config");
      const url = registryMcpEndpoint("reg-123");
      expect(url).toBe(
        "https://agent-registry.us-west-2.api.aws/registry/reg-123/mcp",
      );
      // Guard against a regression to the deprecated preview namespace.
      expect(url).not.toContain("bedrock-agentcore");
    });

    it("builds the protected-resource metadata URL", async () => {
      vi.stubEnv("VITE_AWS_REGION", "us-west-2");
      const { registryMcpMetadataUrl } = await import("../src/config");
      expect(registryMcpMetadataUrl("reg-123")).toBe(
        "https://agent-registry.us-west-2.api.aws/.well-known/oauth-protected-resource/registry/reg-123/mcp",
      );
    });

    it("builds the Cognito OIDC discovery URL used by the registry's JWT authorizer", async () => {
      vi.stubEnv("VITE_AWS_REGION", "us-east-1");
      vi.stubEnv("VITE_USER_POOL_ID", "us-east-1_abc");
      const { cognitoDiscoveryUrl } = await import("../src/config");
      expect(cognitoDiscoveryUrl()).toBe(
        "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_abc/.well-known/openid-configuration",
      );
    });
  });
});
