"""Tests for sandbox/app.py — path validation and exec state tracking.

The sandbox depends on bedrock_agentcore which isn't available locally,
so we mock it before importing.
"""
import json
import os
import sys
import time
import types
import pytest

# Mock bedrock_agentcore before importing sandbox
_mock_agentcore = types.ModuleType("bedrock_agentcore")
_mock_runtime = types.ModuleType("bedrock_agentcore.runtime")


class _MockApp:
    def entrypoint(self, fn):
        return fn
    def run(self):
        pass


_mock_runtime.BedrockAgentCoreApp = _MockApp
_mock_agentcore.runtime = _mock_runtime
sys.modules["bedrock_agentcore"] = _mock_agentcore
sys.modules["bedrock_agentcore.runtime"] = _mock_runtime

# Now we can import sandbox.app
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sandbox"))

# The sandbox imports shared.validation via sys.path manipulation pointing to shared_libs.
# For tests, we ensure the project root (which has shared/) is in sys.path.
import sandbox.app as sandbox_app


class TestSandboxTicketDir:
    """Test _ticket_dir validation in the sandbox."""

    def test_valid_prefix(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sandbox_app, "CODE_MOUNT", str(tmp_path))
        result = sandbox_app._ticket_dir({"ticket_prefix": "TICKET-1"})
        assert result == os.path.join(str(tmp_path), "TICKET-1")
        assert os.path.isdir(result)

    def test_empty_prefix_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sandbox_app, "CODE_MOUNT", str(tmp_path))
        with pytest.raises(ValueError, match="invalid ticket_prefix"):
            sandbox_app._ticket_dir({"ticket_prefix": ""})

    def test_traversal_prefix_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sandbox_app, "CODE_MOUNT", str(tmp_path))
        with pytest.raises(ValueError):
            sandbox_app._ticket_dir({"ticket_prefix": "../etc"})

    def test_slash_prefix_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sandbox_app, "CODE_MOUNT", str(tmp_path))
        with pytest.raises(ValueError):
            sandbox_app._ticket_dir({"ticket_prefix": "sub/dir"})

    def test_shell_metacharacters_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sandbox_app, "CODE_MOUNT", str(tmp_path))
        with pytest.raises(ValueError):
            sandbox_app._ticket_dir({"ticket_prefix": "TICKET;rm -rf /"})

    def test_spaces_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sandbox_app, "CODE_MOUNT", str(tmp_path))
        with pytest.raises(ValueError):
            sandbox_app._ticket_dir({"ticket_prefix": "TICKET 1"})

    def test_null_byte_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sandbox_app, "CODE_MOUNT", str(tmp_path))
        with pytest.raises(ValueError):
            sandbox_app._ticket_dir({"ticket_prefix": "TICKET\x001"})


class TestSandboxSafePath:
    """Test _safe_path boundary enforcement in the sandbox."""

    def test_relative_within_base(self, ticket_dir):
        result = sandbox_app._safe_path(ticket_dir, "file.py")
        assert result == os.path.join(ticket_dir, "file.py")

    def test_nested_relative(self, ticket_dir):
        result = sandbox_app._safe_path(ticket_dir, "src/main.py")
        assert result.startswith(ticket_dir)

    def test_traversal_rejected(self, ticket_dir):
        with pytest.raises(ValueError, match="escapes base"):
            sandbox_app._safe_path(ticket_dir, "../../etc/passwd")

    def test_absolute_outside_rejected(self, ticket_dir):
        with pytest.raises(ValueError, match="escapes base"):
            sandbox_app._safe_path(ticket_dir, "/etc/passwd")

    def test_symlink_escape_rejected(self, ticket_dir):
        link = os.path.join(ticket_dir, "link_to_etc")
        os.symlink("/etc", link)
        with pytest.raises(ValueError, match="escapes base"):
            sandbox_app._safe_path(ticket_dir, "link_to_etc/passwd")

    def test_base_itself_is_valid(self, ticket_dir):
        result = sandbox_app._safe_path(ticket_dir, ".")
        assert result == os.path.realpath(ticket_dir)


class TestSandboxExecState:
    """Test execution state tracking (crash recovery)."""

    def test_write_and_read_state(self, workspace_dir, monkeypatch):
        monkeypatch.setattr(sandbox_app, "WORKSPACE", workspace_dir)
        monkeypatch.setattr(sandbox_app, "EXEC_STATE_FILE",
                            os.path.join(workspace_dir, ".exec_state.json"))

        sandbox_app._write_exec_state({"status": "running", "cmd": "pytest", "started_at": time.time()})
        state = sandbox_app._read_exec_state()
        assert state["status"] == "running"
        assert state["cmd"] == "pytest"

    def test_interrupted_detection(self, workspace_dir, monkeypatch):
        monkeypatch.setattr(sandbox_app, "WORKSPACE", workspace_dir)
        state_file = os.path.join(workspace_dir, ".exec_state.json")
        monkeypatch.setattr(sandbox_app, "EXEC_STATE_FILE", state_file)

        # Simulate a sandbox that died while running
        sandbox_app._write_exec_state({"status": "running", "cmd": "npm test", "started_at": time.time()})

        interrupted = sandbox_app._check_interrupted()
        assert interrupted is not None
        assert interrupted["_interrupted_execution"] is True
        assert "npm test" in interrupted["_previous_cmd"]

    def test_no_interrupted_when_completed(self, workspace_dir, monkeypatch):
        monkeypatch.setattr(sandbox_app, "WORKSPACE", workspace_dir)
        state_file = os.path.join(workspace_dir, ".exec_state.json")
        monkeypatch.setattr(sandbox_app, "EXEC_STATE_FILE", state_file)

        sandbox_app._write_exec_state({"status": "completed", "cmd": "pytest"})
        assert sandbox_app._check_interrupted() is None

    def test_no_state_file(self, workspace_dir, monkeypatch):
        monkeypatch.setattr(sandbox_app, "WORKSPACE", workspace_dir)
        monkeypatch.setattr(sandbox_app, "EXEC_STATE_FILE",
                            os.path.join(workspace_dir, "nonexistent.json"))
        assert sandbox_app._check_interrupted() is None

    def test_corrupted_state_file(self, workspace_dir, monkeypatch):
        monkeypatch.setattr(sandbox_app, "WORKSPACE", workspace_dir)
        state_file = os.path.join(workspace_dir, ".exec_state.json")
        monkeypatch.setattr(sandbox_app, "EXEC_STATE_FILE", state_file)

        # Write invalid JSON
        with open(state_file, "w") as f:
            f.write("not json{{{")
        assert sandbox_app._read_exec_state() is None
