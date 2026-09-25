import argparse

import pytest
from create_payment_session import parse_budget, parse_expiry_minutes


def test_session_arguments_are_normalized() -> None:
    assert parse_budget("0.25") == "0.25"
    assert parse_budget("0.001") == "0.001"
    assert parse_budget("1e-3") == "0.001"
    assert parse_expiry_minutes("60") == 60


@pytest.mark.parametrize(
    ("parser", "value"),
    [(parse_budget, value) for value in ("0", "-1", "NaN", "sNaN", "Infinity", "-Infinity", "0.0000001", "nope")]
    + [(parse_expiry_minutes, value) for value in ("14", "481", "60.5")],
)
def test_session_arguments_reject_out_of_range_values(parser, value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parser(value)
