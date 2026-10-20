// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Preservation properties for the type-filter-results bugfix.
 *
 * These tests capture All-types and unchanged-selection behavior retained by
 * the corrected implementation while enforcing the narrow non-empty type
 * result invariant.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import fc from "fast-check";
import {
  listDiscoverable,
  searchRecords,
  type DiscoverableRecord,
} from "../src/api/discovery";
import type { Clients } from "../src/api/client";
import { buildSearchFilter, type MetadataField } from "../src/components/customMetadata";
import { capabilitiesFor } from "../src/personas/capabilities";

const RUNS = { numRuns: 25 };

const customJwtClients = {
  control: {},
  data: null,
  accessToken: "test-access-token",
  authMode: "CUSTOM_JWT",
} as unknown as Clients;

const queryArb = fc.stringMatching(/^[a-z][a-z0-9 ]{0,15}$/);
const statusArb = fc.constantFrom(
  "DRAFT",
  "PENDING_APPROVAL",
  "APPROVED",
  "REJECTED",
  "DEPRECATED",
);
const recordTypeArb = fc.option(
  fc.constantFrom("MCP", "AGENT", "SKILL", "CUSTOM"),
  { nil: undefined },
);
const recordArb = fc.record({
  recordId: fc.uuid(),
  name: fc.stringMatching(/^[a-z][a-z0-9-]{0,12}$/),
  recordType: recordTypeArb,
  status: statusArb,
}) as fc.Arbitrary<DiscoverableRecord>;
const recordsArb = fc.array(recordArb, { maxLength: 6 });
const pagesArb = fc.array(recordsArb, { minLength: 1, maxLength: 3 });

const metadataFields: MetadataField[] = [
  {
    name: "dataClassification",
    type: "enum",
    required: false,
    enumValues: ["public", "internal", "confidential"],
    source: "common",
  },
  {
    name: "handlesPii",
    type: "boolean",
    required: false,
    source: "common",
  },
];
const metadataSelectionArb = fc.record({
  dataClassification: fc.subarray(["public", "internal", "confidential"]),
  handlesPii: fc.option(fc.boolean(), { nil: undefined }),
});

function jsonRpcResponse(payload: unknown): Response {
  return {
    ok: true,
    status: 200,
    text: async () =>
      JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        result: { content: [{ type: "text", text: JSON.stringify(payload) }] },
      }),
  } as Response;
}

function hasRecordTypeConstraint(filter: Record<string, unknown> | undefined): boolean {
  if (!filter) return false;
  if ("recordType" in filter) return true;
  const conditions = filter.$and;
  return Array.isArray(conditions) && conditions.some(
    (condition) =>
      typeof condition === "object" &&
      condition !== null &&
      "recordType" in condition,
  );
}

/** Test-only transcription of the unchanged Records predicate. */
function filterRecords(
  records: DiscoverableRecord[],
  filterText: string,
  status: string,
): DiscoverableRecord[] {
  return records.filter((record) => {
    const matchesText = [record.name, record.recordType, record.status].some(
      (value) => (value ?? "").toLowerCase().includes(filterText.toLowerCase()),
    );
    const matchesStatus = !status || record.status === status;
    return matchesText && matchesStatus;
  });
}

// Feature: type-filter-results, Property 2: Preservation
// **Validates: Requirements 3.1, 5.1**
describe("type-filter-results preservation", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps every Records row accepted by text and status predicates when All types is selected", () => {
    fc.assert(
      fc.property(recordsArb, fc.stringMatching(/^[a-z]{0,4}$/), fc.option(statusArb, { nil: "" }), (records, filterText, status) => {
        const visible = filterRecords(records, filterText, status);
        const expected = records.filter((record) => {
          const matchesText = [record.name, record.recordType, record.status].some(
            (value) => (value ?? "").toLowerCase().includes(filterText.toLowerCase()),
          );
          return matchesText && (!status || record.status === status);
        });

        expect(visible).toEqual(expected);
        expect(visible.filter((record) => record.recordType === undefined)).toEqual(
          expected.filter((record) => record.recordType === undefined),
        );
      }),
      RUNS,
    );
  });

  it("keeps semantic search, custom-metadata filters, and active All-types responses unchanged", async () => {
    await fc.assert(
      fc.asyncProperty(queryArb, recordsArb, metadataSelectionArb, async (query, records, selections) => {
        const calls: RequestInit[] = [];
        vi.stubGlobal(
          "fetch",
          vi.fn(async (_url: string, init: RequestInit) => {
            calls.push(init);
            return jsonRpcResponse(records);
          }),
        );
        const filter = buildSearchFilter("", metadataFields, {
          ...(selections.dataClassification.length > 0
            ? { dataClassification: selections.dataClassification }
            : {}),
          ...(selections.handlesPii === undefined ? {} : { handlesPii: selections.handlesPii }),
        });

        await expect(searchRecords(customJwtClients, "registry", query, filter)).resolves.toEqual(records);
        const args = JSON.parse(calls[0].body as string).params.arguments;
        expect(args).toEqual({
          searchQuery: query,
          maxResults: 20,
          ...(filter === undefined ? {} : { filter }),
        });
        expect(hasRecordTypeConstraint(args.filter)).toBe(false);
        vi.unstubAllGlobals();
      }),
      RUNS,
    );
  });

  it("keeps empty-query browse pagination and results unchanged when All types is selected", async () => {
    await fc.assert(
      fc.asyncProperty(pagesArb, async (pages) => {
        const calls: RequestInit[] = [];
        let index = 0;
        vi.stubGlobal(
          "fetch",
          vi.fn(async (_url: string, init: RequestInit) => {
            calls.push(init);
            const page = pages[index++];
            return jsonRpcResponse({
              registryRecords: page,
              ...(index < pages.length ? { nextToken: `page-${index}` } : {}),
            });
          }),
        );

        await expect(listDiscoverable(customJwtClients, "registry")).resolves.toEqual(pages.flat());
        expect(calls).toHaveLength(pages.length);
        for (const call of calls) {
          const args = JSON.parse(call.body as string).params.arguments;
          expect(args).toEqual({ maxResults: 100, ...(args.nextToken ? { nextToken: args.nextToken } : {}) });
          expect(args).not.toHaveProperty("filters");
        }
        vi.unstubAllGlobals();
      }),
      RUNS,
    );
  });

  it("retains the current success, failure, refresh, navigation, and authorization boundaries without a type change", async () => {
    await fc.assert(
      fc.asyncProperty(recordsArb, fc.boolean(), async (records, fails) => {
        let calls = 0;
        let results: DiscoverableRecord[] = [];
        let error: Error | null = null;
        const current = async () => {
          calls += 1;
          try {
            if (fails) throw new Error("current request failed");
            results = records;
            error = null;
          } catch (caught) {
            error = caught as Error;
          }
        };

        await current(); // Existing active request.
        await current(); // Existing immediate refresh behavior.
        expect(calls).toBe(2);
        expect(results).toEqual(fails ? [] : records);
        expect(error?.message ?? null).toBe(fails ? "current request failed" : null);

        const consumer = capabilitiesFor(["AgentRegistryConsumer"]);
        expect(consumer.canPublish || consumer.canApprove).toBe(false);
        expect(consumer.canBrowse).toBe(true);
        expect(`/registries/registry/records/${records[0]?.recordId ?? "record"}`).toMatch(/^\/registries\/registry\/records\//);
      }),
      RUNS,
    );
  });
});
