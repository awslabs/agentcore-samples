import importlib
import sys
from pathlib import Path

import pytest

SAMPLE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE_DIR))


@pytest.fixture(scope="session")
def load_script():
    """Import a numbered workflow script such as 04_merge_reviews.py as a module."""
    return lambda name: importlib.import_module(name)
