# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Credit Analyst specialist — assesses corporate creditworthiness.

Registered in AgentCore Registry as the `credit-risk-analysis` agent skill.
The SKILL definition below is the single source of truth: the orchestrator reads
it to scope the agent's tools, and scripts/seed_registry.py reads it to build the
Registry record.
"""

from agents.skill import Skill

SYSTEM_PROMPT = """You are a Senior Credit Analyst at a corporate bank, assessing \
creditworthiness for KYC onboarding.

Use your tools to gather evidence before forming a view:
- get_customer_profile: establish the entity, its industry, and revenue scale
- credit_bureau_report: credit score, facilities, payment history, financial ratios

Analyse:
1. Repayment capacity — net income, current ratio, cash generation
2. Leverage — debt-to-equity against industry norms
3. Payment discipline — on-time rate, late payments, defaults
4. Facility utilization — headroom on existing lines
5. Credit inquiry velocity — many recent inquiries can signal distress

Scoring guidance (0-100, where HIGHER means HIGHER RISK):
- 0-25   Strong: investment-grade ratios, clean payment record, low leverage
- 26-50  Acceptable: adequate capacity, minor blemishes, manageable leverage
- 51-75  Elevated: thin margins or losses, high leverage, repeated late payments
- 76-100 Severe: defaults, negative equity, unsustainable debt service

Be specific and quantitative. Cite the actual figures you relied on — a reviewer must \
be able to trace every claim back to the data.

Return ONLY a JSON object in this exact shape, with no prose before or after:
{
  "score": <integer 0-100>,
  "level": "<low|medium|high|critical>",
  "factors": ["<specific finding citing figures>", ...],
  "recommendations": ["<actionable mitigation>", ...],
  "narrative": "<2-3 sentence assessment>"
}"""

# Catalog documentation. Frontmatter and the "Tools required" section are
# generated from the fields below, so they cannot drift from the code.
_BODY = """# Credit Risk Analysis

Evaluates a corporate customer's ability to service credit obligations as part of
KYC onboarding.

## When to use

Use during corporate onboarding or an annual credit review, when a lending
decision needs a defensible, evidence-backed risk score.

## Inputs

- Corporate customer identifier
- Audited financial statements (assets, liabilities, net income)
- Credit bureau report (score, rating, existing facilities)
- Payment history (on-time, late, defaults)

## Method

1. Repayment capacity — net income and current ratio
2. Leverage — debt-to-equity against industry norms
3. Payment discipline — on-time rate, late payments, defaults
4. Facility utilization — headroom on existing lines
5. Inquiry velocity — recent credit inquiries as a distress signal

## Outputs

- `score` — 0-100, higher means higher risk
- `level` — low | medium | high | critical
- `factors` — specific findings citing figures
- `recommendations` — actionable mitigations"""

SKILL = Skill(
    name="credit-risk-analysis",
    summary=(
        "Assesses corporate creditworthiness from audited financials, credit "
        "bureau data, and payment history, producing a 0-100 credit risk score "
        "with contributing factors and mitigations."
    ),
    record_description=(
        "Agent skill: corporate credit risk analysis producing a 0-100 score "
        "from financials, bureau data, and payment history."
    ),
    tools=("get_customer_profile", "credit_bureau_report"),
    system_prompt=SYSTEM_PROMPT,
    body=_BODY,
)
