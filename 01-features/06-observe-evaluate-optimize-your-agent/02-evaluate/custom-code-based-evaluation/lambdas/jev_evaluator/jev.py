"""Minimal client for the TypeSafe System One API that serves Jev."""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

import boto3

API_URL = os.environ.get("JEV_API_URL", "https://api.typesafe.ai/v1/systemone")
MODEL = os.environ.get("JEV_MODEL", "jev-latest")
SECRET_ARN = os.environ.get("JEV_API_KEY_SECRET_ARN", "")
KEY_TTL_SECONDS = 300
MAX_ATTEMPTS = 4
RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}

_secrets = boto3.client("secretsmanager")
_cached_key: tuple[str, float] | None = None


class JevError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _api_key(refresh: bool = False) -> str:
    global _cached_key
    if not refresh and _cached_key and time.monotonic() - _cached_key[1] < KEY_TTL_SECONDS:
        return _cached_key[0]
    value = _secrets.get_secret_value(SecretId=SECRET_ARN)["SecretString"].strip()
    _cached_key = (value, time.monotonic())
    return value


def _post(body: bytes, api_key: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "agentcore-jev-evaluator/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def ask(
    state: Any,
    questions: dict[str, Any],
    remaining_ms: Callable[[], int],
) -> dict[str, Any]:
    """POST one System One request, retrying throttles within the Lambda deadline."""
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    refreshed_key = False
    for attempt in range(1, MAX_ATTEMPTS + 1):
        budget = remaining_ms() / 1000 - 2
        if budget <= 1:
            raise JevError("JEV_TIMEOUT", "Lambda deadline reached before Jev responded")
        try:
            return _post(body, _api_key(refresh=refreshed_key), timeout=budget)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:500]
            if error.code == 401 and not refreshed_key:
                refreshed_key = True
                continue
            if error.code == 401:
                raise JevError(
                    "JEV_AUTH_FAILED",
                    f"Jev rejected the API key. Store a valid key in the {SECRET_ARN} secret.",
                ) from error
            if error.code not in RETRYABLE_STATUS or attempt == MAX_ATTEMPTS:
                code = "JEV_VALIDATION_FAILED" if error.code == 422 else "JEV_API_ERROR"
                raise JevError(code, f"Jev returned HTTP {error.code}: {detail}") from error
        except (urllib.error.URLError, TimeoutError) as error:
            if attempt == MAX_ATTEMPTS:
                raise JevError("JEV_UNAVAILABLE", f"Could not reach Jev: {error}") from error
        delay = min(8.0, 0.5 * 2 ** (attempt - 1)) * random.uniform(0.5, 1.0)
        if delay >= remaining_ms() / 1000 - 3:
            raise JevError("JEV_TIMEOUT", "Jev stayed throttled until the Lambda deadline")
        time.sleep(delay)
    raise JevError("JEV_UNAVAILABLE", "Jev did not return a result")
