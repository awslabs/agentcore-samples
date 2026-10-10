// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import type { Descriptors, DescriptorSource, RecordType } from "../api/records";

// Per-descriptor schema versions, verified live against AWS Agent Registry
// (2026-09-02). The service validates `data` against the official protocol
// schema for the given version, so these must match a supported version AND
// the payload must be valid for that schema.
export const SCHEMA_VERSION: Record<string, string> = {
  MCP: "2025-12-11", // MCP server.json schema
  AGENT: "0.3", // A2A agent card
  SKILL: "0.1.0", // AgentSkills definition
};

/** Minimal VALID descriptor payloads per record type (validated by the service). */
export const DESCRIPTOR_TEMPLATES: Partial<Record<RecordType, string>> = {
  MCP: JSON.stringify(
    {
      name: "my-org/my-mcp-server",
      description: "What this MCP server provides",
      version: "1.0.0",
    },
    null,
    2,
  ),
  AGENT: JSON.stringify(
    {
      name: "My Agent",
      description: "What this agent does",
      version: "1.0.0",
      protocolVersion: "0.3.0",
      url: "https://api.example.com/a2a",
      capabilities: {},
      defaultInputModes: ["text/plain"],
      defaultOutputModes: ["text/plain"],
      skills: [
        {
          id: "default-skill",
          name: "Default Skill",
          description: "What this skill does",
          tags: ["general"],
        },
      ],
    },
    null,
    2,
  ),
  SKILL: JSON.stringify(
    {
      websiteUrl: "https://example.com/my-skill",
      repository: {
        url: "https://github.com/example/my-skill",
        source: "github",
      },
    },
    null,
    2,
  ),
  CUSTOM: JSON.stringify({ anyKey: "any value" }, null, 2),
};

/**
 * Build the tagged descriptor union for the given record type.
 *
 * Two mutually exclusive shapes:
 *  - no `source`  -> inline descriptor: `{ data, dataSchemaVersion }`.
 *  - with `source` -> synchronized descriptor: `{ source }` ONLY. `data` is
 *    deliberately omitted, because the registry connects to the source URL and
 *    populates the descriptor content itself; sending both would mean declaring
 *    content that the sync is about to overwrite.
 *
 * Only `mcpServer` and `a2aAgentCard` have a `source` field — `agentSkillsDefinition`
 * and `custom` do not, so a source is ignored for those types (the UI blocks it).
 */
export function buildDescriptors(
  recordType: RecordType,
  data: string,
  source?: DescriptorSource,
): Descriptors {
  switch (recordType) {
    case "MCP":
      return source
        ? { mcpServer: { source } }
        : { mcpServer: { data, dataSchemaVersion: SCHEMA_VERSION.MCP } };
    case "AGENT":
      return source
        ? { a2aAgentCard: { source } }
        : { a2aAgentCard: { data, dataSchemaVersion: SCHEMA_VERSION.AGENT } };
    case "SKILL":
      return {
        agentSkillsDefinition: {
          data,
          dataSchemaVersion: SCHEMA_VERSION.SKILL,
        },
      };
    default:
      // CUSTOM (and any other type, e.g. GATEWAY) use the custom branch: any valid JSON, no version.
      return { custom: { data } };
  }
}

export function isValidJson(s: string): boolean {
  try {
    JSON.parse(s);
    return true;
  } catch {
    return false;
  }
}
