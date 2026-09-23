"""
Shared mock data for the HR Assistant agent.

Used by all framework samples (Google ADK, Claude Agent SDK) to ensure
identical tool behavior and evaluation scenarios across frameworks.
"""

PTO_BALANCES = {
    "EMP-001": {"total_days": 15, "used_days": 5, "remaining_days": 10},
    "EMP-002": {"total_days": 15, "used_days": 12, "remaining_days": 3},
    "EMP-042": {"total_days": 20, "used_days": 7, "remaining_days": 13},
}

HR_POLICIES = {
    "pto": (
        "PTO Policy: Full-time employees accrue 15 days of PTO per year (20 days after 3 years). "
        "PTO requests must be submitted at least 2 business days in advance. "
        "Unused PTO up to 5 days rolls over to the next year. "
        "PTO cannot be taken in advance of accrual."
    ),
    "remote_work": (
        "Remote Work Policy: Employees may work remotely up to 3 days per week with manager approval. "
        "Core collaboration hours are 10am-3pm local time. "
        "A dedicated workspace with reliable internet (25 Mbps+) is required. "
        "Employees must be reachable via Slack and email during core hours."
    ),
    "parental_leave": (
        "Parental Leave Policy: Primary caregivers receive 16 weeks of fully paid parental leave. "
        "Secondary caregivers receive 6 weeks of fully paid parental leave. "
        "Leave may begin up to 2 weeks before the expected birth or adoption date. "
        "Benefits continue unchanged during parental leave."
    ),
    "code_of_conduct": (
        "Code of Conduct: All employees are expected to treat colleagues, customers, and partners "
        "with respect and professionalism. Harassment, discrimination, and retaliation of any kind "
        "are strictly prohibited. Violations should be reported to HR or via the anonymous hotline."
    ),
}

BENEFITS = {
    "health": (
        "Health Insurance: The company covers 90% of premiums for employee-only coverage and 75% "
        "for family coverage. Plans available: Blue Shield PPO, Kaiser HMO, and HDHP with HSA. "
        "Annual deductible: $500 (PPO), $0 (HMO), $1,500 (HDHP). "
        "Open enrollment is each November for the following calendar year."
    ),
    "dental": (
        "Dental Insurance: 100% coverage for preventive care (cleanings, X-rays). "
        "80% coverage for basic restorative care (fillings, extractions). "
        "50% coverage for major restorative care (crowns, bridges). "
        "Annual maximum benefit: $2,000 per person. Orthodontia lifetime maximum: $1,500."
    ),
    "vision": (
        "Vision Insurance: Annual eye exam covered in full. "
        "Frames or contacts allowance: $200 per year. "
        "Laser vision correction discount: 15% off at participating providers."
    ),
    "401k": (
        "401(k) Plan: The company matches 100% of employee contributions up to 4% of salary. "
        "An additional 50% match on the next 2% (total effective match up to 5%). "
        "Employees are eligible to contribute immediately; company match vests over 3 years. "
        "2026 IRS contribution limit: $23,500 (under 50), $31,000 (age 50+)."
    ),
    "life_insurance": (
        "Life Insurance: Basic life insurance of 2x annual salary provided at no cost. "
        "Employees may purchase supplemental coverage up to 5x salary during open enrollment. "
        "Accidental death and dismemberment (AD&D) coverage equal to basic life benefit is included."
    ),
}

PAY_STUBS = {
    ("EMP-001", "2025-12"): {
        "gross_pay": 8333.33,
        "federal_tax": 1458.33,
        "state_tax": 416.67,
        "social_security": 516.67,
        "medicare": 120.83,
        "health_premium": 125.00,
        "401k_contribution": 333.33,
        "net_pay": 5362.50,
        "period": "December 2025",
    },
    ("EMP-001", "2026-01"): {
        "gross_pay": 8333.33,
        "federal_tax": 1458.33,
        "state_tax": 416.67,
        "social_security": 516.67,
        "medicare": 120.83,
        "health_premium": 125.00,
        "401k_contribution": 333.33,
        "net_pay": 5362.50,
        "period": "January 2026",
    },
    ("EMP-042", "2026-01"): {
        "gross_pay": 10416.67,
        "federal_tax": 1875.00,
        "state_tax": 520.83,
        "social_security": 645.83,
        "medicare": 151.04,
        "health_premium": 200.00,
        "401k_contribution": 416.67,
        "net_pay": 6607.30,
        "period": "January 2026",
    },
}

SYSTEM_PROMPT = """You are a helpful HR Assistant for Acme Corp.

You help employees with:
- Checking PTO (paid time off) balances
- Submitting PTO requests
- Looking up HR policies (PTO, remote work, parental leave, code of conduct)
- Understanding employee benefits (health, dental, vision, 401k, life insurance)
- Retrieving pay stub information

Always use the available tools to answer questions accurately. Do not make up
policy details, benefit amounts, or pay information. Look them up.
Be concise, professional, and friendly."""

# --- Evaluation scenarios (shared across all framework samples) ---

EVAL_TURNS = [
    "What is the PTO balance for employee EMP-001?",
    "Please submit a PTO request for EMP-001 from 2026-07-14 to 2026-07-18.",
    "What is the company remote work policy?",
]

EXPECTED_RESPONSES = [
    "Employee EMP-001 has 10 remaining PTO days out of 15 total (5 days used).",
    "PTO request submitted for EMP-001 from 2026-07-14 to 2026-07-18. Request ID: PTO-2026-NNN.",
    "The company allows up to 3 days of remote work per week. Core hours are 10am-3pm.",
]

EXPECTED_TRAJECTORY = ["get_pto_balance", "submit_pto_request", "lookup_hr_policy"]

ASSERTIONS = [
    "Agent called get_pto_balance with employee_id=EMP-001",
    "Agent reported 10 remaining PTO days",
    "Agent submitted a PTO request and returned a request ID",
    "Agent described the remote work policy including 3 days/week and core hours",
]
