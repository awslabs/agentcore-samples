"""
AgentCore payments + AWS Marketplace (AWS Billing) helpers.

Three small pieces that every buyer agent needs:

  AgentCorePaymentsClient  boto3 wrapper for the AgentCore payments calls the sample uses.
  x402 helpers             Parse a seller's 402 challenge and build the x402 v2
                           PAYMENT-SIGNATURE header the seller expects on the retry.
  MarketplacePayer         The full loop: call -> 402 -> ProcessPayment -> retry -> content.

Nothing in here holds keys or wallets. The charge lands on the AWS bill of the account that
owns the payment manager, as an AWS Marketplace line item.
"""

from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

import boto3
import requests
from botocore.credentials import DeferredRefreshableCredentials
from botocore.exceptions import ClientError
from botocore.session import get_session

log = logging.getLogger(__name__)

SLR_SERVICE_NAME = "agentcore-payments.marketplace.amazonaws.com"
SLR_ROLE_NAME = "AWSServiceRoleForMarketplaceAgenticCommerce"
# AWS Billing payments: ProcessPayment takes this payment type and this fixed,
# service-owned instrument ID. There is no per-user AWS Billing instrument to create.
AWS_BILLING_PAYMENT_TYPE = "AWS_BILLING_X402"
AWS_BILLING_INSTRUMENT_ID = "aws.agentcorepayments.awsbilling.v1"

# Two roles keep budget-setting and spending apart (created by setup_aws_billing.py):
#   SESSION_ROLE  creates payment sessions with a spend limit. Explicitly denied ProcessPayment.
#   AGENT_ROLE    what the agent runs as: ProcessPayment, GetPaymentSession, the model and the
#                 gateway. Explicitly denied creating sessions, so it can't raise its own budget.
SESSION_ROLE_NAME = "MarketplaceBuyerSessionRole"
AGENT_ROLE_NAME = "MarketplaceBuyerAgentRole"


def assume_role_session(
    role_arn: str, session_name: str, region: str, base: boto3.Session | None = None
) -> boto3.Session:
    """A boto3 session for `role_arn` whose credentials refresh themselves."""
    sts = (base or boto3.Session(region_name=region)).client("sts", region_name=region)

    def fetch() -> dict:
        for attempt in range(6):
            try:
                c = sts.assume_role(RoleArn=role_arn, RoleSessionName=session_name)["Credentials"]
                return {
                    "access_key": c["AccessKeyId"],
                    "secret_key": c["SecretAccessKey"],
                    "token": c["SessionToken"],
                    "expiry_time": c["Expiration"].isoformat(),
                }
            except ClientError as e:
                if e.response["Error"]["Code"] == "AccessDenied" and attempt < 5:
                    time.sleep(5)  # a newly created role can take a few seconds to become assumable
                    continue
                raise
        raise AssertionError("unreachable")

    core = get_session()
    core._credentials = DeferredRefreshableCredentials(refresh_using=fetch, method="sts-assume-role")
    return boto3.Session(botocore_session=core, region_name=region)


class PaymentsApiError(Exception):
    """An error response from AgentCore payments."""

    def __init__(self, status: int, message: str, operation: str):
        super().__init__(f"{operation} failed ({status}): {message}")
        self.status = status
        self.message = message
        self.operation = operation


# ── AgentCore payments client ────────────────────────────────────────────────


class AgentCorePaymentsClient:
    """boto3 wrapper for the AgentCore payments calls this sample uses
    (`bedrock-agentcore-control` and `bedrock-agentcore`)."""

    def __init__(self, region: str, session: boto3.Session | None = None, user_id: str = "default-user"):
        self.region = region
        self.session = session or boto3.Session(region_name=region)
        self.user_id = user_id
        self.cp = self.session.client("bedrock-agentcore-control", region_name=region)
        self.dp = self.session.client("bedrock-agentcore", region_name=region)

    def _boto(self, fn, operation: str, **kwargs) -> dict:
        try:
            out = fn(**kwargs)
        except ClientError as e:
            err = e.response.get("Error", {})
            raise PaymentsApiError(
                e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0),
                err.get("Message") or err.get("Code", str(e)),
                operation,
            ) from e
        out.pop("ResponseMetadata", None)
        return out

    # Control plane
    def create_payment_manager(self, name: str, role_arn: str, description: str = "") -> dict:
        kwargs = {"name": name, "authorizerType": "AWS_IAM", "roleArn": role_arn, "clientToken": str(uuid.uuid4())}
        if description:  # the API rejects an empty string
            kwargs["description"] = description
        return self._boto(self.cp.create_payment_manager, "CreatePaymentManager", **kwargs)

    def get_payment_manager(self, manager_id: str) -> dict:
        return self._boto(self.cp.get_payment_manager, "GetPaymentManager", paymentManagerId=manager_id)

    def list_payment_managers(self) -> list[dict]:
        out, kwargs = [], {}
        while True:
            page = self._boto(self.cp.list_payment_managers, "ListPaymentManagers", **kwargs)
            out += page.get("paymentManagers", [])
            if not page.get("nextToken"):
                return out
            kwargs["nextToken"] = page["nextToken"]

    def delete_payment_manager(self, manager_id: str) -> dict:
        return self._boto(self.cp.delete_payment_manager, "DeletePaymentManager", paymentManagerId=manager_id)

    def create_aws_billing_connector(self, manager_id: str, name: str) -> dict:
        """Add the manager's AWS Billing connector. It takes no credential provider: the service
        binds the AWS Marketplace service-linked role itself. A manager can have at most one."""
        return self._boto(
            self.cp.create_payment_connector,
            "CreatePaymentConnector",
            paymentManagerId=manager_id,
            name=name,
            type="AwsBilling",
            clientToken=str(uuid.uuid4()),
        )

    def list_payment_connectors(self, manager_id: str) -> list[dict]:
        return self._boto(self.cp.list_payment_connectors, "ListPaymentConnectors", paymentManagerId=manager_id).get(
            "paymentConnectors", []
        )

    def delete_payment_connector(self, manager_id: str, connector_id: str) -> dict:
        return self._boto(
            self.cp.delete_payment_connector,
            "DeletePaymentConnector",
            paymentManagerId=manager_id,
            paymentConnectorId=connector_id,
        )

    # Data plane
    def create_payment_session(self, manager_arn: str, max_spend_usd: str, expiry_minutes: int = 60) -> dict:
        return self._boto(
            self.dp.create_payment_session,
            "CreatePaymentSession",
            userId=self.user_id,
            paymentManagerArn=manager_arn,
            expiryTimeInMinutes=expiry_minutes,
            limits={"maxSpendAmount": {"value": max_spend_usd, "currency": "USD"}},
            clientToken=str(uuid.uuid4()),
        )["paymentSession"]

    def get_payment_session(self, manager_arn: str, session_id: str) -> dict:
        return self._boto(
            self.dp.get_payment_session,
            "GetPaymentSession",
            userId=self.user_id,
            paymentManagerArn=manager_arn,
            paymentSessionId=session_id,
        )["paymentSession"]

    def process_aws_billing_payment(
        self, manager_arn: str, session_id: str, accepted: dict, x402_version: int = 2, client_token: str | None = None
    ) -> dict:
        """Pay a seller's x402 requirement with AWS Billing. `accepted` is the exact entry from
        the 402's `accepts` list whose network is aws:base. The manager must have an AWS Billing
        connector, and the service-managed instrument ID is the same for every manager. Reusing
        a clientToken within 8 hours returns the original payment instead of charging again."""
        return self._boto(
            self.dp.process_payment,
            "ProcessPayment",
            userId=self.user_id,
            paymentManagerArn=manager_arn,
            paymentSessionId=session_id,
            paymentInstrumentId=AWS_BILLING_INSTRUMENT_ID,
            paymentType=AWS_BILLING_PAYMENT_TYPE,
            paymentInput={"awsBillingX402": {"version": str(x402_version), "payload": accepted}},
            clientToken=client_token or str(uuid.uuid4()),
        )


# ── x402 helpers ─────────────────────────────────────────────────────────────


def _b64decode(value: str) -> bytes:
    """Decode standard or URL-safe base64, with or without padding."""
    value = value.strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(value + "=" * (-len(value) % 4))


def parse_payment_required(resp: requests.Response) -> dict:
    """Return the x402 challenge from a 402. Most sellers send it base64-encoded in the
    PAYMENT-REQUIRED header (the body is usually `{}`). Some send only a flag in the
    header (`PAYMENT-REQUIRED: true`) and the challenge as JSON in the body, so fall
    back to the body whenever the header doesn't decode to a JSON object."""
    header = resp.headers.get("payment-required")
    if header:
        try:
            decoded = json.loads(_b64decode(header))
            if isinstance(decoded, dict):
                return decoded
        except ValueError:  # covers binascii.Error, UnicodeDecodeError and JSONDecodeError
            pass
    return resp.json()


AWS_BILLING_NETWORK = "aws:base"  # the x402 `network` value AWS Marketplace uses in `accepts`


def select_marketplace_requirement(challenge: dict) -> dict:
    """Pick the AWS Marketplace option: the `accepts` entry with network `aws:base`, a USD
    asset and a quoteToken. Other entries (for example on-chain options) are ignored."""
    for req in challenge.get("accepts", []):
        if (
            req.get("network") == AWS_BILLING_NETWORK
            and req.get("extra", {}).get("quoteToken")
            and req.get("asset", "").startswith("iso4217:")
        ):
            return req
    raise ValueError("402 has no AWS Marketplace (AWS Billing) payment option")


def aws_billing_token_payload(result: dict) -> dict:
    """Return the x402 PaymentPayload `payload` object for the seller retry.

    ProcessPayment returns the payment token, a signed JWT, at
    paymentOutput.awsBillingX402.paymentToken. Treat it as a credential: don't log it."""
    token = ((result.get("paymentOutput") or {}).get("awsBillingX402") or {}).get("paymentToken")
    if not token:
        keys = sorted(result.get("paymentOutput") or {})
        raise ValueError(f"ProcessPayment returned no AWS Billing payment token (paymentOutput keys: {keys})")
    return {"paymentToken": token}


def build_payment_signature(challenge: dict, accepted: dict, token_payload: dict) -> str:
    """x402 v2 PaymentPayload, base64 JSON, for the PAYMENT-SIGNATURE header.
    `accepted` must be the exact requirement whose quoteToken was paid; `token_payload` is
    the {"paymentToken": ...} object from aws_billing_token_payload."""
    payload = {
        "x402Version": challenge.get("x402Version", 2),
        "resource": challenge.get("resource"),
        "accepted": accepted,
        "payload": token_payload,
    }
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def peek_quote(quote_token: str) -> dict:
    """Decode quote claims for display and client-side checks only. This does NOT verify
    the signature; AWS Marketplace verifies it against the seller's registered key."""
    try:
        claims = json.loads(_b64decode(quote_token.split(".")[1]))
    except (IndexError, ValueError):  # ValueError covers bad base64, UTF-8 and JSON
        return {}
    return claims if isinstance(claims, dict) else {}


# ── The buyer loop ───────────────────────────────────────────────────────────


@dataclass
class PaymentReceipt:
    url: str
    amount_usd: str
    product_id: str | None
    process_payment_id: str
    status: str


@dataclass
class PaidResponse:
    status_code: int
    body: str
    receipt: PaymentReceipt | None = None


@dataclass
class MarketplacePayer:
    """Calls an x402 seller and pays with AWS Billing when it returns 402.

    Guardrails, in addition to the server-side session budget:
      max_price_usd   refuse any single quote above this amount
      allowed_hosts   only pay these seller hosts (protects against an agent being
                      steered to an unexpected seller by content it read). None means
                      no host restriction; an empty set means pay nobody.
    """

    client: AgentCorePaymentsClient
    manager_arn: str
    session_id: str
    max_price_usd: Decimal = Decimal("0.02")
    allowed_hosts: set[str] | None = None
    receipts: list[PaymentReceipt] = field(default_factory=list)

    def fetch(self, url: str, method: str = "GET", **kwargs: Any) -> PaidResponse:
        first = requests.request(method, url, timeout=30, **kwargs)
        if first.status_code != 402:
            return PaidResponse(first.status_code, first.text)

        host = urlparse(url).hostname or ""
        if self.allowed_hosts is not None and host not in self.allowed_hosts:
            raise PermissionError(f"Seller {host} is not in the allowed seller list")

        challenge = parse_payment_required(first)
        accepted = select_marketplace_requirement(challenge)
        try:
            amount = Decimal(str(accepted["amount"]))
        except (KeyError, InvalidOperation) as e:
            raise ValueError("402 requirement has no valid amount") from e
        if amount > self.max_price_usd:
            raise PermissionError(f"Quote ${amount} exceeds per-request cap ${self.max_price_usd}")

        quote = accepted["extra"]["quoteToken"]
        x402_version = int(challenge.get("x402Version", 2))
        result = self.client.process_aws_billing_payment(self.manager_arn, self.session_id, accepted, x402_version)
        token_payload = aws_billing_token_payload(result)
        receipt = PaymentReceipt(
            url,
            str(amount),
            peek_quote(quote).get("settlement", {}).get("product_id"),
            result["processPaymentId"],
            result["status"],
        )
        self.receipts.append(receipt)
        log.info("Paid $%s for %s (processPaymentId=%s)", amount, url, receipt.process_payment_id)

        # Redeem right away: the payment token is valid for 5 minutes, and quotes can expire too.
        headers = {
            **kwargs.pop("headers", {}),
            "PAYMENT-SIGNATURE": build_payment_signature(challenge, accepted, token_payload),
        }
        paid = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        return PaidResponse(paid.status_code, paid.text, receipt)

    def remaining_budget(self) -> dict:
        s = self.client.get_payment_session(self.manager_arn, self.session_id)
        return {"available": s["availableLimits"]["availableSpendAmount"], "limit": s["limits"]["maxSpendAmount"]}
