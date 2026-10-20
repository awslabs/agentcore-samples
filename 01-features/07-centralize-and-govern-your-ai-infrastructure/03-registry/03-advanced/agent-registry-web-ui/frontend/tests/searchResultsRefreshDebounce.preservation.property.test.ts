// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Preservation properties for the search-results-refresh-debounce bugfix.
 *
 * These describe inputs outside the original bug condition. They verify that
 * the corrected discovery adapter and page-search behavior retain the existing
 * observable outcomes while handling no-match and stale completions.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import fc from "fast-check";
import {
  listDiscoverable,
  searchRecords,
  type DiscoverableRecord,
} from "../src/api/discovery";
import type { Clients } from "../src/api/client";
import { McpError } from "../src/api/mcp";
import { toFriendlyError, type FriendlyError } from "../src/api/errors";

const DEBOUNCE_MS = 300;
const RUNS = { numRuns: 25 };

const customJwtClients = {
  control: {},
  data: null,
  accessToken: "test-access-token",
  authMode: "CUSTOM_JWT",
} as unknown as Clients;

const queryArb = fc.stringMatching(/^[a-z][a-z0-9 ]{0,15}$/);
const recordTypeArb = fc.constantFrom("", "MCP", "AGENT", "SKILL", "CUSTOM");
const recordArb = fc.record({
  recordId: fc.uuid(),
  name: fc.stringMatching(/^[a-z][a-z0-9-]{0,12}$/),
  recordType: fc.constantFrom("MCP", "AGENT", "SKILL", "CUSTOM"),
}) as fc.Arbitrary<DiscoverableRecord>;
const recordsArb = fc.array(recordArb, { maxLength: 4 });
const filterArb = fc.record({
  recordType: fc.constantFrom("MCP", "AGENT", "SKILL", "CUSTOM").map((value) => ({ $eq: value })),
  dataClassification: fc.constantFrom("public", "internal", "confidential").map((value) => ({ $eq: value })),
  handlesPii: fc.boolean().map((value) => ({ $eq: value })),
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

interface DiscoverySnapshot {
  query: string;
  type?: string;
  filter?: Record<string, unknown>;
}

/**
 * Test-only transcription of the current RegistryDetailPage discovery timing
 * boundary. Rendering is not available in this node-only Vitest configuration.
 */
function createCurrentDiscoveryHarness(dependencies: {
  search(snapshot: DiscoverySnapshot): Promise<DiscoverableRecord[]>;
  list(type?: string): Promise<DiscoverableRecord[]>;
}) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let current: DiscoverySnapshot = { query: "" };
  let results: DiscoverableRecord[] = [];
  let error: FriendlyError | null = null;
  let searching = false;
  const requests: DiscoverySnapshot[] = [];

  const run = async (snapshot: DiscoverySnapshot) => {
    searching = true;
    error = null;
    requests.push(snapshot);
    try {
      results = snapshot.query.trim()
        ? await dependencies.search(snapshot)
        : await dependencies.list(snapshot.type);
    } catch (caught) {
      error = toFriendlyError(caught);
    } finally {
      searching = false;
    }
  };

  return {
    edit(snapshot: DiscoverySnapshot) {
      current = snapshot;
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => void run(snapshot), DEBOUNCE_MS);
    },
    refresh() {
      return run(current);
    },
    state: () => ({ results, error, searching, requests: [...requests] }),
  };
}

describe("search result refresh debounce preservation properties", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  // Feature: search-results-refresh-debounce, Property 2: Preservation
  // **Validates: Requirements 3.1, 3.2**
  it("preserves matching semantic records and their filter document for the active snapshot", async () => {
    await fc.assert(
      fc.asyncProperty(queryArb, recordsArb, filterArb, async (query, records, filter) => {
        const calls: RequestInit[] = [];
        vi.stubGlobal(
          "fetch",
          vi.fn(async (_url: string, init: RequestInit) => {
            calls.push(init);
            return jsonRpcResponse(records);
          }),
        );

        await expect(searchRecords(customJwtClients, "registry", query, filter)).resolves.toEqual(records);
        expect(calls).toHaveLength(1);
        const body = JSON.parse(calls[0].body as string);
        expect(body.params).toEqual({
          name: "search_discoverable_registry_records",
          arguments: { searchQuery: query, maxResults: 20, filter },
        });
        vi.unstubAllGlobals();
      }),
      RUNS,
    );
  });

  // Feature: search-results-refresh-debounce, Property 2: Preservation
  // **Validates: Requirements 3.1, 3.2**
  it("preserves empty-query browse results and the existing record-type list filter after debounce", async () => {
    await fc.assert(
      fc.asyncProperty(recordTypeArb, recordsArb, async (recordType, records) => {
        vi.useFakeTimers();
        const calls: RequestInit[] = [];
        vi.stubGlobal(
          "fetch",
          vi.fn(async (_url: string, init: RequestInit) => {
            calls.push(init);
            return jsonRpcResponse({ registryRecords: records });
          }),
        );
        const page = createCurrentDiscoveryHarness({
          search: () => Promise.resolve([]),
          list: (type) => listDiscoverable(customJwtClients, "registry", type),
        });

        page.edit({ query: "", type: recordType || undefined });
        await vi.advanceTimersByTimeAsync(DEBOUNCE_MS - 1);
        expect(page.state().requests).toEqual([]);
        await vi.advanceTimersByTimeAsync(1);
        expect(page.state()).toMatchObject({ results: records, error: null, searching: false });

        const body = JSON.parse(calls[0].body as string);
        expect(body.params).toEqual({
          name: "list_discoverable_registry_records",
          arguments: {
            maxResults: 100,
            ...(recordType
              ? { filters: [{ name: "recordType", values: [recordType] }] }
              : {}),
          },
        });
        vi.useRealTimers();
        vi.unstubAllGlobals();
      }),
      RUNS,
    );
  });

  // Feature: search-results-refresh-debounce, Property 2: Preservation
  // **Validates: Requirements 3.1, 3.2**
  it("keeps a manual refresh immediate and bound to the current query, type, and metadata filters", async () => {
    await fc.assert(
      fc.asyncProperty(queryArb, filterArb, recordsArb, async (query, filter, records) => {
        const requests: DiscoverySnapshot[] = [];
        const current = { query, type: "MCP", filter };
        const page = createCurrentDiscoveryHarness({
          search: async (snapshot) => {
            requests.push(snapshot);
            return records;
          },
          list: async () => [],
        });

        page.edit(current);
        await page.refresh();
        expect(requests).toEqual([current]);
        expect(page.state()).toMatchObject({ results: records, error: null, searching: false });
      }),
      RUNS,
    );
  });

  // Feature: search-results-refresh-debounce, Property 2: Preservation
  // **Validates: Requirements 3.3**
  it("preserves genuine discovery failures as friendly errors rather than empty results", async () => {
    type Failure = "authorization" | "network" | "jsonRpc" | "malformed";
    const failureArb = fc.constantFrom<Failure>("authorization", "network", "jsonRpc", "malformed");

    await fc.assert(
      fc.asyncProperty(failureArb, queryArb, async (failure, query) => {
        vi.stubGlobal(
          "fetch",
          vi.fn(async () => {
            if (failure === "network") throw new Error("network unavailable");
            if (failure === "authorization") {
              return { ok: false, status: 403, text: async () => "denied" } as Response;
            }
            if (failure === "jsonRpc") {
              return {
                ok: true,
                status: 200,
                text: async () => JSON.stringify({ jsonrpc: "2.0", id: 1, error: { message: "tool failed" } }),
              } as Response;
            }
            return {
              ok: true,
              status: 200,
              text: async () => JSON.stringify({ jsonrpc: "2.0", id: 1, result: { content: [{ type: "text", text: "not-json" }] } }),
            } as Response;
          }),
        );
        const page = createCurrentDiscoveryHarness({
          search: (snapshot) => searchRecords(customJwtClients, "registry", snapshot.query, snapshot.filter),
          list: async () => [],
        });

        vi.useFakeTimers();
        page.edit({ query });
        await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
        expect(page.state().results).toEqual([]);
        expect(page.state().error).not.toBeNull();
        expect(page.state().error?.title).toBe(failure === "authorization" ? "Registry rejected the token" : "Discovery failed");
        expect(page.state().error?.isAccessDenied).toBe(failure === "authorization");
        vi.useRealTimers();
        vi.unstubAllGlobals();
      }),
      RUNS,
    );
  });

  // Feature: search-results-refresh-debounce, Property 2: Preservation
  // **Validates: Requirements 3.1, 3.2, 3.3**
  it("does not reinterpret non-no-match MCP failures as an empty result", async () => {
    await fc.assert(
      fc.asyncProperty(queryArb, async (query) => {
        vi.stubGlobal(
          "fetch",
          vi.fn(async () =>
            ({
              ok: true,
              status: 200,
              text: async () => JSON.stringify({ jsonrpc: "2.0", id: 1, result: { content: [{ type: "text", text: "some other plain text" }] } }),
            }) as Response,
          ),
        );
        await expect(searchRecords(customJwtClients, "registry", query)).rejects.toBeInstanceOf(McpError);
        vi.unstubAllGlobals();
      }),
      RUNS,
    );
  });
});
