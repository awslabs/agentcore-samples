# Copyright 2026 Attenu
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied. See the License for the specific language governing
# permissions and limitations under the License.

"""What the sample claims, asserted. Offline: no AWS, no network."""

import local_run
import pytest
from agents import build_session, denials, require_guard
from attenu_guard import AuditLog, Guard, evidence
from attenu_guard.adapters.strands import DelegationGuard
from attenu_guard.wire import Ed25519Signer
from permissions import (
    ANALYST_NAME,
    GREEDY_REQUEST,
    ORCHESTRATOR,
    ORCHESTRATOR_NAME,
    SCOPE_FOR,
    authority_for,
)
from strands import Agent


@pytest.fixture(scope="module")
def run():
    _result, guard, executed = local_run.run()
    return guard, executed


def test_the_read_the_sub_agent_was_delegated_goes_through(run):
    _guard, executed = run
    assert ("read_logs", 1500) in executed


def test_the_export_is_refused_before_the_tool_body_runs(run):
    guard, executed = run
    refusals = denials(guard)

    assert [d["scope"] for d in refusals] == ["logs.export"]
    assert refusals[0]["agent"] == ANALYST_NAME
    assert refusals[0]["tool"] == "export_findings"
    # The tool body appends to EXECUTED as its first statement, so its
    # absence is the proof the call never reached the function.
    assert not any(name == "export_findings" for name, _ in executed)


def test_the_sub_agent_holds_less_than_the_orchestrator(run):
    guard, _executed = run
    orchestrator = guard.guard_for_name(ORCHESTRATOR_NAME)
    analyst = guard.guard_for_name(ANALYST_NAME)

    assert analyst.is_narrower_than(orchestrator)
    assert not orchestrator.is_narrower_than(analyst)
    assert "logs.export" not in analyst.authority.scopes


def test_a_request_for_more_than_the_parent_holds_is_met_down():
    granted = ORCHESTRATOR.meet(GREEDY_REQUEST)

    assert granted.is_narrower_than(ORCHESTRATOR)
    assert "iam.admin" not in granted.scopes


def test_an_undeclared_sub_agent_cannot_be_delegated_to():
    assert authority_for("somebody_else", "any task") is None


def test_an_undeclared_tool_resolves_to_a_scope_nobody_grants():
    request = SCOPE_FOR({"name": "delete_bucket", "input": {}})

    assert request.scope == "tool.delete_bucket"
    assert not ORCHESTRATOR.permits(request.scope)


def test_the_ledger_verifies_and_names_the_refusal(run):
    guard, _executed = run
    entries = guard.root_guard.audit_log().entries

    ok, err = AuditLog.verify(entries)
    assert ok, err
    assert [e["reason"] for e in entries if e["event"] == "deny"] == ["scope_not_granted"]


def test_a_tampered_ledger_does_not_verify(run):
    guard, _executed = run
    entries = [dict(e) for e in guard.root_guard.audit_log().entries]

    for entry in entries:
        if entry["event"] == "deny":
            entry["event"] = "allow"
            break

    ok, _err = AuditLog.verify(entries)
    assert not ok


def test_the_evidence_bundle_verifies_on_its_own(run):
    guard, _executed = run
    signer = Ed25519Signer.generate(kid="agentcore-test")

    bundle = evidence.export_bundle(guard.root_guard.audit_log(), signer)
    report = evidence.verify_bundle(bundle, signer)

    assert report["ok"]
    assert report["checks"]["integrity"]
    assert report["checks"]["monotonicity"]
    assert report["checks"]["containment"]


def test_an_unbound_entry_agent_refuses_to_start():
    stranger = Agent(name="stranger", callback_handler=None)
    guard = DelegationGuard(
        root_guard=Guard.issue(ORCHESTRATOR_NAME, ORCHESTRATOR),
        root_agent_name=ORCHESTRATOR_NAME,
        scope_for=SCOPE_FOR,
        authority_for=authority_for,
    )

    with pytest.raises(RuntimeError, match="refusing to run unguarded"):
        require_guard(stranger, guard=guard)


def test_the_session_is_built_per_invocation():
    """Two sessions must not share a ledger: one caller's decisions are
    not another's evidence."""
    _o1, _a1, first = build_session(local_run.ScriptedModel([("text", "one")]))
    _o2, _a2, second = build_session(local_run.ScriptedModel([("text", "two")]))

    assert first.root_guard is not second.root_guard
    assert first.root_guard.audit_log() is not second.root_guard.audit_log()
