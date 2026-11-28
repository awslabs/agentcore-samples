#!/usr/bin/env python3
"""Create the sample's dev target without overwriting an existing deployment."""

import json
import os
import re
from pathlib import Path


def configure(project: Path, account: str, region: str, target_name: str = "dev") -> None:
    if not re.fullmatch(r"[0-9]{12}", account):
        raise ValueError("CDK_DEFAULT_ACCOUNT must be a 12-digit account ID")
    if not re.fullmatch(r"[a-z]{2}(?:-[a-z]+)+-[0-9]+", region):
        raise ValueError("AWS_REGION is not a region name")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", target_name):
        raise ValueError("Invalid target name")
    if (project / "agentcore/.cli").exists() or (project / ".sample-deployment.json").exists():
        raise ValueError("State already exists; restore original configuration, do not reset it")
    target = {"name": target_name, "account": account, "region": region}
    file = project / "agentcore/aws-targets.json"
    if file.exists():
        targets = json.loads(file.read_text())
        if targets != [target]:
            raise ValueError(
                "Existing aws-targets.json does not match dev/account/region; review it and deployed state manually"
            )
        return
    state_file = project / "agentcore/.cli/deployed-state.json"
    if state_file.exists() and json.loads(state_file.read_text()).get("targets"):
        raise ValueError(
            "Deployed state exists without aws-targets.json; restore the original targets, do not reset state"
        )
    with file.open("x") as stream:
        stream.write(json.dumps([target], indent=2) + "\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args()
    configure(Path(__file__).resolve().parents[1], args.account, args.region, args.target)
