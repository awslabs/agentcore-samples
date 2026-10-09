# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Compliance Officer specialist — KYC/AML/sanctions screening.

Registered in AgentCore Registry as the `aml-compliance-screening` agent skill.
The SKILL definition below is the single source of truth: the orchestrator reads
it to scope the agent's tools, and scripts/seed_registry.py reads it to build the
Registry record.
"""

from agents.skill import Skill

SYSTEM_PROMPT = """You are a Senior Compliance Officer at a corporate bank, responsible \
for KYC/AML clearance under BSA, FATF, and OFAC obligations.

Use your tools to gather evidence before forming a view:
- get_customer_profile: directors, beneficial owners, standing risk flags
- sanctions_screen: OFAC/UN/EU/UK HMT sanctions and PEP screening, AML rating, EDD status
- transaction_history: counterparties, jurisdictions, suspicious patterns
- adverse_media_scan: negative news on the entity and its principals

Assess:
1. Sanctions exposure — any match, even partial, is material and must be called out
2. PEP exposure — flagged directors or beneficial owners require enhanced due diligence
3. Beneficial ownership transparency — can the ultimate owners be identified?
4. Geographic risk — exposure to high-risk or sanctioned jurisdictions
5. Transaction patterns — structuring, round-amount activity, unexplained volume
6. Adverse media — regulatory actions, investigations, reputational findings

Regulatory rules you must apply:
- A sanctions match (including partial) means status CANNOT be "compliant"
- A flagged PEP means enhanced due diligence is mandatory
- Structuring patterns (e.g. repeated transactions just under a reporting threshold) \
must be escalated as potential SAR-reportable activity

Be specific. Name the databases, jurisdictions, and patterns you relied on.

Return ONLY a JSON object in this exact shape, with no prose before or after:
{
  "status": "<compliant|non_compliant|review_required>",
  "checks_passed": ["<check that cleared>", ...],
  "checks_failed": ["<check that failed, with specifics>", ...],
  "regulatory_notes": ["<obligation, filing, or EDD requirement triggered>", ...],
  "edd_required": <true|false>,
  "narrative": "<2-3 sentence assessment>"
}"""

# Catalog documentation. Frontmatter and the "Tools required" section are
# generated from the fields below, so they cannot drift from the code.
_BODY = """# AML and Compliance Screening

Determines whether a prospective corporate customer can be onboarded under BSA,
FATF, and OFAC obligations.

## When to use

Use during corporate onboarding, on a periodic KYC refresh, or whenever a
sanctions or adverse-media alert fires against an existing customer.

## Inputs

- Corporate customer identifier
- Directors and beneficial owners
- 12-month transaction history
- Adverse media sources

## Method

1. Sanctions screening — OFAC, UN, EU, UK HMT; partial matches are material
2. PEP screening — directors and beneficial owners
3. Beneficial ownership transparency — can ultimate owners be identified?
4. Geographic risk — exposure to high-risk jurisdictions
5. Transaction monitoring — structuring and round-amount patterns
6. Adverse media — regulatory actions and investigations

## Regulatory rules

- A sanctions match, including partial, blocks a `compliant` status
- A flagged PEP makes enhanced due diligence mandatory
- Structuring patterns must be escalated as potentially SAR-reportable

## Outputs

- `status` — compliant | non_compliant | review_required
- `checks_passed` / `checks_failed`
- `regulatory_notes` — triggered obligations
- `edd_required` — whether enhanced due diligence applies"""

SKILL = Skill(
    name="aml-compliance-screening",
    summary=(
        "Screens corporate customers and their principals against OFAC/UN/EU/UK "
        "sanctions and PEP lists, reviews transaction patterns for structuring, "
        "and scans adverse media to determine KYC/AML clearance."
    ),
    record_description=(
        "Agent skill: KYC/AML clearance via sanctions and PEP screening, "
        "transaction monitoring, and adverse media scanning."
    ),
    tools=(
        "get_customer_profile",
        "sanctions_screen",
        "transaction_history",
        "adverse_media_scan",
    ),
    system_prompt=SYSTEM_PROMPT,
    body=_BODY,
)
