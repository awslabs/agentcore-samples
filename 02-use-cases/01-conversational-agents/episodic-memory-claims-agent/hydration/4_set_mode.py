"""Set the decision mode (human or auto) via SSM.

Usage:
    python hydration/4_set_mode.py human
    python hydration/4_set_mode.py auto
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent", "src"))

import boto3
from memory.config import load_config


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("human", "auto"):
        print("Usage: python hydration/4_set_mode.py <human|auto>")
        sys.exit(1)

    mode = sys.argv[1]
    config = load_config()
    region = config["region"]

    ssm = boto3.client("ssm", region_name=region)
    ssm.put_parameter(
        Name="/insurance-claims-demo/decision_mode",
        Value=mode,
        Type="String",
        Overwrite=True,
    )
    print(f"Mode set to: {mode}")


if __name__ == "__main__":
    main()
