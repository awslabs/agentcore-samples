// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest";
import {
  SCHEMA_VERSION,
  DESCRIPTOR_TEMPLATES,
  buildDescriptors,
  isValidJson,
} from "../src/components/descriptors";
import type { RecordType } from "../src/api/records";

// Pins the descriptor schema versions and the tagged-union construction. The
// AWS Agent Registry service validates `data` against the official schema for
// the declared version, so a drift here silently breaks record creation.

describe("SCHEMA_VERSION", () => {
  it("locks the per-type schema versions verified live against the service", () => {
    expect(SCHEMA_VERSION).toEqual({
      MCP: "2025-12-11",
      AGENT: "0.3",
      SKILL: "0.1.0",
    });
  });
});

describe("buildDescriptors", () => {
  it("MCP -> mcpServer branch with the MCP schema version", () => {
    expect(buildDescriptors("MCP", '{"x":1}')).toEqual({
      mcpServer: { data: '{"x":1}', dataSchemaVersion: "2025-12-11" },
    });
  });

  it("AGENT -> a2aAgentCard branch with the A2A schema version", () => {
    expect(buildDescriptors("AGENT", '{"x":1}')).toEqual({
      a2aAgentCard: { data: '{"x":1}', dataSchemaVersion: "0.3" },
    });
  });

  it("SKILL -> agentSkillsDefinition branch with the skills schema version", () => {
    expect(buildDescriptors("SKILL", '{"x":1}')).toEqual({
      agentSkillsDefinition: { data: '{"x":1}', dataSchemaVersion: "0.1.0" },
    });
  });

  it("CUSTOM -> custom branch with NO schema version", () => {
    expect(buildDescriptors("CUSTOM", '{"x":1}')).toEqual({
      custom: { data: '{"x":1}' },
    });
  });

  it("an unknown/extra type (e.g. GATEWAY) falls back to the custom branch", () => {
    expect(buildDescriptors("GATEWAY" as RecordType, '{"x":1}')).toEqual({
      custom: { data: '{"x":1}' },
    });
  });
});

describe("DESCRIPTOR_TEMPLATES", () => {
  it("provides a template for MCP, AGENT, SKILL and CUSTOM", () => {
    expect(Object.keys(DESCRIPTOR_TEMPLATES).sort()).toEqual([
      "AGENT",
      "CUSTOM",
      "MCP",
      "SKILL",
    ]);
  });

  it("every template is itself valid JSON", () => {
    for (const [type, tpl] of Object.entries(DESCRIPTOR_TEMPLATES)) {
      expect(
        isValidJson(tpl as string),
        `${type} template must be valid JSON`,
      ).toBe(true);
    }
  });

  it("templates carry the fields the service requires per type", () => {
    const mcp = JSON.parse(DESCRIPTOR_TEMPLATES.MCP!);
    expect(mcp).toMatchObject({
      name: expect.any(String),
      version: expect.any(String),
    });

    const agent = JSON.parse(DESCRIPTOR_TEMPLATES.AGENT!);
    expect(agent).toMatchObject({
      protocolVersion: expect.any(String),
      url: expect.any(String),
      skills: expect.any(Array),
    });

    const skill = JSON.parse(DESCRIPTOR_TEMPLATES.SKILL!);
    expect(skill).toMatchObject({
      websiteUrl: expect.any(String),
      repository: { source: expect.any(String) },
    });
  });
});

describe("isValidJson", () => {
  it("accepts valid JSON", () => {
    expect(isValidJson("{}")).toBe(true);
    expect(isValidJson('{"a":[1,2,3]}')).toBe(true);
    expect(isValidJson("123")).toBe(true);
  });
  it("rejects invalid JSON", () => {
    expect(isValidJson("")).toBe(false);
    expect(isValidJson("{not json")).toBe(false);
    expect(isValidJson("undefined")).toBe(false);
  });
});
