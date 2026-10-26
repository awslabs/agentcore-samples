# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""U6 search-path: the orchestrator's pull counterpart to U5's event path
(the behavior behind the search_and_invoke tool).

Design:
  - Search the agent-registry data plane with
    search_discoverable_registry_records (registryIds holds exactly one id,
    searchQuery <=256, maxResults optional 1-20), as accepted by boto3 1.43.116.
  - Search response records carry descriptors inline (the same `descriptors`
    shape batch_get returns), so the common path passes the chosen record
    straight to the shared helper with no separate batch_get call.
  - If a selected record's inline descriptor is not usable (assert_card_access
    fails closed on it), fall back once to batch_get_discoverable_registry_record
    for the full card. That call uses U5's existing GetDiscoverableRegistryRecord
    grant (no new IAM grant). The live search output carries descriptors inline
    and complete, so the fallback is a defensive path.
  - Reuse U5's verify_and_invoke helper, the single copy of the
    security-critical access/account/A2A logic. From "have a card record"
    onward, U5's event path and U6's search path are identical. U6 imports
    agent_registry_sample.orchestrator.verify_and_invoke rather than
    re-implementing anything, so the A2A-auth path is the same injected SigV4
    send_message.

Peer selection: U6 selects the intended peer from the results by a stable
identifier (recordId by default, name as a fallback selector). It never takes
the first result and never assumes exactly one result.

The two fail-closed outcomes are distinct:
  - No peer matches the search/selection -> a clean, logged, empty result
    (return None, not an exception).
  - A peer matches but verify/invoke fails -> the shared helper raises
    (VerificationError / DriftError). U6 does not swallow it, so the error
    reaches the caller, distinct from the clean "not found".

Observability: log lines are correlated by the search query and the selected
peer identifier, not an event recordId (U6 has no event, unlike U5). Steps are
search -> peer-selection -> account-check -> a2a-greet -> result. A fail-closed
reason is logged before raising and before the empty return.
"""

from __future__ import annotations

import json
import logging

from agent_registry_sample.orchestrator.verify_and_invoke import (
    DriftError,
    verify_and_invoke,
)

logger = logging.getLogger()

# Default A2A search-call bound. maxResults is optional (1-20) on the client,
# and capping the page keeps selection on a bounded result set.
_DEFAULT_MAX_RESULTS = 20


def _log(step: str, query: str | None = None, peer_id: str | None = None, **fields: object) -> None:
    """Query/selected-peer-correlated structured JSON log line.

    Correlated by (searchQuery, selectedPeerId), not by an event recordId,
    because U6 has no arrival event. A fail-closed reason is logged before the
    empty return and before raising.
    """
    logger.info(
        json.dumps({"step": step, "searchQuery": query, "selectedPeerId": peer_id, **fields})
    )


def _search(data_plane_client, registry_id: str, query: str, max_results: int) -> list[dict]:
    """Search the agent-registry data plane.

    registryIds holds exactly one registry id (single-element list, the client
    enforces min=1/max=1). searchQuery is required (<=256) and maxResults is
    optional (1-20). Returns the (possibly empty) list of registryRecords, each
    carrying its `descriptors` inline.
    """
    resp = data_plane_client.search_discoverable_registry_records(
        registryIds=[registry_id],
        searchQuery=query,
        maxResults=max_results,
    )
    return resp.get("registryRecords") or []


def _select_peer(
    records: list[dict], *, select_record_id: str | None, select_name: str | None
) -> dict | None:
    """Select the intended peer by a stable identifier.

    Selection is by recordId (preferred) or by name (fallback selector), never
    by taking the first result or assuming exactly one result. Returns the
    matching record, or None when nothing matches (the caller turns that into
    the clean fail-closed empty outcome).
    """
    if select_record_id is not None:
        for rec in records:
            if rec.get("recordId") == select_record_id:
                return rec
        return None
    if select_name is not None:
        for rec in records:
            if rec.get("name") == select_name:
                return rec
        return None
    # No selector given, so refuse to guess. A single result is the only case
    # that resolves safely without a selector. Anything else is "not uniquely
    # selectable" and yields a clean empty result (never the first of many).
    if len(records) == 1:
        return records[0]
    return None


def _ensure_card_record(data_plane_client, registry_id: str, record: dict) -> dict:
    """Use the inline descriptor as-is, falling back once to batch_get for
    the full card if it is not usable (batch_get uses U5's existing grant).

    "Not usable" means U0's assert_card_access fails closed on the record. In
    that case the record is re-fetched via batch_get and that record is
    returned. If the re-fetched record also fails, the shared helper fails
    closed on it (the caller does not swallow that)."""
    from agent_registry_sample.sdk_reconciliation.reconciliation import assert_card_access

    try:
        assert_card_access(record)
        return record  # common path: inline descriptor is complete
    except DriftError:
        # Defensive fallback. The live search output carries descriptors inline
        # and complete. Re-fetch the full record.
        record_id = record.get("recordId")
        _log(
            "descriptor_fallback",
            peer_id=record_id,
            reason="inline descriptor not usable; batch_get",
        )
        resp = data_plane_client.batch_get_discoverable_registry_record(
            entries=[{"registryId": registry_id, "recordIds": [record_id]}]
        )
        errors = resp.get("errors") or []
        records = resp.get("registryRecords") or []
        if errors or not records:
            raise RuntimeError(
                f"batch_get fallback failed for recordId={record_id}: errors={errors}"
            )
        return records[0]


def search_and_invoke_impl(
    query: str,
    *,
    registry_id: str,
    data_plane_client,
    allowed_accounts: set[str],
    send_message,
    select_record_id: str | None = None,
    select_name: str | None = None,
    greet_name: str = "arrival-signal-sample",
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> str | None:
    """The search_and_invoke behavior (U6), injected with its collaborators
    so it is testable without live AWS.

    Returns the greet result text ("hello {greet_name}") on a successful
    verify+invoke, or None on a clean fail-closed empty result (no peer matched
    the search/selection).

    Raises (fail-closed error, distinct from the clean empty):
        VerificationError / DriftError: a peer matched but its card failed
            the account check or card-access. The shared helper raises and
            U6 does not swallow it.
    """
    _log("search", query=query, registryId=registry_id)
    records = _search(data_plane_client, registry_id, query, max_results)
    _log("search_result", query=query, count=len(records))

    record = _select_peer(records, select_record_id=select_record_id, select_name=select_name)
    if record is None:
        # Clean fail-closed empty result, not an exception. Logged.
        _log(
            "no_peer_selected",
            query=query,
            reason="no result matched the explicit selector (or not uniquely selectable)",
        )
        return None

    peer_id = record.get("recordId")
    _log("peer_selected", query=query, peer_id=peer_id, name=record.get("name"))

    # Use the inline descriptor, with a batch_get fallback only if it is not usable.
    card_record = _ensure_card_record(data_plane_client, registry_id, record)

    # Reuse the shared helper (the single copy of the account check + A2A greet).
    # A failure here raises a fail-closed error, which U6 lets propagate. That is
    # distinct from the clean "not found" empty above.
    result = verify_and_invoke(card_record, greet_name, allowed_accounts, send_message)
    _log("invoked", query=query, peer_id=peer_id, greet=result)
    return result


__all__ = ["search_and_invoke_impl"]
