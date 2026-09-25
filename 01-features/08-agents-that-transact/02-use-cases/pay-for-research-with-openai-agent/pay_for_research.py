"""Three-agent financial research workflow with isolated payment authority."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agents import Agent, Model, ModelSettings, Runner, WebSearchTool, function_tool, trace
from bedrock_openai import configure_bedrock_openai
from dotenv import load_dotenv
from payment import X402PaymentClient

LEAD_INSTRUCTIONS = """Role: Research lead.

Goal: Produce an evidence-backed financial research brief by managing two bounded
specialists while retaining ownership of the final answer.

Workflow:
- Always call research_public_evidence first.
- Review its residual evidence gaps before considering premium evidence.
- Call research_premium_evidence only when it is available and a named, material
  gap remains. Pass that gap and the public evidence it should corroborate.
- Treat a payment failure or budget rejection as final. Never seek another merchant,
  trial URL, redirect, or payment workaround.
- A null payment_made means the payment outcome is unknown. Disclose it and stop;
  never describe it as a free request or request another payment.
- Do not claim that a specialist ran, a source was checked, or a payment occurred
  unless the corresponding specialist output says so.

Evidence and output:
- Cite URLs next to the claims they support.
- Label facts from public sources separately from facts from paid data.
- Distinguish direct evidence from inference and name material conflicts.
- Include: executive summary, evidence table, analysis, risks and limitations,
  paid-data ledger, and sources.
- Do not execute trades or present the brief as personalized investment advice.

Stop when the requested brief is supported by useful evidence, or when the
remaining evidence gap cannot be closed within the available tools and budget."""

PUBLIC_EVIDENCE_INSTRUCTIONS = """Role: Public evidence analyst.

Research the supplied financial question using public sources only.

- Use web search when it is available.
- If web search is unavailable, evaluate only evidence supplied in the request.
  Explicitly report that current public sources could not be retrieved. Do not
  invent citations or describe remembered information as newly verified evidence.
- Return a compact evidence report with claim-level URLs.
- Distinguish direct evidence from inference and note source dates.
- Name conflicts, stale observations, and material residual evidence gaps.
- Never recommend a purchase and never claim access to paid evidence.
- Do not provide personalized investment advice or execute trades."""

PREMIUM_EVIDENCE_INSTRUCTIONS = """Role: Premium evidence analyst.

Investigate the specific material evidence gap supplied by the research lead.
You are the only specialist with payment capability.

- Use fetch_approved_premium_source for the single source bound by the application.
  The tool intentionally accepts no URL argument.
- Never seek another merchant, path, redirect, trial, or workaround.
- Treat a payment failure or budget rejection as final.
- A null payment_made means the outcome is unknown; report it and do not retry.
- After a successful fetch, call payment_session_status.
- Return the source URL, evidence obtained, the gap it closes, conflicts with public
  evidence, payment outcome, and remaining budget.
- Do not execute trades or provide personalized investment advice."""


@dataclass(frozen=True)
class ResearchAgentTeam:
    """The manager and specialists, exposed for testing and inspection."""

    lead: Agent
    public_evidence: Agent
    premium_evidence: Agent | None


def _model_settings() -> ModelSettings:
    return ModelSettings(
        reasoning={"effort": os.getenv("OPENAI_REASONING_EFFORT", "medium")},
        verbosity="medium",
        parallel_tool_calls=False,
    )


def build_agent_team(
    payment_client: X402PaymentClient | None,
    *,
    approved_paid_url: str | None = None,
    require_payment_approval: bool = False,
    model: str | Model | None = None,
    include_web_search: bool = True,
) -> ResearchAgentTeam:
    """Build a manager-style team with payment authority isolated to one specialist."""
    resolved_model = model or os.getenv("BEDROCK_OPENAI_MODEL", "openai.gpt-5.5")
    public_tools = [WebSearchTool(search_context_size="medium")] if include_web_search else []
    public_evidence = Agent(
        name="Public evidence analyst",
        instructions=PUBLIC_EVIDENCE_INSTRUCTIONS,
        model=resolved_model,
        model_settings=_model_settings(),
        tools=public_tools,
    )

    lead_tools = [
        public_evidence.as_tool(
            tool_name="research_public_evidence",
            tool_description=(
                "Research the question using public sources and return cited evidence, "
                "conflicts, and residual gaps. Always call this specialist first."
            ),
        )
    ]
    premium_evidence = None

    if approved_paid_url:
        if payment_client is None:
            raise ValueError("payment_client is required when approved_paid_url is set")

        async def fetch_approved_premium_source() -> str:
            """Fetch the one premium source approved and bound by the application."""
            return await asyncio.to_thread(payment_client.fetch, approved_paid_url)

        async def payment_session_status() -> str:
            """Return the maximum and remaining AgentCore payment-session budget."""
            return await asyncio.to_thread(payment_client.session_status)

        premium_evidence = Agent(
            name="Premium evidence analyst",
            instructions=PREMIUM_EVIDENCE_INSTRUCTIONS,
            model=resolved_model,
            model_settings=_model_settings(),
            tools=[
                function_tool(
                    fetch_approved_premium_source,
                    needs_approval=require_payment_approval,
                ),
                function_tool(payment_session_status),
            ],
        )
        lead_tools.append(
            premium_evidence.as_tool(
                tool_name="research_premium_evidence",
                tool_description=(
                    "Investigate one named material evidence gap using the application-bound "
                    "premium source. Use only after public research leaves that gap."
                ),
            )
        )

    lead = Agent(
        name="Financial research lead",
        instructions=LEAD_INSTRUCTIONS,
        model=resolved_model,
        model_settings=_model_settings(),
        tools=lead_tools,
    )
    return ResearchAgentTeam(
        lead=lead,
        public_evidence=public_evidence,
        premium_evidence=premium_evidence,
    )


def build_agent(
    payment_client: X402PaymentClient | None,
    *,
    approved_paid_url: str | None = None,
    require_payment_approval: bool = False,
    model: str | Model | None = None,
    include_web_search: bool = True,
) -> Agent:
    """Return the research lead for callers that do not need to inspect the team."""
    return build_agent_team(
        payment_client,
        approved_paid_url=approved_paid_url,
        require_payment_approval=require_payment_approval,
        model=model,
        include_web_search=include_web_search,
    ).lead


def build_prompt(query: str, paid_url: str | None) -> str:
    paid_context = (
        "\nAn approved premium source is available through research_premium_evidence. "
        "Its exact URL is bound by the application and cannot be changed by an agent."
        if paid_url
        else "\nNo premium source was supplied; the premium specialist is unavailable."
    )
    return (
        f"Research request: {query}\n"
        f"{paid_context}\n\n"
        "Delegate public research first. Use premium evidence only if that work leaves "
        "a material gap. Record each specialist used, whether payment occurred, and what "
        "the paid evidence added."
    )


async def run_research(
    query: str,
    *,
    paid_url: str | None = None,
    require_payment_approval: bool = False,
    approve_interactively: bool = True,
) -> str:
    payment_client = X402PaymentClient.from_env() if paid_url else None
    runtime = configure_bedrock_openai()
    agent = build_agent(
        payment_client,
        approved_paid_url=paid_url,
        require_payment_approval=require_payment_approval,
        model=runtime.model,
        include_web_search=runtime.include_web_search,
    )
    prompt = build_prompt(query, paid_url)

    with trace("AgentCore multi-agent paid financial research"):
        result = await Runner.run(agent, prompt)
        while result.interruptions:
            if not approve_interactively:
                return "Payment approval required; run paused before the paid tool call."

            print("\nThe team paused before spending. Pending paid tool call(s):")
            for interruption in result.interruptions:
                print(f"- {interruption}")
            approved = input("Approve all pending paid calls? [y/N] ").strip().lower() == "y"

            state = result.to_state()
            for interruption in result.interruptions:
                if approved:
                    state.approve(interruption)
                else:
                    state.reject(interruption)
            result = await Runner.run(agent, state)

    return str(result.final_output)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a budget-bounded OpenAI multi-agent financial research team")
    parser.add_argument("query", help="Research question, company, or market topic")
    paid_source = parser.add_mutually_exclusive_group()
    paid_source.add_argument(
        "--paid-url",
        default=os.getenv("PAID_RESEARCH_URL"),
        help="Exact approved x402 URL the agent may buy",
    )
    paid_source.add_argument(
        "--public-only",
        action="store_true",
        help="Run without a premium specialist, even when PAID_RESEARCH_URL is configured",
    )
    parser.add_argument(
        "--require-payment-approval",
        action="store_true",
        help="Pause for human approval before each paid tool call",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    load_dotenv(Path(__file__).with_name(".env"))
    args = _parser().parse_args(argv)
    output = asyncio.run(
        run_research(
            args.query,
            paid_url=None if args.public_only else args.paid_url,
            require_payment_approval=args.require_payment_approval,
        )
    )
    print(output)


if __name__ == "__main__":
    main()
