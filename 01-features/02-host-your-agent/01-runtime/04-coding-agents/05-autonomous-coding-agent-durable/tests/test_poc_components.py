"""Tests for the PoC additions: memory helper, swift toolchain env, hydrate validation."""
import os
import sys
import types
import importlib
from unittest.mock import patch, MagicMock

import pytest

# Mock bedrock_agentcore before importing the sandbox app (not available locally).
_mock_agentcore = types.ModuleType("bedrock_agentcore")
_mock_runtime = types.ModuleType("bedrock_agentcore.runtime")


class _MockApp:
    def entrypoint(self, fn):
        return fn

    def run(self):
        pass


_mock_runtime.BedrockAgentCoreApp = _MockApp
_mock_agentcore.runtime = _mock_runtime
sys.modules.setdefault("bedrock_agentcore", _mock_agentcore)
sys.modules.setdefault("bedrock_agentcore.runtime", _mock_runtime)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sandbox"))


# ---------------------------------------------------------------------------
# shared/memory.py — must never raise; no-ops cleanly without MEMORY_ID
# ---------------------------------------------------------------------------
class TestMemoryHelper:
    def _fresh(self, monkeypatch, memory_id=""):
        monkeypatch.setenv("MEMORY_ID", memory_id)
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        from shared import memory
        importlib.reload(memory)
        return memory

    def test_disabled_without_memory_id(self, monkeypatch):
        mem = self._fresh(monkeypatch, "")
        assert mem.enabled() is False
        assert mem.recall("rainbow", "add theme") == []
        assert mem.remember("rainbow", ["a lesson"]) == 0

    def test_namespace_is_per_repo_and_sanitized(self, monkeypatch):
        mem = self._fresh(monkeypatch, "mem-123")
        assert mem._namespace("rainbow") == "lessons/rainbow"
        assert mem._namespace("../evil") == "lessons/evil"   # stripped
        assert mem._namespace("") == "lessons/shared"

    def test_format_for_prompt_empty_and_nonempty(self, monkeypatch):
        mem = self._fresh(monkeypatch, "")
        assert mem.format_for_prompt([]) == ""
        block = mem.format_for_prompt(["use --enable-test-discovery"])
        assert "lessons_learned" in block and "discovery" in block

    def test_recall_swallows_errors(self, monkeypatch):
        mem = self._fresh(monkeypatch, "mem-123")
        boom = MagicMock()
        boom.retrieve_memory_records.side_effect = RuntimeError("throttled")
        with patch.object(mem, "_mem", return_value=boom):
            assert mem.recall("rainbow", "q") == []   # must not raise

    def test_remember_writes_records(self, monkeypatch):
        mem = self._fresh(monkeypatch, "mem-123")
        client = MagicMock()
        client.batch_create_memory_records.return_value = {
            "successfulRecords": [{"status": "SUCCEEDED"}]
        }
        with patch.object(mem, "_mem", return_value=client):
            n = mem.remember("rainbow", ["lesson one"])
        assert n == 1
        _, kwargs = client.batch_create_memory_records.call_args
        rec = kwargs["records"][0]
        assert rec["namespaces"] == ["lessons/rainbow"]
        assert rec["content"]["text"] == "lesson one"


# ---------------------------------------------------------------------------
# sandbox/app.py — swift toolchain env + hydrate input validation
# ---------------------------------------------------------------------------
class TestSwiftToolchain:
    def _sandbox(self, monkeypatch, lang):
        monkeypatch.setenv("SANDBOX_LANG", lang)
        monkeypatch.setenv("WORKSPACE_PATH", "/tmp/ws")  # nosec B108 — test fixture
        import sandbox.app as app
        importlib.reload(app)
        return app

    def test_swift_sets_spm_build_dir_not_venv(self, monkeypatch):
        app = self._sandbox(monkeypatch, "swift")
        env = app._toolchain_env({"PATH": "/usr/bin"})
        assert env.get("SWIFTPM_BUILD_DIR", "").endswith("spm-build")
        assert "VIRTUAL_ENV" not in env  # no python venv for swift

    def test_python_sets_venv_path(self, monkeypatch):
        app = self._sandbox(monkeypatch, "python")
        env = app._toolchain_env({"PATH": "/usr/bin"})
        assert "VIRTUAL_ENV" in env and "venv/bin" in env["PATH"]

    def test_ensure_venv_noop_for_swift(self, monkeypatch):
        app = self._sandbox(monkeypatch, "swift")
        with patch("subprocess.run") as run:
            app._ensure_venv()
            run.assert_not_called()  # swift never creates a python venv


class TestHydrateValidation:
    def _sandbox(self, monkeypatch):
        monkeypatch.setenv("SANDBOX_LANG", "swift")
        monkeypatch.setenv("BUCKET", "test-bucket")
        import sandbox.app as app
        importlib.reload(app)
        return app

    def test_rejects_non_allowlisted_repo_url(self, monkeypatch, tmp_path):
        app = self._sandbox(monkeypatch)
        for bad in ["file:///etc/passwd", "https://evil.example.com/x/y",
                    "http://github.com/a/b", "https://github.com/only-one-segment"]:
            out = app._hydrate({"repo_url": bad}, str(tmp_path))
            assert "error" in out, bad

    def test_accepts_allowlisted_repo_url_shape(self, monkeypatch, tmp_path):
        # Regex must accept a well-formed github URL (we don't actually clone here — dir is
        # empty so it proceeds past validation; clone failure is a separate concern).
        app = self._sandbox(monkeypatch)
        import re
        assert app._ALLOWED_GIT_HOST.match("https://github.com/onevcat/Rainbow.git")
        assert app._ALLOWED_GIT_HOST.match("https://gitlab.com/group/proj")

    def test_skips_when_dir_not_empty(self, monkeypatch, tmp_path):
        app = self._sandbox(monkeypatch)
        (tmp_path / "existing.txt").write_text("x")
        out = app._hydrate({"repo_url": "https://github.com/onevcat/Rainbow.git"}, str(tmp_path))
        assert out["hydrated"] is False and "already hydrated" in out["reason"]
