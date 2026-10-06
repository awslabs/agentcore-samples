"""Shared paths and helpers for the numbered workflow scripts."""

import json
from pathlib import Path
from typing import Any

SAMPLE_DIR = Path(__file__).resolve().parent
DATA_DIR = SAMPLE_DIR / "data"
OUTPUT_DIR = SAMPLE_DIR / "output"
EVALUATORS_DIR = SAMPLE_DIR / "evaluators"
AGENT_CONFIG_FILE = SAMPLE_DIR / "agent_config.json"

SCENARIOS_FILE = DATA_DIR / "scenarios.json"
SME_GROUND_TRUTH_FILE = DATA_DIR / "sme_ground_truth.json"
SESSIONS_FILE = OUTPUT_DIR / "sessions.json"
WORKBOOK_FILE = OUTPUT_DIR / "ground_truth_workbook.xlsx"
REVIEW_CASES_FILE = OUTPUT_DIR / "review_cases.json"
REVIEWS_DIR = OUTPUT_DIR / "reviews"
HUMAN_REFERENCE_FILE = OUTPUT_DIR / "human_reference.json"
EVALUATOR_IDS_FILE = OUTPUT_DIR / "evaluator_ids.json"
BATCH_RUNS_FILE = OUTPUT_DIR / "batch_runs.json"
JUDGE_RESULTS_FILE = OUTPUT_DIR / "judge_results.json"
COMPARISON_FILE = OUTPUT_DIR / "comparison.json"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
    print(f"  Wrote {path.relative_to(SAMPLE_DIR)}")


def load_agent_config() -> dict:
    if not AGENT_CONFIG_FILE.exists():
        raise SystemExit("agent_config.json not found. Run `python deploy_agent.py` first.")
    return read_json(AGENT_CONFIG_FILE)


DEMO_DIR = DATA_DIR / "demo"
DEMO_OUTPUT_DIR = OUTPUT_DIR / "demo"
ACCEPTANCE_GATES_FILE = DATA_DIR / "acceptance_gates.json"
