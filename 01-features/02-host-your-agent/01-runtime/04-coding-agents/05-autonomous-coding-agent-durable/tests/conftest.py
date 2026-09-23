"""Shared test fixtures and configuration."""
import os
import sys
import tempfile

import pytest

# Ensure project modules are importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "coding-agent"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "orchestrator"))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "sandbox"))


@pytest.fixture
def tmp_base(tmp_path):
    """Provide a temporary base directory for path validation tests."""
    return str(tmp_path)


@pytest.fixture
def ticket_dir(tmp_path):
    """Create a temporary ticket directory structure."""
    base = tmp_path / "mnt" / "shared"
    base.mkdir(parents=True)
    ticket = base / "TICKET-1"
    ticket.mkdir()
    return str(ticket)


@pytest.fixture
def workspace_dir(tmp_path):
    """Create a temporary workspace directory."""
    ws = tmp_path / "mnt" / "workspace"
    ws.mkdir(parents=True)
    return str(ws)
