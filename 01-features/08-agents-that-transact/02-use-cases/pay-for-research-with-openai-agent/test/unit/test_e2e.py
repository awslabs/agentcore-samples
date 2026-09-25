import base64
import json

import httpx
import pytest
from e2e import main, merchant_challenge


@pytest.mark.parametrize(
    "challenge",
    [{}, [], {"x402Version": 2, "accepts": []}, {"x402Version": 99, "accepts": [{"network": "eip155:84532"}]}],
)
def test_a_bare_or_unsupported_402_is_not_a_passing_smoke_test(monkeypatch, challenge) -> None:
    monkeypatch.setattr("e2e.httpx.get", lambda *args, **kwargs: httpx.Response(402, json=challenge))

    with pytest.raises(RuntimeError, match="supported x402 challenge"):
        merchant_challenge("https://merchant.example/data")


@pytest.mark.parametrize("x402_version", [1, 2])
def test_challenge_reports_the_quoted_price(monkeypatch, x402_version) -> None:
    challenge = {
        "x402Version": x402_version,
        "accepts": [
            {
                "network": "eip155:84532",
                "scheme": "exact",
                "asset": "USDC",
                "amount" if x402_version == 2 else "maxAmountRequired": "2000",
            }
        ],
    }
    response = (
        httpx.Response(402, headers={"payment-required": base64.b64encode(json.dumps(challenge).encode()).decode()})
        if x402_version == 2
        else httpx.Response(402, json=challenge)
    )
    monkeypatch.setattr("e2e.httpx.get", lambda *args, **kwargs: response)

    result = merchant_challenge("https://merchant.example/data")

    assert result["status"] == "passed"
    assert result["x402_version"] == x402_version
    assert result["offers"][0]["amount_base_units"] == "2000"


def test_merchant_only_mode_never_invokes_model_or_payment(monkeypatch, capsys) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected model or payment call")

    monkeypatch.setattr("e2e.model_smoke", forbidden)
    monkeypatch.setattr("e2e.payment_smoke", forbidden)
    monkeypatch.setattr("e2e.merchant_challenge", lambda url: {"status": "passed"})
    main(["--merchant-only", "--url", "https://merchant.example/data"])

    report = json.loads(capsys.readouterr().out)
    assert report["model"]["status"] == "skipped"
    assert report["payment"]["status"] == "skipped"
