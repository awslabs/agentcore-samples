"""pytest configuration — ensures backend/ is on sys.path."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
