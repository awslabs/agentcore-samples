# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The shared card-access -> account-check -> A2A-greet helper.

U5 defines this helper and U6 (search path) reuses it, so one copy of the
security-sensitive access/account/A2A logic exists. From "have a card record" onward,
U5's event path and U6's search path are identical.

Sequence (all fail-closed, all before any send):
  1. assert_card_access(record), U0's fail-closed card primitive (imported
     directly from agent_registry_sample.sdk_reconciliation.reconciliation).
     It asserts the card descriptor path, schema version, and json.loads parse.
  2. Confirm the card's runtime account is in the allowed set. This is the
     code-layer trust check. Every AgentCore runtime shares one host, so the
     meaningful identity is the account in the card URL's runtime ARN.
  3. Only then, send the A2A message/send to the peer's greet skill in U3's
     shape (role "user", parts [{kind "text", text <name>}], messageId).

Trust note. The card is read from the approval-gated data plane (only approved
records are returned), so the registry approval gate is the admission-control
layer. This helper adds the account check. IAM's resource-based policy (the
consumer-group model) is the enforced invoke gate. The card is not signed in
this sample (see agent_registry_sample.peer.card), so there is no signature
step here.

The A2A invoke is the SigV4-gated bedrock-agentcore InvokeAgentRuntime binding.
The injected send_message callable performs it, so this helper
stays transport-agnostic and unit-testable with a double.
"""

from __future__ import annotations

import json
import logging
import uuid
from urllib.parse import unquote

from a2a.types import AgentCard

from agent_registry_sample.sdk_reconciliation.reconciliation import (
    DriftError,
    assert_card_access,
)

logger = logging.getLogger()


def _log(step: str, **fields: object) -> None:
    logger.info(json.dumps({"step": step, **fields}))


class VerificationError(Exception):
    """Raised (fail-closed) when the peer's card fails the account check. The
    event/search path lets this propagate so the handoff is not performed and
    (for U5) the adapter raises -> SQS retry -> DLQ."""


def _card_runtime_account(url: str) -> str | None:
    """Extract the AWS account id from the AgentCore runtime ARN embedded in
    the peer's invocations URL (.../runtimes/<url-encoded-arn>/invocations),
    or None if no ARN/account can be parsed (-> fail closed).

    Every AgentCore runtime shares one host (bedrock-agentcore.<region>.
    amazonaws.com), so a host allowlist is vacuous. The identity that
    varies, and that this fail-closed check validates, is the
    peer's account, carried in the runtime ARN of the card URL.
    """
    marker = "/runtimes/"
    if marker in url and "/invocations" in url:
        arn = unquote(url.split(marker, 1)[1].split("/invocations", 1)[0])
    else:
        arn = url  # tolerate a bare ARN
    parts = arn.split(":")
    # arn:aws:bedrock-agentcore:<region>:<account>:runtime/<id>
    if len(parts) >= 6 and parts[0] == "arn" and parts[4]:
        return parts[4]
    return None


def verify_and_invoke(record: dict, name: str, allowed_accounts: set[str], send_message) -> str:
    """Check the peer's card (account, fail-closed) then call its greet skill
    over A2A. Returns the greet result text ("hello {name}").

    Args:
        record: a registry record dict (from batch_get or search) whose
            descriptors carry the peer's A2A card.
        name: the name to greet.
        allowed_accounts: the set of permitted peer AWS account ids.
        send_message: a callable (url, message_send_params) -> response that
            performs the A2A message/send (injected so tests can run without a
            live peer. Production wires the SigV4 invoke client here).

    Raises:
        DriftError: if assert_card_access fails closed (U0's primitive).
        VerificationError: if the peer's runtime account is not in
            allowed_accounts, raised before any send.
    """
    # (1) U0's card primitive. Returns the parsed card dict.
    card_dict = assert_card_access(record)  # raises DriftError (fail closed) on failure
    card = AgentCard.model_validate(card_dict)

    # (2) Account check, fail closed before any send. This is the code layer of
    # defense-in-depth (WAFR SEC "apply security at all layers"). IAM's
    # resource-based policy (the consumer-group model) enforces the exact peer
    # at invoke time. The registry approval gate is the admission-control layer.
    account = _card_runtime_account(card.url)
    if account is None or account not in allowed_accounts:
        _log("account_check", result="fail", account=account)
        raise VerificationError(
            f"peer runtime account not allowed: account={account} url={card.url}"
        )
    _log("account_check", result="pass", account=account)

    # (3) The A2A message/send in U3's shape.
    from a2a.types import Message, MessageSendParams, Role, TextPart

    message = Message(
        role=Role.user,
        parts=[TextPart(text=name)],
        message_id=str(uuid.uuid4()),
    )
    params = MessageSendParams(message=message)
    _log("a2a_greet", target=card.url)
    response = send_message(card.url, params)
    result_text = _extract_text(response)
    _log("a2a_greet", result="ok", greet=result_text)
    return result_text


def _extract_text(response) -> str:
    """Pull the text out of the peer's A2A response Message
    ({role:"agent", parts:[{kind:"text", text:"hello {name}"}]})."""
    parts = getattr(response, "parts", None) or []
    for part in parts:
        root = getattr(part, "root", part)
        text = getattr(root, "text", None)
        if text is not None:
            return text
    # Fall back to a dict-shaped response (injected test doubles).
    if isinstance(response, dict):
        for part in response.get("parts", []):
            if "text" in part:
                return part["text"]
    raise VerificationError("peer A2A response carried no text part")


__all__ = ["verify_and_invoke", "VerificationError", "DriftError"]
