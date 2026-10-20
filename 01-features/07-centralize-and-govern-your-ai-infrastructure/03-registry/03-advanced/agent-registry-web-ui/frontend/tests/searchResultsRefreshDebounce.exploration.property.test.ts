// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Exploration tests for the search-results-refresh-debounce bugfix.
 *
 * These assert the corrected MCP decoder and RegistryDetailPage request
 * lifecycle behavior. They preserve the counterexamples that established the
 * original bug condition as regression coverage.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import fc from "fast-check";
import { searchRecords, type DiscoverableRecord } from "../src/api/discovery";
import type { Clients } from "../src/api/client";
import { McpError } from "../src/api/mcp";

const DEBOUNCE_MS = 300;
const RUNS = { numRuns: 25 };
const NO_MATCH_TEXT = "No matching registry records found.";

const customJwtClients = {
  control: {},
  data: null,
  accessToken: "test-access-token",
  authMode: "CUSTOM_JWT",
} as unknown as Clients;

function mcpTextResponse(text: string): Response {
  return {
    ok: true,
    status: 200,
    text: async () =>
      JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        result: { content: [{ type: "text", text }] },
      }),
  } as Response;
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason: unknown): void;
}

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

/**
 * Test-only transcription of RegistryDetailPage's versioned effect + runSearch
 * behavior. React rendering is not available in the node-only Vitest
 * configuration, so this isolates the corrected async boundary.
 */
function createVersionedDiscoveryPageHarness(
  discover: (query: string) => Promise<DiscoverableRecord[]>,
) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let results: DiscoverableRecord[] = [];
  let error: Error | null = null;
  let searching = false;
  let latestRequestId = 0;
  const requests: string[] = [];

  const runSearch = async (snapshot: { requestId: number; query: string }) => {
    const isCurrent = () => snapshot.requestId === latestRequestId;
    if (!isCurrent()) return;
    searching = true;
    error = null;
    requests.push(snapshot.query);
    try {
      const discovered = await discover(snapshot.query);
      if (isCurrent()) results = discovered;
    } catch (caught) {
      if (isCurrent()) {
        error = caught instanceof Error ? caught : new Error(String(caught));
      }
    } finally {
      if (isCurrent()) searching = false;
    }
  };

  return {
    edit(nextQuery: string) {
      if (timer) clearTimeout(timer);
      const snapshot = { requestId: ++latestRequestId, query: nextQuery.trim() };
      results = [];
      error = null;
      searching = false;
      timer = setTimeout(() => void runSearch(snapshot), DEBOUNCE_MS);
    },
    state: () => ({ results, error, searching, requests: [...requests] }),
  };
}

const queryArb = fc.stringMatching(/^[a-z]{1,12}$/);

// Feature: search-results-refresh-debounce, Property 1: Bug Condition
// **Validates: Requirements 2.1, 2.2**
describe("search result refresh debounce exploration", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("normalizes the exact successful CUSTOM_JWT no-match response to an empty record array", async () => {
    await fc.assert(
      fc.asyncProperty(queryArb, async (query) => {
        vi.stubGlobal("fetch", vi.fn(async () => mcpTextResponse(NO_MATCH_TEXT)));

        // Desired post-fix outcome: a documented no-match is not a parser error.
        await expect(searchRecords(customJwtClients, "registry", query)).resolves.toEqual([]);
        vi.unstubAllGlobals();
      }),
      RUNS,
    );
  });

  it("clears matching rows and omits an error when the next debounced query has no matches", async () => {
    vi.useFakeTimers();
    const matching = [{ recordId: "match", name: "deploy record" }];
    const first = deferred<DiscoverableRecord[]>();
    const noMatch = deferred<DiscoverableRecord[]>();
    const page = createVersionedDiscoveryPageHarness((query) =>
      query === "deploy" ? first.promise : noMatch.promise,
    );

    page.edit("deploy");
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    first.resolve(matching);
    await Promise.resolve();
    expect(page.state().results).toEqual(matching);

    page.edit("nothing-here");
    await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
    noMatch.resolve([]);
    await Promise.resolve();

    // Desired post-fix outcome: stale rows are replaced by the no-results state.
    expect(page.state()).toMatchObject({ results: [], error: null, searching: false });
  });

  it("accepts only the current snapshot when generated searches complete out of order", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.uniqueArray(queryArb, { minLength: 2, maxLength: 4 }),
        async (queries) => {
          vi.useFakeTimers();
          const completions = new Map<string, Deferred<DiscoverableRecord[]>>();
          const page = createVersionedDiscoveryPageHarness((query) => {
            const completion = deferred<DiscoverableRecord[]>();
            completions.set(query, completion);
            return completion.promise;
          });

          for (const query of queries) {
            page.edit(query);
            await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
          }

          const latest = queries[queries.length - 1];
          completions.get(latest)!.resolve([{ name: latest }]);
          await Promise.resolve();
          for (const query of [...queries.slice(0, -1)].reverse()) {
            completions.get(query)!.resolve([{ name: query }]);
            await Promise.resolve();
          }

          // A completion with requestId < currentRequestId must not commit state.
          expect(page.state()).toMatchObject({
            results: [{ name: latest }],
            error: null,
            searching: false,
          });
          vi.useRealTimers();
        },
      ),
      RUNS,
    );
  });

  it("prevents stale failures from changing the latest snapshot state", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.tuple(queryArb, queryArb).filter(([older, newer]) => older !== newer),
        async ([older, newer]) => {
          vi.useFakeTimers();
          const completions = new Map<string, Deferred<DiscoverableRecord[]>>();
          const page = createVersionedDiscoveryPageHarness((query) => {
            const completion = deferred<DiscoverableRecord[]>();
            completions.set(query, completion);
            return completion.promise;
          });

          page.edit(older);
          await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);
          page.edit(newer);
          await vi.advanceTimersByTimeAsync(DEBOUNCE_MS);

          completions.get(newer)!.resolve([{ name: newer }]);
          await Promise.resolve();
          completions
            .get(older)!
            .reject(new McpError(`stale failure for ${older}`));
          await Promise.resolve();

          // A stale failure must not replace the latest result or set an error.
          expect(page.state()).toMatchObject({
            results: [{ name: newer }],
            error: null,
            searching: false,
          });
          vi.useRealTimers();
        },
      ),
      RUNS,
    );
  });

  it("schedules only the final edit after a rapid sequence shorter than 300 ms", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.uniqueArray(queryArb, { minLength: 2, maxLength: 6 }),
        async (queries) => {
          vi.useFakeTimers();
          const page = createVersionedDiscoveryPageHarness(async () => []);
          for (const query of queries) {
            page.edit(query);
            await vi.advanceTimersByTimeAsync(DEBOUNCE_MS - 1);
          }
          expect(page.state().requests).toEqual([]);

          await vi.advanceTimersByTimeAsync(1);
          await Promise.resolve();
          expect(page.state().requests).toEqual([queries[queries.length - 1]]);
          vi.useRealTimers();
        },
      ),
      RUNS,
    );
  });
});
