"""Unit tests for the deterministic line-item table parser (Phase 4, C2).
Pure functions over the OCR digest shape — no AWS, no mocks."""

import pytest

from tools.table_parser import parse_line_items, parse_success_rate

pytestmark = pytest.mark.unit


def _li(**fields):
    """Build an OCR line-item block from type=value pairs."""
    return {"fields": [{"type": t, "value": v, "confidence": 99.0} for t, v in fields.items()]}


def test_parses_clean_rows():
    items = [
        _li(ITEM="Latte", QUANTITY="1", UNIT_PRICE="5.50", PRICE="5.50"),
        _li(ITEM="Croissant", QUANTITY="1", PRICE="4.25"),
    ]
    rows = parse_line_items(items)
    assert len(rows) == 2
    assert rows[0]["description"] == "Latte"
    assert rows[0]["amount"] == 5.50
    assert rows[1]["qty"] == 1  # default when absent


def test_strips_currency_and_commas():
    rows = parse_line_items([_li(ITEM="Big Item", PRICE="$1,234.00")])
    assert rows[0]["amount"] == 1234.00


def test_drops_noise_rows():
    # A block with neither description nor amount is dropped.
    rows = parse_line_items([_li(QUANTITY="1"), _li(ITEM="Real", PRICE="2.00")])
    assert len(rows) == 1
    assert rows[0]["description"] == "Real"


def test_success_rate():
    items = [_li(ITEM="A", PRICE="1.00"), _li(QUANTITY="1")]  # 1 of 2 parses
    rows = parse_line_items(items)
    assert parse_success_rate(items, rows) == 0.5


def test_empty_input():
    assert parse_line_items([]) == []
    assert parse_success_rate([], []) == 0.0


def test_unit_price_fallback_to_amount():
    # When only PRICE is present, unitPrice falls back to amount.
    rows = parse_line_items([_li(ITEM="X", PRICE="9.99")])
    assert rows[0]["unitPrice"] == 9.99
    assert rows[0]["amount"] == 9.99
