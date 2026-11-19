---
name: kyc-onboarding-assessment
description: >-
  Corporate KYC onboarding risk method — how to combine credit analysis and
  AML/compliance screening into one APPROVE / REJECT / ESCALATE decision with a
  0-100 risk score. Use when assessing a prospective corporate customer for
  account opening or a periodic KYC refresh.
---

# Corporate KYC Onboarding Assessment

Domain method for deciding whether a prospective corporate customer can be
onboarded, under BSA, FATF, and OFAC obligations. This skill describes *how* to
reason about the evidence; the KYC data tools (exposed through the gateway)
supply the evidence itself.

## When to use

Corporate onboarding, a periodic KYC refresh, or whenever a sanctions or
adverse-media alert fires against an existing customer.

## Evidence to gather (via the KYC tools)

Pull all of these before forming a view — a decision on partial evidence is
itself a finding to escalate, not an approval:

- **`get_customer_profile`** — legal entity, directors, beneficial owners,
  standing risk flags, KYC status.
- **`credit_bureau_report`** — rating, facilities, payment history, leverage and
  liquidity ratios.
- **`sanctions_screen`** — OFAC / UN / EU / UK HMT sanctions and PEP screening,
  AML risk rating, whether enhanced due diligence (EDD) is required.
- **`transaction_history`** — counterparties, geographic distribution, high-risk
  jurisdictions, suspicious patterns.
- **`adverse_media_scan`** — negative news on the entity and its principals.

## Method

1. **Sanctions exposure.** Any match — *including a partial match* — is
   material and must be called out. It blocks a clean compliance status.
2. **PEP exposure.** A flagged director or beneficial owner makes EDD mandatory
   before onboarding.
3. **Beneficial-ownership transparency.** Can the ultimate owners be identified?
   Opacity is a risk factor, not a neutral fact.
4. **Geographic risk.** Weight exposure to high-risk or sanctioned jurisdictions.
5. **Transaction patterns.** Flag structuring (repeated transactions just under a
   reporting threshold, e.g. amounts at $99,999), round-amount activity, and
   unexplained volume.
6. **Adverse media.** Regulatory actions, investigations, and reputational
   findings relevant to the banking relationship.
7. **Credit standing.** Rating, repayment capacity, leverage, and payment
   discipline — a thin or deteriorating credit profile raises the score but does
   not, by itself, block onboarding.

## Decision rules

- A sanctions match (including partial) means the customer **cannot** be
  approved without resolution — escalate or reject.
- A flagged PEP triggers **mandatory EDD**; do not approve until it is complete.
- A structuring pattern must be escalated as **potentially SAR-reportable**.
- **Compliance failures dominate credit ones**: a customer with strong credit but
  an unresolved sanctions/PEP/structuring finding is REJECT or ESCALATE, never
  APPROVE.
- If the assessment cannot be completed, return **ESCALATE** — never a silent
  APPROVE.

## Output

Return a single decision with its rationale:

- **decision** — `APPROVE` | `REJECT` | `ESCALATE`
- **risk_score** — 0-100 (higher is riskier)
- **key_factors** — the specific findings driving the decision, naming the
  databases, jurisdictions, and patterns relied on
- **obligations** — any triggered filing or EDD requirement (e.g. SAR, EDD)

## Cite specifics

Name what you relied on: the sanctions databases screened, the jurisdictions
involved, the transaction pattern observed, and the regulatory obligation
triggered (for example, 31 USC 5324 for structuring, or FATF Recommendation 12
for PEPs). A vague verdict is not auditable.
