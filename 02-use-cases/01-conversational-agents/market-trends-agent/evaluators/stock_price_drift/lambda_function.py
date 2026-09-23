"""Stock price drift checker for Market Trends Agent.

Evaluation level: TRACE

Extracts (ticker, quoted_price) pairs from the agent's response on a
trace and compares each quoted price against a real-time reference
pulled from Yahoo Finance's public quote endpoint. Drift greater than
DRIFT_THRESHOLD_PCT is flagged as a failure.

This is a genuine "Lambda-grounded fact check" demo: no seeded data,
no mocks — the evaluator hits a live external source and compares.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DRIFT_THRESHOLD_PCT = float(os.environ.get("DRIFT_THRESHOLD_PCT", "2.0"))
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
HTTP_TIMEOUT_S = 4.0
USER_AGENT = "market-trends-eval/1.0"

# Match strings like "AAPL $182.45", "AAPL trading at $182.45", "(AAPL) 182.45",
# or "AAPL ... quoted price of $182.45". Allow ≤40 intervening characters
# (including letters), and require either a dollar sign or a decimal to
# distinguish a price from an arbitrary integer.
TICKER_PRICE_RE = re.compile(
    r"\b(?P<ticker>[A-Z]{1,5})\b.{0,40}?"
    r"(?:\$\s?(?P<dprice>\d{1,6}(?:\.\d{1,4})?)|(?P<fprice>\d{1,6}\.\d{1,4}))",
    flags=re.DOTALL,
)


def _response_text(spans: Iterable[Dict[str, Any]]) -> str:
    """Best-effort extraction of the assistant response text on a trace.

    Traceloop / openllmetry stores LangGraph workflow and tool outputs in
    ``gen_ai.task.output`` / ``gen_ai.tool.call.result`` / ``traceloop.entity.output``.
    The classic OpenTelemetry ``gen_ai.completion.*`` keys are kept as fallbacks
    for compatibility with other instrumentations (Strands, OpenInference).
    """
    chunks: List[str] = []
    keys = (
        "gen_ai.task.output",
        "traceloop.entity.output",
        "gen_ai.tool.call.result",
        "gen_ai.completion.0.content",
        "gen_ai.completion",
        "output.value",
    )
    for span in spans:
        attrs = span.get("attributes") or {}
        for key in keys:
            v = attrs.get(key)
            if isinstance(v, str) and v.strip():
                chunks.append(v)
                break
    return "\n".join(chunks)


def _filter_trace_spans(spans: Iterable[Dict[str, Any]], target: Dict[str, Any]) -> List[Dict[str, Any]]:
    trace_ids = (target or {}).get("traceIds") or []
    if not trace_ids:
        return list(spans)
    wanted = set(trace_ids)
    return [s for s in spans if s.get("traceId") in wanted]


def _extract_ticker_price_pairs(text: str) -> List[Tuple[str, float]]:
    seen: Dict[str, float] = {}
    for m in TICKER_PRICE_RE.finditer(text):
        ticker = m.group("ticker")
        raw = m.group("dprice") or m.group("fprice")
        if raw is None:
            continue
        try:
            price = float(raw)
        except (TypeError, ValueError):
            continue
        # Skip common English tokens and financial-jargon acronyms that
        # match the ticker shape but aren't real tickers.
        if ticker in _NON_TICKERS:
            continue
        # keep first occurrence per ticker to stay deterministic
        seen.setdefault(ticker, price)
    return list(seen.items())


_NON_TICKERS: frozenset = frozenset(
    {
        # Currency / markets
        "USD",
        "EUR",
        "GBP",
        "JPY",
        "CNY",
        "INR",
        "BPS",
        "EDT",
        "EST",
        "UTC",
        "ET",
        "PT",
        # Corporate / finance jargon
        "CEO",
        "CFO",
        "COO",
        "CTO",
        "EPS",
        "IPO",
        "GDP",
        "CPI",
        "PPI",
        "ROI",
        "ROE",
        "PE",
        "PB",
        "EV",
        "DCF",
        "LBO",
        "MBO",
        "MBS",
        "NAV",
        "SEC",
        "FED",
        "IRS",
        "ETF",
        "MRS",
        "LTM",
        "TTM",
        "YTD",
        "WTD",
        "MTD",
        "QTD",
        "AUM",
        "NYSE",
        # Common
        "AI",
        "API",
        "SDK",
        "LLM",
        "AM",
        "PM",
        "MOU",
        "NDA",
        "KYC",
        "AML",
        # Quarters
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "FY",
        "FYE",
        # Letters used in ranges/labels
        "P",
        "S",
        "T",
        "M",
        "B",
        "K",
        "N",
    }
)


def _fetch_reference_price(ticker: str) -> Optional[float]:
    url = YAHOO_CHART_URL.format(symbol=ticker)
    # Defense-in-depth: the URL is a compile-time constant pointing at
    # Yahoo Finance over https, but we still reject anything that isn't
    # https to shut the door on file:// / ftp:// / custom handlers.
    if not url.startswith("https://"):
        raise ValueError(f"refusing non-https reference URL: {url!r}")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        # Scheme pinned to https above; ticker is regex-validated.
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:  # nosec B310  # noqa: S310
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("reference lookup failed for %s: %s", ticker, exc)
        return None

    results = ((payload.get("chart") or {}).get("result")) or []
    if not results:
        return None
    meta = (results[0] or {}).get("meta") or {}
    for key in (
        "regularMarketPrice",
        "postMarketPrice",
        "preMarketPrice",
        "previousClose",
    ):
        value = meta.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    try:
        session_spans = (event.get("evaluationInput") or {}).get("sessionSpans") or []
        target = event.get("evaluationTarget") or {}
        trace_spans = _filter_trace_spans(session_spans, target)

        text = _response_text(trace_spans)
        if not text.strip():
            return {
                "label": "NO_OUTPUT",
                "value": 1.0,
                "explanation": "No agent response text on this trace.",
            }

        pairs = _extract_ticker_price_pairs(text)
        if not pairs:
            return {
                "label": "NO_PRICES",
                "value": 1.0,
                "explanation": "Response contains no ticker+price pairs to verify.",
            }

        checked: List[str] = []
        drifts: List[str] = []
        for ticker, quoted in pairs:
            ref = _fetch_reference_price(ticker)
            if ref is None:
                checked.append(f"{ticker}=unverifiable")
                continue
            drift_pct = abs(quoted - ref) / ref * 100.0
            checked.append(f"{ticker} quoted={quoted:.2f} ref={ref:.2f} drift={drift_pct:.2f}%")
            if drift_pct > DRIFT_THRESHOLD_PCT:
                drifts.append(f"{ticker} ({drift_pct:.2f}%)")

        if not drifts:
            return {
                "label": "PASS",
                "value": 1.0,
                "explanation": "All ticker+price pairs within drift threshold. " + "; ".join(checked),
            }

        return {
            "label": "DRIFT",
            "value": 0.0,
            "explanation": (
                f"Drift greater than {DRIFT_THRESHOLD_PCT}% vs Yahoo reference for: "
                f"{', '.join(drifts)}. Details: {'; '.join(checked)}"
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("stock_price_drift failed unexpectedly")
        return {
            "errorCode": "EvaluatorInternalError",
            "errorMessage": f"{type(exc).__name__}: {exc}"[:500],
        }
