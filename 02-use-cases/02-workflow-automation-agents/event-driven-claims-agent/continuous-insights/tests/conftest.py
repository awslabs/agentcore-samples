"""Make the scripts in the sibling scripts/ folder importable from the tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
