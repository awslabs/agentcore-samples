from __future__ import annotations

import time
from typing import Any

from x402_secure import X402SecureClient, is_payment_required
from x402_service_client import X402ServiceClient
from x402_service_registry import (
    DEFAULT_SERVICE_ID,
    default_x402_service_registry,
    resolve_x402_trust_cache_ttl_seconds,
    resolve_x402_trust_fail_closed,
    resolve_x402_trust_threshold,
    validate_service_operation,
)
from x402_trust_state import cached_request_trust_result, store_request_trust_result


class TrustedX402ServiceGateway:
    def __init__(
        self,
        *,
        services: dict[str, dict[str, Any]] | None = None,
        trust_client: X402SecureClient | None = None,
        trust_threshold: int | None = None,
        cache_ttl_seconds: int | None = None,
        fail_closed: bool | None = None,
    ) -> None:
        self.services = services or default_x402_service_registry()
        self.trust_client = trust_client or X402SecureClient()
        self.trust_threshold = trust_threshold if trust_threshold is not None else resolve_x402_trust_threshold()
        self.cache_ttl_seconds = (
            cache_ttl_seconds if cache_ttl_seconds is not None else resolve_x402_trust_cache_ttl_seconds()
        )
        self.fail_closed = fail_closed if fail_closed is not None else resolve_x402_trust_fail_closed()

    def _service(self, service_id: str) -> dict[str, Any]:
        service = self.services.get(service_id)
        if service is None:
            raise ValueError(f"Unsupported x402 service: {service_id}")
        return service

    def _service_url(self, service_id: str) -> str:
        return str(self._service(service_id)["base_url"]).rstrip("/")

    def _service_client(self, service_id: str) -> X402ServiceClient:
        service = self._service(service_id)
        client = service.get("client")
        if client is None:
            client = X402ServiceClient(
                service_id=service_id,
                base_url=service["base_url"],
                operations=service["operations"],
            )
            service["client"] = client
        return client

    def _cached_trust_result(self, endpoint_url: str) -> dict[str, Any]:
        return cached_request_trust_result(endpoint_url, time.time())

    def check_x402_endpoint_trust(
        self,
        service_id: str | None = None,
        url: str | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | str:
        endpoint_url = (url or self._service_url(service_id or DEFAULT_SERVICE_ID)).rstrip("/")
        if headers is None:
            try:
                return self._cached_trust_result(endpoint_url)
            except KeyError:
                pass

        result = self.trust_client.score_endpoint(endpoint_url, headers=headers)
        if is_payment_required(result):
            return result

        expires_at = time.time() + self.cache_ttl_seconds
        store_request_trust_result(endpoint_url, expires_at, result)
        return result

    def _blocked_result(self, reason: str, trust_result: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "status": "blocked",
            "reason": reason,
            "trust": trust_result or {},
        }

    def call_trusted_x402_service(
        self,
        service_id: str,
        operation: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any] | str:
        body = payload or {}
        service = self._service(service_id)
        validate_service_operation(service_id, operation, body, service["operations"])
        endpoint_url = self._service_url(service_id)

        try:
            trust_result = self._cached_trust_result(endpoint_url)
        except KeyError as exc:
            if self.fail_closed:
                return self._blocked_result(f"x402-secure trust check is required before target payment: {exc}")
            trust_result = {"status": "unavailable", "error": str(exc)}

        score = int(trust_result.get("overall_score", 0) or 0)
        risk_level = trust_result.get("risk_level", "unknown")
        is_scam = bool(trust_result.get("is_scam", False))
        if is_scam:
            return self._blocked_result(
                f"Endpoint flagged as scam by x402-secure; risk level: {risk_level}",
                trust_result,
            )
        if score < self.trust_threshold:
            return self._blocked_result(
                f"Trust score {score}/100 is below threshold {self.trust_threshold}; risk level: {risk_level}",
                trust_result,
            )

        result = self._service_client(service_id).call_operation(
            operation,
            body,
            headers=headers,
        )
        if isinstance(result, dict):
            result.setdefault("trust", trust_result)
        return result
