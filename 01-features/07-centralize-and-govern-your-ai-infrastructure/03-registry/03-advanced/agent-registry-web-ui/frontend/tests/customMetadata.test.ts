// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Example-based unit tests for the custom metadata logic module. The
// property tests in customMetadata.property.test.ts cover the general rules;
// these pin specific cases from the design.
import { describe, it, expect } from "vitest";
import type { CustomMetadataSchemaConfiguration } from "@aws-sdk/client-agent-registry-control";
import {
  buildSearchFilter,
  complianceLabel,
  exampleFilter,
  parseSchema,
  validateMetadata,
  type MetadataField,
} from "../src/components/customMetadata";

const schema = (properties: Record<string, unknown>, required: string[] = []) =>
  JSON.stringify({ type: "object", properties, required });

/** The docs' example: common owner/dataClassification, AGENT override. */
const DOCS_CONFIG: CustomMetadataSchemaConfiguration = {
  defaultSchema: schema(
    {
      owner: { type: "string" },
      dataClassification: { type: "string", enum: ["public", "internal", "confidential"] },
    },
    ["owner"],
  ),
  recordTypeSchemaOverrides: [
    {
      recordType: "AGENT",
      schema: schema(
        {
          dataClassification: {
            type: "string",
            enum: ["public", "internal", "confidential", "restricted"],
          },
          modelFamily: { type: "string" },
        },
        ["dataClassification"],
      ),
    },
  ],
};

describe("parseSchema", () => {
  it("returns no fields when the config is absent (Req 4.3)", () => {
    expect(parseSchema(undefined, "AGENT")).toEqual({ ok: true, fields: [] });
    expect(parseSchema({}, "MCP")).toEqual({ ok: true, fields: [] });
  });

  it("merges the docs' owner/dataClassification AGENT example", () => {
    expect(parseSchema(DOCS_CONFIG, "AGENT")).toEqual({
      ok: true,
      fields: [
        { name: "owner", type: "text", required: true, source: "common" },
        {
          name: "dataClassification",
          type: "enum",
          required: true,
          enumValues: ["public", "internal", "confidential", "restricted"],
          source: "common",
        },
        { name: "modelFamily", type: "text", required: false, source: "recordType" },
      ],
    });
  });

  it("gives other record types only the common attributes", () => {
    expect(parseSchema(DOCS_CONFIG, "MCP")).toEqual({
      ok: true,
      fields: [
        { name: "owner", type: "text", required: true, source: "common" },
        {
          name: "dataClassification",
          type: "enum",
          required: false,
          enumValues: ["public", "internal", "confidential"],
          source: "common",
        },
      ],
    });
  });

  it("reads field-level title and description", () => {
    const r = parseSchema(
      {
        defaultSchema: schema({
          owner: { type: "string", title: "Owner team", description: "Who runs it" },
        }),
      },
      "MCP",
    );
    expect(r).toEqual({
      ok: true,
      fields: [
        {
          name: "owner",
          type: "text",
          required: false,
          title: "Owner team",
          description: "Who runs it",
          source: "common",
        },
      ],
    });
  });

  it("rejects a format other than uri, naming the schema and field (Req 4.5)", () => {
    const r = parseSchema(
      { defaultSchema: schema({ contact: { type: "string", format: "email" } }) },
      "MCP",
    );
    expect(r.ok).toBe(false);
    if (!r.ok) {
      expect(r.error).toContain("default schema");
      expect(r.error).toContain('"contact"');
    }
  });
});

describe("validateMetadata", () => {
  const fields: MetadataField[] = [
    { name: "owner", type: "text", required: true, source: "common" },
    { name: "runbookUrl", type: "url", required: false, source: "common" },
    {
      name: "dataClassification",
      type: "enum",
      required: false,
      enumValues: ["public", "internal", "confidential"],
      source: "common",
    },
    { name: "handlesPii", type: "boolean", required: false, source: "common" },
  ];

  it("accepts 128 characters and rejects 129 (Req 5.2)", () => {
    expect(validateMetadata(fields, { owner: "a".repeat(128) })).toEqual([]);
    const errors = validateMetadata(fields, { owner: "a".repeat(129) });
    expect(errors).toHaveLength(1);
    expect(errors[0].field).toBe("owner");
    expect(errors[0].message).toContain("128");
  });

  it("accepts mailto:x as a URL and rejects a value without a scheme (Req 5.3)", () => {
    expect(validateMetadata(fields, { owner: "sre", runbookUrl: "mailto:x" })).toEqual([]);
    const errors = validateMetadata(fields, { owner: "sre", runbookUrl: "example.com" });
    expect(errors.map((e) => e.field)).toEqual(["runbookUrl"]);
  });

  it("counts false as a value, so required fields apply", () => {
    const errors = validateMetadata(fields, { handlesPii: false });
    expect(errors).toEqual([{ field: "owner", message: "owner is required" }]);
  });

  it("lists the allowed values for a bad enum value (Req 5.4)", () => {
    const errors = validateMetadata(fields, { owner: "sre", dataClassification: "secret" });
    expect(errors).toHaveLength(1);
    expect(errors[0].field).toBe("dataClassification");
    expect(errors[0].message).toContain("public, internal, confidential");
  });
});

describe("buildSearchFilter", () => {
  const filterFields: MetadataField[] = [
    {
      name: "dataClassification",
      type: "enum",
      required: true,
      enumValues: ["public", "internal", "confidential", "restricted"],
      source: "common",
    },
    { name: "handlesPii", type: "boolean", required: false, source: "common" },
  ];

  it("builds the Data Models example (Req 10.4, 10.5)", () => {
    expect(
      buildSearchFilter("AGENT", filterFields, {
        dataClassification: ["confidential", "restricted"],
        handlesPii: true,
      }),
    ).toEqual({
      $and: [
        { recordType: { $eq: "AGENT" } },
        { "customMetadata.dataClassification": { $in: ["confidential", "restricted"] } },
        { "customMetadata.handlesPii": { $eq: true } },
      ],
    });
  });

  it("returns a single condition without $and", () => {
    expect(buildSearchFilter("", filterFields, { dataClassification: ["internal"] })).toEqual({
      "customMetadata.dataClassification": { $eq: "internal" },
    });
  });
});

describe("exampleFilter (Req 11.1, 11.2)", () => {
  it("uses the first value of an Enum attribute that comes first", () => {
    expect(exampleFilter(DOCS_CONFIG)).toEqual({
      "customMetadata.dataClassification": { $eq: "public" },
    });
  });

  it("uses true for a Boolean attribute that comes first", () => {
    const config = {
      defaultSchema: schema({
        owner: { type: "string" },
        handlesPii: { type: "boolean" },
        tier: { type: "string", enum: ["gold"] },
      }),
    };
    expect(exampleFilter(config)).toEqual({ "customMetadata.handlesPii": { $eq: true } });
  });

  it("returns undefined when there are only Text attributes", () => {
    const config = { defaultSchema: schema({ owner: { type: "string" } }) };
    expect(exampleFilter(config)).toBeUndefined();
    expect(exampleFilter(undefined)).toBeUndefined();
  });
});

describe("complianceLabel (Req 8.5)", () => {
  it("maps compliance status to an indicator", () => {
    expect(complianceLabel("COMPLIANT")).toEqual({ type: "success", label: "Compliant" });
    expect(complianceLabel("NON_COMPLIANT")).toEqual({ type: "warning", label: "Non-compliant" });
    expect(complianceLabel(undefined)).toEqual({ type: "none", label: "-" });
  });
});
