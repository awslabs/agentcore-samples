"""
Intake Agent — conversational front-end for the claims pipeline.

Handles the multi-turn conversation with the policyholder (LLM).
Once it has all claim details, it calls `process_claim_tool` which triggers
the Strands Graph pipeline (Investigation + Memory → Adjudication → Router).
"""

import json
import logging

from agents.prompts import with_current_date
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from claims_graph import process_claim
from memory.config import AGENT_MODEL_ID
from schemas import TypedClaimSummary
from strands import Agent, tool

logger = logging.getLogger("claims-demo.intake_agent")


INTAKE_V2_PROMPT = """\
You are Claims Agent, a single insurance claims assistant that helps
policyholders file and process their claims.

CRITICAL — present as ONE assistant. The policyholder must NEVER know there are
multiple internal processing steps. This is a hard rule.
- NEVER mention "intake", "investigation", "adjudication", "pipeline",
  "agents", "specialists", "stages", "phases", "routing", or any internal step.
- Speak in the first person ("I need a few more details", "I've reviewed your
  claim", "I've approved your claim").
- Do NOT use emojis in any output. Use plain text formatting only.

YOUR WORKFLOW:
1. Collect all necessary claim details from the policyholder:
   - Policy number
   - Date of incident
   - Type of incident (water damage, auto collision, theft, fire, etc.)
   - Description of what happened
   - What was damaged (specific items/areas)
   - Estimated damage amount
   - How long between incident and filing (reporting timeline)
   - Whether a police/fire report was filed
   - Whether anyone was injured
   - Available documentation (photos, receipts, reports)
   - Contact information

2. If the policyholder provides most details upfront, confirm with them.
   If key details are missing, ask follow-up questions.

3. Once you have ALL required information, call process_claim with the
   structured details. The tool returns a structured outcome (DECISION,
   CUSTOMER REASON, NEXT STEPS).

4. Craft a warm, empathetic response to the policyholder based on the outcome.
   Present it as YOUR decision. Rules for communication:
   - Use the CUSTOMER REASON and NEXT STEPS to inform your message, but
     rewrite them naturally in your own words.
   - NEVER mention fraud, risk scores, claim IDs, pattern names, or any
     internal terminology.
   - For approvals: congratulate, state the amount, explain next steps.
   - For denials: be empathetic, give a brief general reason, explain their
     right to appeal.
   - For under review: reassure them, explain timeline expectations.
   - Keep it concise (3-5 sentences max). Do not over-explain.

IMPORTANT: When calling process_claim, map the policyholder's information to the
correct fields. For reporting_timeline, describe the gap between incident and
filing (e.g. "filed next day", "3 week delay", "same day"). For documentation,
list what evidence they have.

The tool returns a FINAL decision (APPROVED, DENIED, or ESCALATED). Once you
receive the result, communicate it to the policyholder and handle follow-up
questions conversationally. The decision is final — do NOT call process_claim
again. For ESCALATED: tell the policyholder their claim needs specialist review
and they will be contacted.
"""


def create_intake_agent(
    memory_id: str,
    memory_client,
    actor_id: str,
    session_id: str,
    region: str = "us-east-1",
    mode: str = "auto",
    reviews_api_url: str = "",
) -> Agent:
    """Create the v2 orchestrator: Intake Agent with process_claim tool."""

    @tool
    def process_claim_tool(
        policy_number: str,
        incident_type: str,
        incident_date: str,
        description: str,
        damage_description: str,
        estimated_amount: float,
        reporting_timeline: str,
        documentation: str,
        injuries: bool,
        police_report: bool,
        policyholder_name: str,
        contact_info: str = "",
    ) -> str:
        """Process a complete insurance claim through the evaluation pipeline.

        Call this ONLY when you have collected ALL required claim information
        from the policyholder. This evaluates the claim and returns the outcome.

        Args:
            policy_number: The policy number (e.g. HO-2024-1001).
            incident_type: Type of incident with cause detail (e.g. "electrical fire",
                "water damage from burst pipe", "auto collision", "theft break-in").
            incident_date: Date the incident occurred (YYYY-MM-DD).
            description: Full description of what happened.
            damage_description: What was damaged (specific items and areas).
            estimated_amount: Total estimated damage in dollars (number only).
            reporting_timeline: Gap between incident and filing (e.g. "filed next day",
                "21 day delay", "same day").
            documentation: Comma-separated list of available evidence (e.g.
                "fire department report, photos, receipts").
            injuries: Whether anyone was injured.
            police_report: Whether a police or fire department report was filed.
            policyholder_name: The policyholder's full name.
            contact_info: Phone, address, or other contact details.

        Returns:
            The claim decision/outcome message to relay to the policyholder.
        """
        doc_list = [d.strip() for d in documentation.split(",") if d.strip()]

        claim = TypedClaimSummary(
            policy_number=policy_number,
            incident_type=incident_type,
            incident_date=incident_date,
            description=description,
            damage_description=damage_description,
            estimated_amount=estimated_amount,
            reporting_timeline=reporting_timeline,
            documentation=doc_list,
            injuries=injuries,
            police_report=police_report,
            actor_id=actor_id,
            policyholder_name=policyholder_name,
            contact_info=contact_info,
        )

        return process_claim(
            claim=claim,
            memory_id=memory_id,
            memory_client=memory_client,
            session_id=session_id,
            mode=mode,
            reviews_api_url=reviews_api_url,
            region=region,
        )

    # STM session manager for conversation continuity
    memory_config = AgentCoreMemoryConfig(
        memory_id=memory_id,
        session_id=session_id,
        actor_id=actor_id,
        retrieval_config={},
        filter_restored_tool_context=True,
    )
    session_manager = AgentCoreMemorySessionManager(
        agentcore_memory_config=memory_config,
        region_name=region,
    )

    return Agent(
        model=AGENT_MODEL_ID,
        system_prompt=with_current_date(INTAKE_V2_PROMPT),
        tools=[process_claim_tool],
        session_manager=session_manager,
        state={"actor_id": actor_id, "session_id": session_id},
    )
