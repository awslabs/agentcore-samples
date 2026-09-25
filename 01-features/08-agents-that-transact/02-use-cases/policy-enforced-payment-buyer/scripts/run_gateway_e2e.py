"""Exercise an AgentCore Policy Gateway without creating a payment."""

from __future__ import annotations

import os
import sys
import uuid
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError, URLError


SAMPLE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SAMPLE_ROOT))

from buyer.core import (  # noqa: E402
    PaymentRequirementError,
    PolicyDenied,
    fetch_payment_requirement,
)
from buyer.gateway import GatewayPolicyAuthorizer, GatewayPolicyContext  # noqa: E402


REQUIRED_ENVIRONMENT = (
    "AWS_REGION",
    "POLICY_GATEWAY_URL",
    "POLICY_TARGET_NAME",
    "X402_RESOURCE_URL",
)


def _required_environment() -> dict[str, str]:
    values = {name: os.environ.get(name, "").strip() for name in REQUIRED_ENVIRONMENT}
    missing = [name for name, value in values.items() if not value]
    if missing:
        names = ", ".join(missing)
        raise ValueError(
            f"Missing required environment variable(s): {names}. "
            "See README.md, Gateway E2E (no payment)."
        )
    return values


def _redact(value: str) -> str:
    if len(value) <= 12:
        return "redacted"
    return f"{value[:6]}...{value[-4:]}"


def _changed_recipient(pay_to: str) -> str:
    """Return a same-shape recipient string that differs from the seller input."""

    if not pay_to:
        raise ValueError("Seller requirement did not contain a recipient")
    replacement = "0" if pay_to[-1] != "0" else "1"
    return f"{pay_to[:-1]}{replacement}"


def main() -> None:
    values = _required_environment()
    requirement = fetch_payment_requirement(values["X402_RESOURCE_URL"])
    authorizer = GatewayPolicyAuthorizer(
        GatewayPolicyContext(
            gateway_url=values["POLICY_GATEWAY_URL"],
            target_name=values["POLICY_TARGET_NAME"],
            policy_session_id=os.environ.get("POLICY_SESSION_ID", str(uuid.uuid4())),
            region=values["AWS_REGION"],
        )
    )

    authorizer.authorize(requirement)
    print(
        "happy_path=AUTHORIZED "
        f"amount={requirement.amount} "
        f"recipient={_redact(requirement.pay_to)}"
    )

    changed_recipient = replace(
        requirement,
        pay_to=_changed_recipient(requirement.pay_to),
    )
    try:
        authorizer.authorize(changed_recipient)
    except PolicyDenied:
        print("failure_path=DENIED scenario=changed_recipient")
    else:
        raise RuntimeError(
            "Failure path was authorized. The Policy must deny a changed recipient."
        )

    print("payment_processing=NOT_RUN settlement=NOT_APPLICABLE")


def run() -> None:
    """Run the Gateway check and present expected environment failures cleanly."""

    try:
        main()
    except (
        HTTPError,
        URLError,
        PaymentRequirementError,
        PolicyDenied,
        RuntimeError,
        ValueError,
    ) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    run()
