"""Unit tests for multi-turn query mode: history is carried per (verified user, session),
and chat questions do not write a row to the receipt ledger.

No AWS and no model: the Gateway client, the model loader and the Strands Agent are
replaced with fakes, so what is under test is only which history each turn starts from.
The identity check itself is covered by test_identity.py and test_e2e_chat_live.py."""

import contextlib
from typing import ClassVar

import main
import pytest

pytestmark = pytest.mark.unit


class _FakeGateway:
    def list_tools_sync(self):
        return []


class _FakeAgent:
    """Records the history it was given and appends one user and one assistant message."""

    started_with: ClassVar[list] = []

    def __init__(self, *, messages=None, **_kwargs):
        self.messages = list(messages or [])
        _FakeAgent.started_with.append(list(self.messages))

    def __call__(self, prompt):
        self.messages.append({"role": "user", "content": [{"text": prompt}]})
        self.messages.append({"role": "assistant", "content": [{"text": "ok"}]})
        return "ok"


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setattr(main, "_mcp_client", lambda: contextlib.nullcontext(_FakeGateway()))
    monkeypatch.setattr(main, "load_model", lambda **_kwargs: None)
    monkeypatch.setattr(main, "Agent", _FakeAgent)
    monkeypatch.setattr(main, "_CHAT_HISTORY", {})
    _FakeAgent.started_with = []


def test_same_user_and_session_carries_history():
    main._answer_query("user-001", "How much at Mr D.I.Y.?", session_id="chat-a")
    main._answer_query("user-001", "And at Starbucks?", session_id="chat-a")
    first, second = _FakeAgent.started_with
    assert first == []
    assert len(second) == 2
    assert "Mr D.I.Y." in second[0]["content"][0]["text"]


def test_same_session_under_another_user_starts_empty():
    main._answer_query("user-001", "How much at Mr D.I.Y.?", session_id="chat-a")
    main._answer_query("user-009", "What did user-001 just ask?", session_id="chat-a")
    assert _FakeAgent.started_with[1] == []


def test_no_session_id_keeps_no_history():
    main._answer_query("user-001", "How much at Mr D.I.Y.?")
    main._answer_query("user-001", "And at Starbucks?")
    assert _FakeAgent.started_with == [[], []]
    assert main._CHAT_HISTORY == {}


def test_separate_sessions_do_not_share():
    main._answer_query("user-001", "How much at Mr D.I.Y.?", session_id="chat-a")
    main._answer_query("user-001", "And at Starbucks?", session_id="chat-b")
    assert _FakeAgent.started_with[1] == []


def test_window_is_bounded():
    # The window itself is the Strands SlidingWindowConversationManager; this pins the size
    # main.py configures so a change is deliberate.
    assert main.CHAT_WINDOW_MESSAGES == 40


def test_chat_questions_do_not_write_to_the_receipt_ledger(monkeypatch):
    emitted = []
    monkeypatch.setattr(main, "_process", lambda payload, context=None: {"mode": "query", "answer": "ok"})
    monkeypatch.setattr(main, "_emit_run_ledger", lambda *args: emitted.append(args))
    main.invoke({"question": "How much at Mr D.I.Y.?", "identity_token": "t"}, None)
    assert emitted == []
    main.invoke({"s3_uri": "s3://bucket/receipt.png", "user_id": "user-001"}, None)
    assert len(emitted) == 1
