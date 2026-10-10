// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect, vi, afterEach } from "vitest";
import {
  listRecords,
  getRecord,
  createRecord,
  updateRecord,
  submitForApproval,
  setRecordStatus,
  listRecordTags,
  tagRecord,
  untagRecord,
} from "../src/api/records";
import { listRegistries, getRegistry } from "../src/api/registry";
import {
  searchRecords,
  listDiscoverable,
  batchGetDiscoverable,
} from "../src/api/discovery";
import type { Clients } from "../src/api/client";

// Pins the exact command class + input each API helper sends. Because the real
// SDK commands just wrap their input, a fake `send` that records the command's
// constructor name and `.input` lets us assert the wire shape without AWS.

interface Captured {
  command: string;
  input: unknown;
}

function fakeClients(
  responses: Record<string, unknown[]> = {},
  authMode: "AWS_IAM" | "CUSTOM_JWT" = "AWS_IAM",
): {
  clients: Clients;
  control: Captured[];
  data: Captured[];
} {
  const control: Captured[] = [];
  const data: Captured[] = [];
  let controlIdx = 0;
  let dataIdx = 0;

  const makeSend =
    (log: Captured[], plane: "control" | "data") => (command: any) => {
      const name = command.constructor.name;
      log.push({ command: name, input: command.input });
      const queue = responses[name];
      if (queue) {
        const idx = plane === "control" ? controlIdx++ : dataIdx++;
        return Promise.resolve(queue[Math.min(idx, queue.length - 1)] ?? {});
      }
      return Promise.resolve({});
    };

  const clients = {
    control: { send: makeSend(control, "control") },
    // A CUSTOM_JWT registry has no SigV4 data-plane client — discovery goes over MCP.
    data: authMode === "AWS_IAM" ? { send: makeSend(data, "data") } : null,
    accessToken: "test-access-token",
    authMode,
  } as unknown as Clients;

  return { clients, control, data };
}

describe("records API command shapes", () => {
  it("listRecords paginates until nextToken is exhausted", async () => {
    const { clients, control } = fakeClients({
      ListRegistryRecordsCommand: [
        { registryRecords: [{ recordId: "a" }], nextToken: "t1" },
        { registryRecords: [{ recordId: "b" }], nextToken: undefined },
      ],
    });
    const out = await listRecords(clients, "reg-1");
    expect(out.map((r) => r.recordId)).toEqual(["a", "b"]);
    expect(control).toHaveLength(2);
    expect(control[0]).toEqual({
      command: "ListRegistryRecordsCommand",
      input: { registryId: "reg-1", nextToken: undefined, maxResults: 100 },
    });
    expect((control[1].input as any).nextToken).toBe("t1");
  });

  it("getRecord sends GetRegistryRecordCommand with registryId + recordId", async () => {
    const { clients, control } = fakeClients();
    await getRecord(clients, "reg-1", "rec-1");
    expect(control[0]).toEqual({
      command: "GetRegistryRecordCommand",
      input: { registryId: "reg-1", recordId: "rec-1" },
    });
  });

  it("createRecord forwards all fields and returns recordArn", async () => {
    const { clients, control } = fakeClients({
      CreateRegistryRecordCommand: [{ recordArn: "arn:rec" }],
    });
    const arn = await createRecord(clients, {
      registryId: "reg-1",
      name: "n",
      displayName: "d",
      description: "desc",
      recordType: "MCP",
      recordVersion: "1.0.0",
      descriptors: { custom: { data: "{}" } },
    });
    expect(arn).toBe("arn:rec");
    expect(control[0].command).toBe("CreateRegistryRecordCommand");
    expect(control[0].input).toEqual({
      registryId: "reg-1",
      name: "n",
      displayName: "d",
      description: "desc",
      recordType: "MCP",
      recordVersion: "1.0.0",
      descriptors: { custom: { data: "{}" } },
      tags: undefined,
    });
  });

  it("createRecord applies tags at creation (no follow-up TagResource call)", async () => {
    const { clients, control } = fakeClients({
      CreateRegistryRecordCommand: [{ recordArn: "arn:rec" }],
    });
    await createRecord(clients, {
      registryId: "reg-1",
      name: "n",
      recordType: "MCP",
      descriptors: { custom: { data: "{}" } },
      tags: { owner: "platform-team", env: "prod" },
    });
    expect(control).toHaveLength(1);
    expect((control[0].input as any).tags).toEqual({
      owner: "platform-team",
      env: "prod",
    });
  });

  it("createRecord omits an empty tag map rather than sending {}", async () => {
    const { clients, control } = fakeClients({
      CreateRegistryRecordCommand: [{ recordArn: "arn:rec" }],
    });
    await createRecord(clients, {
      registryId: "reg-1",
      name: "n",
      recordType: "MCP",
      descriptors: { custom: { data: "{}" } },
      tags: {},
    });
    expect((control[0].input as any).tags).toBeUndefined();
  });

  describe("updateRecord PATCH-wrapper semantics", () => {
    it("undefined optional -> field omitted (unchanged)", async () => {
      const { clients, control } = fakeClients();
      await updateRecord(clients, { registryId: "r", recordId: "x" });
      const input = control[0].input as any;
      expect(input.displayName).toBeUndefined();
      expect(input.description).toBeUndefined();
    });

    it("empty string optional -> empty wrapper {} (clear)", async () => {
      const { clients, control } = fakeClients();
      await updateRecord(clients, {
        registryId: "r",
        recordId: "x",
        description: "",
      });
      expect((control[0].input as any).description).toEqual({});
    });

    it("set string optional -> { optionalValue } (never { optionalValue: '' })", async () => {
      const { clients, control } = fakeClients();
      await updateRecord(clients, {
        registryId: "r",
        recordId: "x",
        displayName: "New",
        description: "hello",
      });
      const input = control[0].input as any;
      expect(input.displayName).toEqual({ optionalValue: "New" });
      expect(input.description).toEqual({ optionalValue: "hello" });
    });

    it("empty name is omitted (name is required, cannot be cleared)", async () => {
      const { clients, control } = fakeClients();
      await updateRecord(clients, { registryId: "r", recordId: "x", name: "" });
      expect((control[0].input as any).name).toBeUndefined();
    });

    it("carries no tags field — tag changes go through TagResource/UntagResource", async () => {
      const { clients, control } = fakeClients();
      await updateRecord(clients, {
        registryId: "r",
        recordId: "x",
        displayName: "New",
      });
      expect((control[0].input as any).tags).toBeUndefined();
    });
  });

  it("submitForApproval sends SubmitRegistryRecordForApprovalCommand", async () => {
    const { clients, control } = fakeClients();
    await submitForApproval(clients, "r", "x");
    expect(control[0]).toEqual({
      command: "SubmitRegistryRecordForApprovalCommand",
      input: { registryId: "r", recordId: "x" },
    });
  });

  it("setRecordStatus forwards status + required statusReason", async () => {
    const { clients, control } = fakeClients();
    await setRecordStatus(clients, "r", "x", "APPROVED" as any, "looks good");
    expect(control[0]).toEqual({
      command: "UpdateRegistryRecordStatusCommand",
      input: {
        registryId: "r",
        recordId: "x",
        status: "APPROVED",
        statusReason: "looks good",
      },
    });
  });
});

describe("record tag helpers", () => {
  it("listRecordTags reads tags off the RECORD ARN and defaults to {}", async () => {
    const { clients, control } = fakeClients({
      ListTagsForResourceCommand: [{ tags: { owner: "team" } }],
    });
    expect(await listRecordTags(clients, "arn:rec")).toEqual({ owner: "team" });
    expect(control[0]).toEqual({
      command: "ListTagsForResourceCommand",
      input: { resourceArn: "arn:rec" },
    });
  });

  it("listRecordTags returns {} when the resource has no tags", async () => {
    const { clients } = fakeClients({ ListTagsForResourceCommand: [{}] });
    expect(await listRecordTags(clients, "arn:rec")).toEqual({});
  });

  it("tagRecord sends TagResourceCommand", async () => {
    const { clients, control } = fakeClients();
    await tagRecord(clients, "arn:rec", { env: "prod" });
    expect(control[0]).toEqual({
      command: "TagResourceCommand",
      input: { resourceArn: "arn:rec", tags: { env: "prod" } },
    });
  });

  it("untagRecord sends UntagResourceCommand", async () => {
    const { clients, control } = fakeClients();
    await untagRecord(clients, "arn:rec", ["env"]);
    expect(control[0]).toEqual({
      command: "UntagResourceCommand",
      input: { resourceArn: "arn:rec", tagKeys: ["env"] },
    });
  });

  it("both tag mutations short-circuit on empty input (no call)", async () => {
    const { clients, control } = fakeClients();
    await tagRecord(clients, "arn:rec", {});
    await untagRecord(clients, "arn:rec", []);
    expect(control).toHaveLength(0);
  });
});

describe("registry API command shapes", () => {
  it("listRegistries paginates", async () => {
    const { clients, control } = fakeClients({
      ListRegistriesCommand: [
        { registries: [{ registryId: "r1" }], nextToken: "n" },
        { registries: [{ registryId: "r2" }] },
      ],
    });
    const out = await listRegistries(clients);
    expect(out).toHaveLength(2);
    expect(control).toHaveLength(2);
  });

  it("getRegistry sends GetRegistryCommand", async () => {
    const { clients, control } = fakeClients();
    await getRegistry(clients, "r1");
    expect(control[0]).toEqual({
      command: "GetRegistryCommand",
      input: { registryId: "r1" },
    });
  });

  it("exposes no registry create/delete surface", async () => {
    const mod = await import("../src/api/registry");
    expect(mod).not.toHaveProperty("createRegistry");
    expect(mod).not.toHaveProperty("deleteRegistry");
  });
});

describe("discovery over SigV4 (AWS_IAM registry)", () => {
  it("searchRecords sends a single semantic search with optional recordType filter", async () => {
    const { clients, data } = fakeClients({
      SearchDiscoverableRegistryRecordsCommand: [
        { registryRecords: [{ name: "hit" }] },
      ],
    });
    const out = await searchRecords(clients, "reg-1", "find me", "MCP", 5);
    expect(out).toHaveLength(1);
    expect(data[0].command).toBe("SearchDiscoverableRegistryRecordsCommand");
    expect(data[0].input).toEqual({
      registryIds: ["reg-1"],
      searchQuery: "find me",
      maxResults: 5,
      filters: { recordType: { $eq: "MCP" } },
    });
  });

  it("searchRecords omits the filter when no recordType is given, default maxResults 20", async () => {
    const { clients, data } = fakeClients();
    await searchRecords(clients, "reg-1", "q");
    expect(data[0].input).toEqual({
      registryIds: ["reg-1"],
      searchQuery: "q",
      maxResults: 20,
      filters: undefined,
    });
  });

  it("listDiscoverable paginates and maps recordType to the filter list shape", async () => {
    const { clients, data } = fakeClients({
      ListDiscoverableRegistryRecordsCommand: [
        { registryRecords: [{ name: "a" }], nextToken: "t" },
        { registryRecords: [{ name: "b" }] },
      ],
    });
    const out = await listDiscoverable(clients, "reg-1", "AGENT");
    expect(out).toHaveLength(2);
    expect(data[0].input).toEqual({
      registryId: "reg-1",
      filters: [{ name: "recordType", values: ["AGENT"] }],
      nextToken: undefined,
      maxResults: 100,
    });
  });

  it("batchGetDiscoverable short-circuits on an empty id list (no call)", async () => {
    const { clients, data } = fakeClients();
    const out = await batchGetDiscoverable(clients, "reg-1", []);
    expect(out).toEqual([]);
    expect(data).toHaveLength(0);
  });

  it("batchGetDiscoverable wraps ids in the entries shape", async () => {
    const { clients, data } = fakeClients({
      BatchGetDiscoverableRegistryRecordCommand: [
        { registryRecords: [{ name: "x" }] },
      ],
    });
    await batchGetDiscoverable(clients, "reg-1", ["id1", "id2"]);
    expect(data[0].input).toEqual({
      entries: [{ registryId: "reg-1", recordIds: ["id1", "id2"] }],
    });
  });
});

describe("discovery over the MCP endpoint (CUSTOM_JWT registry)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  /** Capture the fetch calls and answer with one JSON-RPC tool result. */
  function stubFetch(
    payload: unknown,
    init: { sse?: boolean; status?: number } = {},
  ) {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const bodyText = JSON.stringify({
      jsonrpc: "2.0",
      id: 1,
      result: { content: [{ type: "text", text: JSON.stringify(payload) }] },
    });
    const fetchMock = vi.fn(async (url: string, reqInit: RequestInit) => {
      calls.push({ url, init: reqInit });
      return {
        ok: (init.status ?? 200) < 400,
        status: init.status ?? 200,
        text: async () =>
          init.sse ? `event: message\ndata: ${bodyText}\n\n` : bodyText,
      } as unknown as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    return calls;
  }

  it("searchRecords POSTs a JSON-RPC tools/call with a bearer token", async () => {
    const calls = stubFetch({ registryRecords: [{ name: "hit" }] });
    const { clients } = fakeClients({}, "CUSTOM_JWT");

    const out = await searchRecords(clients, "reg-1", "find me", "MCP", 5);
    expect(out).toEqual([{ name: "hit" }]);
    expect(calls).toHaveLength(1);

    // GA namespace endpoint — never the deprecated bedrock-agentcore host.
    expect(calls[0].url).toContain("/registry/reg-1/mcp");
    expect(calls[0].url).toContain("agent-registry.");
    expect(calls[0].url).not.toContain("bedrock-agentcore");

    const headers = calls[0].init.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer test-access-token");

    const body = JSON.parse(calls[0].init.body as string);
    expect(body.method).toBe("tools/call");
    expect(body.params.name).toBe("search_discoverable_registry_records");
    // The MCP tool takes a singular `filter`; the REST API takes `filters`.
    expect(body.params.arguments).toEqual({
      searchQuery: "find me",
      maxResults: 5,
      filter: { recordType: { $eq: "MCP" } },
    });
  });

  it("listDiscoverable uses the list tool with the array filter shape", async () => {
    const calls = stubFetch({ registryRecords: [{ name: "a" }] });
    const { clients } = fakeClients({}, "CUSTOM_JWT");

    await listDiscoverable(clients, "reg-1", "AGENT");
    const body = JSON.parse(calls[0].init.body as string);
    expect(body.params.name).toBe("list_discoverable_registry_records");
    expect(body.params.arguments).toEqual({
      maxResults: 100,
      filters: [{ name: "recordType", values: ["AGENT"] }],
    });
  });

  it("batchGetDiscoverable uses the batch tool with a bare recordIds list", async () => {
    const calls = stubFetch({ registryRecords: [{ name: "x" }] });
    const { clients } = fakeClients({}, "CUSTOM_JWT");

    await batchGetDiscoverable(clients, "reg-1", ["id1", "id2"]);
    const body = JSON.parse(calls[0].init.body as string);
    expect(body.params.name).toBe("batch_get_discoverable_registry_record");
    expect(body.params.arguments).toEqual({ recordIds: ["id1", "id2"] });
  });

  it("decodes an SSE-framed response as well as a plain JSON body", async () => {
    stubFetch({ registryRecords: [{ name: "sse" }] }, { sse: true });
    const { clients } = fakeClients({}, "CUSTOM_JWT");
    expect(await searchRecords(clients, "reg-1", "q")).toEqual([
      { name: "sse" },
    ]);
  });

  // VERIFIED LIVE against the GA endpoint 2026-09-04: the three discovery tools do NOT
  // agree on their payload shape, and none of them returns structuredContent.
  // Mishandling this is SILENT -- the call succeeds and the UI shows nothing -- which is
  // exactly how it got past the first round of mocked tests.
  it("search returns a BARE ARRAY of records (live shape) and still unwraps", async () => {
    stubFetch([{ name: "incident-enricher-tool" }, { name: "triage-agent" }]);
    const { clients } = fakeClients({}, "CUSTOM_JWT");
    expect(await searchRecords(clients, "reg-1", "incident")).toEqual([
      { name: "incident-enricher-tool" },
      { name: "triage-agent" },
    ]);
  });

  it("list returns { registryRecords, nextToken } (live shape) and paginates on it", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    const bodies = [
      { registryRecords: [{ name: "a" }], nextToken: "t1" },
      { registryRecords: [{ name: "b" }] },
    ];
    let call = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url, init });
        const body = JSON.stringify({
          jsonrpc: "2.0",
          id: 1,
          result: {
            content: [
              { type: "text", text: JSON.stringify(bodies[call++] ?? {}) },
            ],
          },
        });
        return {
          ok: true,
          status: 200,
          text: async () => body,
        } as unknown as Response;
      }),
    );
    const { clients } = fakeClients({}, "CUSTOM_JWT");
    expect(await listDiscoverable(clients, "reg-1")).toEqual([
      { name: "a" },
      { name: "b" },
    ]);
    expect(calls).toHaveLength(2);
    expect(
      JSON.parse(calls[1].init.body as string).params.arguments.nextToken,
    ).toBe("t1");
  });

  it("a bare array carries no nextToken, so pagination terminates instead of looping", async () => {
    stubFetch([{ name: "only" }]);
    const { clients } = fakeClients({}, "CUSTOM_JWT");
    expect(await listDiscoverable(clients, "reg-1")).toEqual([
      { name: "only" },
    ]);
  });

  it("surfaces a 403 from the MCP endpoint as an auth failure", async () => {
    stubFetch({}, { status: 403 });
    const { clients } = fakeClients({}, "CUSTOM_JWT");
    await expect(searchRecords(clients, "reg-1", "q")).rejects.toMatchObject({
      name: "McpError",
      isAuthFailure: true,
    });
  });

  it("batchGetDiscoverable still short-circuits on an empty id list", async () => {
    const calls = stubFetch({});
    const { clients } = fakeClients({}, "CUSTOM_JWT");
    expect(await batchGetDiscoverable(clients, "reg-1", [])).toEqual([]);
    expect(calls).toHaveLength(0);
  });
});
