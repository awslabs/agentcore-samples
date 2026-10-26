"""
Inner-loop skill evaluation with the Strands Evals SDK.

`evaluate.py` runs the *outer loop*: it scores a deployed AgentCore runtime by
reconstructing trajectories from the runtime's CloudWatch spans. This script
runs the *inner loop* used during development and CI — it reruns the
skill-equipped HR Assistant in process, captures the trajectory with
`TracedHandler`, and scores it with the same three skill evaluators. No deployed
runtime is required; you only need Bedrock model access.

Evaluators (all from `strands_evals`):
  SkillSelectionAccuracyEvaluator     LLM judge     — was the right skill chosen?
  SkillInstructionFollowingEvaluator  LLM judge     — was the SKILL.md workflow followed?
  SkillInvoked(skill_name=...)        deterministic — was the named skill invoked?

The two judge evaluators use the SDK's default judge model
(`global.anthropic.claude-sonnet-4-6`) unless you pass `model=...`. SkillInvoked
does not call a model.

Usage:
    pip install -r requirements.txt
    python inner_loop_eval.py                     # run all scenarios
    python inner_loop_eval.py --validate-extraction  # preflight the trace format
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Reuse the shared HR Assistant tools and prompt so the in-process agent matches
# the deployed one. The skills live in this folder; point AgentSkills at them.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "utils"))

_SKILLS_DIR = Path(__file__).resolve().parent / "skills"

# name, expected skill (None = no-skill control), prompt
_SCENARIOS = [
    (
        "pto-balance",
        "pto-planning",
        "Use the pto-planning skill. What is the available PTO balance for employee EMP-001?",
    ),
    (
        "health-benefits",
        "benefits-advisor",
        "Use the benefits-advisor skill. What does Acme's health insurance cover?",
    ),
    (
        "no-skill-control",
        None,
        "Retrieve the January 2026 pay stub for employee EMP-001.",
    ),
]

_CONFIGURED_SKILLS = ["pto-planning", "benefits-advisor"]


def _build_hr_agent():
    """Return a fresh skill-equipped HR Assistant agent (invoked once per case)."""
    # Imported from the shared source so this agent matches the deployed runtime.
    from hr_assistant_agent import (
        _SKILLS_PROMPT,
        SYSTEM_PROMPT,
        get_benefits_summary,
        get_pay_stub,
        get_pto_balance,
        lookup_hr_policy,
        submit_pto_request,
    )
    from strands import Agent, AgentSkills
    from strands.models import BedrockModel

    return Agent(
        model=BedrockModel(model_id="us.amazon.nova-lite-v1:0"),
        tools=[get_pto_balance, submit_pto_request, lookup_hr_policy, get_benefits_summary, get_pay_stub],
        system_prompt=SYSTEM_PROMPT + _SKILLS_PROMPT,
        plugins=[AgentSkills(skills=[str(_SKILLS_DIR)])],
    )


def _evaluators_for(expected_skill):
    """Skill evaluators for one scenario; SkillInvoked is bound to the expected skill."""
    from strands_evals.evaluators import (
        SkillInstructionFollowingEvaluator,
        SkillInvoked,
        SkillSelectionAccuracyEvaluator,
    )

    evaluators = [SkillSelectionAccuracyEvaluator(), SkillInstructionFollowingEvaluator()]
    if expected_skill:
        evaluators.append(SkillInvoked(skill_name=expected_skill))
    else:
        # Control: assert each deployed skill stayed out of the trajectory (deterministic 0.0).
        # Multiple instances of the same evaluator need a unique name within an experiment.
        evaluators.extend(SkillInvoked(skill_name=s, name=f"SkillInvoked[{s}]") for s in _CONFIGURED_SKILLS)
    return evaluators


def _validate_extraction():
    """Preflight the trace format before a large run.

    An unsupported trace format is not always a parse error, so the SDK guidance
    is to treat extraction validation as part of evaluator setup: rerun one case
    and confirm the offered and selected skills are recovered from the trajectory.
    """
    from strands_evals import Case, TracedHandler, eval_task
    from strands_evals.extractors import extract_selected_skills, parse_available_skills

    handler = TracedHandler()

    @eval_task(handler)
    def task(case):
        return _build_hr_agent()

    name, _, prompt = _SCENARIOS[0]
    # The decorated task returns {"output": ..., "trajectory": Session}.
    trajectory = task(Case(name=name, input=prompt))["trajectory"]

    print(f"Extraction preflight (scenario: {name})")
    print("  available skills:", parse_available_skills(trajectory) or "<none parsed>")
    print("  selected skills :", extract_selected_skills(trajectory) or "<none parsed>")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inner-loop skill evaluation with Strands Evals")
    parser.add_argument(
        "--validate-extraction",
        action="store_true",
        help="Rerun one scenario and print the parsed skills to confirm the trace format",
    )
    parser.add_argument("--region", help="AWS region for Bedrock (default: boto3 session region)")
    args = parser.parse_args()

    if args.region:
        os.environ["AWS_REGION"] = args.region
        os.environ["AWS_DEFAULT_REGION"] = args.region

    try:
        import strands_evals
    except ImportError:
        print(
            "ERROR: the skill evaluators need the strands-agents-evals package. "
            "Install dependencies with: pip install -r requirements.txt"
        )
        return 1

    from strands_evals import Case, Experiment, TracedHandler, eval_task
    from strands_evals.types.evaluation_report import EvaluationReport

    print("=" * 88)
    print("HR Assistant — Inner-Loop Skill Evaluation (Strands Evals)")
    print("=" * 88)
    print(f"Skills : {', '.join(_CONFIGURED_SKILLS)}")

    if args.validate_extraction:
        print()
        _validate_extraction()
        return 0

    reports = []
    for name, expected_skill, prompt in _SCENARIOS:
        print(f"\n--- {name} (expected skill: {expected_skill or 'none'}) ---")

        # A fresh TracedHandler per scenario keeps each run's spans isolated.
        @eval_task(TracedHandler())
        def task(case):
            return _build_hr_agent()

        experiment = Experiment(
            cases=[Case(name=name, input=prompt)],
            evaluators=_evaluators_for(expected_skill),
        )
        report = experiment.run_evaluations(task)
        # display() renders once (static); run_display() would prompt on stdin and block in CI.
        report.display()
        reports.append(report)

    merged = EvaluationReport.flatten(reports)
    print("\n" + "=" * 88)
    print(f"Overall score across {len(merged.cases)} evaluator row(s): {merged.overall_score:.3f}")
    print(
        "Note: for the no-skill control, SkillInvoked scoring 0.0 (test_pass ✗) is the\n"
        "      expected result — the skills correctly stayed out of the trajectory."
    )
    print("=" * 88)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
