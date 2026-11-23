"""VPC/WebRTC entrypoint for the shared streaming Runtime V2 workflow."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "utils"))
import runtime_v2

if __name__ == "__main__":
    runtime_v2.main(webrtc=True)
