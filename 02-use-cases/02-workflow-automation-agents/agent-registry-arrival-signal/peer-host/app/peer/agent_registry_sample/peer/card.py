# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""U3 peer-agent. The served A2A 0.3 agent card.

The peer is the A2A provider. U4 publishes its card into the registry record,
and U5/U6 read it back to learn the peer's invocation endpoint and skill. This
module builds the peer's complete A2A 0.3 AgentCard via the a2a-sdk
``AgentCard`` model rather than a hand-rolled dict, so the serialized card is
schema-0.3 compliant and camelCase-aliased the same way the orchestrator's own
card is. The live service rejects a card that drifts from the schema at
create_registry_record.

**Card trust in this sample.** The card is published unsigned. Trust rests on
three other layers that do not require distributing key material with the
sample:

  1. the registry approval gate. The data plane returns only approved
     records, so a consumer only reads a card that passed governance.
  2. the orchestrator's account-scoped endpoint check (verify_and_invoke).
     The peer's runtime account must be in the allowed set.
  3. IAM. The peer's resource-based policy (the consumer-group model) is the
     enforced invoke gate.

A2A card signing (JWS over the card, verified against a distributed trust
anchor) is a valid additional layer, but it requires provisioning and
distributing a signing keypair. For a reference sample whose threat model has
the approval gate as admission control, it adds key-management surface without
changing the trust decision, so it is omitted. A deployment that
sources cards from a channel without an approval gate should add it (a2a-sdk
``create_agent_card_signer`` / ``create_signature_verifier``), sourcing the
private key from KMS or Secrets Manager.
"""

from __future__ import annotations

from a2a.types import AgentCapabilities, AgentCard, AgentSkill


def build_peer_card(a2a_endpoint_url: str) -> AgentCard:
    """Build the peer's complete A2A 0.3 card.

    All required a2a-sdk AgentCard fields are populated (name, description,
    url, version, capabilities, default_input_modes, default_output_modes,
    skills). The single greet AgentSkill carries the full required shape
    (id, name, description, tags).
    """
    return AgentCard(
        name="peer-greeter",
        description="Greets a given name over A2A: returns 'hello {name}'.",
        url=a2a_endpoint_url,
        version="0.1.0",
        protocol_version="0.3.0",
        capabilities=AgentCapabilities(),
        default_input_modes=["text"],
        default_output_modes=["text"],
        skills=[
            AgentSkill(
                id="greet",
                name="greet",
                description="Returns the literal string 'hello {name}' for the given name.",
                tags=["greet", "demo"],
            )
        ],
    )
