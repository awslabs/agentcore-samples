"""Attach every registered evaluator to the online evaluation config.

The sample registers its evaluators through two separate paths and only one of
them wires the result into online evaluation:

  evaluators/scripts/deploy.py   creates the five code-based evaluator Lambdas
                                 and an online evaluation config containing them.

  evaluators/custom_evaluators.py creates the three LLM-as-a-judge evaluators and
                                 stops there. Nothing attaches them to the online
                                 config, so by default they never score live
                                 traffic.

That gap matters for drift detection specifically. The five code-based evaluators
are structural checks: schema validity, tool-contract ordering, PII scanning,
quoted price against a reference. A weaker model still satisfies all of them, so a
model change produces no signal on any of those streams. The three judges are the
ones that score semantic quality, which is what actually degrades. Without them
attached, the most common real cause of drift is invisible.

Usage:
  attach_evaluators.py --dry-run
  attach_evaluators.py
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List

import boto3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOG = logging.getLogger("attach-evaluators")

REGION = os.environ.get("AWS_REGION", "us-east-1")
AGENT_ROOT = Path(__file__).resolve().parent.parent.parent

# Service limit on evaluators per online evaluation config.
MAX_EVALUATORS = 10


def resolve_config_id(cp) -> str:
    from_env = os.environ.get("ONLINE_EVAL_CONFIG_ID", "")
    if from_env:
        return from_env
    out = AGENT_ROOT / "evaluators" / "scripts" / ".deploy_output.json"
    if out.exists():
        cid = json.loads(out.read_text()).get("onlineEvaluationConfigId", "")
        if cid:
            return cid
    raise SystemExit("Set ONLINE_EVAL_CONFIG_ID or run evaluators/scripts/deploy.py first.")


def list_all_evaluators(cp) -> Dict[str, str]:
    """Every evaluator visible in the account, keyed by base name."""
    found: Dict[str, str] = {}
    token = None
    while True:
        resp = cp.list_evaluators(**({"nextToken": token} if token else {}))
        for ev in resp.get("evaluators", []):
            eid = ev.get("evaluatorId", "")
            name = ev.get("evaluatorName") or (eid.rsplit("-", 1)[0] if eid else "")
            if name and eid:
                found[name] = eid
        token = resp.get("nextToken")
        if not token:
            break
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="show the change without applying it")
    args = parser.parse_args()

    sys.path.insert(0, str(AGENT_ROOT))
    from drift_detection.detector import config as cfg

    cp = boto3.client("bedrock-agentcore-control", region_name=REGION)
    config_id = resolve_config_id(cp)

    current = cp.get_online_evaluation_config(onlineEvaluationConfigId=config_id)
    attached = [e["evaluatorId"] for e in current.get("evaluators", [])]

    available = list_all_evaluators(cp)
    wanted = [c.evaluator for c in cfg.ALL_EVALUATORS]

    target: List[str] = list(attached)
    added: List[str] = []
    missing: List[str] = []

    for name in wanted:
        eid = available.get(name)
        if not eid:
            missing.append(name)
            continue
        if eid in target:
            continue
        if len(target) >= MAX_EVALUATORS:
            LOG.warning("Reached the %d evaluator limit; %s not attached", MAX_EVALUATORS, name)
            continue
        target.append(eid)
        added.append(f"{name} ({eid})")

    LOG.info("Config %s currently has %d evaluators", config_id, len(attached))
    if missing:
        LOG.warning("Not registered in this account: %s", ", ".join(missing))
    if not added:
        LOG.info("Nothing to add; all registered evaluators are already attached")
        print(json.dumps({"configId": config_id, "attached": len(target), "added": []}, indent=2))
        return 0

    LOG.info("Adding: %s", "; ".join(added))
    if args.dry_run:
        print(json.dumps({"configId": config_id, "wouldAdd": added}, indent=2))
        return 0

    # UpdateOnlineEvaluationConfig replaces the fields it is given, so the rule and
    # data source have to be passed through or they are reset.
    cp.update_online_evaluation_config(
        onlineEvaluationConfigId=config_id,
        rule=current["rule"],
        dataSourceConfig=current["dataSourceConfig"],
        evaluators=[{"evaluatorId": eid} for eid in target],
        evaluationExecutionRoleArn=current["evaluationExecutionRoleArn"],
    )

    after = cp.get_online_evaluation_config(onlineEvaluationConfigId=config_id)
    print(
        json.dumps(
            {
                "configId": config_id,
                "status": after.get("status"),
                "executionStatus": after.get("executionStatus"),
                "added": added,
                "notRegistered": missing,
                "evaluators": [e["evaluatorId"] for e in after.get("evaluators", [])],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
