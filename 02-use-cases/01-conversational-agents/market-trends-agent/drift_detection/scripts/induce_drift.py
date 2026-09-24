"""Induce quality drift on the deployed agent, on demand.

Every trigger works by setting an environment variable on the AgentCore Runtime,
so switching one on or off takes an UpdateAgentRuntime call and no container
rebuild. That matters because the container build is the slowest step in this
sample by a wide margin.

Honesty about what this is
--------------------------
These triggers exist so drift can be demonstrated on a schedule instead of waited
for. Production drift does not arrive with a toggle, which is the entire reason
detection is needed. The triggers simulate causes; the detector knows nothing
about them and reads only the evaluator scores.

Why model_swap is the primary trigger
-------------------------------------
A foundation model update is the canonical cause of agent quality drift with no
corresponding deploy event: the code, the prompt, and the tools are all unchanged,
and quality moves anyway. It is also the honest way to run this demo. Nothing here
scripts which evaluator should degrade. The model is changed and the agent's own
evaluators are left to discover whatever actually moves, which means the result
can disagree with the prediction. A hand-written degraded prompt would make the
demo circular: break citations, then observe that the citation evaluator noticed.

Usage:
  induce_drift.py --list
  induce_drift.py --trigger model_swap
  induce_drift.py --clear
  induce_drift.py --status
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("induce-drift")

REGION = os.environ.get("AWS_REGION", "us-east-1")
AGENT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class Trigger:
    """One way to make the agent worse, and what it is expected to do."""

    name: str
    represents: str
    """The real-world cause this stands in for."""

    env: Dict[str, str]
    """Runtime environment variables to set."""

    expected_to_move: List[str] = field(default_factory=list)
    """Evaluators predicted to degrade. Recorded so the demo can report whether
    the prediction held. Being wrong here is a result, not a bug."""

    note: str = ""
    requires_agent_support: bool = False
    """True if the agent code must read this variable for the trigger to do
    anything. model_swap does not, which is why it is the default."""


# The registry. Adding a trigger means appending here; nothing else changes.
TRIGGERS: Dict[str, Trigger] = {
    t.name: t
    for t in [
        Trigger(
            name="model_swap",
            represents="Foundation model update, the canonical cause with no deploy event",
            env={"MODEL_ID": "us.anthropic.claude-3-haiku-20240307-v1:0"},
            expected_to_move=["mt_financial_professionalism", "mt_market_data_accuracy"],
            note=(
                "Swaps Claude Haiku 4.5 for the much older Claude 3 Haiku. Same prompt, "
                "same tools, same code, no container rebuild. "
                "This list is what was measured on a clean, fully warmed-up baseline "
                "(324 TRACE / 108 SESSION samples) plus 40 swap sessions. The weaker "
                "model degrades on judgment and tone, not mechanics: professionalism "
                "fell from a mean of 0.68 to 0.33 and market-data grounding fell from "
                "0.65 to 0.18, both confirmed. mt_stock_price_drift's own failure rate "
                "rose sharply (about 6% to 28% of turns) but the failures were "
                "scattered rather than clustered, so the memoryless method never "
                "confirmed it, a real near-miss rather than a null result. The tool "
                "contract, PII, schema, and personalization streams stayed flat: a "
                "weaker model still calls the right tools and avoids leaking PII, it "
                "just gets sloppier about tone and grounding. Run shape_report.py "
                "before trusting any prediction here."
            ),
        ),
        Trigger(
            name="stale_prices",
            represents="Tool or retrieval decay: a data source silently goes stale",
            env={"STALE_PRICES": "1"},
            expected_to_move=["mt_stock_price_drift"],
            note=(
                "Makes the market-data tool return a fixed cached quote instead of a "
                "live one. Measured on 40 sessions: mt_stock_price_drift's own DRIFT "
                "rate rose from about 6% to 39% of turns, a real and large increase, "
                "but scattered rather than clustered, so it did not cross the "
                "persistence gate, the same near-miss shape as model_swap produces on "
                "this stream. mt_market_data_accuracy barely moved (0.65 healthy vs "
                "0.65 here): a frozen price is still a retrieved value, so the judge "
                "that checks whether figures are grounded in retrieved data has "
                "nothing to flag even though the source itself has gone stale. That is "
                "the coverage gap this trigger demonstrates: no evaluator here scores "
                "whether a quote is fresh, only whether it looks plausible."
            ),
            requires_agent_support=True,
        ),
        Trigger(
            name="skip_profile_step",
            represents="Prompt or orchestration regression",
            env={"SKIP_PROFILE_STEP": "1"},
            expected_to_move=["mt_workflow_contract_gsr"],
            note=(
                "Drops the broker-identification and profile tools from the bound "
                "tool list (and the corresponding prompt instructions), so the model "
                "cannot call them regardless of what it tries. Measured on 40 "
                "sessions: mt_workflow_contract_gsr went from 100% PASS to 100% "
                "PARTIAL, the detector confirmed after 5, and it is the cleanest "
                "signal of any trigger here because the failure is structural rather "
                "than probabilistic; there is no way for a session to pass by chance. "
                "mt_broker_personalization, predicted to move since personalization "
                "depends on a loaded profile, stayed flat instead. Being wrong is the "
                "point of recording a prediction: this agent's personalization judge "
                "apparently does not lean on the tool-loaded profile as much as the "
                "contract evaluator does on the tool call itself."
            ),
            requires_agent_support=True,
        ),
    ]
}

# Environment variables the agent needs in normal operation. Clearing a trigger
# restores exactly these, so a trigger cannot leave residue behind.
BASELINE_ENV: Dict[str, str] = {
    "MODEL_ID": "global.anthropic.claude-haiku-4-5-20251001-v1:0",
    # AgentCore Runtime routes spans to its own log group by default, but
    # AgentCore Evaluations reads aws/spans. Without this the agent still works
    # and simply stops being evaluated, which would look like a detector fault.
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS": "x-aws-log-group=aws/spans,x-aws-log-stream=default",
}


def resolve_runtime_id() -> str:
    from_env = os.environ.get("AGENT_RUNTIME_ID", "")
    if from_env:
        return from_env
    arn_file = AGENT_ROOT / ".agent_arn"
    if arn_file.exists():
        arn = arn_file.read_text().strip()
        if arn:
            return arn.split("/")[-1]
    raise SystemExit("Set AGENT_RUNTIME_ID or ensure .agent_arn exists in the project root.")


def _client():
    return boto3.client("bedrock-agentcore-control", region_name=REGION)


def get_runtime(cp, runtime_id: str) -> dict:
    return cp.get_agent_runtime(agentRuntimeId=runtime_id)


def apply_env(cp, runtime_id: str, env: Dict[str, str]) -> None:
    """Update the runtime's environment, preserving everything else about it.

    UpdateAgentRuntime replaces the whole configuration, so the artifact, role,
    network, and protocol have to be read back and passed through. Omitting any of
    them silently resets it.
    """
    current = get_runtime(cp, runtime_id)

    kwargs = {
        "agentRuntimeId": runtime_id,
        "agentRuntimeArtifact": current["agentRuntimeArtifact"],
        "roleArn": current["roleArn"],
        "networkConfiguration": current["networkConfiguration"],
        "environmentVariables": env,
    }
    for optional in ("protocolConfiguration", "description", "authorizerConfiguration"):
        if optional in current:
            kwargs[optional] = current[optional]

    resp = cp.update_agent_runtime(**kwargs)
    LOG.info("Runtime updated to version %s", resp.get("agentRuntimeVersion"))

    # Wait for READY so the next invocation actually runs the new configuration.
    for _ in range(40):
        status = get_runtime(cp, runtime_id).get("status")
        if status == "READY":
            LOG.info("Runtime READY")
            return
        if status in {"CREATE_FAILED", "UPDATE_FAILED"}:
            raise SystemExit(f"Runtime entered {status}")
        time.sleep(5)
    LOG.warning("Runtime did not report READY within the wait window")


def cmd_list() -> int:
    print("\nAvailable triggers\n")
    for t in TRIGGERS.values():
        flag = "  (requires agent support)" if t.requires_agent_support else ""
        print(f"  {t.name}{flag}")
        print(f"    represents : {t.represents}")
        print(f"    sets       : {json.dumps(t.env)}")
        print(f"    expected   : {', '.join(t.expected_to_move)}")
        print(f"    note       : {t.note}\n")
    return 0


def cmd_status(cp, runtime_id: str) -> int:
    current = get_runtime(cp, runtime_id)
    env = current.get("environmentVariables", {}) or {}
    active = [t.name for t in TRIGGERS.values() if all(env.get(k) == v for k, v in t.env.items())]
    print(
        json.dumps(
            {
                "runtimeId": runtime_id,
                "version": current.get("agentRuntimeVersion"),
                "status": current.get("status"),
                "environmentVariables": env,
                "activeTriggers": active or ["none"],
            },
            indent=2,
        )
    )
    return 0


def cmd_trigger(cp, runtime_id: str, name: str) -> int:
    if name not in TRIGGERS:
        raise SystemExit(f"Unknown trigger {name!r}. Options: {', '.join(TRIGGERS)}")
    trigger = TRIGGERS[name]

    if trigger.requires_agent_support:
        LOG.warning(
            "Trigger %s requires the agent code to read %s. If the agent does not "
            "read it, nothing will change and no drift will appear.",
            name,
            ", ".join(trigger.env),
        )

    env = dict(BASELINE_ENV)
    env.update(trigger.env)
    LOG.info("Applying trigger %s: %s", name, json.dumps(trigger.env))
    apply_env(cp, runtime_id, env)

    print(
        json.dumps(
            {
                "trigger": name,
                "represents": trigger.represents,
                "applied": trigger.env,
                "predictedToMove": trigger.expected_to_move,
                "nextStep": (
                    "Generate traffic, then watch the drift dashboard. Prediction is "
                    "recorded so the demo can report whether it held."
                ),
            },
            indent=2,
        )
    )
    return 0


def cmd_clear(cp, runtime_id: str) -> int:
    LOG.info("Restoring baseline environment")
    apply_env(cp, runtime_id, dict(BASELINE_ENV))
    print(json.dumps({"cleared": True, "environmentVariables": BASELINE_ENV}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="show the trigger registry")
    group.add_argument("--trigger", metavar="NAME", help="apply a trigger")
    group.add_argument("--clear", action="store_true", help="restore the baseline environment")
    group.add_argument("--status", action="store_true", help="show the runtime's current environment")
    args = parser.parse_args()

    if args.list:
        return cmd_list()

    runtime_id = resolve_runtime_id()
    cp = _client()

    if args.status:
        return cmd_status(cp, runtime_id)
    if args.clear:
        return cmd_clear(cp, runtime_id)
    return cmd_trigger(cp, runtime_id, args.trigger)


if __name__ == "__main__":
    sys.exit(main())
