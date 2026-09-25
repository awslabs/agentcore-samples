"""Exercise the installed GA SDK against botocore's public service schemas."""

import base64
import json
from datetime import datetime, timezone

import boto3
import httpx
import pytest
from bedrock_agentcore.payments import PaymentManager
from botocore.stub import ANY, Stubber
from cleanup_payment_session import main as cleanup_main
from create_payment_session import create_session
from payment import PaymentConfig, X402PaymentClient

MANAGER_ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:payment-manager/research-0123456789"
SESSION_ID = "payment-session-" + "a" * 15
INSTRUMENT_ID = "payment-instrument-" + "b" * 15
USER_ID = "sample-user"
NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)
URL = "https://merchant.example/data"


@pytest.fixture
def sdk():
    # Explicit dummy credentials and Stubber keep every test offline.
    session = boto3.Session(
        aws_access_key_id="testing",
        aws_secret_access_key="testing",  # pragma: allowlist secret
        aws_session_token="testing",
        region_name="us-east-1",
    )
    manager = PaymentManager(payment_manager_arn=MANAGER_ARN, boto3_session=session)
    with Stubber(manager._payment_client) as stubber:
        yield manager, stubber
        stubber.assert_no_pending_responses()


def session_response():
    return {
        "paymentSession": {
            "paymentSessionId": SESSION_ID,
            "paymentManagerArn": MANAGER_ARN,
            "userId": USER_ID,
            "expiryTimeInMinutes": 15,
            "limits": {"maxSpendAmount": {"value": "0.001", "currency": "USD"}},
            "availableLimits": {"availableSpendAmount": {"value": "0.001", "currency": "USD"}},
            "createdAt": NOW,
            "updatedAt": NOW,
        }
    }


def payment_config():
    return PaymentConfig(
        manager_arn=MANAGER_ARN,
        instrument_id=INSTRUMENT_ID,
        session_id=SESSION_ID,
        user_id=USER_ID,
        allowed_hosts=frozenset({"merchant.example"}),
    )


def test_create_session_uses_ga_schema_and_preserves_subcent_budget(sdk):
    manager, stubber = sdk
    stubber.add_response(
        "create_payment_session",
        session_response(),
        {
            "paymentManagerArn": MANAGER_ARN,
            "userId": USER_ID,
            "limits": {"maxSpendAmount": {"value": "0.001", "currency": "USD"}},
            "expiryTimeInMinutes": 15,
            "clientToken": ANY,
        },
    )

    assert create_session(manager, USER_ID, "0.001", 15)["paymentSessionId"] == SESSION_ID


def test_session_status_unwraps_the_ga_response(sdk):
    manager, stubber = sdk
    stubber.add_response(
        "get_payment_session",
        session_response(),
        {"paymentManagerArn": MANAGER_ARN, "paymentSessionId": SESSION_ID, "userId": USER_ID},
    )

    result = json.loads(X402PaymentClient(payment_config(), manager).session_status())

    assert result["available_spend"] == "0.001"
    assert result["maximum_spend"] == "0.001"
    assert SESSION_ID not in json.dumps(result)


def test_cleanup_script_deletes_only_the_selected_session(sdk, monkeypatch, capsys):
    manager, stubber = sdk
    monkeypatch.setenv("PAYMENT_MANAGER_ARN", MANAGER_ARN)
    monkeypatch.setenv("PAYMENT_USER_ID", USER_ID)
    monkeypatch.setenv("PAYMENT_SESSION_ID", "another-session")
    monkeypatch.setattr("cleanup_payment_session.create_payment_manager", lambda arn: manager)
    stubber.add_response(
        "delete_payment_session",
        {"status": "DELETED"},
        {"paymentManagerArn": MANAGER_ARN, "paymentSessionId": SESSION_ID, "userId": USER_ID},
    )

    cleanup_main(["--session-id", SESSION_ID])

    assert f"Deleted payment session {SESSION_ID}" in capsys.readouterr().out


@pytest.mark.parametrize("x402_version", [1, 2])
def test_real_sdk_generates_x402_headers_using_public_process_payment_schema(sdk, x402_version):
    manager, stubber = sdk
    offer = {
        "scheme": "exact",
        "network": "eip155:84532",
        "amount" if x402_version == 2 else "maxAmountRequired": "2000",
        "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "payTo": "0x" + "1" * 40,
        "maxTimeoutSeconds": 60,
    }
    challenge = {
        "x402Version": x402_version,
        "resource": {"url": URL},
        "accepts": [offer],
    }
    stubber.add_response(
        "get_payment_instrument",
        {
            "paymentInstrument": {
                "paymentInstrumentId": INSTRUMENT_ID,
                "paymentManagerArn": MANAGER_ARN,
                "paymentConnectorId": "coinbase-0123456789",
                "userId": USER_ID,
                "paymentInstrumentType": "EMBEDDED_CRYPTO_WALLET",
                "paymentInstrumentDetails": {"embeddedCryptoWallet": {"network": "ETHEREUM", "linkedAccounts": []}},
                "status": "ACTIVE",
                "createdAt": NOW,
                "updatedAt": NOW,
            }
        },
        {"paymentManagerArn": MANAGER_ARN, "paymentInstrumentId": INSTRUMENT_ID, "userId": USER_ID},
    )
    proof_payload = {"signature": "sample-proof", "authorization": {"value": "2000"}}
    stubber.add_response(
        "process_payment",
        {
            "processPaymentId": "00000000-0000-4000-8000-000000000000",
            "paymentManagerArn": MANAGER_ARN,
            "paymentSessionId": SESSION_ID,
            "paymentInstrumentId": INSTRUMENT_ID,
            "paymentType": "CRYPTO_X402",
            "status": "PROOF_GENERATED",
            "paymentOutput": {"cryptoX402": {"version": str(x402_version), "payload": proof_payload}},
            "createdAt": NOW,
            "updatedAt": NOW,
        },
        {
            "paymentManagerArn": MANAGER_ARN,
            "paymentSessionId": SESSION_ID,
            "paymentInstrumentId": INSTRUMENT_ID,
            "userId": USER_ID,
            "paymentType": "CRYPTO_X402",
            "paymentInput": {"cryptoX402": {"version": str(x402_version), "payload": offer}},
            "clientToken": ANY,
        },
    )
    headers_sent = []

    def get(url, address, headers):
        headers_sent.append(headers)
        if headers:
            return httpx.Response(200, text="premium evidence")
        if x402_version == 2:
            encoded = base64.b64encode(json.dumps(challenge).encode()).decode()
            return httpx.Response(402, headers={"payment-required": encoded})
        return httpx.Response(402, json=challenge)

    client = X402PaymentClient(payment_config(), manager, get=get, resolver=lambda host, port: ["8.8.8.8"])
    result = json.loads(client.fetch(URL))
    header_name = "PAYMENT-SIGNATURE" if x402_version == 2 else "X-PAYMENT"

    assert result["payment_made"] is True
    assert result["payment_attempts"] == 1
    proof = json.loads(base64.b64decode(headers_sent[1][header_name]))
    assert proof["x402Version"] == x402_version
    assert proof["payload"] == proof_payload
