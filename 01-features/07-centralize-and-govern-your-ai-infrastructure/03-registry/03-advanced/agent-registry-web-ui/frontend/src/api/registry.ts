// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  ListRegistriesCommand,
  GetRegistryCommand,
  type RegistrySummary,
  type GetRegistryResponse,
} from "@aws-sdk/client-agent-registry-control";
import type { Clients } from "./client";

// Read-only by design. Creating a registry picks its IMMUTABLE authorization model
// (authorizerType and, for CUSTOM_JWT, the discovery URL can never be changed
// afterwards) and provisions an AgentCore workload identity — an infrastructure
// decision that belongs in the console, the CLI or IaC, not in a day-to-day
// publisher/approver/consumer UI. This sample provisions it in
// `deploy/setup/03-registry.sh`. There is deliberately no CreateRegistry or
// DeleteRegistry call in this app.

export type { RegistrySummary, GetRegistryResponse };

export async function listRegistries(c: Clients): Promise<RegistrySummary[]> {
  const out: RegistrySummary[] = [];
  let nextToken: string | undefined;
  do {
    const res = await c.control.send(
      new ListRegistriesCommand({ nextToken, maxResults: 100 }),
    );
    out.push(...(res.registries ?? []));
    nextToken = res.nextToken;
  } while (nextToken);
  return out;
}

export async function getRegistry(
  c: Clients,
  registryId: string,
): Promise<GetRegistryResponse> {
  return c.control.send(new GetRegistryCommand({ registryId }));
}
