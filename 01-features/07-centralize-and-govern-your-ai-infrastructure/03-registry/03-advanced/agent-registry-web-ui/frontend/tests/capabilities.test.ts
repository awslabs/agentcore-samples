// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { describe, it, expect } from "vitest";
import {
  resolvePersona,
  capabilitiesFor,
  GROUP_TO_PERSONA,
  type Persona,
} from "../src/personas/capabilities";

// Pins the persona resolution + capability matrix. This is the UI's convenience
// authorization layer; changing any of these values changes what buttons a user
// sees, so they must survive a repackage unchanged.
//
// The UI serves three personas only. Registry administration (create/delete a
// registry, choose its immutable authorization model) is out of scope by design,
// so there is no admin persona to resolve to.

describe("resolvePersona", () => {
  it("maps each known group to its persona", () => {
    expect(resolvePersona(["AgentRegistryApprover"])).toBe("Approver");
    expect(resolvePersona(["AgentRegistryPublisher"])).toBe("Publisher");
    expect(resolvePersona(["AgentRegistryConsumer"])).toBe("Consumer");
  });

  it("defaults to Consumer when no known group is present", () => {
    expect(resolvePersona([])).toBe("Consumer");
    expect(resolvePersona(["SomethingElse"])).toBe("Consumer");
  });

  it("does not recognise a legacy admin group", () => {
    expect(resolvePersona(["AgentRegistryAdmin"])).toBe("Consumer");
    expect(GROUP_TO_PERSONA).not.toHaveProperty("AgentRegistryAdmin");
  });

  it("picks the highest-privilege persona when several groups are present", () => {
    expect(
      resolvePersona(["AgentRegistryConsumer", "AgentRegistryApprover"]),
    ).toBe("Approver");
    expect(
      resolvePersona(["AgentRegistryPublisher", "AgentRegistryApprover"]),
    ).toBe("Approver");
    expect(
      resolvePersona(["AgentRegistryConsumer", "AgentRegistryPublisher"]),
    ).toBe("Publisher");
  });

  it("keeps the group->persona table stable", () => {
    expect(GROUP_TO_PERSONA).toEqual({
      AgentRegistryApprover: "Approver",
      AgentRegistryPublisher: "Publisher",
      AgentRegistryConsumer: "Consumer",
    });
  });
});

describe("capabilitiesFor", () => {
  const matrix: Record<
    Persona,
    { canBrowse: boolean; canPublish: boolean; canApprove: boolean }
  > = {
    Consumer: { canBrowse: true, canPublish: false, canApprove: false },
    Publisher: { canBrowse: true, canPublish: true, canApprove: false },
    Approver: { canBrowse: true, canPublish: false, canApprove: true },
  };

  const groupFor: Record<Persona, string> = {
    Consumer: "AgentRegistryConsumer",
    Publisher: "AgentRegistryPublisher",
    Approver: "AgentRegistryApprover",
  };

  (Object.keys(matrix) as Persona[]).forEach((persona) => {
    it(`grants ${persona} exactly its capability set`, () => {
      const caps = capabilitiesFor([groupFor[persona]]);
      expect(caps.persona).toBe(persona);
      expect(caps.canBrowse).toBe(matrix[persona].canBrowse);
      expect(caps.canPublish).toBe(matrix[persona].canPublish);
      expect(caps.canApprove).toBe(matrix[persona].canApprove);
    });
  });

  it("exposes no admin capability at all", () => {
    const caps = capabilitiesFor(["AgentRegistryApprover"]);
    expect(caps).not.toHaveProperty("canAdmin");
  });

  it("an unknown group is treated as Consumer (browse only)", () => {
    const caps = capabilitiesFor(["nope"]);
    expect(caps).toEqual({
      persona: "Consumer",
      canBrowse: true,
      canPublish: false,
      canApprove: false,
    });
  });
});
