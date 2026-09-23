"""Deterministic line-item table parser (spec §7, C2).

Receipts are a header plus a line-item table, and the table is where single-shot
LLM extraction silently drops or duplicates rows. Textract AnalyzeExpense already
returns structured LineItemGroups with typed fields (ITEM, PRICE, QUANTITY,
UNIT_PRICE) — this parses them deterministically into clean rows so the agent
doesn't have to retype them. The agent falls back to LLM reasoning only when the
parser yields nothing usable (messy / unstructured layouts) — the hybrid approach.

Pure stdlib so it's unit-testable without AWS or the runtime.
"""

from typing import Any

# Textract LineItemExpenseField types we map onto our row shape.
_ITEM = {"ITEM", "DESCRIPTION", "PRODUCT_CODE"}
_QTY = {"QUANTITY"}
_UNIT = {"UNIT_PRICE"}
_AMOUNT = {"PRICE", "AMOUNT", "EXPENSE_ROW"}


def _to_number(text: str):
    """Parse '5.50', '$5.50', '1,234.00' -> float; None if not numeric."""
    if not text:
        return None
    cleaned = text.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_line_items(ocr_line_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn the OCR digest's line_items (from tools.ocr.analyze_receipt) into clean
    rows: [{description, qty, unitPrice, amount}].

    Each input item is {"fields": [{"type","value","confidence"}, ...]}. We pick the
    best field per role. Rows with no description AND no amount are dropped (noise).
    """
    rows: list[dict[str, Any]] = []
    for li in ocr_line_items:
        desc, qty, unit, amount = "", None, None, None
        for f in li.get("fields", []):
            ftype = (f.get("type") or "").upper()
            val = f.get("value", "")
            if ftype in _ITEM and not desc:
                desc = val.strip()
            elif ftype in _QTY:
                qty = _to_number(val)
            elif ftype in _UNIT:
                unit = _to_number(val)
            elif ftype in _AMOUNT:
                amount = _to_number(val)
        if not desc and amount is None:
            continue  # not a real line item
        rows.append(
            {
                "description": desc,
                "qty": qty if qty is not None else 1,
                "unitPrice": unit if unit is not None else amount,
                "amount": amount if amount is not None else unit,
            }
        )
    return rows


def parse_success_rate(ocr_line_items: list[dict[str, Any]], parsed: list[dict[str, Any]]) -> float:
    """Fraction of OCR line-item blocks that became clean rows. The agent uses this
    to decide whether to trust the parser or fall back to the LLM for the table."""
    n = len(ocr_line_items)
    return round(len(parsed) / n, 2) if n else 0.0
