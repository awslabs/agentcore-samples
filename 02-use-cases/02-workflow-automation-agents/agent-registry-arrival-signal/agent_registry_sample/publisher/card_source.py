# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""U4 publisher. Sources the peer's A2A card to serialize into the registry
record descriptor.

The publisher publishes the card the peer defines and does not HTTP-fetch it
from the runtime. An AgentCore Runtime agent exposes only the SigV4-gated
`.../runtimes/<arn>/invocations` POST binding. It has no well-known card GET
endpoint, and a GET returns `HTTP 405 Method Not Allowed`. U3 authors the
peer's card (`build_peer_card`) as the source of truth. The publisher builds
that same card with the peer's deployed endpoint URL as the card's `url` and
publishes it into the registry record. In a multi-team setup the peer team
hands the publisher this card (a file or artifact). The static-file path models
that, and the SDK-model build is the sample's one-command convenience. Either
way the card has U3's schema-0.3-compliant shape.
"""

from __future__ import annotations

import json
from pathlib import Path


def _build_card_json_for_endpoint(peer_endpoint_url: str) -> str:
    """Build the peer's card (U3's source-of-truth builder) with the peer's
    deployed endpoint URL as the card's `url`, and return it as a JSON string
    for descriptors.a2aAgentCard.data.

    No HTTP GET is made, because the invocations endpoint is POST/SigV4 only
    and a GET returns 405. The card's `url` is the peer's invocations endpoint
    so a consumer that reads this card back knows which runtime to invoke.
    """
    from agent_registry_sample.peer.card import build_peer_card

    card = build_peer_card(peer_endpoint_url)
    return card.model_dump_json(exclude_none=True, by_alias=True)


def _load_card_json_from_file(file_path: str) -> str:
    """Local-dev fallback: read a static card JSON file and return it as a
    JSON string (validated to parse)."""
    text = Path(file_path).read_text(encoding="utf-8")
    return json.dumps(json.loads(text))


def load_peer_card_json(url: str | None = None, file_path: str | None = None) -> str:
    """Resolve the peer card JSON to publish into the registry record.

    - ``url`` given  -> build U3's card with that peer endpoint URL as
      the card's `url` (the walkthrough path, not an HTTP fetch). ``url`` is
      the peer's deployed invocations endpoint.
    - only ``file_path`` given -> static card JSON file (models the multi-team
      hand-off, where the peer team gives the publisher the card file).
    - neither given -> error (we cannot publish a card we cannot source).
    """
    if url:
        return _build_card_json_for_endpoint(url)
    if file_path:
        return _load_card_json_from_file(file_path)
    raise ValueError(
        "No peer card source: pass the peer endpoint URL (built via U3's "
        "card builder) or a static --peer-card-file."
    )


def build_sample_card_json_for_dev() -> str:
    """Produce a static card JSON for the local-dev fallback, reusing U3's
    own card builder so the fallback carries the same shape the peer serves.

    Use this to generate the --peer-card-file content in a dry run. It imports
    U3's peer package so the shape cannot drift from what the peer serves.
    """
    from agent_registry_sample.peer.card import build_peer_card

    card = build_peer_card("https://peer.example.invalid/a2a")
    return card.model_dump_json(exclude_none=True, by_alias=True)
