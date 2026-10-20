// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Exploration tests for the type-filter-results bugfix.
 *
 * These tests exercise the corrected discovery adapter and RegistryDetailPage
 * result-commit boundary. The result assertions preserve the original bug
 * counterexamples as regression coverage for the response-side type invariant.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import fc from "fast-check";
import {
  listDiscoverable,
  searchRecords,
  type DiscoverableRecord,
} from "../src/api/discovery";
import type { Clients } from "../src/api/client";
import { buildSearchFilter } from "../src/components/customMetadata";
import {
  filterResultsBySelectedType,
} from "../src/pages/RegistryDetailPage";

const RUNS = { numRuns: 25 };
const selectedTypeArb = fc.constantFrom("MCP", "AGENT", "SKILL", "CUSTOM");
const queryArb = fc.stringMatching(/^[a-z][a-z0-9 ]{0,15}$/);

const customJwtClients = {
  control: {},
  data: null,
  accessToken: "test-access-token",
  authMode: "CUSTOM_JWT",
} as unknown as Clients;

function mixedRecords(selectedType: string): DiscoverableRecord[] {
  const otherType =
    (["MCP", "AGENT", "SKILL", "CUSTOM"] as const).find(
      (recordType) => recordType !== selectedType,
    )!;
  return [
    { recordId: "matching", name: "matching", recordType: selectedType },
    { recordId: "other", name: "other", recordType: otherType },
    { recordId: "missing", name: "missing" },
  ];
}

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

/** Test-only transcription of the corrected RegistryDetailPage discovery boundary. */
function createCurrentDiscoveryHarness(
  discover: (type: string) => Promise<DiscoverableRecord[]>,
) {
  let latestRequestId = 0;
  let results: DiscoverableRecord[] = [];
  let error: unknown = null;
  let searching = false;

  const start = (type: string) => {
    const requestId = ++latestRequestId;
    const isCurrent = () => requestId === latestRequestId;
    searching = true;
    error = null;
    void discover(type).then(
      (discovered) => {
        if (isCurrent()) results = filterResultsBySelectedType(discovered, type);
      },
      (caught) => {
        if (isCurrent()) error = caught;
      },
    ).finally(() => {
      if (isCurrent()) searching = false;
    });
  };

  return {
    start,
    state: () => ({ results, error, searching }),
  };
}

// Feature: type-filter-results, Property 1: Bug Condition
// **Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 2.3, 2.4, 4.1, 4.2, 4.3, 4.4**
describe("type-filter-results exploration", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("keeps Records rows constrained to every selected type", () => {
    fc.assert(
      fc.property(selectedTypeArb, (selectedType) => {
        const rendered = filterResultsBySelectedType(
          mixedRecords(selectedType),
          selectedType,
        );
        expect(rendered).not.toHaveLength(0);
        expect(rendered.every((record) => record.recordType === selectedType)).toBe(true);
      }),
      RUNS,
    );
  });

  it("captures the selected type in a semantic-search request but must not commit mixed rows", async () => {
    await fc.assert(
      fc.asyncProperty(selectedTypeArb, queryArb, async (selectedType, query) => {
        const calls: RequestInit[] = [];
        vi.stubGlobal(
          "fetch",
          vi.fn(async (_url: string, init: RequestInit) => {
            calls.push(init);
            return jsonRpcResponse(mixedRecords(selectedType));
          }),
        );

        const snapshot = { query, type: selectedType };
        const discovered = await searchRecords(
          customJwtClients,
          "registry",
          snapshot.query,
          buildSearchFilter(snapshot.type, [], {}),
        );
        const body = JSON.parse(calls[0].body as string);
        expect(body.params.arguments.filter).toEqual({
          recordType: { $eq: selectedType },
        });

        // The RegistryDetailPage commit boundary permits only exact matches.
        const rendered = filterResultsBySelectedType(discovered, snapshot.type);
        expect(rendered.every((record) => record.recordType === snapshot.type)).toBe(true);
        vi.unstubAllGlobals();
      }),
      RUNS,
    );
  });

  it("puts the selected type on every browse page but must not commit a mixed aggregate", async () => {
    await fc.assert(
      fc.asyncProperty(selectedTypeArb, async (selectedType) => {
        const calls: RequestInit[] = [];
        let page = 0;
        vi.stubGlobal(
          "fetch",
          vi.fn(async (_url: string, init: RequestInit) => {
            calls.push(init);
            page += 1;
            return jsonRpcResponse({
              registryRecords: mixedRecords(selectedType),
              ...(page === 1 ? { nextToken: "next-page" } : {}),
            });
          }),
        );

        const discovered = await listDiscoverable(customJwtClients, "registry", selectedType);
        expect(calls).toHaveLength(2);
        for (const call of calls) {
          const body = JSON.parse(call.body as string);
          expect(body.params.arguments.filters).toEqual([
            { name: "recordType", values: [selectedType] },
          ]);
        }

        // The RegistryDetailPage commit boundary applies after every page aggregates.
        const rendered = filterResultsBySelectedType(discovered, selectedType);
        expect(rendered.every((record) => record.recordType === selectedType)).toBe(true);
        vi.unstubAllGlobals();
      }),
      RUNS,
    );
  });

  it("prevents stale type successes and failures from changing the latest snapshot", async () => {
    await fc.assert(
      fc.asyncProperty(
        fc.tuple(selectedTypeArb, selectedTypeArb).filter(([older, newer]) => older !== newer),
        fc.boolean(),
        async ([older, newer], staleSucceedsFirst) => {
          const pending = new Map<string, Deferred<DiscoverableRecord[]>>();
          const page = createCurrentDiscoveryHarness((type) => {
            const completion = deferred<DiscoverableRecord[]>();
            pending.set(type, completion);
            return completion.promise;
          });

          page.start(older);
          page.start(newer);
          const newerRows = [{ recordId: "new", recordType: newer }];
          if (staleSucceedsFirst) {
            pending.get(older)!.resolve([{ recordId: "old", recordType: older }]);
            await Promise.resolve();
            pending.get(newer)!.resolve(newerRows);
          } else {
            pending.get(newer)!.resolve(newerRows);
            await Promise.resolve();
            pending.get(older)!.reject(new Error("stale failure"));
          }
          await Promise.resolve();
          await Promise.resolve();

          expect(page.state()).toMatchObject({
            results: newerRows,
            error: null,
            searching: false,
          });
        },
      ),
      RUNS,
    );
  });
});
