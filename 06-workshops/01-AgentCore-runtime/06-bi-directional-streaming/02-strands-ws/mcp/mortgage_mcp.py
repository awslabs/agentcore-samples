#!/usr/bin/env python3
"""
Mortgage MCP Server

Provides mortgage services tools via Model Context Protocol.
All tools return static JSON responses for demonstration purposes.
"""

import asyncio
import json
import logging
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create MCP server
server = Server("mortgage-tools")


# ============================================================================
# Tool Functions
# ============================================================================


def get_mortgage_rates() -> str:
    """
    Get current mortgage rates and loan products.

    Returns:
        Current mortgage rates for various loan types
    """
    response = {
        "status": "success",
        "effective_date": "2024-03-04",
        "rates": [
            {
                "product": "30-Year Fixed",
                "rate": 6.875,
                "apr": 7.125,
                "points": 0.5,
                "monthly_payment_per_100k": 658.00,
                "description": "Traditional 30-year fixed-rate mortgage",
            },
            {
                "product": "15-Year Fixed",
                "rate": 6.125,
                "apr": 6.375,
                "points": 0.5,
                "monthly_payment_per_100k": 855.00,
                "description": "15-year fixed-rate mortgage with lower interest",
            },
            {
                "product": "5/1 ARM",
                "rate": 6.250,
                "apr": 7.450,
                "points": 0.0,
                "monthly_payment_per_100k": 615.00,
                "description": "Adjustable rate mortgage, fixed for 5 years",
            },
            {
                "product": "FHA 30-Year",
                "rate": 6.500,
                "apr": 6.750,
                "points": 0.0,
                "monthly_payment_per_100k": 632.00,
                "description": "FHA-insured loan with lower down payment",
            },
        ],
        "disclaimer": "Rates are subject to change. Actual rate depends on credit score, down payment, and other factors.",
        "message": "Current mortgage rates retrieved successfully",
    }

    return json.dumps(response, indent=2)


def calculate_mortgage_payment(
    loan_amount: float,
    interest_rate: float,
    loan_term_years: int,
    down_payment: float = 0,
) -> str:
    """
    Calculate monthly mortgage payment.

    Args:
        loan_amount: Total loan amount
        interest_rate: Annual interest rate (e.g., 6.5 for 6.5%)
        loan_term_years: Loan term in years (e.g., 30)
        down_payment: Down payment amount (default: 0)

    Returns:
        Detailed mortgage payment calculation
    """
    principal = loan_amount - down_payment
    monthly_rate = interest_rate / 100 / 12
    num_payments = loan_term_years * 12

    # Calculate monthly payment using mortgage formula
    if monthly_rate > 0:
        monthly_payment = (
            principal * (monthly_rate * (1 + monthly_rate) ** num_payments) / ((1 + monthly_rate) ** num_payments - 1)
        )
    else:
        monthly_payment = principal / num_payments

    total_payment = monthly_payment * num_payments
    total_interest = total_payment - principal

    response = {
        "status": "success",
        "loan_details": {
            "home_price": loan_amount,
            "down_payment": down_payment,
            "down_payment_percent": (down_payment / loan_amount * 100) if loan_amount > 0 else 0,
            "loan_amount": principal,
            "interest_rate": interest_rate,
            "loan_term_years": loan_term_years,
        },
        "monthly_payment": {
            "principal_and_interest": round(monthly_payment, 2),
            "property_tax": round(loan_amount * 0.012 / 12, 2),
            "homeowners_insurance": round(loan_amount * 0.005 / 12, 2),
            "pmi": round(principal * 0.005 / 12, 2) if down_payment < loan_amount * 0.2 else 0,
            "total_monthly": round(
                monthly_payment
                + (loan_amount * 0.012 / 12)
                + (loan_amount * 0.005 / 12)
                + (principal * 0.005 / 12 if down_payment < loan_amount * 0.2 else 0),
                2,
            ),
        },
        "loan_summary": {
            "total_payments": round(total_payment, 2),
            "total_interest": round(total_interest, 2),
            "total_cost": round(total_payment + down_payment, 2),
        },
        "message": f"Monthly payment: ${round(monthly_payment, 2):,.2f} (Principal & Interest)",
    }

    return json.dumps(response, indent=2)


def check_mortgage_eligibility(customer_id: str, annual_income: float, monthly_debts: float, credit_score: int) -> str:
    """
    Check mortgage eligibility and pre-qualification amount.

    Args:
        customer_id: Customer's unique identifier
        annual_income: Annual gross income
        monthly_debts: Total monthly debt payments
        credit_score: Credit score (300-850)

    Returns:
        Mortgage eligibility assessment
    """
    monthly_income = annual_income / 12
    debt_to_income = (monthly_debts / monthly_income * 100) if monthly_income > 0 else 100

    # Determine eligibility
    eligible = credit_score >= 620 and debt_to_income <= 43

    # Calculate max loan amount (rough estimate)
    max_monthly_payment = monthly_income * 0.28  # 28% front-end ratio
    max_loan_amount = max_monthly_payment * 12 * 30 / 0.07  # Rough estimate at 7% rate

    response = {
        "status": "success",
        "customer_id": customer_id,
        "eligibility": {
            "eligible": eligible,
            "confidence": "high" if credit_score >= 740 else "medium" if credit_score >= 670 else "low",
            "credit_score": credit_score,
            "credit_tier": "Excellent"
            if credit_score >= 740
            else "Good"
            if credit_score >= 670
            else "Fair"
            if credit_score >= 620
            else "Poor",
        },
        "financial_ratios": {
            "monthly_income": round(monthly_income, 2),
            "monthly_debts": monthly_debts,
            "debt_to_income_ratio": round(debt_to_income, 2),
            "max_dti_allowed": 43.0,
            "front_end_ratio": 28.0,
            "back_end_ratio": 36.0,
        },
        "pre_qualification": {
            "estimated_max_loan": round(max_loan_amount, 2),
            "estimated_max_home_price": round(max_loan_amount * 1.2, 2),
            "recommended_down_payment": round(max_loan_amount * 0.2, 2),
            "estimated_monthly_payment": round(max_monthly_payment, 2),
        },
        "next_steps": [
            "Complete full mortgage application",
            "Provide income verification documents",
            "Schedule home appraisal",
            "Review loan options with mortgage specialist",
        ]
        if eligible
        else [
            "Improve credit score to at least 620",
            "Reduce monthly debt obligations",
            "Increase income or consider co-borrower",
            "Consult with financial advisor",
        ],
        "message": f"You are {'pre-qualified' if eligible else 'not currently eligible'} for a mortgage. Estimated max loan: ${round(max_loan_amount, 2):,.2f}",
    }

    return json.dumps(response, indent=2)


def get_mortgage_application_status(application_id: str) -> str:
    """
    Check the status of a mortgage application.

    Args:
        application_id: The mortgage application ID

    Returns:
        Current status of the mortgage application
    """
    response = {
        "status": "success",
        "application_id": application_id,
        "application_status": "Under Review",
        "submitted_date": "2024-02-15",
        "last_updated": "2024-03-01",
        "progress": {
            "application_submitted": {"status": "completed", "date": "2024-02-15"},
            "document_verification": {"status": "completed", "date": "2024-02-20"},
            "credit_check": {"status": "completed", "date": "2024-02-22", "score": 750},
            "income_verification": {"status": "completed", "date": "2024-02-25"},
            "appraisal_ordered": {"status": "in_progress", "date": "2024-03-01"},
            "underwriting": {"status": "pending", "date": None},
            "final_approval": {"status": "pending", "date": None},
        },
        "loan_details": {
            "loan_amount": 280000,
            "property_address": "123 Main St, Seattle, WA 98101",
            "loan_type": "30-Year Fixed",
            "interest_rate": 6.875,
        },
        "pending_items": [
            "Home appraisal (scheduled for 2024-03-05)",
            "Final employment verification",
        ],
        "estimated_closing_date": "2024-03-20",
        "loan_officer": {
            "name": "Sarah Johnson",
            "phone": "555-0123",
            "email": "sarah.johnson@bank.com",
        },
        "message": "Your application is under review. Appraisal scheduled for March 5th.",
    }

    return json.dumps(response, indent=2)


# ============================================================================
# MCP Server Configuration
# ============================================================================

TOOLS = [
    Tool(
        name="get_mortgage_rates",
        description="Get current mortgage rates and loan products",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="calculate_mortgage_payment",
        description="Calculate monthly mortgage payment",
        inputSchema={
            "type": "object",
            "properties": {
                "loan_amount": {"type": "number", "description": "Total loan amount"},
                "interest_rate": {
                    "type": "number",
                    "description": "Annual interest rate (e.g., 6.5 for 6.5%)",
                },
                "loan_term_years": {
                    "type": "integer",
                    "description": "Loan term in years (e.g., 30)",
                },
                "down_payment": {
                    "type": "number",
                    "description": "Down payment amount",
                    "default": 0,
                },
            },
            "required": ["loan_amount", "interest_rate", "loan_term_years"],
        },
    ),
    Tool(
        name="check_mortgage_eligibility",
        description="Check mortgage eligibility and pre-qualification amount",
        inputSchema={
            "type": "object",
            "properties": {
                "customer_id": {
                    "type": "string",
                    "description": "Customer's unique identifier",
                },
                "annual_income": {
                    "type": "number",
                    "description": "Annual gross income",
                },
                "monthly_debts": {
                    "type": "number",
                    "description": "Total monthly debt payments",
                },
                "credit_score": {
                    "type": "integer",
                    "description": "Credit score (300-850)",
                },
            },
            "required": [
                "customer_id",
                "annual_income",
                "monthly_debts",
                "credit_score",
            ],
        },
    ),
    Tool(
        name="get_mortgage_application_status",
        description="Check the status of a mortgage application",
        inputSchema={
            "type": "object",
            "properties": {
                "application_id": {
                    "type": "string",
                    "description": "The mortgage application ID",
                }
            },
            "required": ["application_id"],
        },
    ),
]

TOOL_FUNCTIONS = {
    "get_mortgage_rates": get_mortgage_rates,
    "calculate_mortgage_payment": calculate_mortgage_payment,
    "check_mortgage_eligibility": check_mortgage_eligibility,
    "get_mortgage_application_status": get_mortgage_application_status,
}


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools"""
    return TOOLS


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Call a tool with the given arguments"""
    if name not in TOOL_FUNCTIONS:
        raise ValueError(f"Unknown tool: {name}")

    try:
        func = TOOL_FUNCTIONS[name]
        result = func(**arguments)
        return [TextContent(type="text", text=result)]
    except Exception as e:
        logger.error(f"Error calling tool {name}: {e}")
        raise


async def main():
    """Run the MCP server"""
    logger.info("Starting Mortgage Tools MCP Server")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
