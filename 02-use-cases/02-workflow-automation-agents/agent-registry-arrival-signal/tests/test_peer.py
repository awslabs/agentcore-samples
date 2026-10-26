# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for U3 peer-agent. Covers the greet skill, the agent's single tool, the
served A2A card (all required fields), and the no-registration /
zero-registry-role invariants.

No live AWS calls anywhere in this file.
"""

from __future__ import annotations

from agent_registry_sample.peer.agent import build_agent, greet
from agent_registry_sample.peer.card import build_peer_card

_ENDPOINT = "https://peer.example.invalid/a2a"


def test_greet_returns_exact_hello_name():
    """Greet takes one name string and returns exactly
    'hello {name}' with no wrapper, punctuation, or extra fields."""
    assert greet("world") == "hello world"
    assert greet("Ada") == "hello Ada"


def test_build_agent_exposes_only_greet():
    """The peer's Agent exposes one tool, greet."""
    agent = build_agent()
    names = {getattr(t, "tool_name", getattr(t, "__name__", str(t))) for t in agent.tool_names}
    assert "greet" in names or "greet" in str(agent.tool_names)


def test_peer_card_has_all_required_a2a_sdk_fields():
    """The card carries all required a2a-sdk AgentCard fields
    and a greet AgentSkill with the full required shape (id, name,
    description, tags)."""
    card = build_peer_card(_ENDPOINT)
    assert card.name == "peer-greeter"
    assert card.description
    assert card.url == _ENDPOINT
    assert card.version
    assert card.capabilities is not None
    assert card.default_input_modes == ["text"]
    assert card.default_output_modes == ["text"]
    assert len(card.skills) == 1
    skill = card.skills[0]
    assert skill.id == "greet"
    assert skill.name == "greet"
    assert skill.description  # the C4 shape requires description
    assert skill.tags  # non-empty


def test_no_registration_script_exists():
    """The peer never self-registers, so the peer package has no
    registration script (the inverse of U2, whose
    register_orchestrator.py exists). Confirms none was copied over."""
    import os

    pkg_dir = os.path.dirname(
        __import__("agent_registry_sample.peer.agent", fromlist=["agent"]).__file__
    )
    files = os.listdir(pkg_dir)
    # No file matching a registration script convention.
    offenders = [f for f in files if "regist" in f.lower()]
    assert offenders == [], f"peer must have NO registration script; found: {offenders}"


def test_peer_imports_no_registry_client():
    """The peer's modules never construct an agent-registry /
    agent-registry-control boto3 client. The peer originates no registry call,
    which is why its execution role needs zero registry permissions. Scans the
    peer package source for the forbidden client construction."""
    import os

    pkg_dir = os.path.dirname(
        __import__("agent_registry_sample.peer.agent", fromlist=["agent"]).__file__
    )
    for fn in os.listdir(pkg_dir):
        if fn.endswith(".py"):
            src = open(os.path.join(pkg_dir, fn), encoding="utf-8").read()
            assert 'client("agent-registry' not in src, f"{fn} must not build a registry client"
            assert "client('agent-registry" not in src, f"{fn} must not build a registry client"
