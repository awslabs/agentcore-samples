"""Shared utilities for the AgentCore Policy sample."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import boto3
import yaml


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    path = Path(config_path)
    if not path.exists():
        print(f"Error: config file not found: {config_path}")
        sys.exit(1)
    with open(path) as f:
        return yaml.safe_load(f)


def get_account_id(region: str = "us-west-2") -> str:
    """Get the current AWS account ID via STS."""
    sts = boto3.client("sts", region_name=region)
    return sts.get_caller_identity()["Account"]


def resolve_gateway_arn(region: str, gateway_id: str) -> str:
    """Build the full Gateway ARN from region and gateway ID."""
    account_id = get_account_id(region)
    return f"arn:aws:bedrock-agentcore:{region}:{account_id}:gateway/{gateway_id}"


def save_state(state: dict, path: str = ".state.json") -> None:
    """Save deployment state to a JSON file."""
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def load_state(path: str = ".state.json") -> dict:
    """Load deployment state from a JSON file."""
    p = Path(path)
    if not p.exists():
        return {}
    with open(p) as f:
        return json.load(f)


def green(text: str) -> str:
    if sys.stdout.isatty():
        return f"\033[32m{text}\033[0m"
    return text


def red(text: str) -> str:
    if sys.stdout.isatty():
        return f"\033[31m{text}\033[0m"
    return text


def bold(text: str) -> str:
    if sys.stdout.isatty():
        return f"\033[1m{text}\033[0m"
    return text
