#!/usr/bin/env python3
"""Configure a fresh CLI v1 target. Infrastructure has ONE owner: the custom CDK app.

This replaces the old SDK deployer, which created a second, incomplete set of
Runtime/Memory/Gateway resources. No AWS clients or calls are made here.
"""

import argparse
import json
import re
from pathlib import Path


def configure(root: Path, project_name: str, account: str, region: str, fresh: bool):
    if not fresh:
        raise ValueError("Explicit --fresh acknowledgement is required; existing stack adoption is unsupported")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,22}", project_name):
        raise ValueError("Project name must be 1-23 alphanumeric characters, starting with a letter")
    if not re.fullmatch(r"\d{12}", account) or not re.fullmatch(r"[a-z]{2}-[a-z]+-\d", region):
        raise ValueError("An explicit 12-digit account and AWS region are required")
    config_dir = root / "agentcore"
    for path in [
        root / "deploy_outputs.json",
        root / ".bedrock_agentcore.yaml",
        config_dir / ".bedrock_agentcore.yaml",
        config_dir / ".cli" / "deployed-state.json",
        config_dir / "deployed-state.json",
    ]:
        if path.exists():
            raise ValueError(f"Existing state {path}: do not delete it to bypass this check")
    targets_path = config_dir / "aws-targets.json"
    if json.loads(targets_path.read_text()) != []:
        raise ValueError("Targets already configured or legacy object shape found; use a separate fresh checkout")
    config_path = config_dir / "agentcore.json"
    spec = json.loads(config_path.read_text())
    if spec.get("version") != 2:
        raise ValueError("Expected migrated schema version 2; refusing a blind version bump")
    spec["name"] = project_name
    config_path.write_text(json.dumps(spec, indent=2) + "\n")
    targets = [{"name": "dev", "account": account, "region": region}]
    targets_path.write_text(json.dumps(targets, indent=2) + "\n")
    (config_dir / "fresh-port.json").write_text(
        json.dumps(
            {
                "version": 1,
                "projectName": project_name,
                "targets": targets,
            },
            indent=2,
        )
        + "\n"
    )
    return f"AgentCore-{project_name}-dev"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--account", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()
    try:
        stack = configure(Path(__file__).resolve().parent, args.project_name, args.account, args.region, args.fresh)
    except ValueError as error:
        parser.error(str(error))
    print(f"Configured {stack}. Before deployment independently confirm this remote name is unused.")
    print(f"Set DATA_ANALYST_FRESH_STACK={stack}; follow MIGRATION.md for Cognito parameters and CDK ownership.")


if __name__ == "__main__":
    main()
