"""Tests for coding-agent/path_security.py — agent-side path enforcement."""
import os
import sys
import pytest

# path_security uses env var and module-level state, so we need to set up carefully
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "coding-agent"))
# Also need shared module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "coding-agent", "shared_libs"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class TestPathSecurityConfigure:
    """Test path_security.configure() ticket directory setup."""

    def test_valid_ticket_id(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("MOUNT_PATH", str(tmp_path))
        # Force reimport to pick up env change
        import path_security
        importlib.reload(path_security)

        result = path_security.configure("TICKET-42")
        expected = os.path.join(str(tmp_path), "TICKET-42")
        assert result == os.path.realpath(expected)
        assert os.path.isdir(result)

    def test_traversal_rejected(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("MOUNT_PATH", str(tmp_path))
        import path_security
        importlib.reload(path_security)

        with pytest.raises(PermissionError, match="Access denied"):
            path_security.configure("../etc")

    def test_empty_ticket_id_rejected(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("MOUNT_PATH", str(tmp_path))
        import path_security
        importlib.reload(path_security)

        with pytest.raises(PermissionError):
            path_security.configure("")

    def test_dots_in_ticket_id_rejected(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("MOUNT_PATH", str(tmp_path))
        import path_security
        importlib.reload(path_security)

        with pytest.raises(PermissionError):
            path_security.configure("ticket.with.dots")


class TestPathSecurityCheckPath:
    """Test path_security.check_path() enforcement."""

    def test_relative_path_allowed(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("MOUNT_PATH", str(tmp_path))
        import path_security
        importlib.reload(path_security)

        ticket_dir = path_security.configure("MYTICKET")
        result = path_security.check_path("subdir/file.py")
        assert result.startswith(ticket_dir)

    def test_traversal_blocked(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("MOUNT_PATH", str(tmp_path))
        import path_security
        importlib.reload(path_security)

        path_security.configure("MYTICKET")
        with pytest.raises(PermissionError, match="Access denied"):
            path_security.check_path("../../etc/passwd")

    def test_absolute_outside_blocked(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("MOUNT_PATH", str(tmp_path))
        import path_security
        importlib.reload(path_security)

        path_security.configure("MYTICKET")
        with pytest.raises(PermissionError, match="Access denied"):
            path_security.check_path("/etc/passwd")

    def test_check_before_configure_raises(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("MOUNT_PATH", str(tmp_path))
        import path_security
        importlib.reload(path_security)
        # Clear any prior state
        path_security._allowed_paths.clear()

        with pytest.raises(PermissionError, match="not configured"):
            path_security.check_path("anything")

    def test_symlink_escape_blocked(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("MOUNT_PATH", str(tmp_path))
        import path_security
        importlib.reload(path_security)

        ticket_dir = path_security.configure("MYTICKET")
        # Create a symlink inside ticket dir pointing outside
        link = os.path.join(ticket_dir, "escape")
        os.symlink("/etc", link)
        with pytest.raises(PermissionError, match="Access denied"):
            path_security.check_path("escape/passwd")
