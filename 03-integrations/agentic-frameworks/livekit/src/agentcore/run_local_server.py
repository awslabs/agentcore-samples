"""
Run AgentCore as local server for testing
"""

import os
import sys

# Add current directory to path
sys.path.insert(0, os.path.dirname(__file__))

from nova_realtime_agentcore import app

if __name__ == "__main__":
    print("=" * 60)
    print("Starting AgentCore Local Server")
    print("Server will run on http://localhost:8050")
    print("=" * 60)
    app.run(port=8050)
