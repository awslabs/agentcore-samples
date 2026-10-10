// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// Maps Cognito User Pool groups -> UI capability flags.
//
// SCOPE: this UI deliberately serves only the three day-to-day registry personas.
// Registry ADMINISTRATION (creating a registry, choosing its immutable
// authorization model, deleting it) is an infrastructure concern and is done in the
// AWS console, the CLI, or IaC — in this sample by `deploy/setup/03-registry.sh`.
// There is no admin persona and no registry-create/delete path in the app.
//
// IMPORTANT: these flags are a convenience layer only. The real enforcement is
// the scoped IAM role the Identity Pool vends for the user's group; an out-of-role
// SDK call fails with AccessDenied regardless of what the UI shows.

export type Persona = "Approver" | "Publisher" | "Consumer";

export const GROUP_TO_PERSONA: Record<string, Persona> = {
  AgentRegistryApprover: "Approver",
  AgentRegistryPublisher: "Publisher",
  AgentRegistryConsumer: "Consumer",
};

// Highest-privilege group wins when a user is in several.
const PERSONA_RANK: Persona[] = ["Approver", "Publisher", "Consumer"];

export function resolvePersona(groups: string[]): Persona {
  const personas = groups
    .map((g) => GROUP_TO_PERSONA[g])
    .filter((p): p is Persona => Boolean(p));
  for (const p of PERSONA_RANK) {
    if (personas.includes(p)) return p;
  }
  return "Consumer";
}

export interface Capabilities {
  persona: Persona;
  canBrowse: boolean; // list/search/get discoverable + registries
  canPublish: boolean; // create/update records, submit for approval, tag records
  canApprove: boolean; // approve / reject / deprecate
}

export function capabilitiesFor(groups: string[]): Capabilities {
  const persona = resolvePersona(groups);
  return {
    persona,
    canBrowse: true,
    canPublish: persona === "Publisher",
    canApprove: persona === "Approver",
  };
}
