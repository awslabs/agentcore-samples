"""mcp_server.py — Financial Tools MCP Server

Production ECS deployment of the financial tools MCP server.
Runs on port 8080, streamable-http transport.

Auth inbound: SigV4-signed requests from the Strands agent ECS task.
The network boundary (VPC private subnet + security group) is the primary
guard; SigV4 header verification is the secondary layer.

Tools:
  get_financial_data(period)   — returns simulated P&L for a given quarter
  get_kpi_benchmarks()         — returns benchmark thresholds and KPI formulas
"""

import os

import uvicorn
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

mcp = FastMCP("financial-tools-mcp")

# ── Optional: load extended data from S3 at startup ──────────────────────────
# If SKILLS_BUCKET is set, the server could read additional reference data
# from S3 at startup. For now, data is inline.
SKILLS_BUCKET = os.environ.get("SKILLS_BUCKET", "")

# Simulated quarterly P&L data
FINANCIAL_DATA = {
    "Q3 2025": {
        "revenue": 4_200_000,
        "cogs": 1_890_000,
        "operating_expenses": 1_050_000,
        "ebitda": 1_260_000,
    },
    "Q2 2025": {
        "revenue": 3_800_000,
        "cogs": 1_710_000,
        "operating_expenses": 980_000,
        "ebitda": 1_110_000,
    },
    "Q1 2025": {
        "revenue": 3_500_000,
        "cogs": 1_575_000,
        "operating_expenses": 910_000,
        "ebitda": 1_015_000,
    },
    "Q4 2024": {
        "revenue": 4_000_000,
        "cogs": 1_800_000,
        "operating_expenses": 1_000_000,
        "ebitda": 1_200_000,
    },
}


@mcp.tool()
def get_financial_data(period: str) -> dict:
    """Retrieve P&L financial data for a given quarter.

    Args:
        period: Quarter identifier, e.g. 'Q3 2025', 'Q2 2025', 'Q1 2025', 'Q4 2024'.

    Returns:
        Dict with keys: period, revenue, cogs, operating_expenses, ebitda.
    """
    data = FINANCIAL_DATA.get(period)
    if data is None:
        return {"error": f"No data for '{period}'. Available: {list(FINANCIAL_DATA.keys())}"}
    return {"period": period, **data}


@mcp.tool()
def get_kpi_benchmarks() -> dict:
    """Retrieve industry benchmark thresholds and formulas for financial KPIs.

    Returns benchmark values, calculation formulas, and GREEN/YELLOW/RED status
    thresholds for Gross Margin %, EBITDA Margin %, Operating Expense Ratio,
    and Revenue Growth % QoQ.
    """
    return {
        "kpis": {
            "gross_margin_pct": {
                "formula": "(Revenue - COGS) / Revenue * 100",
                "general_benchmark": 40.0,
                "higher_is_better": True,
            },
            "ebitda_margin_pct": {
                "formula": "EBITDA / Revenue * 100",
                "general_benchmark": 15.0,
                "higher_is_better": True,
            },
            "opex_ratio": {
                "formula": "Operating Expenses / Revenue * 100",
                "general_benchmark": 30.0,
                "higher_is_better": False,
            },
            "revenue_growth_qoq_pct": {
                "formula": "(Current Revenue - Prior Revenue) / Prior Revenue * 100",
                "high_growth_benchmark": 20.0,
                "stable_growth_benchmark": 5.0,
                "higher_is_better": True,
            },
        },
        "status_thresholds": {
            "GREEN": "At or above general_benchmark",
            "YELLOW": "Within 5 percentage points below general_benchmark",
            "RED": "More than 5 percentage points below general_benchmark",
        },
    }


async def health(request: Request):
    return JSONResponse({"status": "healthy"})


# Wrap MCP ASGI app with a /health route for ALB health checks.
# json_response=True: server returns plain JSON (not SSE streams) for POST requests.
# AWS Agent Registry docs state: "SSE stream from MCP server is not supported yet"
# (docs.aws.amazon.com/bedrock-agentcore/latest/devguide/registry-sync-records.html).
# This is a public-preview limitation of the registry crawler — it applies to any
# MCP server (internal or external). json_response=True disables SSE on FastMCP so
# the registry crawler receives plain JSON. Once AWS adds SSE support to the crawler
# this setting can be removed. The MCP client (streamablehttp_client) negotiates
# response format via Accept: application/json, text/event-stream and handles both.
# Pass mcp_app.lifespan so the FastMCP session manager initialises on startup.
mcp_app = mcp.http_app(json_response=True)
app = Starlette(
    routes=[
        Route("/health", health),
        Mount("/", app=mcp_app),
    ],
    lifespan=mcp_app.lifespan,
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"Starting financial-tools-mcp on {host}:{port}/mcp  health on {host}:{port}/health")
    uvicorn.run(app, host=host, port=port)
