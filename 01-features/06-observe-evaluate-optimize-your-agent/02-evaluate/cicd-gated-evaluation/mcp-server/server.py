# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""MCP Server with fastmcp AuthMiddleware for role-based tool access.

Auth flow:
1. AgentCore validates JWT (signature, issuer, expiry) via authorizer_configuration
2. request_header_allowlist=["Authorization"] passes the token through to this container
3. get_access_token() reads the token from HTTP headers via fastmcp.server.dependencies
4. AuthMiddleware checks custom:roles claim against tool meta to enforce access
"""

import logging
import os
from datetime import datetime
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastmcp import FastMCP
from fastmcp.server.middleware.error_handling import ErrorHandlingMiddleware
from pydantic import Field
from pythonjsonlogger.json import JsonFormatter
from src.auth import auth_meta
from src.auth.middleware import AuthMiddleware

_formatter = JsonFormatter(
    "%(asctime)s %(levelname)s %(name)s %(filename)s %(lineno)d %(message)s",
    datefmt="%Y-%m-%d %H:%M:%Sz",
)
_handler = logging.StreamHandler()
_handler.setFormatter(_formatter)
logging.basicConfig(
    level=logging.getLevelNamesMapping().get(os.getenv("LOG_LEVEL", "INFO")),
    handlers=[_handler],
)
logger = logging.getLogger(__name__)

# mask_error_details=True: internal exception text is not returned to the caller, which
# would otherwise leak paths, claim values and internal state. Errors are still
# logged server-side with full detail via the middleware below.
mcp = FastMCP(name="MCP Server", mask_error_details=True)
mcp.add_middleware(ErrorHandlingMiddleware(logger=logger, include_traceback=False))
mcp.add_middleware(AuthMiddleware())


@mcp.tool(tags={"DateTime"})
def get_current_datetime(
    timezone_name: Annotated[
        str,
        Field(
            description="IANA time zone name (e.g. 'Australia/Perth', 'UTC')",
            min_length=1,
        ),
    ] = "UTC",
) -> str:
    """Returns the current date and time in ISO 8601 format."""
    try:
        return datetime.now(ZoneInfo(timezone_name)).isoformat(timespec="seconds")
    except ZoneInfoNotFoundError:
        return f"Error: '{timezone_name}' is not a valid time zone."


@mcp.tool(tags={"Finance"}, meta=auth_meta(roles=["FinanceUser"], scopes=["mcp/finance"]))
def get_stock_price(
    symbol: Annotated[str, Field(description="Stock symbol (e.g., AAPL, GOOGL)", min_length=1)],
) -> str:
    """Returns the current stock price for a stock symbol."""
    prices = {"aapl": "$175.50", "googl": "$142.30", "msft": "$380.20", "amzn": "$178.90"}
    return f"{symbol.upper()}: {prices.get(symbol.lower(), '$100.00')}"


@mcp.tool(tags={"HR"}, meta=auth_meta(roles=["HRUser"], scopes=["mcp/hr"]))
def get_employee_count(
    department: Annotated[str, Field(description="Department name", min_length=1)],
) -> str:
    """Returns the employee count for a department."""
    counts = {"engineering": "150 employees", "sales": "80 employees", "hr": "25 employees", "finance": "40 employees"}
    return f"{department}: {counts.get(department.lower(), '50 employees')}"


app = mcp.http_app(stateless_http=True)

if __name__ == "__main__":
    mcp.run(transport="streamable-http", stateless_http=True)
