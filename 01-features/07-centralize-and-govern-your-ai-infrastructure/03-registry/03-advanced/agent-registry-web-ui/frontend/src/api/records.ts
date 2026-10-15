// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  ListRegistryRecordsCommand,
  GetRegistryRecordCommand,
  CreateRegistryRecordCommand,
  UpdateRegistryRecordCommand,
  SubmitRegistryRecordForApprovalCommand,
  UpdateRegistryRecordStatusCommand,
  ListTagsForResourceCommand,
  TagResourceCommand,
  UntagResourceCommand,
  type RegistryRecordSummary,
  type GetRegistryRecordResponse,
  type Descriptors,
  type DescriptorSource,
  type RegistryRecordStatus,
  type RecordType,
} from "@aws-sdk/client-agent-registry-control";
import type { Clients } from "./client";

export type {
  RegistryRecordSummary,
  GetRegistryRecordResponse,
  Descriptors,
  DescriptorSource,
  RegistryRecordStatus,
  RecordType,
};

export async function listRecords(
  c: Clients,
  registryId: string,
): Promise<RegistryRecordSummary[]> {
  const out: RegistryRecordSummary[] = [];
  let nextToken: string | undefined;
  do {
    const res = await c.control.send(
      new ListRegistryRecordsCommand({
        registryId,
        nextToken,
        maxResults: 100,
      }),
    );
    out.push(...(res.registryRecords ?? []));
    nextToken = res.nextToken;
  } while (nextToken);
  return out;
}

export async function getRecord(
  c: Clients,
  registryId: string,
  recordId: string,
): Promise<GetRegistryRecordResponse> {
  return c.control.send(new GetRegistryRecordCommand({ registryId, recordId }));
}

export interface CreateRecordInput {
  registryId: string;
  name: string;
  displayName?: string;
  description?: string;
  recordType: RecordType;
  recordVersion?: string;
  /**
   * The descriptor union. Either carries inline `data`, or a `source.fromUrl` that
   * the registry invokes and introspects — see components/source.ts. When a source is
   * used the record lands in CREATING while the registry fetches it, then DRAFT; a
   * failed fetch lands in CREATE_FAILED with the reason in `statusReason`.
   */
  descriptors: Descriptors;
  /**
   * Tags applied at creation. `CreateRegistryRecord` takes tags directly, so no
   * follow-up TagResource call is needed for a new record. Tags are metadata for
   * cost allocation / ownership / governance — they are NOT a discovery filter
   * (the filterable record fields are name, recordType and status only).
   */
  tags?: Record<string, string>;
}

export async function createRecord(
  c: Clients,
  input: CreateRecordInput,
): Promise<string> {
  const tags =
    input.tags && Object.keys(input.tags).length > 0 ? input.tags : undefined;
  const res = await c.control.send(
    new CreateRegistryRecordCommand({
      registryId: input.registryId,
      name: input.name,
      displayName: input.displayName,
      description: input.description,
      recordType: input.recordType,
      recordVersion: input.recordVersion,
      descriptors: input.descriptors,
      tags,
    }),
  );
  return res.recordArn!;
}

export async function updateRecord(
  c: Clients,
  input: {
    registryId: string;
    recordId: string;
    name?: string;
    displayName?: string;
    description?: string;
    recordVersion?: string;
  },
): Promise<void> {
  // PATCH-wrapper semantics for the optional string fields:
  //   undefined      -> omit the field (leave unchanged)
  //   "" (empty)     -> send an empty wrapper {} to CLEAR/unset it
  //   "value"        -> send { optionalValue: "value" } to set it
  // (Sending { optionalValue: "" } fails the service's min-length-1 constraint.)
  const wrap = (v: string | undefined) =>
    v === undefined ? undefined : v === "" ? {} : { optionalValue: v };

  await c.control.send(
    new UpdateRegistryRecordCommand({
      registryId: input.registryId,
      recordId: input.recordId,
      name: input.name || undefined, // name is a plain string; empty = omit (name is required, can't clear)
      displayName: wrap(input.displayName),
      description: wrap(input.description),
      recordVersion: input.recordVersion,
    }),
  );
}

export async function submitForApproval(
  c: Clients,
  registryId: string,
  recordId: string,
): Promise<void> {
  await c.control.send(
    new SubmitRegistryRecordForApprovalCommand({ registryId, recordId }),
  );
}

/** Approver action: approve / reject / deprecate. statusReason is required. */
export async function setRecordStatus(
  c: Clients,
  registryId: string,
  recordId: string,
  status: RegistryRecordStatus,
  statusReason: string,
): Promise<void> {
  await c.control.send(
    new UpdateRegistryRecordStatusCommand({
      registryId,
      recordId,
      status,
      statusReason,
    }),
  );
}

// ---------------------------------------------------------------------------
// Tags
//
// GetRegistryRecord does NOT return tags, so a record's tags are read separately
// via ListTagsForResource against the RECORD ARN. UpdateRegistryRecord has no tags
// field either — changing tags after creation goes through TagResource /
// UntagResource. TagResource supports both registries and registry records.
// ---------------------------------------------------------------------------

export async function listRecordTags(
  c: Clients,
  recordArn: string,
): Promise<Record<string, string>> {
  const res = await c.control.send(
    new ListTagsForResourceCommand({ resourceArn: recordArn }),
  );
  return res.tags ?? {};
}

/** Add or overwrite tags on an existing record. */
export async function tagRecord(
  c: Clients,
  recordArn: string,
  tags: Record<string, string>,
): Promise<void> {
  if (Object.keys(tags).length === 0) return;
  await c.control.send(
    new TagResourceCommand({ resourceArn: recordArn, tags }),
  );
}

/** Remove tags from an existing record by key. */
export async function untagRecord(
  c: Clients,
  recordArn: string,
  tagKeys: string[],
): Promise<void> {
  if (tagKeys.length === 0) return;
  await c.control.send(
    new UntagResourceCommand({ resourceArn: recordArn, tagKeys }),
  );
}
