// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest";
import {
  SYNCABLE_RECORD_TYPES,
  supportsSource,
  defaultSourceConfig,
  parseScopes,
  validateSource,
  buildSource,
  describeCredential,
  summarizeDescriptorSource,
  type SourceConfig,
} from "../src/components/source";
import { buildDescriptors } from "../src/components/descriptors";

// The descriptor `source` is the one place the registry reaches OUT to an endpoint on
// the publisher's behalf, so the wire shape and the guardrails around it are pinned
// here: which record types may sync, what credential is attached, and the fact that a
// synchronized descriptor must NOT also carry inline data.

const sourced = (over: Partial<SourceConfig> = {}): SourceConfig => ({
  ...defaultSourceConfig(),
  mode: "fromUrl",
  url: "https://gw-abc.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
  oauthProviderArn:
    "arn:aws:bedrock-agentcore:us-east-1:111122223333:token-vault/default/oauth2credentialprovider/p",
  ...over,
});

describe("which record types can synchronize", () => {
  it("only mcpServer and a2aAgentCard descriptors have a source field", () => {
    expect(SYNCABLE_RECORD_TYPES).toEqual(["MCP", "AGENT"]);
    expect(supportsSource("MCP")).toBe(true);
    expect(supportsSource("AGENT")).toBe(true);
    expect(supportsSource("SKILL")).toBe(false);
    expect(supportsSource("CUSTOM")).toBe(false);
  });

  it("rejects a source on a type that cannot carry one", () => {
    const errors = validateSource("SKILL", sourced());
    expect(errors).toHaveLength(1);
    expect(errors[0]).toMatch(/only MCP and AGENT/i);
  });
});

describe("defaults", () => {
  it("defaults to inline authoring with OAuth preselected for when you switch", () => {
    const d = defaultSourceConfig();
    expect(d.mode).toBe("inline");
    expect(d.credentialMode).toBe("OAUTH");
  });

  it("defaults the SigV4 signing service to bedrock-agentcore", () => {
    // AgentCore Gateway and Runtime sources sign as bedrock-agentcore (an AWS_IAM
    // gateway rejects agent-registry-signed requests with HTTP 401).
    expect(defaultSourceConfig().iamService).toBe("bedrock-agentcore");
  });
});

describe("validateSource", () => {
  it("passes a well-formed OAuth source", () => {
    expect(validateSource("MCP", sourced())).toEqual([]);
  });

  it("an inline descriptor is always valid and ignores the other fields", () => {
    expect(
      validateSource("CUSTOM", { ...sourced(), mode: "inline", url: "" }),
    ).toEqual([]);
  });

  it("requires a URL", () => {
    expect(validateSource("MCP", sourced({ url: "  " }))).toContainEqual(
      expect.stringMatching(/URL is required/i),
    );
  });

  it("requires an absolute https URL", () => {
    expect(validateSource("MCP", sourced({ url: "not-a-url" }))).toContainEqual(
      expect.stringMatching(/valid absolute URL/i),
    );
    expect(
      validateSource("MCP", sourced({ url: "http://gw.example.com/mcp" })),
    ).toContainEqual(expect.stringMatching(/must use https/i));
  });

  it("requires a provider ARN for the OAuth path", () => {
    expect(
      validateSource("MCP", sourced({ oauthProviderArn: "" })),
    ).toContainEqual(
      expect.stringMatching(/credential provider ARN is required/i),
    );
  });

  it("requires role + service for the IAM path", () => {
    const errors = validateSource(
      "MCP",
      sourced({ credentialMode: "IAM", iamService: "" }),
    );
    expect(errors).toContainEqual(
      expect.stringMatching(/role ARN is required/i),
    );
    expect(errors).toContainEqual(
      expect.stringMatching(/signing service name is required/i),
    );
  });

  it("an unauthenticated source needs only the URL", () => {
    expect(
      validateSource(
        "MCP",
        sourced({ credentialMode: "NONE", oauthProviderArn: "" }),
      ),
    ).toEqual([]);
  });
});

describe("parseScopes", () => {
  it("splits on commas and whitespace and drops empties", () => {
    expect(parseScopes(" a/read ,  b/write\nc/x ")).toEqual([
      "a/read",
      "b/write",
      "c/x",
    ]);
    expect(parseScopes("")).toEqual([]);
    expect(parseScopes("   ")).toEqual([]);
  });
});

describe("buildSource wire shape", () => {
  it("returns undefined for an inline descriptor", () => {
    expect(buildSource({ ...sourced(), mode: "inline" })).toBeUndefined();
  });

  it("builds the OAuth credential provider configuration", () => {
    const s = buildSource(sourced({ oauthScopes: "sample-mcp/invoke" }));
    expect(s).toEqual({
      fromUrl: {
        url: "https://gw-abc.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
        credentialProviderConfigurations: [
          {
            credentialProviderType: "OAUTH",
            credentialProvider: {
              oauthCredentialProvider: {
                providerArn:
                  "arn:aws:bedrock-agentcore:us-east-1:111122223333:token-vault/default/oauth2credentialprovider/p",
                grantType: "CLIENT_CREDENTIALS",
                scopes: ["sample-mcp/invoke"],
              },
            },
          },
        ],
      },
    });
  });

  it("omits scopes entirely when none are given", () => {
    const s = buildSource(sourced({ oauthScopes: "" })) as any;
    const provider =
      s.fromUrl.credentialProviderConfigurations[0].credentialProvider
        .oauthCredentialProvider;
    expect(provider).not.toHaveProperty("scopes");
    expect(provider.grantType).toBe("CLIENT_CREDENTIALS");
  });

  it("builds the IAM credential provider configuration", () => {
    const s = buildSource(
      sourced({
        credentialMode: "IAM",
        iamRoleArn: "arn:aws:iam::111122223333:role/sync",
        iamService: "agent-registry",
        iamRegion: "us-west-2",
      }),
    ) as any;
    expect(s.fromUrl.credentialProviderConfigurations[0]).toEqual({
      credentialProviderType: "IAM",
      credentialProvider: {
        iamCredentialProvider: {
          roleArn: "arn:aws:iam::111122223333:role/sync",
          service: "agent-registry",
          region: "us-west-2",
        },
      },
    });
  });

  it("omits an empty signing region so the service derives it", () => {
    const s = buildSource(
      sourced({
        credentialMode: "IAM",
        iamRoleArn: "arn:role",
        iamRegion: "  ",
      }),
    ) as any;
    expect(
      s.fromUrl.credentialProviderConfigurations[0].credentialProvider
        .iamCredentialProvider,
    ).not.toHaveProperty("region");
  });

  it("attaches no credential configuration at all for a public source", () => {
    const s = buildSource(sourced({ credentialMode: "NONE" })) as any;
    expect(s.fromUrl).toEqual({
      url: "https://gw-abc.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
    });
  });

  it("trims whitespace off the URL and the ARNs", () => {
    const s = buildSource(
      sourced({
        url: "  https://x.example.com/mcp  ",
        oauthProviderArn: " arn:p ",
      }),
    ) as any;
    expect(s.fromUrl.url).toBe("https://x.example.com/mcp");
    expect(
      s.fromUrl.credentialProviderConfigurations[0].credentialProvider
        .oauthCredentialProvider.providerArn,
    ).toBe("arn:p");
  });
});

describe("buildDescriptors with a source", () => {
  const src = buildSource(sourced());

  it("a synchronized MCP descriptor carries ONLY the source, never inline data", () => {
    const d = buildDescriptors("MCP", '{"name":"ignored"}', src) as any;
    expect(d.mcpServer.source).toEqual(src);
    expect(d.mcpServer).not.toHaveProperty("data");
    expect(d.mcpServer).not.toHaveProperty("dataSchemaVersion");
  });

  it("a synchronized AGENT descriptor uses the a2aAgentCard branch", () => {
    const d = buildDescriptors("AGENT", "{}", src) as any;
    expect(d.a2aAgentCard.source).toEqual(src);
    expect(d.a2aAgentCard).not.toHaveProperty("data");
  });

  it("without a source the descriptor is inline with its schema version", () => {
    const d = buildDescriptors("MCP", '{"name":"x"}') as any;
    expect(d.mcpServer.data).toBe('{"name":"x"}');
    expect(d.mcpServer.dataSchemaVersion).toBe("2025-12-11");
    expect(d.mcpServer).not.toHaveProperty("source");
  });

  it("SKILL and CUSTOM ignore a source (their descriptors have no source field)", () => {
    const skill = buildDescriptors("SKILL", "{}", src) as any;
    expect(skill.agentSkillsDefinition).not.toHaveProperty("source");
    expect(skill.agentSkillsDefinition.data).toBe("{}");
    const custom = buildDescriptors("CUSTOM", "{}", src) as any;
    expect(custom.custom).not.toHaveProperty("source");
  });
});

describe("describeCredential", () => {
  it("names the AgentCore Identity grant and any scopes", () => {
    expect(describeCredential(sourced({ oauthScopes: "a/b" }))).toMatch(
      /AgentCore Identity.*client_credentials.*scopes: a\/b/,
    );
  });

  it("names the role and signing service for SigV4", () => {
    expect(
      describeCredential(
        sourced({ credentialMode: "IAM", iamRoleArn: "arn:r" }),
      ),
    ).toMatch(/SigV4 as arn:r · service bedrock-agentcore/);
  });

  it("says so plainly when there is no credential", () => {
    expect(describeCredential(sourced({ credentialMode: "NONE" }))).toMatch(
      /None/,
    );
  });
});

describe("summarizeDescriptorSource", () => {
  it("returns null for an inline descriptor", () => {
    expect(summarizeDescriptorSource({ mcpServer: { data: "{}" } })).toBeNull();
  });

  it("returns null for junk input rather than throwing", () => {
    expect(summarizeDescriptorSource(undefined)).toBeNull();
    expect(summarizeDescriptorSource("nope")).toBeNull();
    expect(summarizeDescriptorSource({ mcpServer: { source: {} } })).toBeNull();
  });

  it("summarizes an OAuth-sourced MCP record", () => {
    const s = summarizeDescriptorSource(
      buildDescriptors("MCP", "", buildSource(sourced({ oauthScopes: "x/y" }))),
    );
    expect(s).toEqual({
      url: "https://gw-abc.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
      credentialType: "OAUTH",
      credentialRef:
        "arn:aws:bedrock-agentcore:us-east-1:111122223333:token-vault/default/oauth2credentialprovider/p",
      detail: "scopes: x/y",
    });
  });

  it("summarizes an IAM-sourced record", () => {
    const s = summarizeDescriptorSource(
      buildDescriptors(
        "MCP",
        "",
        buildSource(
          sourced({
            credentialMode: "IAM",
            iamRoleArn: "arn:r",
            iamRegion: "eu-west-1",
          }),
        ),
      ),
    );
    expect(s?.credentialType).toBe("IAM");
    expect(s?.credentialRef).toBe("arn:r");
    expect(s?.detail).toBe("service bedrock-agentcore · region eu-west-1");
  });

  it("summarizes a public source", () => {
    const s = summarizeDescriptorSource(
      buildDescriptors(
        "MCP",
        "",
        buildSource(sourced({ credentialMode: "NONE" })),
      ),
    );
    expect(s?.credentialType).toBe("NONE");
    expect(s?.credentialRef).toBe("");
  });

  // The data-plane (discovery) record strips credentialProviderConfigurations -- verified
  // live 2026-09-04. Absent credentials there mean "not disclosed", NOT "public", so the
  // summary must not claim NONE or the record page lies about the record's auth.
  it("reports UNKNOWN, not NONE, when a data-plane read omits the credential", () => {
    const stripped = {
      mcpServer: { source: { fromUrl: { url: "https://gw.example.com/mcp" } } },
    };
    const s = summarizeDescriptorSource(stripped, "data");
    expect(s?.credentialType).toBe("UNKNOWN");
    expect(s?.detail).toMatch(/discovery view/i);
  });

  it("still reports NONE for the same shape on a control-plane read", () => {
    const stripped = {
      mcpServer: { source: { fromUrl: { url: "https://gw.example.com/mcp" } } },
    };
    expect(summarizeDescriptorSource(stripped, "control")?.credentialType).toBe(
      "NONE",
    );
    // control is the default
    expect(summarizeDescriptorSource(stripped)?.credentialType).toBe("NONE");
  });

  it("a disclosed OAuth credential is reported regardless of read path", () => {
    const d = buildDescriptors("MCP", "", buildSource(sourced()));
    expect(summarizeDescriptorSource(d, "data")?.credentialType).toBe("OAUTH");
    expect(summarizeDescriptorSource(d, "control")?.credentialType).toBe(
      "OAUTH",
    );
  });

  it("finds a source on the a2aAgentCard branch too", () => {
    const s = summarizeDescriptorSource(
      buildDescriptors("AGENT", "", buildSource(sourced())),
    );
    expect(s?.credentialType).toBe("OAUTH");
  });
});
