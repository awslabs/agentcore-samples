# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for U7 walkthrough-teardown.

Covers:
  - the A2A send transport is the bedrock-agentcore InvokeAgentRuntime path,
    with no httpx/a2a-sdk HTTPS client, and the response decoder reads the
    artifacts[].parts[].text envelope.
  - teardown's records-before-stack deprecate -> delete ordering per record.
  - teardown best-effort idempotence. "Already gone" at either sub-step is
    tolerated and the run converges.
  - the walkthrough composes the unit entry points and fails fast (a failed
    composed step raises, no silent continue).

No live AWS: control-plane and CLI calls are faked/monkeypatched.
"""

from __future__ import annotations

import inspect

import pytest

from agent_registry_sample.orchestrator import runtime_wiring
from agent_registry_sample.walkthrough import teardown as td
from agent_registry_sample.walkthrough import walkthrough as wt

# ------------------------- A2A send wiring -------------------------


def test_a2a_send_is_wired_via_invoke_agent_runtime():
    """The A2A send is backed by the bedrock-agentcore InvokeAgentRuntime API
    (boto3 auto-SigV4), not an httpx/a2a-sdk HTTPS client. Building the
    callable does not raise (it reaches the network only when invoked)."""
    build_src = inspect.getsource(runtime_wiring._build_a2a_send)
    assert "A2A send is not wired" not in build_src
    send = runtime_wiring._build_a2a_send()
    assert callable(send)
    # the send uses invoke_agent_runtime with an A2A JSON-RPC envelope
    assert "invoke_agent_runtime" in build_src
    assert "message/send" in build_src


def test_old_https_a2a_machinery_is_gone():
    """Regression guard against an HTTPS-native A2A transport. There is no
    _SigV4Auth httpx.Auth and no _async_send coroutine, and the send function
    body does not construct an a2a-sdk A2ACardResolver GET (an AgentCore
    runtime has no well-known card GET, and a GET returns HTTP 405). The module
    docstring may name these, so this guard checks the code, not the prose."""
    assert not hasattr(runtime_wiring, "_SigV4Auth")
    assert not hasattr(runtime_wiring, "_async_send")
    # Check the send function body, not the module docstring, which may name
    # the HTTPS machinery to explain why it is not used.
    send_src = inspect.getsource(runtime_wiring._build_a2a_send)
    assert "A2ACardResolver" not in send_src
    assert "auth_flow" not in send_src
    assert "invoke_agent_runtime" in send_src


def test_decode_invoke_response_reads_artifacts_parts_text():
    """The invoke-response decoder pulls the reply from the envelope
    shape result.artifacts[].parts[].text (not result.message.parts)."""
    import json as _json

    real = {
        "id": "1",
        "jsonrpc": "2.0",
        "result": {
            "artifacts": [
                {
                    "artifactId": "a1",
                    "name": "agent_response",
                    "parts": [{"kind": "text", "text": "hello world"}],
                }
            ],
            "status": {"state": "completed"},
        },
    }

    class _Body:
        def __init__(self, b):
            self._b = b

        def read(self):
            return self._b

    resp = {"response": _Body(_json.dumps(real).encode())}
    assert runtime_wiring._decode_invoke_response(resp) == "hello world"


def test_runtime_arn_from_invocations_url():
    """The send derives the agentRuntimeArn from the peer's invocations URL
    (the URL-encoded ARN between /runtimes/ and /invocations)."""
    from urllib.parse import quote

    arn = "arn:aws:bedrock-agentcore:us-east-1:111111111111:runtime/peer-xyz"
    url = (
        "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/"
        f"{quote(arn, safe='')}/invocations"
    )
    assert runtime_wiring._runtime_arn_from_invocations_url(url) == arn


# ------------------------- teardown: ordering + idempotence -------------------------


class _FakeControlClient:
    """Records the control-plane call order; can be told a record is already
    gone (raises a ResourceNotFound-named error)."""

    def __init__(self, missing: set[str] | None = None):
        self.calls: list[tuple[str, str]] = []
        self._missing = missing or set()

    def update_registry_record_status(self, *, registryId, recordId, status, statusReason):  # noqa: N803
        if recordId in self._missing:
            raise RuntimeError("ResourceNotFoundException: record already gone")
        assert status == "DEPRECATED"
        assert statusReason  # never omitted
        self.calls.append(("deprecate", recordId))

    def delete_registry_record(self, *, registryId, recordId):  # noqa: N803
        if recordId in self._missing:
            raise RuntimeError("ResourceNotFoundException: record already gone")
        self.calls.append(("delete", recordId))


def test_deprecate_then_delete_in_order():
    """Each record is DEPRECATED first, then deleted, in that order."""
    client = _FakeControlClient()
    outcome = td.deprecate_then_delete_record(client, "reg-1", "rec-abc")
    assert outcome == "removed"
    assert client.calls == [("deprecate", "rec-abc"), ("delete", "rec-abc")]


def test_deprecate_then_delete_tolerates_already_gone():
    """Best-effort idempotent: an already-gone record is tolerated at both
    sub-steps and reports 'already-gone' rather than raising."""
    client = _FakeControlClient(missing={"rec-gone"})
    outcome = td.deprecate_then_delete_record(client, "reg-1", "rec-gone")
    assert outcome == "already-gone"
    assert client.calls == []  # nothing recorded; both sub-steps tolerated


def test_teardown_removes_every_record_before_stack(monkeypatch):
    """teardown() deprecates and deletes every supplied record (peer and
    orchestrator) before it destroys the runtimes/stack, and the CLI
    destroys are invoked (tolerant)."""
    import boto3

    client = _FakeControlClient()
    monkeypatch.setattr(boto3, "client", lambda *a, **k: client)
    # stub the CLI destroys so no agentcore/cdk runs
    destroyed: list[str] = []
    monkeypatch.setattr(td, "_run_tolerant", lambda cmd, *, cwd, what: destroyed.append(what))

    outcome = td.teardown(
        registry_id="reg-1",
        record_ids=["rec-peer", "rec-orch"],
        region="us-east-1",
        repo_root=".",
        orchestrator_project_dir="orchestrator-host",
        peer_project_dir="peer-host",
    )
    # both records removed, in records-before-stack order
    assert client.calls == [
        ("deprecate", "rec-peer"),
        ("delete", "rec-peer"),
        ("deprecate", "rec-orch"),
        ("delete", "rec-orch"),
    ]
    # runtimes + stack destroyed after the records
    assert destroyed == ["orchestrator-runtime", "peer-runtime", "ArrivalSignalInfrastructureStack"]
    assert outcome["records"] == {"rec-peer": "removed", "rec-orch": "removed"}


# ------------------------- walkthrough: fail-fast composition -------------------------


def test_walkthrough_run_is_fail_fast_not_silent(monkeypatch):
    """A failed composed step raises WalkthroughError instead of continuing
    silently."""

    def _boom(cmd, **kwargs):
        class _P:
            returncode = 1
            stdout = ""
            stderr = "deploy failed"

        return _P()

    monkeypatch.setattr(wt.subprocess, "run", _boom)
    with pytest.raises(wt.WalkthroughError):
        wt._run(["agentcore", "deploy"], cwd="orchestrator-host")


# ------------------------- phase split: deploy -> values file -> demo -------------------------

import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402


def _deploy_args(tmp_path, values_file=None):
    return SimpleNamespace(
        registry_id="reg-1",
        registry_arn="arn:aws:agent-registry:us-east-1:111111111111:registry/reg-1",
        region="us-east-1",
        repo_root=".",
        orchestrator_project_dir="orchestrator-host",
        peer_project_dir="peer-host",
        peer_name="peer-greeter",
        search_query="greeter",
        values_file=values_file or str(tmp_path / "walkthrough-env.json"),
    )


def _stub_deploy_steps(monkeypatch):
    """Stub every deploy step so run_deploy() runs with no AWS/CLI."""
    monkeypatch.setattr(
        wt,
        "deploy_orchestrator",
        lambda project_dir, region, registry_id: {
            "arn": "arn:aws:bedrock-agentcore:us-east-1:111111111111:runtime/orch",
            "id": "orch-id",
            "roleArn": "arn:aws:iam::111111111111:role/orch",
            "a2aUrl": "https://orchestrator.example.invalid/",
            "logGroup": "/aws/bedrock-agentcore/runtime/orchestrator",
        },
    )
    monkeypatch.setattr(
        wt,
        "deploy_infra",
        lambda repo_root, runtime, registry_arn: {
            "submitterRoleArn": "arn:aws:iam::111111111111:role/submitter",
            "approverRoleArn": "arn:aws:iam::111111111111:role/approver",
        },
    )
    monkeypatch.setattr(
        wt, "register_orchestrator_record", lambda registry_id, a2a_url, region: "orch-record-id"
    )
    monkeypatch.setattr(
        wt,
        "deploy_peer",
        lambda peer_dir: {
            "cardUrl": "https://peer.example.invalid/card.json",
            "arn": "arn:aws:bedrock-agentcore:us-east-1:111111111111:runtime/peer-xyz",
        },
    )
    # (f) the consumer-group grant, stubbed so run_deploy() makes no AWS call.
    # Mirrors grant_consumer_access's signature and return shape.
    monkeypatch.setattr(
        wt,
        "grant_consumer_access",
        lambda peer_runtime_arn, consumer_role_arn, region, demo_operator_arn=None: {
            "runtimeArn": peer_runtime_arn,
            "endpointArn": f"{peer_runtime_arn}/runtime-endpoint/DEFAULT",
        },
    )
    # The operator-ARN lookup (STS) is stubbed so run_deploy() makes no
    # AWS call. The returned ARN is the demo operator added to the group set.
    monkeypatch.setattr(
        wt, "_operator_arn", lambda region: "arn:aws:iam::111122223333:user/demo-op"
    )


def test_deploy_writes_a_complete_values_file(tmp_path, monkeypatch):
    """`deploy` captures every post-deploy value and writes a complete values
    file, including the five captured post-deploy values."""
    _stub_deploy_steps(monkeypatch)
    values_file = str(tmp_path / "walkthrough-env.json")
    rc = wt.main(
        [
            "deploy",
            "--registry-id",
            "reg-1",
            "--registry-arn",
            "arn:aws:agent-registry:us-east-1:111111111111:registry/reg-1",
            "--values-file",
            values_file,
        ]
    )
    assert rc == 0
    with open(values_file, encoding="utf-8") as handle:
        values = json.load(handle)
    # the five captured post-deploy values are present
    assert values["publisher"]["submitterRoleArn"].endswith("role/submitter")
    assert values["publisher"]["approverRoleArn"].endswith("role/approver")
    assert values["orchestrator"]["a2aUrl"] == "https://orchestrator.example.invalid/"
    assert values["orchestrator"]["logGroup"].endswith("/orchestrator")
    assert values["peer"]["cardUrl"].endswith("/card.json")
    # the peer opted into the consumer group. Its runtime ARN and the
    # DEFAULT-endpoint ARN convention are recorded for the demo/teardown.
    assert values["peer"]["arn"].endswith("runtime/peer-xyz")
    assert values["peer"]["endpointArn"].endswith("/runtime-endpoint/DEFAULT")
    # and the values file is complete per the schema check
    assert wt._missing_value_keys(values) == []


def test_demo_reads_values_file_and_runs_both_demos(tmp_path, monkeypatch):
    """`demo` reads the values file and runs push + pull (both stubbed),
    writing the result file, with no post-deploy args."""
    values = {
        "registryId": "reg-1",
        "region": "us-east-1",
        "orchestrator": {"logGroup": "/aws/bedrock-agentcore/runtime/orchestrator"},
        "peer": {"cardUrl": "https://peer.example.invalid/card.json", "name": "peer-greeter"},
        "publisher": {
            "submitterRoleArn": "arn:aws:iam::111111111111:role/submitter",
            "approverRoleArn": "arn:aws:iam::111111111111:role/approver",
        },
        "searchQuery": "greeter",
    }
    values_file = tmp_path / "walkthrough-env.json"
    values_file.write_text(json.dumps(values), encoding="utf-8")
    result_file = tmp_path / "walkthrough-result.json"

    monkeypatch.setattr(
        wt,
        "run_push_demo",
        lambda **kw: {"recordId": "rec-1", "greetProof": "hello arrival-signal-sample"},
    )
    monkeypatch.setattr(wt, "run_pull_demo", lambda **kw: "hello arrival-signal-sample")

    rc = wt.main(["demo", "--values-file", str(values_file), "--result-file", str(result_file)])
    assert rc == 0
    result = json.loads(result_file.read_text(encoding="utf-8"))
    assert result["pushProof"] == "hello arrival-signal-sample"
    assert result["pullProof"] == "hello arrival-signal-sample"
    assert result["bothPathsProven"] is True


def test_demo_fails_cleanly_when_values_file_absent(tmp_path, capsys):
    """`demo` with no values file exits cleanly (rc 2, clear message) instead
    of with a traceback."""
    missing = tmp_path / "does-not-exist.json"
    rc = wt.main(["demo", "--values-file", str(missing)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "run `walkthrough.py deploy` first" in err
    assert "not found" in err


def test_demo_fails_cleanly_when_values_file_incomplete(tmp_path, capsys):
    """`demo` with an incomplete values file exits cleanly naming the missing
    keys instead of with a traceback."""
    bad = tmp_path / "walkthrough-env.json"
    bad.write_text(json.dumps({"registryId": "reg-1", "region": "us-east-1"}), encoding="utf-8")
    rc = wt.main(["demo", "--values-file", str(bad)])
    assert rc == 2
    err = capsys.readouterr().err
    assert "incomplete" in err
    assert "publisher.submitterRoleArn" in err


def test_run_does_deploy_then_demo(tmp_path, monkeypatch):
    """`run` deploys (capturing its own outputs) then proves both paths, with
    no operator-supplied post-deploy args."""
    _stub_deploy_steps(monkeypatch)
    monkeypatch.setattr(
        wt,
        "run_push_demo",
        lambda **kw: {"recordId": "rec-1", "greetProof": "hello arrival-signal-sample"},
    )
    monkeypatch.setattr(wt, "run_pull_demo", lambda **kw: "hello arrival-signal-sample")
    values_file = str(tmp_path / "walkthrough-env.json")
    result_file = str(tmp_path / "walkthrough-result.json")

    rc = wt.main(
        [
            "run",
            "--registry-id",
            "reg-1",
            "--registry-arn",
            "arn:aws:agent-registry:us-east-1:111111111111:registry/reg-1",
            "--values-file",
            values_file,
            "--result-file",
            result_file,
        ]
    )
    assert rc == 0
    # deploy wrote the values file and demo wrote the result file
    assert json.loads(open(values_file, encoding="utf-8").read())["publisher"]["approverRoleArn"]
    assert json.loads(open(result_file, encoding="utf-8").read())["bothPathsProven"] is True


def test_extract_publisher_roles_from_cdk_outputs_file(tmp_path):
    """deploy_infra captures the role ARNs from the `cdk deploy --outputs-file`
    JSON ({<StackName>: {<OutputKey>: value}}), so the role ARNs are not
    required args."""
    outputs = {
        "ArrivalSignalInfrastructureStack": {
            "PublisherSubmitterRoleArn": "arn:aws:iam::111111111111:role/submitter",
            "PublisherApproverRoleArn": "arn:aws:iam::111111111111:role/approver",
            "IdempotencyTableName": "reg-1-idempotency",
        }
    }
    outputs_path = tmp_path / "cdk-outputs.json"
    outputs_path.write_text(json.dumps(outputs), encoding="utf-8")
    roles = wt._extract_publisher_roles(str(outputs_path))
    assert roles["submitterRoleArn"].endswith("role/submitter")
    assert roles["approverRoleArn"].endswith("role/approver")


def _stub_push_demo_collaborators(monkeypatch, calls, *, prior_peer_id):
    """Stub run_push_demo's collaborators (all imported at call time from their
    source modules, so patch there, not on wt). Records an ordered call log."""
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: object())
    monkeypatch.setattr(
        "agent_registry_sample.sdk_reconciliation.reconciliation.find_record_id_by_name",
        lambda client, registry_id, name: prior_peer_id,
    )
    monkeypatch.setattr(
        "agent_registry_sample.walkthrough.teardown.deprecate_then_delete_record",
        lambda client, registry_id, record_id: calls.append(("delete", record_id)) or "removed",
    )
    monkeypatch.setattr(
        "agent_registry_sample.publisher.card_source.load_peer_card_json",
        lambda url, file_path: "{}",
    )
    monkeypatch.setattr(
        "agent_registry_sample.publisher.publish_and_approve.publish_and_approve",
        lambda **kw: calls.append(("publish", kw["name"])) or "NEW_PEER",
    )
    monkeypatch.setattr(
        wt, "_poll_orchestrator_log_for_greet", lambda *a, **k: "hello arrival-signal-sample"
    )


def _run_push_demo():
    return wt.run_push_demo(
        registry_id="reg-1",
        peer_name="peer-greeter",
        submitter_role_arn="arn:aws:iam::111111111111:role/submitter",
        approver_role_arn="arn:aws:iam::111111111111:role/approver",
        peer_card_url="https://peer.example.invalid/card.json",
        region="us-east-1",
        orchestrator_log_group="/aws/bedrock-agentcore/runtimes/orchestrator-DEFAULT",
    )


def test_push_demo_deletes_prior_peer_record_before_publishing(monkeypatch):
    """Re-runnability: a pre-existing 'peer-greeter' record is deleted before a
    fresh one is published. The peer is the newcomer whose fresh
    DRAFT->APPROVED transition fires the push arrival, so it is re-published,
    never reused (the standing orchestrator is reused)."""
    calls = []
    _stub_push_demo_collaborators(monkeypatch, calls, prior_peer_id="PRIOR_PEER")
    out = _run_push_demo()
    assert out["recordId"] == "NEW_PEER"
    assert ("delete", "PRIOR_PEER") in calls
    assert ("publish", "peer-greeter") in calls
    # Delete strictly precedes publish.
    assert calls.index(("delete", "PRIOR_PEER")) < calls.index(("publish", "peer-greeter"))


def test_push_demo_skips_delete_when_no_prior_peer_record(monkeypatch):
    """On a clean account (no prior record) nothing is deleted and publish runs
    straight away."""
    calls = []
    _stub_push_demo_collaborators(monkeypatch, calls, prior_peer_id=None)
    out = _run_push_demo()
    assert out["recordId"] == "NEW_PEER"
    assert all(kind != "delete" for kind, _ in calls)
    assert ("publish", "peer-greeter") in calls
