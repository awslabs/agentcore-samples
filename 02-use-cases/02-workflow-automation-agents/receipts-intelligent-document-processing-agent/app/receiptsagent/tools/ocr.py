"""OCR tool — Amazon Textract AnalyzeExpense on a receipt in S3.

AnalyzeExpense is Textract's purpose-built receipt/invoice API: it returns
SummaryFields (vendor, total, tax, date, ...) and LineItemGroups, each with a
confidence score. We hand the agent a compact, readable digest plus the raw
summary values so it can reason and fill the extraction schema (spec §7).

Confidence drives the human-review gate (spec §7): we surface per-field and an
overall mean so the agent/validator can decide auto-persist vs needs_review.
"""

import json
from typing import Any
from urllib.parse import urlparse

import boto3

_textract = boto3.client("textract")


def _parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    p = urlparse(s3_uri)
    if p.scheme != "s3" or not p.netloc or not p.path:
        raise ValueError(f"not an s3:// uri: {s3_uri}")
    return p.netloc, p.path.lstrip("/")


def analyze_receipt(s3_uri: str) -> dict[str, Any]:
    """Run Textract AnalyzeExpense on an S3 receipt; return a digest dict.

    Shape:
      {
        "summary_fields": [{"type","label","value","confidence"}, ...],
        "line_items":     [{"fields": [{"type","value","confidence"}, ...]}, ...],
        "overall_confidence": float,   # mean confidence across summary fields
        "raw_text": "TYPE: value\n..."  # a flat readable rendering for the LLM
      }
    """
    bucket, key = _parse_s3_uri(s3_uri)
    resp = _textract.analyze_expense(Document={"S3Object": {"Bucket": bucket, "Name": key}})

    summary_fields: list[dict] = []
    line_items: list[dict] = []
    confidences: list[float] = []

    for doc in resp.get("ExpenseDocuments", []):
        for f in doc.get("SummaryFields", []):
            ftype = (f.get("Type") or {}).get("Text", "")
            label = (f.get("LabelDetection") or {}).get("Text", "")
            val = (f.get("ValueDetection") or {}).get("Text", "")
            conf = (f.get("ValueDetection") or {}).get("Confidence", 0.0)
            summary_fields.append({"type": ftype, "label": label, "value": val, "confidence": round(conf, 2)})
            confidences.append(conf)
        for group in doc.get("LineItemGroups", []):
            for li in group.get("LineItems", []):
                fields = []
                for ef in li.get("LineItemExpenseFields", []):
                    fields.append(
                        {
                            "type": (ef.get("Type") or {}).get("Text", ""),
                            "value": (ef.get("ValueDetection") or {}).get("Text", ""),
                            "confidence": round((ef.get("ValueDetection") or {}).get("Confidence", 0.0), 2),
                        }
                    )
                line_items.append({"fields": fields})

    overall = round(sum(confidences) / len(confidences), 2) if confidences else 0.0

    # Flat readable rendering for the model.
    lines = [f"{sf['type'] or sf['label']}: {sf['value']}" for sf in summary_fields if sf["value"]]
    for i, li in enumerate(line_items, 1):
        parts = [f"{x['type']}={x['value']}" for x in li["fields"] if x["value"]]
        if parts:
            lines.append(f"  line {i}: " + " | ".join(parts))
    raw_text = "\n".join(lines)

    return {
        "summary_fields": summary_fields,
        "line_items": line_items,
        "overall_confidence": overall,
        "raw_text": raw_text,
    }


def ocr_digest_json(s3_uri: str) -> str:
    """Convenience: analyze and return a JSON string (for prompt embedding)."""
    return json.dumps(analyze_receipt(s3_uri), default=str)
