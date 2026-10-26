from __future__ import annotations

from typing import Any

import requests
from x402_secure import payment_required_result
from x402_service_registry import validate_service_operation


class X402ServiceClient:
    def __init__(
        self,
        *,
        service_id: str,
        base_url: str,
        operations: dict[str, dict[str, Any]],
        session: requests.Session | None = None,
        timeout: int = 30,
    ) -> None:
        self.service_id = service_id
        self.base_url = base_url.rstrip("/")
        self.operations = operations
        self.session = session or requests.Session()
        self.timeout = timeout

    def call_operation(
        self,
        operation: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | str:
        body = payload or {}
        validate_service_operation(self.service_id, operation, body, self.operations)

        response = self.session.post(
            f"{self.base_url}/{operation}",
            json=body,
            headers=headers,
            timeout=self.timeout,
        )
        if response.status_code == 402:
            return payment_required_result(response)
        if response.status_code >= 400:
            raise RuntimeError(
                f"x402 service call failed for {self.service_id}.{operation}: "
                f"{response.status_code} {response.text[:500]}"
            )
        return response.json()
