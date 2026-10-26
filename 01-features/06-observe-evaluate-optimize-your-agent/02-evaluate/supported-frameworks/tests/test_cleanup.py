"""Unit tests for the framework cleanup scripts."""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest
from botocore.exceptions import ClientError

_SUPPORTED_FRAMEWORKS_DIR = Path(__file__).parents[1]
_FRAMEWORKS = ("openai-agents", "llamaindex")


def _load_cleanup_module(framework: str) -> ModuleType:
    path = _SUPPORTED_FRAMEWORKS_DIR / framework / "cleanup.py"
    spec = importlib.util.spec_from_file_location(f"{framework}_cleanup", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=_FRAMEWORKS)
def cleanup_module(request: pytest.FixtureRequest) -> ModuleType:
    """Load each framework cleanup script as a module."""
    return _load_cleanup_module(cast(str, request.param))


def _client_error(code: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code, "Message": "test error"},
            "ResponseMetadata": {
                "RequestId": "test-request",
                "HostId": "",
                "HTTPStatusCode": 400,
                "HTTPHeaders": {},
                "RetryAttempts": 0,
            },
        },
        "TestOperation",
    )


def test_collect_evaluation_state_merges_old_and_new_formats(
    cleanup_module: ModuleType,
    tmp_path: Path,
) -> None:
    (tmp_path / "on_demand_results.json").write_text(
        json.dumps(
            {
                "custom_evaluator_ids": {
                    "builtin": "Builtin.Correctness",
                    "custom": "custom-old",
                }
            }
        )
    )
    (tmp_path / "online_eval_config.json").write_text(
        json.dumps(
            {
                "online_evaluation_config_id": "config-old",
                "evaluation_role_name": "role-old",
                "results_log_group": "/aws/results/old",
            }
        )
    )
    (tmp_path / "cleanup_state.json").write_text(
        json.dumps(
            {
                "online_evaluation_config_ids": ["config-old", "config-new"],
                "custom_evaluator_ids": ["custom-old", "custom-new"],
                "evaluation_role_names": ["role-old", "role-new"],
                "results_log_groups": ["/aws/results/old", "/aws/results/new"],
            }
        )
    )

    state = cleanup_module._collect_evaluation_state(tmp_path)

    assert state == {
        "online_config_ids": ["config-new", "config-old"],
        "custom_evaluator_ids": ["custom-new", "custom-old"],
        "evaluation_role_names": ["role-new", "role-old"],
        "results_log_groups": ["/aws/results/new", "/aws/results/old"],
    }


def test_async_delete_waits_until_resource_is_absent(
    cleanup_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: Iterator[dict[str, str] | ClientError] = iter(
        [
            {"status": "ACTIVE"},
            {"status": "DELETING"},
            _client_error("ResourceNotFoundException"),
        ]
    )
    delete_calls: list[bool] = []

    def get_action() -> dict[str, str]:
        response = next(responses)
        if isinstance(response, ClientError):
            raise response
        return response

    def delete_action() -> None:
        delete_calls.append(True)

    monkeypatch.setattr(cleanup_module.time, "sleep", lambda _: None)
    failures: list[str] = []

    deleted = cleanup_module._delete_async_resource(
        "test resource",
        delete_action,
        get_action,
        cleanup_module._flat_status,
        failures,
        poll_interval=0,
        timeout=1,
    )

    assert deleted is True
    assert delete_calls == [True]
    assert failures == []


def test_async_delete_is_idempotent_when_resource_is_absent(cleanup_module: ModuleType) -> None:
    def get_action() -> dict[str, str]:
        raise _client_error("ResourceNotFoundException")

    def delete_action() -> None:
        raise AssertionError("delete must not be called")

    failures: list[str] = []
    deleted = cleanup_module._delete_async_resource(
        "missing resource",
        delete_action,
        get_action,
        cleanup_module._flat_status,
        failures,
        poll_interval=0,
        timeout=0,
    )

    assert deleted is True
    assert failures == []


class _Paginator:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = pages

    def paginate(self, **_: str) -> list[dict[str, Any]]:
        return self._pages


class _IamClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_paginator(self, name: str) -> _Paginator:
        if name == "list_role_policies":
            return _Paginator([{"PolicyNames": ["inline-a", "inline-b"]}])
        return _Paginator([{"AttachedPolicies": [{"PolicyArn": "arn:aws:iam::aws:policy/ReadOnlyAccess"}]}])

    def delete_role_policy(self, *, RoleName: str, PolicyName: str) -> None:
        self.calls.append(("delete-inline", PolicyName))

    def detach_role_policy(self, *, RoleName: str, PolicyArn: str) -> None:
        self.calls.append(("detach", PolicyArn))

    def delete_role(self, *, RoleName: str) -> None:
        self.calls.append(("delete-role", RoleName))


def test_delete_iam_role_removes_policies_first(cleanup_module: ModuleType) -> None:
    iam = _IamClient()

    cleanup_module._delete_iam_role(iam, "sample-role")

    assert iam.calls == [
        ("delete-inline", "inline-a"),
        ("delete-inline", "inline-b"),
        ("detach", "arn:aws:iam::aws:policy/ReadOnlyAccess"),
        ("delete-role", "sample-role"),
    ]


def test_main_fails_before_creating_session_when_config_is_missing(
    cleanup_module: ModuleType,
    tmp_path: Path,
) -> None:
    exit_code = cleanup_module.main(
        [
            "--config",
            str(tmp_path / "missing.json"),
            "--results-dir",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
