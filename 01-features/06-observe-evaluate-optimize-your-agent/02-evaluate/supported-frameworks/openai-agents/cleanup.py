"""Delete AWS resources created by the OpenAI Agents sample.

The script reads agent_config.json plus optional files in results/ and removes
the sample-specific evaluation, runtime, memory, logging, S3, and IAM resources.

Usage:
    uv run --frozen --with-requirements requirements.txt python cleanup.py
        [--region REGION] [--profile PROFILE]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Any, TypedDict

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

_SCRIPT_DIR = Path(__file__).parent
_DEFAULT_CONFIG = _SCRIPT_DIR / "agent_config.json"
_DEFAULT_RESULTS_DIR = _SCRIPT_DIR / "results"
_NOT_FOUND_CODES = {
    "404",
    "NoSuchBucket",
    "NoSuchEntity",
    "NotFoundException",
    "ResourceNotFoundException",
}
_Action = Callable[[], object]
_GetAction = Callable[[], dict[str, Any]]
_StatusGetter = Callable[[dict[str, Any]], str | None]


class EvaluationState(TypedDict):
    """Resource identifiers written by evaluate.py."""

    online_config_ids: list[str]
    custom_evaluator_ids: list[str]
    evaluation_role_names: list[str]
    results_log_groups: list[str]


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object from path.

    Args:
        path: JSON file to read.

    Returns:
        The decoded object, or an empty dictionary when the file is absent.

    Raises:
        ValueError: If the JSON root is not an object.
    """
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def _collect_evaluation_state(results_dir: Path) -> EvaluationState:
    """Merge cleanup identifiers from evaluation result files."""
    online_config_ids: set[str] = set()
    evaluator_ids: set[str] = set()
    evaluation_role_names: set[str] = set()
    results_log_groups: set[str] = set()

    for filename in ("on_demand_results.json", "online_eval_config.json", "cleanup_state.json"):
        data = _read_json(results_dir / filename)
        _collect_strings(data.get("online_evaluation_config_id"), online_config_ids)
        _collect_strings(data.get("online_evaluation_config_ids"), online_config_ids)
        _collect_strings(data.get("config_id"), online_config_ids)
        _collect_strings(data.get("custom_evaluator_ids"), evaluator_ids, exclude_builtins=True)
        _collect_strings(data.get("evaluation_role_name"), evaluation_role_names)
        _collect_strings(data.get("evaluation_role_names"), evaluation_role_names)
        _collect_strings(data.get("results_log_group"), results_log_groups)
        _collect_strings(data.get("results_log_groups"), results_log_groups)

    return {
        "online_config_ids": sorted(online_config_ids),
        "custom_evaluator_ids": sorted(evaluator_ids),
        "evaluation_role_names": sorted(evaluation_role_names),
        "results_log_groups": sorted(results_log_groups),
    }


def _collect_strings(value: object, destination: set[str], *, exclude_builtins: bool = False) -> None:
    """Collect strings from a scalar, list, or dictionary value."""
    candidates: Sequence[object]
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, list):
        candidates = value
    elif isinstance(value, dict):
        candidates = list(value.values())
    else:
        return

    destination.update(
        candidate
        for candidate in candidates
        if isinstance(candidate, str) and candidate and (not exclude_builtins or not candidate.startswith("Builtin."))
    )


def _role_name_from_arn(role_arn: str) -> str | None:
    """Return the IAM role name from an ARN."""
    marker = ":role/"
    if marker not in role_arn:
        return None
    return role_arn.split(marker, 1)[1].rsplit("/", 1)[-1]


def _error_code(error: ClientError) -> str:
    """Return an AWS service error code."""
    return str(error.response.get("Error", {}).get("Code", "Unknown"))


def _run_step(label: str, action: _Action, failures: list[str]) -> bool:
    """Run one cleanup action while treating missing resources as success."""
    print(f"Deleting {label} ...")
    try:
        action()
        print("  [ok]")
        return True
    except ClientError as error:
        code = _error_code(error)
        if code in _NOT_FOUND_CODES:
            print("  [skip] already absent")
            return True
        failures.append(f"{label}: {code}: {error}")
        print(f"  [failed] {code}: {error}")
        return False
    except (BotoCoreError, OSError, RuntimeError, TimeoutError) as error:
        failures.append(f"{label}: {error}")
        print(f"  [failed] {error}")
        return False


def _flat_status(response: dict[str, Any]) -> str | None:
    """Read a top-level AgentCore resource status."""
    status = response.get("status")
    return status if isinstance(status, str) else None


def _memory_status(response: dict[str, Any]) -> str | None:
    """Read an AgentCore Memory status."""
    memory = response.get("memory")
    if not isinstance(memory, dict):
        return None
    status = memory.get("status")
    return status if isinstance(status, str) else None


def _delete_async_resource(
    label: str,
    delete_action: _Action,
    get_action: _GetAction,
    get_status: _StatusGetter,
    failures: list[str],
    *,
    poll_interval: float,
    timeout: float,
) -> bool:
    """Delete an AgentCore resource and wait until it no longer exists."""
    print(f"Deleting {label} ...")
    try:
        try:
            response = get_action()
        except ClientError as error:
            if _error_code(error) in _NOT_FOUND_CODES:
                print("  [skip] already absent")
                return True
            raise

        if get_status(response) != "DELETING":
            try:
                delete_action()
            except ClientError as error:
                code = _error_code(error)
                if code in _NOT_FOUND_CODES:
                    print("  [ok]")
                    return True
                if code != "ConflictException":
                    raise
                response = get_action()
                if get_status(response) != "DELETING":
                    raise RuntimeError(
                        f"delete request conflicted; current status is {get_status(response) or 'unknown'}"
                    ) from error

        deadline = time.monotonic() + timeout
        while True:
            try:
                response = get_action()
            except ClientError as error:
                if _error_code(error) in _NOT_FOUND_CODES:
                    print("  [ok]")
                    return True
                raise

            status = get_status(response)
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"deletion did not finish within {timeout:g} seconds; current status is {status or 'unknown'}"
                )
            time.sleep(poll_interval)
    except ClientError as error:
        code = _error_code(error)
        failures.append(f"{label}: {code}: {error}")
        print(f"  [failed] {code}: {error}")
        return False
    except (BotoCoreError, RuntimeError, TimeoutError) as error:
        failures.append(f"{label}: {error}")
        print(f"  [failed] {error}")
        return False


def _delete_iam_role(iam: Any, role_name: str) -> None:
    """Delete all policies from an IAM role, then delete the role."""
    inline_pages = iam.get_paginator("list_role_policies").paginate(RoleName=role_name)
    for page in inline_pages:
        for policy_name in page.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)

    attached_pages = iam.get_paginator("list_attached_role_policies").paginate(RoleName=role_name)
    for page in attached_pages:
        for policy in page.get("AttachedPolicies", []):
            policy_arn = policy.get("PolicyArn")
            if policy_arn:
                iam.detach_role_policy(RoleName=role_name, PolicyArn=policy_arn)

    iam.delete_role(RoleName=role_name)


def _delete_empty_bucket(s3: Any, bucket: str) -> None:
    """Delete the code bucket only when no other sample artifacts remain."""
    response = s3.list_objects_v2(Bucket=bucket, MaxKeys=1)
    if response.get("KeyCount", 0):
        print("  Bucket contains other objects and will be retained.")
        return
    s3.delete_bucket(Bucket=bucket)


def _require_string(config: dict[str, Any], key: str) -> str:
    """Return a required non-empty string from configuration."""
    value = config.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing required '{key}' in agent config")
    return value


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Clean up the OpenAI Agents evaluation sample")
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG, help="Path to agent_config.json")
    parser.add_argument("--results-dir", type=Path, default=_DEFAULT_RESULTS_DIR, help="Evaluation results directory")
    parser.add_argument("--region", default=None, help="Override the region saved by deploy.py")
    parser.add_argument("--profile", default="default", help="AWS profile (default: default)")
    parser.add_argument(
        "--poll-interval", type=_non_negative_float, default=5.0, help="Seconds between deletion checks"
    )
    parser.add_argument(
        "--timeout",
        type=_non_negative_float,
        default=300.0,
        help="Seconds to wait for each asynchronous deletion",
    )
    return parser.parse_args(argv)


def _non_negative_float(value: str) -> float:
    """Parse a non-negative command-line number."""
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    """Run cleanup and return a process exit code."""
    args = _parse_args(argv)
    try:
        config = _read_json(args.config)
        if not config:
            raise ValueError(f"Agent config not found: {args.config}. Run deploy.py first.")

        region = args.region or _require_string(config, "region")
        agent_id = _require_string(config, "agent_id")
        evaluation = _collect_evaluation_state(args.results_dir)

        session = boto3.Session(profile_name=args.profile, region_name=region)
        client_config = Config(retries={"mode": "adaptive", "total_max_attempts": 5})
        identity = session.client("sts", config=client_config).get_caller_identity()
        control = session.client("bedrock-agentcore-control", config=client_config)
        logs = session.client("logs", config=client_config)
        iam = session.client("iam", config=client_config)
        s3 = session.client("s3", config=client_config)

        print(f"AWS account: {identity['Account']}")
        print(f"Region: {region}")
        print(f"Runtime: {agent_id}")
        print()

        failures: list[str] = []

        online_configs_deleted = True
        for online_config_id in evaluation["online_config_ids"]:
            deleted = _delete_async_resource(
                f"online evaluation config {online_config_id}",
                lambda: control.delete_online_evaluation_config(
                    onlineEvaluationConfigId=online_config_id,
                ),
                lambda: control.get_online_evaluation_config(
                    onlineEvaluationConfigId=online_config_id,
                ),
                _flat_status,
                failures,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
            )
            online_configs_deleted = deleted and online_configs_deleted

        if online_configs_deleted:
            for evaluator_id in evaluation["custom_evaluator_ids"]:
                _delete_async_resource(
                    f"custom evaluator {evaluator_id}",
                    partial(control.delete_evaluator, evaluatorId=evaluator_id),
                    partial(control.get_evaluator, evaluatorId=evaluator_id),
                    _flat_status,
                    failures,
                    poll_interval=args.poll_interval,
                    timeout=args.timeout,
                )
        else:
            print("Skipping evaluators and dependent resources because an online evaluation config remains.")

        runtime_deleted = False
        if online_configs_deleted:
            runtime_deleted = _delete_async_resource(
                f"AgentCore Runtime {agent_id}",
                lambda: control.delete_agent_runtime(agentRuntimeId=agent_id),
                lambda: control.get_agent_runtime(agentRuntimeId=agent_id),
                _flat_status,
                failures,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
            )
        else:
            print("Skipping AgentCore Runtime because an online evaluation config remains.")

        memory_id = config.get("memory_id")
        if runtime_deleted and isinstance(memory_id, str) and memory_id:
            _delete_async_resource(
                f"AgentCore Memory {memory_id}",
                lambda: control.delete_memory(memoryId=memory_id),
                lambda: control.get_memory(memoryId=memory_id),
                _memory_status,
                failures,
                poll_interval=args.poll_interval,
                timeout=args.timeout,
            )

        runtime_log_group = config.get("cw_log_group")
        if runtime_deleted and isinstance(runtime_log_group, str) and runtime_log_group:
            _run_step(
                f"runtime log group {runtime_log_group}",
                lambda: logs.delete_log_group(logGroupName=runtime_log_group),
                failures,
            )

        if online_configs_deleted:
            for results_log_group in evaluation["results_log_groups"]:
                _run_step(
                    f"evaluation results log group {results_log_group}",
                    partial(logs.delete_log_group, logGroupName=results_log_group),
                    failures,
                )

        s3_bucket = config.get("s3_bucket")
        s3_key = config.get("s3_key")
        if runtime_deleted and isinstance(s3_bucket, str) and s3_bucket and isinstance(s3_key, str) and s3_key:
            _run_step(
                f"S3 object s3://{s3_bucket}/{s3_key}",
                lambda: s3.delete_object(Bucket=s3_bucket, Key=s3_key),
                failures,
            )
            _run_step(
                f"empty S3 bucket {s3_bucket}",
                lambda: _delete_empty_bucket(s3, s3_bucket),
                failures,
            )

        if online_configs_deleted:
            for evaluation_role_name in evaluation["evaluation_role_names"]:
                _run_step(
                    f"evaluation IAM role {evaluation_role_name}",
                    partial(_delete_iam_role, iam, evaluation_role_name),
                    failures,
                )

        role_arn = config.get("role_arn")
        runtime_role_name = _role_name_from_arn(role_arn) if isinstance(role_arn, str) else None
        if runtime_deleted and runtime_role_name:
            _run_step(
                f"runtime IAM role {runtime_role_name}",
                lambda: _delete_iam_role(iam, runtime_role_name),
                failures,
            )

        if failures:
            print("\nCleanup finished with failures:")
            for failure in failures:
                print(f"  - {failure}")
            print("Re-run cleanup.py after resolving the reported errors.")
            return 1

        print("\nCleanup complete.")
        return 0
    except (BotoCoreError, ClientError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Cleanup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
