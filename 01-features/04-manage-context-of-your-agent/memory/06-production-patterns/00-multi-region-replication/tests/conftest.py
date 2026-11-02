import sys
from pathlib import Path

# Make the sample's agentcore_replication package importable when pytest runs from the sample directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
