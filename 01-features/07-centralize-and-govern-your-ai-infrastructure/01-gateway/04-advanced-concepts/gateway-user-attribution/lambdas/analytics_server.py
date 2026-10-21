"""Analytics MCP server - an AgentCore Gateway Lambda target.

Tools:
  - GetMetrics(product, period?)  -> mock product metrics
  - GenerateReport(type)          -> a short generated summary report
"""

import json

TOOL_NAME_DELIMITER = "___"

# Mock engagement metrics keyed by the fictional AnyCompany product lines.
METRICS = {
    "edge-sensor": {"views": 4820, "downloads": 612, "inquiries": 44},
    "gateway-router": {"views": 2110, "downloads": 288, "inquiries": 19},
    "secure-controller": {"views": 3990, "downloads": 501, "inquiries": 37},
}


def _get_metrics(args):
    product = args.get("product")
    if not product:
        raise ValueError("product is required")
    period = args.get("period", "last-30-days")
    data = METRICS.get(product)
    if data is None:
        raise ValueError(f"Unknown product: {product}")
    return {"product": product, "period": period, "metrics": data}


def _generate_report(args):
    report_type = args.get("type")
    if not report_type:
        raise ValueError("type is required")
    totals = {
        "views": sum(m["views"] for m in METRICS.values()),
        "downloads": sum(m["downloads"] for m in METRICS.values()),
        "inquiries": sum(m["inquiries"] for m in METRICS.values()),
    }
    return {
        "type": report_type,
        "summary": (
            f"{report_type} report: {totals['views']} total views, "
            f"{totals['downloads']} downloads, {totals['inquiries']} inquiries "
            f"across {len(METRICS)} product lines."
        ),
        "totals": totals,
    }


TOOLS = {
    "GetMetrics": _get_metrics,
    "GenerateReport": _generate_report,
}


def _resolve_tool_name(context):
    custom = getattr(context, "client_context", None)
    raw = None
    if custom is not None and getattr(custom, "custom", None):
        raw = custom.custom.get("bedrockAgentCoreToolName")
    if not raw:
        return None
    if TOOL_NAME_DELIMITER in raw:
        return raw.split(TOOL_NAME_DELIMITER, 1)[1]
    return raw


def lambda_handler(event, context):
    tool_name = _resolve_tool_name(context)
    print(json.dumps({"tool": tool_name, "args": event}))

    handler = TOOLS.get(tool_name)
    if handler is None:
        return {
            "statusCode": 400,
            "error": f"Unknown tool: {tool_name}",
            "availableTools": list(TOOLS.keys()),
        }

    try:
        return handler(event or {})
    except ValueError as exc:
        return {"statusCode": 400, "error": str(exc)}
