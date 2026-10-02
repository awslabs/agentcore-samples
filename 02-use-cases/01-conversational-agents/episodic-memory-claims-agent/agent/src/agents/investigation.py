"""Investigation Agent — gathers factual evidence using 4 tools.

Called as a graph node. Produces a structured summary of policy status,
coverage determination, fraud risk, and claims history. Does NOT make
decisions — just reports facts.
"""

from agents.prompts import with_current_date
from memory.config import AGENT_MODEL_ID
from strands import Agent
from tools.claims_history import make_check_claims_history_tool
from tools.coverage_validator import make_validate_coverage_tool
from tools.fraud_check import make_check_fraud_indicators_tool
from tools.policy_lookup import make_lookup_policy_tool

INVESTIGATION_PROMPT = """\
You are the Claims Investigation Agent. You receive a claim summary and must
use your tools to gather factual evidence. You decide HOW to use the tools —
how to describe the incident for coverage validation, which dates to pass for
fraud checking, etc.

Your tools:
- lookup_policy(policy_number) — verify the policy is active, get coverage details
- check_claims_history(actor_id) — review prior claims for this policyholder
- check_fraud_indicators(actor_id, claim_type, incident_date, filing_date, claimed_amount) — assess fraud risk
- validate_coverage(policy_type, incident_type) — check if the incident is covered

IMPORTANT — coverage classification:
- When calling validate_coverage, pass the incident with its CAUSE/SOURCE.
  "water damage from burst pipe" vs "water damage from storm drain backup".
- Exclusions take precedence. If validate_coverage returns EXCLUDED, coverage is denied.

IMPORTANT — fraud check:
- For filing_date, use today's date (the claim is being filed now).
- Report fraud risk exactly as returned (LOW/MEDIUM/HIGH). Do NOT downgrade it.

After calling ALL FOUR tools, produce a clear structured summary:

POLICY: [status] | [type] | deductible: [amount] | limit: [amount]
COVERAGE: [COVERED/EXCLUDED/UNCERTAIN] — [matched term or reason]
FRAUD RISK: [LOW/MEDIUM/HIGH] (score [X]/100) — [flags if any]
CLAIMS HISTORY: [count] prior claim(s) — [brief details if any]

Do NOT make a final decision — that is not your role. Just report the facts.
Do NOT use emojis.
"""


def create_investigation_agent(session_id: str) -> Agent:
    return Agent(
        name="investigation",
        model=AGENT_MODEL_ID,
        system_prompt=with_current_date(INVESTIGATION_PROMPT),
        tools=[
            make_lookup_policy_tool(session_id),
            make_check_claims_history_tool(session_id),
            make_check_fraud_indicators_tool(session_id),
            make_validate_coverage_tool(session_id),
        ],
    )
