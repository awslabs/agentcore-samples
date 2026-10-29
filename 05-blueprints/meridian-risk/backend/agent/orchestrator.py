# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""KYC assessment orchestrator.

Runs two specialists concurrently against the Gateway's MCP tools, then
synthesizes their findings into a single onboarding recommendation.

Flow:
  1. recall prior assessments for the customer (AgentCore Memory)
  2. run Credit Analyst and Compliance Officer in parallel, each with Gateway tools
  3. synthesize an overall risk score and APPROVE / REJECT / ESCALATE decision
  4. persist the outcome back to Memory for the next assessment
"""

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from strands import Agent

from agents.compliance_officer import SKILL as COMPLIANCE_SKILL
from agents.credit_analyst import SKILL as CREDIT_SKILL
from agents.skill import Skill
from lib.gateway import create_gateway_mcp_client, list_all_tools
from lib.inference import build_model, current_model_id, current_route
from lib.memory import (
    count_assessment_sessions,
    format_prior_context,
    recall_prior_assessments,
    record_assessment,
)

logger = logging.getLogger(__name__)

# Model selection lives in lib/inference.py, which owns the direct-vs-gateway
# decision and the per-route model id. Nothing here reads MODEL_ID directly.

# The guardrail id is set by Terraform on the runtime env so we can echo it
# back to the console — the model plane's proof-of-scope.
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID")
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION")

# Policy plane. The Gateway authorizes every tool call against Cedar policies
# before invoking the tool Lambda, so in ENFORCE mode each successful call in
# `tools_invoked` is also a request that passed authorization. Echoed here so
# the console can say that rather than leaving enforcement invisible.
POLICY_ENGINE_ID = os.environ.get("POLICY_ENGINE_ID")
POLICY_MODE = os.environ.get("POLICY_MODE")

# Each specialist's prompt and tool scope come from its Skill, which is also what
# scripts/seed_registry.py registers in the Registry — so the catalog cannot
# describe a tool set the orchestrator does not actually grant. Scoping tools per
# specialist keeps the division of labour visible in the demo's tool-call trace.

SYNTHESIS_PROMPT = """You are a Senior Risk Assessment Supervisor for corporate banking \
KYC onboarding. Two specialists have already gathered all evidence and reported it \
to you below. Your only job is to synthesize their findings into the final decision.

You have NO tools and MUST NOT attempt to call any. All data you need is in the \
report provided. Do not request additional information. Do not emit tool calls of \
any kind. Respond with the JSON object described below and nothing else.

Decision rules — apply strictly:
- Any sanctions match (including partial) => ESCALATE at minimum; never APPROVE
- Compliance status non_compliant => REJECT
- Compliance status review_required => ESCALATE
- Credit score > 75 (severe risk) => REJECT unless compliance is spotless and \
collateral fully mitigates
- Flagged PEP or required EDD => ESCALATE
- Detected structuring / suspicious transaction patterns => ESCALATE and note \
potential SAR obligation
- Only APPROVE when compliance is clear AND credit risk is 50 or below

Weight the overall risk score: compliance findings dominate credit findings, because a \
compliance failure is a regulatory bar to onboarding regardless of financial strength.

Return ONLY a JSON object in this exact shape, with no prose before or after:
{
  "overall_risk_score": <integer 0-100, higher = riskier>,
  "risk_level": "<low|medium|high|critical>",
  "recommendation": "<APPROVE|REJECT|ESCALATE>",
  "summary": "<3-4 sentence executive summary for the decision-maker>",
  "key_risks": ["<the risks that drove the decision>", ...],
  "conditions": ["<condition or requirement if approving or escalating>", ...],
  "regulatory_actions": ["<required filing, EDD, or review step>", ...]
}"""


def _extract_json(text: str, *, require: tuple[str, ...] = ()) -> dict[str, Any]:
    """Pull the intended JSON object out of a model response.

    Models wrap JSON in prose or ```json fences despite instructions, and some
    (notably reasoning models like DeepSeek) emit *several* fenced blocks or
    nested fences in one response. A naive first-match parse then returns an
    inner fragment that happens to be valid JSON but is not the object asked
    for — which surfaces downstream as a verdict with every field null rather
    than as a parse error.

    So gather every plausible candidate, newest/outermost first, and when
    `require` names keys, return the first candidate that actually carries them.
    Only fall back to "any parsable object" when none match, so a well-formed
    but wrong fragment never wins over the real object.

    Args:
        text: The raw model response.
        require: Keys the intended object must contain (e.g. the verdict's
            "recommendation"). Empty means accept the first parsable object.

    Raises:
        ValueError: If no parsable JSON object is present at all.
    """
    if not text:
        raise ValueError("Empty model response")

    # Some models (DeepSeek via the gateway's OpenAI-compat path) emit their
    # native tool-call tokens as plain text when they try to call a tool that
    # isn't bound. Those tokens carry braces and would corrupt the outermost
    # brace-span fallback, so strip any such block before scanning for JSON.
    text = re.sub(r"<｜tool.*?｜>.*?<｜tool.*?｜>", " ", text, flags=re.DOTALL)

    candidates: list[str] = []
    # Every fenced block, greedy within each fence, in document order. The last
    # fence is usually the model's final answer, so try fences last-first.
    fences = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates.extend(reversed(fences))
    # The outermost brace-delimited span — catches unfenced output and a single
    # object whose braces the fence regex's non-greedy body would clip.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    parsed: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        if require and all(key in obj for key in require):
            return obj
        parsed.append(obj)

    if parsed:
        # No candidate had the required keys (or none were required): return the
        # first parsable object rather than failing, matching prior behaviour.
        return parsed[0]

    raise ValueError(f"No parsable JSON in response: {text[:300]}")


def _filter_tools(all_tools: list, allowed: tuple[str, ...]) -> list:
    """Select the MCP tools a specialist may use.

    Gateway tool names arrive prefixed (e.g. "gateway___sanctions_screen"), so
    match on suffix rather than equality.
    """
    selected = [
        tool
        for tool in all_tools
        if any(getattr(tool, "tool_name", "").endswith(name) for name in allowed)
    ]
    if not selected:
        logger.warning(
            "No Gateway tools matched %s; available: %s",
            allowed,
            [getattr(t, "tool_name", "?") for t in all_tools],
        )
    return selected


class KYCOrchestrator:
    """Coordinates the Credit Analyst and Compliance Officer specialists."""

    def __init__(self) -> None:
        # No shared model instance. The gateway route's provider SDKs hold an
        # async client bound to the event loop that created it, and the
        # specialists run on separate ThreadPoolExecutor threads — each with
        # its own loop. Sharing one instance across them raises "Event loop is
        # closed" as soon as the second thread uses it. Building per call is
        # cheap (no network I/O in the constructor) and keeps each client on
        # the loop that will actually drive it.
        self.route = current_route()
        # The two routes name the same model differently — Bedrock uses
        # inference-profile ids, the Gateway connector uses its own catalog
        # ids — so report whichever one actually applies.
        self.model_id = current_model_id()

    def _run_specialist(
        self,
        name: str,
        skill: Skill,
        tools: list,
        all_tool_names: list[str],
        task: str,
    ) -> dict[str, Any]:
        """Run one specialist agent and parse its structured verdict.

        Args:
            name: Agent name, for logging and the UI panel.
            skill: The specialist's Skill — supplies the prompt, and its `name`
                is the Registry record this agent corresponds to.
            tools: The already-filtered MCP tools this specialist may call.
            all_tool_names: Every tool the Gateway advertised this run, so the
                withheld set can be reported as an observed difference.
            task: The assessment instruction.
        """
        logger.info(
            "[%s] skill=%s starting with %d of %d Gateway tool(s)",
            name,
            skill.name,
            len(tools),
            len(all_tool_names),
        )
        agent = Agent(
            name=name,
            system_prompt=skill.system_prompt,
            # Per-thread model: see __init__ on why this is not shared.
            model=build_model(),
            tools=tools,
        )
        response = agent(task)
        text = str(response)

        # Record which tools actually fired — the demo surfaces this as proof
        # that the Gateway was exercised rather than the model guessing.
        tool_calls = [
            block["toolUse"]["name"]
            for message in getattr(agent, "messages", [])
            for block in (message.get("content") or [])
            if isinstance(block, dict) and "toolUse" in block
        ]

        try:
            verdict = _extract_json(text)
        except ValueError:
            logger.exception("[%s] returned unparsable output", name)
            verdict = {"error": "unparsable specialist output", "raw": text[:1000]}

        # Scoping evidence for the demo. Every value here is *observed* rather
        # than restated: the granted list is read back off the tool objects
        # actually handed to the Agent, and the available count is what the
        # Gateway advertised on this run. So the UI can show that the skill
        # definition genuinely constrained the agent — if these were inert, every
        # specialist would show all of the Gateway's tools.
        granted = [getattr(tool, "tool_name", "?") for tool in tools]
        verdict["_skill"] = skill.name
        verdict["_tools_granted"] = granted
        verdict["_tools_available"] = len(all_tool_names)
        # Withheld = advertised minus granted, computed by difference rather than
        # restated, so it stays correct if the Gateway's catalog changes.
        verdict["_withheld"] = sorted(
            {name.split("___")[-1] for name in all_tool_names}
            - {name.split("___")[-1] for name in granted}
        )
        verdict["_tool_calls"] = tool_calls
        logger.info("[%s] done; tools used: %s", name, tool_calls)
        return verdict

    def run_assessment(
        self,
        customer_id: str,
        session_id: str,
        assessment_type: str = "full",
        context: str | None = None,
    ) -> dict[str, Any]:
        """Run a KYC assessment end to end.

        Args:
            customer_id: Corporate customer identifier, e.g. "CUST001".
            session_id: Identifier for this assessment run.
            assessment_type: "full", "credit_only", or "compliance_only".
            context: Optional extra instructions from the analyst.

        Returns:
            The synthesized assessment, including each specialist's findings and
            the Gateway tools they invoked.
        """
        customer_id = customer_id.upper()

        prior = recall_prior_assessments(customer_id)
        prior_context = format_prior_context(prior)

        task = f"Assess corporate customer {customer_id} for KYC onboarding."
        if context:
            task += f"\n\nAdditional analyst context: {context}"
        task += prior_context

        gateway = create_gateway_mcp_client()
        with gateway:
            # Paginated: the Gateway splits tools/list across pages per target,
            # and the first page is empty now that a non-MCP inference target
            # is attached. See list_all_tools.
            all_tools = list_all_tools(gateway)
            logger.info(
                "[GATEWAY] %d tool(s) discovered: %s",
                len(all_tools),
                [getattr(t, "tool_name", "?") for t in all_tools],
            )

            # What the Gateway actually advertised on this run. Each specialist's
            # grant is compared against this, so the UI's scoping evidence is an
            # observed difference rather than a restated constant.
            all_tool_names = [getattr(t, "tool_name", "?") for t in all_tools]

            jobs = {}
            if assessment_type in ("full", "credit_only"):
                jobs["credit"] = (
                    "credit_analyst",
                    CREDIT_SKILL,
                    _filter_tools(all_tools, CREDIT_SKILL.tools),
                )
            if assessment_type in ("full", "compliance_only"):
                jobs["compliance"] = (
                    "compliance_officer",
                    COMPLIANCE_SKILL,
                    _filter_tools(all_tools, COMPLIANCE_SKILL.tools),
                )

            # Specialists are independent — run them concurrently so the demo
            # shows genuine parallel multi-agent execution.
            with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                futures = {
                    key: pool.submit(
                        self._run_specialist,
                        name,
                        skill,
                        tools,
                        all_tool_names,
                        task,
                    )
                    for key, (name, skill, tools) in jobs.items()
                }
                findings = {key: future.result() for key, future in futures.items()}

        assessment = self._synthesize(customer_id, findings, prior_context)
        assessment["customer_id"] = customer_id
        assessment["session_id"] = session_id
        assessment["assessment_type"] = assessment_type
        assessment["credit_risk"] = findings.get("credit")
        assessment["compliance"] = findings.get("compliance")
        # len(prior) is what recall fed the model, which is capped at top_k —
        # not how many times this customer has been assessed. Report both so the
        # console can say "5 of 27" instead of implying 27 runs never happened.
        assessment["prior_assessment_count"] = len(prior)
        assessment["prior_assessment_total"] = count_assessment_sessions(customer_id)
        assessment["tools_invoked"] = sorted(
            {
                call
                for finding in findings.values()
                for call in finding.get("_tool_calls", [])
            }
        )
        # The denominator for the UI's scoping evidence: what the Gateway offered
        # this run, against which each specialist's grant can be compared.
        assessment["gateway_tools_available"] = len(all_tool_names)

        # Model-plane scoping evidence, symmetric to the tool-plane above.
        # Which route was used, which model was named, which guardrail
        # applied — surfaced so the demo can prove the LLM-gateway path
        # is not decorative.
        assessment["inference"] = {
            "route": self.route,
            "model_id": self.model_id,
            "guardrail_id": GUARDRAIL_ID,
            "guardrail_version": GUARDRAIL_VERSION,
        }

        # Policy-plane evidence. `authorized_calls` counts tool invocations that
        # the Gateway let through — under ENFORCE, a denied call raises before it
        # reaches the tool, so anything in tools_invoked was authorized. Reported
        # as an observed count rather than a restated config value, same as the
        # tool-scoping evidence above.
        assessment["policy"] = {
            "mode": POLICY_MODE,
            "engine_id": POLICY_ENGINE_ID,
            "authorized_calls": len(
                [
                    call
                    for finding in findings.values()
                    for call in finding.get("_tool_calls", [])
                ]
            ),
        }

        assessment["memory_event_id"] = record_assessment(
            customer_id, session_id, assessment
        )
        return assessment

    @staticmethod
    def _decision_view(finding: dict[str, Any]) -> dict[str, Any]:
        """The parts of a finding the supervisor should reason over.

        The `_`-prefixed keys (`_tool_calls`, `_tools_granted`, …) are UI scoping
        evidence and carry Gateway tool-call names like
        `gateway_kyc-tools___sanctions_screen`. Feeding those to the supervisor
        makes some connector models (DeepSeek) imitate the examples and emit
        their own native tool-call tokens instead of the verdict JSON — which
        then fails to parse and trips the ESCALATE fail-safe. The supervisor
        only needs the actual findings, so drop the internal fields.
        """
        return {k: v for k, v in finding.items() if not k.startswith("_")}

    def _synthesize(
        self, customer_id: str, findings: dict[str, Any], prior_context: str
    ) -> dict[str, Any]:
        """Combine specialist findings into a final recommendation."""
        report = f"Customer: {customer_id}\n\n"
        if "credit" in findings:
            report += (
                "=== CREDIT ANALYST FINDINGS ===\n"
                + json.dumps(self._decision_view(findings["credit"]), indent=2, default=str)
                + "\n\n"
            )
        if "compliance" in findings:
            report += (
                "=== COMPLIANCE OFFICER FINDINGS ===\n"
                + json.dumps(self._decision_view(findings["compliance"]), indent=2, default=str)
                + "\n\n"
            )
        report += prior_context

        supervisor = Agent(
            name="risk_supervisor",
            system_prompt=SYNTHESIS_PROMPT,
            model=build_model(),
        )
        text = str(supervisor(report))

        try:
            # Require the verdict's own fields so a stray fenced fragment from a
            # multi-block reasoning-model response cannot win over the real
            # object and yield an all-null verdict.
            return _extract_json(text, require=("recommendation", "overall_risk_score"))
        except ValueError:
            logger.exception("Synthesis returned unparsable output")
            # Fail safe: an unparsable synthesis must not read as an approval.
            return {
                "overall_risk_score": None,
                "risk_level": "unknown",
                "recommendation": "ESCALATE",
                "summary": (
                    "Automated synthesis failed to produce a structured decision. "
                    "Escalated for manual review."
                ),
                "key_risks": ["Automated synthesis failure"],
                "conditions": ["Manual review required"],
                "regulatory_actions": [],
                "synthesis_error": text[:1000],
            }
