// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { AgentRegistryControlClient } from "@aws-sdk/client-agent-registry-control";
import { AgentRegistryClient } from "@aws-sdk/client-agent-registry";
import type { AwsCredentialIdentityProvider } from "@aws-sdk/types";
import { config, type RegistryAuthMode } from "../config";

export interface ClientInputs {
  /** Persona-scoped temporary IAM credentials from the Cognito Identity Pool. */
  credentials: AwsCredentialIdentityProvider;
  /** Cognito user-pool ACCESS token — the bearer credential for a CUSTOM_JWT registry. */
  accessToken: string;
}

/**
 * Build the API surface bound to the caller's session.
 *
 * Two independent authorization paths, because the service uses two:
 *
 *  - `control` — the control plane (list/create/update records, submit, approve,
 *    reject, deprecate, tag). ALWAYS SigV4 with the persona-scoped IAM role, for
 *    every registry, regardless of the registry's authorizer type.
 *
 *  - discovery — the data plane. Under `AWS_IAM` this is the SigV4 SDK client
 *    (`data`). Under `CUSTOM_JWT` there is no SigV4 path, so `data` is null and
 *    discovery goes through the registry's MCP endpoint with `accessToken` as a
 *    bearer token (see `api/mcp.ts`).
 *
 * Clients are cheap; we build them per session and reuse via `useClients`.
 */
export function makeClients({ credentials, accessToken }: ClientInputs) {
  const authMode: RegistryAuthMode = config.registryAuthMode;

  const control = new AgentRegistryControlClient({
    region: config.region,
    credentials,
  });

  // Only meaningful for an AWS_IAM registry — a CUSTOM_JWT registry rejects SigV4
  // on the discovery operations, so we don't build a client that cannot be used.
  const data =
    authMode === "AWS_IAM"
      ? new AgentRegistryClient({ region: config.region, credentials })
      : null;

  return { control, data, accessToken, authMode };
}

export type Clients = ReturnType<typeof makeClients>;
