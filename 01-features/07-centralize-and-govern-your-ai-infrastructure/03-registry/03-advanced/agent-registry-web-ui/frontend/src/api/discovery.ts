// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  SearchDiscoverableRegistryRecordsCommand,
  ListDiscoverableRegistryRecordsCommand,
  BatchGetDiscoverableRegistryRecordCommand,
  type RegistryRecordFilterName,
} from "@aws-sdk/client-agent-registry";
import type { Clients } from "./client";
import { callMcpTool, McpError } from "./mcp";
import { registryMcpEndpoint } from "../config";

// The data-plane record shape (approved, discoverable records). Kept loose because
// the discoverable record type mirrors the control-plane record with descriptors,
// and both the SDK and the MCP endpoint return the same field names.
export interface DiscoverableRecord {
  recordId?: string;
  recordArn?: string;
  registryArn?: string;
  name?: string;
  displayName?: string;
  description?: string;
  recordType?: string;
  recordVersion?: string;
  status?: string;
  descriptors?: unknown;
  [k: string]: unknown;
}

interface McpRecordsPayload {
  registryRecords?: DiscoverableRecord[];
  records?: DiscoverableRecord[];
  nextToken?: string;
}

/**
 * The three MCP discovery tools do NOT agree on their payload shape — verified live
 * against the GA endpoint 2026-09-04:
 *   - search_discoverable_registry_records -> a BARE JSON ARRAY of records
 *   - list_discoverable_registry_records   -> { registryRecords, nextToken }
 *   - batch_get_discoverable_registry_record -> { registryRecords }
 * so unwrap defensively. Getting this wrong is silent: the call succeeds and the UI
 * just shows no results.
 */
function recordsOf(
  p: McpRecordsPayload | DiscoverableRecord[] | null | undefined,
): DiscoverableRecord[] {
  if (!p) return [];
  if (Array.isArray(p)) return p;
  return p.registryRecords ?? p.records ?? [];
}

/** `nextToken` only exists on the object-shaped payloads. */
function nextTokenOf(
  p: McpRecordsPayload | DiscoverableRecord[] | null | undefined,
): string | undefined {
  return p && !Array.isArray(p) ? p.nextToken : undefined;
}

function endpointFor(registryId: string): string {
  return registryMcpEndpoint(registryId);
}

/**
 * Natural-language semantic search over APPROVED records (relevance-ranked,
 * single page, max 20).
 */
export async function searchRecords(
  c: Clients,
  registryId: string,
  query: string,
  recordType?: string,
  maxResults = 20,
): Promise<DiscoverableRecord[]> {
  if (c.authMode === "CUSTOM_JWT") {
    // Note: the MCP tool takes a singular `filter` object; the REST API takes `filters`.
    const payload = await callMcpTool<McpRecordsPayload | DiscoverableRecord[]>(
      endpointFor(registryId),
      c.accessToken,
      "search_discoverable_registry_records",
      {
        searchQuery: query,
        maxResults,
        ...(recordType ? { filter: { recordType: { $eq: recordType } } } : {}),
      },
    );
    return recordsOf(payload);
  }

  const res = await dataClient(c).send(
    new SearchDiscoverableRegistryRecordsCommand({
      registryIds: [registryId],
      searchQuery: query,
      maxResults,
      filters: recordType ? { recordType: { $eq: recordType } } : undefined,
    }),
  );
  return (res.registryRecords ?? []) as unknown as DiscoverableRecord[];
}

/** Keyword/attribute list of APPROVED records (paginated). */
export async function listDiscoverable(
  c: Clients,
  registryId: string,
  recordType?: string,
): Promise<DiscoverableRecord[]> {
  const out: DiscoverableRecord[] = [];
  let nextToken: string | undefined;

  if (c.authMode === "CUSTOM_JWT") {
    const endpoint = endpointFor(registryId);
    do {
      const payload = await callMcpTool<
        McpRecordsPayload | DiscoverableRecord[]
      >(endpoint, c.accessToken, "list_discoverable_registry_records", {
        maxResults: 100,
        ...(nextToken ? { nextToken } : {}),
        ...(recordType
          ? { filters: [{ name: "recordType", values: [recordType] }] }
          : {}),
      });
      out.push(...recordsOf(payload));
      nextToken = nextTokenOf(payload);
    } while (nextToken);
    return out;
  }

  const filters = recordType
    ? [{ name: "recordType" as RegistryRecordFilterName, values: [recordType] }]
    : undefined;
  do {
    const res = await dataClient(c).send(
      new ListDiscoverableRegistryRecordsCommand({
        registryId,
        filters,
        nextToken,
        maxResults: 100,
      }),
    );
    out.push(
      ...((res.registryRecords ?? []) as unknown as DiscoverableRecord[]),
    );
    nextToken = res.nextToken;
  } while (nextToken);
  return out;
}

/** Fetch full discoverable records (with descriptors) by id. */
export async function batchGetDiscoverable(
  c: Clients,
  registryId: string,
  recordIds: string[],
): Promise<DiscoverableRecord[]> {
  if (recordIds.length === 0) return [];

  if (c.authMode === "CUSTOM_JWT") {
    const payload = await callMcpTool<McpRecordsPayload | DiscoverableRecord[]>(
      endpointFor(registryId),
      c.accessToken,
      "batch_get_discoverable_registry_record",
      { recordIds },
    );
    return recordsOf(payload);
  }

  const res = await dataClient(c).send(
    new BatchGetDiscoverableRegistryRecordCommand({
      entries: [{ registryId, recordIds }],
    }),
  );
  return (res.registryRecords ?? []) as unknown as DiscoverableRecord[];
}

/** The SigV4 data-plane client exists only for an AWS_IAM registry. */
function dataClient(c: Clients) {
  if (!c.data) {
    throw new McpError(
      "This registry uses CUSTOM_JWT authorization, so discovery must go through its " +
        "MCP endpoint with a bearer token rather than SigV4.",
    );
  }
  return c.data;
}
