"""In-process x402 seller used by the no-side-effect local E2E."""

from __future__ import annotations

import base64
import io
import json
from contextlib import AbstractContextManager
from dataclasses import dataclass
from email.message import Message
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse


DEFAULT_PAY_TO = "0x1111111111111111111111111111111111111111"
DEFAULT_NETWORK = "eip155:84532"
DEFAULT_ASSET = "0x2222222222222222222222222222222222222222"
DEFAULT_AMOUNT = 1000


def _payment_required(pay_to: str, amount: int) -> str:
    payload = {
        "x402Version": 2,
        "resource": {"url": "/premium"},
        "accepts": [
            {
                "scheme": "exact",
                "network": DEFAULT_NETWORK,
                "asset": DEFAULT_ASSET,
                "amount": str(amount),
                "payTo": pay_to,
            }
        ],
    }
    return base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


@dataclass
class InMemoryResponse(AbstractContextManager["InMemoryResponse"]):
    """Minimal response compatible with the buyer's urllib transport."""

    status: int
    body: bytes

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "InMemoryResponse":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback


@dataclass
class InMemorySeller(AbstractContextManager["InMemorySeller"]):
    """Emulates seller challenge and retry behavior without opening a socket."""

    retries: int = 0
    base_url: str = "https://seller.local"

    def __enter__(self) -> "InMemorySeller":
        return self

    def open(self, request: Any, timeout: int = 20) -> InMemoryResponse:
        """Return a 402, then a 200 only after the simulated payment header."""

        del timeout
        url = request.full_url
        parsed = urlparse(url)
        if parsed.path != "/premium":
            return InMemoryResponse(404, b'{"error":"not found"}')

        if request.headers.get("X-payment") == "simulated-proof":
            self.retries += 1
            return InMemoryResponse(
                200,
                json.dumps(
                    {
                        "content": "Premium content delivered",
                        "payment": "simulated",
                        "settlement": "not-verified",
                    }
                ).encode("utf-8"),
            )

        query = parse_qs(parsed.query)
        pay_to = query.get("pay_to", [DEFAULT_PAY_TO])[0]
        amount = int(query.get("amount", [str(DEFAULT_AMOUNT)])[0])
        headers = Message()
        headers["Payment-Required"] = _payment_required(pay_to, amount)
        raise HTTPError(
            url,
            402,
            "Payment Required",
            headers,
            io.BytesIO(b'{"error":"payment required"}'),
        )

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback
