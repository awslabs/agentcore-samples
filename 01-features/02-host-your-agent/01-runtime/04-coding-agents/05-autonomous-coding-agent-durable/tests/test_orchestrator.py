"""Tests for the DURABLE orchestrator/handler.py.

Covers the pure helpers (session derivation, sandbox routing, review parsing,
prompt building) plus a full durable-flow run via DurableFunctionTestRunner with
mocked AWS calls and a simulated coder callback.
"""
import json
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "orchestrator"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Set required environment variables for handler import."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("BUCKET", "test-bucket")
    monkeypatch.setenv("CODING_AGENT_ARN", "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/cagent_coding")
    monkeypatch.setenv("SANDBOX_SWIFT_ARN", "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/cagent_sandbox_swift")
    monkeypatch.setenv("SANDBOX_ARN", "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/cagent_sandbox")
    monkeypatch.setenv("EVALUATOR_ARN", "")
    monkeypatch.setenv("MEMORY_ID", "")
    monkeypatch.setenv("PROJECT", "cagent")
    monkeypatch.setenv("SNS_TOPIC_ARN", "")


@pytest.fixture
def handler_mod(mock_env):
    """Import handler with mocked boto3 clients."""
    with patch("boto3.client") as mock_client:
        mock_client.return_value = MagicMock()
        import importlib
        import handler
        importlib.reload(handler)
        # No SSM in tests → force runtime_arn to fall back to env vars (the documented
        # fallback path) by making get_parameter raise, and clear any cached values.
        handler.ssm.get_parameter.side_effect = Exception("no ssm in tests")
        handler._arn_cache.clear()
        yield handler


class TestSessionIdDerivation:
    def test_deterministic(self, handler_mod):
        assert handler_mod._session_id_for("TICKET-1") == handler_mod._session_id_for("TICKET-1")

    def test_minimum_length(self, handler_mod):
        """Session IDs must be >= 33 chars (AgentCore requirement)."""
        assert len(handler_mod._session_id_for("T")) >= 33

    def test_different_tickets_different_sessions(self, handler_mod):
        assert handler_mod._session_id_for("TICKET-1") != handler_mod._session_id_for("TICKET-2")

    def test_project_prefix_included(self, handler_mod):
        assert handler_mod._session_id_for("TICKET-1").startswith("cagent-")


class TestSandboxRouting:
    def test_swift_routes_to_swift_sandbox(self, handler_mod):
        assert "swift" in handler_mod._sandbox_arn_for("swift")

    def test_default_routes_to_python_sandbox(self, handler_mod):
        arn = handler_mod._sandbox_arn_for("python")
        assert arn.endswith("cagent_sandbox")


class TestGateCommand:
    def test_swift_gate_redirects_scratch_off_shared_mount(self, handler_mod):
        """The swift gate MUST move SwiftPM's scratch dir off /mnt/shared (NFS) — the
        default .build there fails with 'database is locked' on a correct package."""
        cmd = handler_mod._gate_command("swift", "RAINBOW-1")
        assert "--scratch-path" in cmd
        scratch = cmd.split("--scratch-path", 1)[1].split()[0]
        assert scratch.startswith("/tmp/")  # nosec B108  # microVM-local, NOT the shared mount
        assert not scratch.startswith("/mnt/")

    def test_swift_gate_scratch_is_per_ticket(self, handler_mod):
        """Per-ticket scratch path so two tickets can never read/write each other's build
        tree (defence in depth on top of separate microVM sessions)."""
        a = handler_mod._gate_command("swift", "TICKET-A")
        b = handler_mod._gate_command("swift", "TICKET-B")
        assert "TICKET-A" in a.split("--scratch-path", 1)[1].split()[0]
        assert "TICKET-B" in b.split("--scratch-path", 1)[1].split()[0]
        assert a != b

    def test_gate_only_enters_its_own_ticket_dir(self, handler_mod):
        """The gate cd's strictly into its own ticket dir under the shared mount."""
        cmd = handler_mod._gate_command("swift", "RAINBOW-1")
        assert "cd /mnt/shared/RAINBOW-1 " in cmd

    def test_python_gate_unchanged(self, handler_mod):
        cmd = handler_mod._gate_command("python", "TICKET-1")
        assert "pytest" in cmd and "/mnt/shared/TICKET-1" in cmd


class TestReviewParsing:
    def test_parses_trailing_json(self, handler_mod):
        resp = {"result": 'Looks good overall.\n{"verdict": "approve", "issues": []}'}
        v = handler_mod._parse_review(resp)
        assert v["verdict"] == "approve"
        assert v["issues"] == []

    def test_parses_request_changes_with_issues(self, handler_mod):
        resp = {"result": 'Problems found.\n{"verdict": "request_changes", "issues": ["missing test", "off-by-one"]}'}
        v = handler_mod._parse_review(resp)
        assert v["verdict"] == "request_changes"
        assert "missing test" in v["issues"]

    def test_parses_code_fenced_json(self, handler_mod):
        # The agent commonly wraps the verdict in a ```json fence; the trailing ``` used to
        # break parsing and silently default to approve, swallowing request_changes.
        resp = {"result": 'My review.\n```json\n{"verdict": "request_changes", "issues": ["no docs"]}\n```'}
        v = handler_mod._parse_review(resp)
        assert v["verdict"] == "request_changes"
        assert v["issues"] == ["no docs"]

    def test_parses_json_with_braces_in_issue_strings(self, handler_mod):
        resp = {"result": '```\n{"verdict":"request_changes","issues":["use {x} not y"]}\n```'}
        v = handler_mod._parse_review(resp)
        assert v["verdict"] == "request_changes"
        assert v["issues"] == ["use {x} not y"]

    def test_extracts_durable_lessons(self, handler_mod):
        resp = {"result": 'review.\n```json\n{"verdict":"approve","issues":[],'
                          '"lessons":["NamedColor enum is canonical","use --enable-test-discovery"]}\n```'}
        v = handler_mod._parse_review(resp)
        assert v["lessons"] == ["NamedColor enum is canonical", "use --enable-test-discovery"]

    def test_lessons_default_empty_when_absent(self, handler_mod):
        resp = {"result": '{"verdict":"approve","issues":[]}'}
        v = handler_mod._parse_review(resp)
        assert v["lessons"] == []

    def test_defaults_open_on_unparseable(self, handler_mod):
        """A review with no JSON must not block the pipeline (default approve)."""
        v = handler_mod._parse_review({"result": "no structured verdict here"})
        assert v["verdict"] == "approve"


class TestCoderPrompt:
    def test_includes_ticket_and_workdir(self, handler_mod):
        ticket = {"id": "RAINBOW-1", "title": "Add theme", "description": "do it", "runtime": "swift"}
        p = handler_mod._coder_prompt(ticket, "RAINBOW-1", "", "")
        assert "RAINBOW-1" in p and "/mnt/shared/RAINBOW-1/" in p and "swift" in p

    def test_includes_error_context_on_retry(self, handler_mod):
        ticket = {"id": "T1", "title": "x", "description": "y", "runtime": "swift"}
        p = handler_mod._coder_prompt(ticket, "T1", "", "error: build failed at line 5")
        assert "PREVIOUS ATTEMPT FAILED" in p and "build failed" in p

    def test_includes_lessons_block(self, handler_mod):
        ticket = {"id": "T1", "title": "x", "description": "y", "runtime": "swift"}
        p = handler_mod._coder_prompt(ticket, "T1", "\n<lessons_learned>\nuse discovery\n</lessons_learned>\n", "")
        assert "lessons_learned" in p


# ---------------------------------------------------------------------------
# Full durable-flow test (mocked AWS) — runs the handler under the local runner,
# simulating the coder callback and a passing test gate.
# ---------------------------------------------------------------------------
durable_testing = pytest.importorskip(
    "aws_durable_execution_sdk_python_testing",
    reason="durable testing SDK not installed",
)


class TestDurableFlow:
    def test_happy_path_passes_and_finalizes(self, handler_mod):
        import threading
        from aws_durable_execution_sdk_python_testing import DurableFunctionTestRunner

        ticket = {"id": "RAINBOW-1", "title": "Add theme", "description": "do it",
                  "repo": "rainbow", "runtime": "swift"}

        # Mock the helpers that touch AWS so the durable flow runs offline.
        with patch.object(handler_mod, "_fetch_ticket", return_value=ticket), \
             patch.object(handler_mod, "_invoke_sandbox", return_value={"hydrated": True, "files": 30}), \
             patch.object(handler_mod, "_run_test_gate", return_value={"exit_code": 0, "passed": True, "output_tail": "118 tests passed"}), \
             patch.object(handler_mod, "_invoke_coder", return_value=None) as mock_coder, \
             patch.object(handler_mod, "_emit_stage"), \
             patch.object(handler_mod, "_notify"):

            runner = DurableFunctionTestRunner(handler=handler_mod.handler)
            with runner:
                # Start the durable execution; it suspends at the coder callback.
                arn = runner.run_async(input=json.dumps({"ticketId": "RAINBOW-1"}), timeout=30)

                # The runner wraps wait_for_callback in a child context; the callback
                # operation is named "<name> create callback id". Resolve it from a
                # thread so wait_for_result can proceed in parallel.
                def _resolve():
                    cb_id = runner.wait_for_callback(arn, name="coder_attempt_1 create callback id", timeout=25)
                    runner.send_callback_success(cb_id, json.dumps({"result": "done"}).encode("utf-8"))
                t = threading.Thread(target=_resolve)
                t.start()
                result = runner.wait_for_result(arn, timeout=30)
                t.join()

            out = result.result if isinstance(result.result, dict) else json.loads(result.result)
            assert out["status"] == "PASS"
            assert out["runtime"] == "swift"
            assert mock_coder.called  # coder invoked (non-blocking async callback)
