// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import type { DescriptorSource, RecordType } from "../api/records";
import { config } from "../config";

/**
 * A record's descriptor content comes from exactly ONE of two places:
 *
 *  - "inline"  — you paste the descriptor payload (`data` + `dataSchemaVersion`).
 *  - "fromUrl" — you give the registry the LIVE endpoint URL. The registry connects
 *                to it, introspects the server and tool definitions, and populates
 *                the descriptors itself (also overwriting name/description/version
 *                when it finds them at the source). This is what you want for an
 *                AgentCore Gateway or Runtime: point at its MCP endpoint.
 *
 * The URL is NOT a link to a static `server.json` document — it is the endpoint the
 * registry invokes. Which is why it needs outbound credentials.
 *
 * Sync is only supported on the `mcpServer` and `a2aAgentCard` descriptors; the
 * `agentSkillsDefinition` and `custom` branches have no `source` field at all.
 */
export type SourceMode = "inline" | "fromUrl";

/** Which outbound credential the registry uses when it invokes the source URL. */
export type CredentialMode = "OAUTH" | "IAM" | "NONE";

/** Record types whose descriptor supports URL synchronization. */
export const SYNCABLE_RECORD_TYPES: RecordType[] = ["MCP", "AGENT"];

export function supportsSource(recordType: RecordType): boolean {
  return SYNCABLE_RECORD_TYPES.includes(recordType);
}

export interface SourceConfig {
  mode: SourceMode;
  url: string;
  credentialMode: CredentialMode;
  /** OAUTH: ARN of an AgentCore Identity OAuth2 credential provider. */
  oauthProviderArn: string;
  /** OAUTH: space/comma separated scopes requested with the client_credentials grant. */
  oauthScopes: string;
  /** IAM: role the registry assumes to SigV4-sign the request. */
  iamRoleArn: string;
  /** IAM: signing service name. `bedrock-agentcore` for AgentCore Gateway / Runtime. */
  iamService: string;
  /** IAM: signing region. Defaults to the registry's region when blank. */
  iamRegion: string;
}

/**
 * Defaults. The credential mode defaults to OAUTH against the AgentCore Identity
 * provider this sample deploys, which is itself wired to the same Cognito user pool
 * that fronts this app — so a record pointing at the sample's Gateway authenticates
 * with a Cognito machine-to-machine token out of the box, with nothing to fill in.
 */
export function defaultSourceConfig(): SourceConfig {
  return {
    mode: "inline",
    url: "",
    credentialMode: "OAUTH",
    oauthProviderArn: config.oauthCredentialProviderArn ?? "",
    oauthScopes: config.oauthCredentialProviderScopes ?? "",
    iamRoleArn: "",
    // AgentCore Gateway and Runtime sources sign as `bedrock-agentcore` (verified live:
    // an AWS_IAM gateway rejects `agent-registry`-signed requests with HTTP 401).
    iamService: "bedrock-agentcore",
    iamRegion: config.region ?? "",
  };
}

/** Split a user-entered scope list on commas and/or whitespace. */
export function parseScopes(raw: string): string[] {
  return raw
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * Validate the source step. Returns every problem so the wizard can block Next with
 * a complete message rather than one error at a time.
 */
export function validateSource(
  recordType: RecordType,
  s: SourceConfig,
): string[] {
  if (s.mode === "inline") return [];
  const errors: string[] = [];

  if (!supportsSource(recordType)) {
    errors.push(
      `${recordType} records cannot synchronize from a URL — only MCP and AGENT descriptors support a source. Use an inline descriptor.`,
    );
    return errors;
  }

  const url = s.url.trim();
  if (!url) {
    errors.push("Source URL is required when synchronizing from an endpoint.");
  } else {
    let parsed: URL | undefined;
    try {
      parsed = new URL(url);
    } catch {
      errors.push("Source URL must be a valid absolute URL.");
    }
    if (parsed && parsed.protocol !== "https:") {
      errors.push("Source URL must use https.");
    }
  }

  if (s.credentialMode === "OAUTH" && !s.oauthProviderArn.trim()) {
    errors.push(
      "An AgentCore Identity OAuth credential provider ARN is required. Deploy one with deploy/setup/06-sample-mcp-gateway.sh, or choose a different credential type.",
    );
  }
  if (s.credentialMode === "IAM") {
    if (!s.iamRoleArn.trim())
      errors.push("An IAM role ARN is required for SigV4 signing.");
    if (!s.iamService.trim())
      errors.push("A signing service name is required for SigV4 signing.");
  }
  return errors;
}

/**
 * Build the descriptor `source` for the API, or undefined for an inline descriptor.
 * Optional fields are omitted rather than sent empty, so the service applies its own
 * defaults (e.g. deriving the signing region from the URL / registry).
 */
export function buildSource(s: SourceConfig): DescriptorSource | undefined {
  if (s.mode !== "fromUrl") return undefined;

  const url = s.url.trim();
  if (!url) return undefined;

  if (s.credentialMode === "NONE") {
    return { fromUrl: { url } };
  }

  if (s.credentialMode === "OAUTH") {
    const scopes = parseScopes(s.oauthScopes);
    return {
      fromUrl: {
        url,
        credentialProviderConfigurations: [
          {
            credentialProviderType: "OAUTH",
            credentialProvider: {
              oauthCredentialProvider: {
                providerArn: s.oauthProviderArn.trim(),
                // The only grant type the registry supports for a source.
                grantType: "CLIENT_CREDENTIALS",
                ...(scopes.length > 0 ? { scopes } : {}),
              },
            },
          },
        ],
      },
    };
  }

  const region = s.iamRegion.trim();
  return {
    fromUrl: {
      url,
      credentialProviderConfigurations: [
        {
          credentialProviderType: "IAM",
          credentialProvider: {
            iamCredentialProvider: {
              roleArn: s.iamRoleArn.trim(),
              service: s.iamService.trim(),
              ...(region ? { region } : {}),
            },
          },
        },
      ],
    },
  };
}

/** Human-readable one-liner for the review step and the record detail page. */
export function describeCredential(s: SourceConfig): string {
  switch (s.credentialMode) {
    case "OAUTH":
      return `OAuth 2.0 (AgentCore Identity, client_credentials)${
        parseScopes(s.oauthScopes).length
          ? ` · scopes: ${parseScopes(s.oauthScopes).join(" ")}`
          : ""
      }`;
    case "IAM":
      return `IAM SigV4 as ${s.iamRoleArn || "(role not set)"} · service ${s.iamService}`;
    default:
      return "None (public endpoint)";
  }
}

export interface SourceSummary {
  url: string;
  /** UNKNOWN = the read path did not disclose the credential, not "there isn't one". */
  credentialType: "OAUTH" | "IAM" | "NONE" | "UNKNOWN";
  /** Provider ARN (OAUTH) or role ARN (IAM); empty otherwise. */
  credentialRef: string;
  /** Extra detail: scopes for OAUTH, signing service/region for IAM. */
  detail: string;
}

/**
 * Read a stored descriptor union and summarize its source, or return null when the
 * record carries an inline descriptor. Written defensively because this runs against
 * both the control-plane and data-plane record shapes.
 *
 * `readVia` matters for correctness, not cosmetics: the DATA-PLANE (discovery) view
 * returns `source.fromUrl` with the url ONLY — it strips
 * `credentialProviderConfigurations`, because an outbound credential reference is not
 * a consumer's business. Verified live 2026-09-04. So an absent credential means
 * "public endpoint" only when we read the control plane; over the data plane it means
 * "not disclosed", and claiming "None" there would be a lie.
 */
export function summarizeDescriptorSource(
  descriptors: unknown,
  readVia: "control" | "data" = "control",
): SourceSummary | null {
  if (!descriptors || typeof descriptors !== "object") return null;
  const branches = descriptors as Record<
    string,
    { source?: unknown } | undefined
  >;
  for (const key of [
    "mcpServer",
    "a2aAgentCard",
    "http",
    "agui",
    "agentSkillsMd",
  ]) {
    const fromUrl = (branches[key]?.source as { fromUrl?: unknown } | undefined)
      ?.fromUrl as
      | {
          url?: string;
          credentialProviderConfigurations?: Array<{
            credentialProviderType?: string;
            credentialProvider?: {
              oauthCredentialProvider?: {
                providerArn?: string;
                scopes?: string[];
              };
              iamCredentialProvider?: {
                roleArn?: string;
                service?: string;
                region?: string;
              };
            };
          }>;
        }
      | undefined;
    if (!fromUrl?.url) continue;

    const cfg = fromUrl.credentialProviderConfigurations?.[0];
    const oauth = cfg?.credentialProvider?.oauthCredentialProvider;
    const iam = cfg?.credentialProvider?.iamCredentialProvider;

    if (oauth?.providerArn) {
      return {
        url: fromUrl.url,
        credentialType: "OAUTH",
        credentialRef: oauth.providerArn,
        detail: oauth.scopes?.length
          ? `scopes: ${oauth.scopes.join(" ")}`
          : "client_credentials",
      };
    }
    if (iam?.roleArn) {
      return {
        url: fromUrl.url,
        credentialType: "IAM",
        credentialRef: iam.roleArn,
        detail: [
          iam.service && `service ${iam.service}`,
          iam.region && `region ${iam.region}`,
        ]
          .filter(Boolean)
          .join(" · "),
      };
    }
    return readVia === "data"
      ? {
          url: fromUrl.url,
          credentialType: "UNKNOWN",
          credentialRef: "",
          detail: "the discovery view does not expose outbound credentials",
        }
      : {
          url: fromUrl.url,
          credentialType: "NONE",
          credentialRef: "",
          detail: "public endpoint",
        };
  }
  return null;
}
