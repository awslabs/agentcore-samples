# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for U6 search-path: the search_and_invoke behavior.

Covers:
  - The search call shape. registryIds holds exactly one id (single-element
    list), searchQuery is passed, and maxResults is optional.
  - Peer selection by a stable identifier (recordId / name), never "the first
    result" and never "assume exactly one result".
  - The two distinct fail-closed outcomes, a clean non-raising empty result
    when nothing matches vs. a raised error from the shared helper when a
    matched peer fails the account check.
  - The common path uses the inline search descriptor with no batch_get
    (a data-plane double fails the test if batch_get is called).
  - Reuse of U5's shared verify_and_invoke (checked structurally: search_path
    imports verify_and_invoke and does not define its own).

No live AWS. The agent-registry data-plane client is an in-memory fake, the
A2A send is injected, and the peer card is U3's (unsigned) keypair, so U3's
verifier runs end-to-end rather than mocked.
"""

from __future__ import annotations

import pytest

from agent_registry_sample.orchestrator.search_path import search_and_invoke_impl
from agent_registry_sample.orchestrator.verify_and_invoke import VerificationError
from agent_registry_sample.peer.card import build_peer_card

_PEER_URL = "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A111111111111%3Aruntime%2Fpeer-xyz/invocations"
_ALLOWED_ACCOUNTS = {"111111111111"}
_REGISTRY_ID = "abcdEFGH1234"
_PEER_RECORD_ID = "peerRECORDid01"
_OTHER_RECORD_ID = "otherRECORD002"
_QUERY = "greeter"


def _peer_record(record_id: str, url: str = _PEER_URL, name: str = "peer-greeter") -> dict:
    """A search-result record whose descriptors carry U3's card inline (the
    shape the live search output returns)."""
    card = build_peer_card(url)
    return {
        "recordId": record_id,
        "name": name,
        "status": "APPROVED",
        "descriptors": {
            "a2aAgentCard": {
                "data": card.model_dump_json(exclude_none=True, by_alias=True),
                "dataSchemaVersion": "0.3",
            }
        },
    }


def _ok_send(url, params):
    name = params.message.parts[0].root.text if hasattr(params.message.parts[0], "root") else None
    return {"role": "agent", "parts": [{"kind": "text", "text": f"hello {name}"}]}


class _FakeSearchDataPlane:
    """An agent-registry data-plane double.

    Captures the search kwargs so the single-registry call shape can be
    asserted. batch_get fails the test if called, because the common path
    must use the inline descriptor."""

    def __init__(self, records):
        self._records = records
        self.search_kwargs = None
        self.batch_get_called = False

    def search_discoverable_registry_records(self, **kwargs):
        self.search_kwargs = kwargs
        return {"registryRecords": list(self._records)}

    def batch_get_discoverable_registry_record(self, entries):
        self.batch_get_called = True
        raise AssertionError("batch_get must NOT be called on the common (inline-descriptor) path")


# ------------------------- reuse -------------------------


def test_search_path_reuses_the_shared_verify_and_invoke_one_copy():
    """Search_path uses U5's shared verify_and_invoke. It imports the same
    function object and defines no verifier of its own."""
    import agent_registry_sample.orchestrator.search_path as sp
    import agent_registry_sample.orchestrator.verify_and_invoke as vai

    # the symbol search_path calls is the exact object verify_and_invoke exports
    assert sp.verify_and_invoke is vai.verify_and_invoke
    # search_path defines no verify/allowlist helper of its own (no second copy)
    assert not hasattr(sp, "_card_verifier")
    assert not hasattr(sp, "_card_runtime_account")


# ------------------------- search call shape -------------------------


def test_search_call_uses_exactly_one_registry_id():
    """RegistryIds is a single-element list and searchQuery is passed."""
    dp = _FakeSearchDataPlane([_peer_record(_PEER_RECORD_ID)])
    search_and_invoke_impl(
        _QUERY,
        registry_id=_REGISTRY_ID,
        data_plane_client=dp,
        allowed_accounts=_ALLOWED_ACCOUNTS,
        send_message=_ok_send,
        select_record_id=_PEER_RECORD_ID,
    )
    assert dp.search_kwargs["registryIds"] == [_REGISTRY_ID]
    assert len(dp.search_kwargs["registryIds"]) == 1
    assert dp.search_kwargs["searchQuery"] == _QUERY
    # maxResults is within the client's 1-20 bound
    assert 1 <= dp.search_kwargs["maxResults"] <= 20


# ------------------------- inline descriptor common path -------------------------


def test_common_path_uses_inline_descriptor_no_batch_get():
    """The chosen search-result record's inline descriptor goes straight to
    the shared helper with no batch_get (the double raises if batch_get is
    called)."""
    dp = _FakeSearchDataPlane([_peer_record(_PEER_RECORD_ID)])
    result = search_and_invoke_impl(
        _QUERY,
        registry_id=_REGISTRY_ID,
        data_plane_client=dp,
        allowed_accounts=_ALLOWED_ACCOUNTS,
        send_message=_ok_send,
        select_record_id=_PEER_RECORD_ID,
        greet_name="world",
    )
    assert result == "hello world"
    assert dp.batch_get_called is False


# ------------------------- explicit selection -------------------------


def test_explicit_selection_by_record_id_not_first_result():
    """The intended peer is selected by recordId even when it is not the
    first result."""
    records = [
        _peer_record(_OTHER_RECORD_ID, url="https://other.invalid/a2a", name="other"),
        _peer_record(_PEER_RECORD_ID),
    ]
    dp = _FakeSearchDataPlane(records)
    result = search_and_invoke_impl(
        _QUERY,
        registry_id=_REGISTRY_ID,
        data_plane_client=dp,
        allowed_accounts=_ALLOWED_ACCOUNTS,
        send_message=_ok_send,
        select_record_id=_PEER_RECORD_ID,  # the second result
        greet_name="world",
    )
    assert result == "hello world"


def test_selection_by_name_fallback_selector():
    """Name is a valid stable-identifier selector."""
    records = [
        _peer_record(_OTHER_RECORD_ID, url="https://other.invalid/a2a", name="other"),
        _peer_record(_PEER_RECORD_ID, name="peer-greeter"),
    ]
    dp = _FakeSearchDataPlane(records)
    result = search_and_invoke_impl(
        _QUERY,
        registry_id=_REGISTRY_ID,
        data_plane_client=dp,
        allowed_accounts=_ALLOWED_ACCOUNTS,
        send_message=_ok_send,
        select_name="peer-greeter",
        greet_name="world",
    )
    assert result == "hello world"


def test_multiple_results_without_selector_is_clean_empty_not_first():
    """With multiple results and no selector, the path does not pick the
    first. It returns the clean empty result (None)."""
    records = [
        _peer_record(_OTHER_RECORD_ID, url="https://other.invalid/a2a", name="other"),
        _peer_record(_PEER_RECORD_ID),
    ]
    sends = []
    dp = _FakeSearchDataPlane(records)
    result = search_and_invoke_impl(
        _QUERY,
        registry_id=_REGISTRY_ID,
        data_plane_client=dp,
        allowed_accounts=_ALLOWED_ACCOUNTS,
        send_message=lambda u, p: sends.append(1),
    )
    assert result is None  # clean empty, not the first result
    assert sends == []  # no peer invoked


# ------------------------- the two distinct outcomes -------------------------


def test_no_peer_matches_is_clean_empty_not_exception():
    """No result matches the selector -> a clean, non-raising empty result
    (None), not an exception."""
    dp = _FakeSearchDataPlane([_peer_record(_OTHER_RECORD_ID, name="other")])
    sends = []
    result = search_and_invoke_impl(
        _QUERY,
        registry_id=_REGISTRY_ID,
        data_plane_client=dp,
        allowed_accounts=_ALLOWED_ACCOUNTS,
        send_message=lambda u, p: sends.append(1),
        select_record_id=_PEER_RECORD_ID,  # not present among results
    )
    assert result is None
    assert sends == []


def test_empty_search_results_is_clean_empty():
    """Zero search results -> clean empty (None)."""
    dp = _FakeSearchDataPlane([])
    result = search_and_invoke_impl(
        _QUERY,
        registry_id=_REGISTRY_ID,
        data_plane_client=dp,
        allowed_accounts=_ALLOWED_ACCOUNTS,
        send_message=_ok_send,
        select_record_id=_PEER_RECORD_ID,
    )
    assert result is None


def test_matched_peer_verify_failure_raises_distinct_from_empty():
    """A peer matches but its account is not allowed -> the shared helper
    raises (VerificationError) and U6 does not swallow it. That fail-closed
    error is distinct from the clean 'not found' empty."""
    dp = _FakeSearchDataPlane([_peer_record(_PEER_RECORD_ID)])
    sends = []
    with pytest.raises(VerificationError):
        search_and_invoke_impl(
            _QUERY,
            registry_id=_REGISTRY_ID,
            data_plane_client=dp,
            allowed_accounts={"999999999999"},  # matched peer, but account not allowed
            send_message=lambda u, p: sends.append(1),
            select_record_id=_PEER_RECORD_ID,
        )
    assert sends == []  # fail-closed before any send
