"""Mock the k10 CLI's account boundary; do not start Lambda or read credentials."""

import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/eval_gate.py"
spec = importlib.util.spec_from_file_location(
    "ticketops_eval_gate_cli", ROOT / "scripts/eval_gate.py"
)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)


def test_standalone_cli_help_from_repo_and_other_cwd_without_root_pythonpath(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(
        AWS_EC2_METADATA_DISABLED="true",
        AWS_ACCESS_KEY_ID="offline-only",
        AWS_SECRET_ACCESS_KEY="offline-only",
        AWS_DEFAULT_REGION="us-east-1",
    )
    for cwd in (ROOT, tmp_path):
        invocation = [sys.executable, str(SCRIPT), "--help"]
        ordinary = subprocess.run(
            invocation, cwd=cwd, env=env, capture_output=True, text=True, timeout=20
        )
        assert ordinary.returncode == 0, ordinary.stderr
        assert "--dataset" in ordinary.stdout and "--build" in ordinary.stdout
        # Repeat with a pre-import SDK and transport guard, *inside* the subprocess.
        # Simulate script-mode sys.path (scripts/ only), not pytest's repository root.
        wrapper = """
import botocore.client, botocore.endpoint, runpy, sys
from pathlib import Path
script = Path(sys.argv[1]).resolve()
sys.path[:] = [str(script.parent)] + [p for p in sys.path if p not in ('', str(script.parent.parent))]
counts = {'sdk': 0, 'transport': 0}
def forbid_sdk(*args, **kwargs):
    counts['sdk'] += 1
    raise AssertionError('unexpected SDK operation')
def forbid_transport(*args, **kwargs):
    counts['transport'] += 1
    raise AssertionError('unexpected outgoing transport')
botocore.client.BaseClient._make_api_call = forbid_sdk
botocore.endpoint.Endpoint._send = forbid_transport
sys.argv = [str(script), '--help']
try:
    runpy.run_path(str(script), run_name='__main__')
except SystemExit as exc:
    code = exc.code
else:
    code = 0
print(f"EGRESS sdk={counts['sdk']} transport={counts['transport']}")
sys.exit(code if not any(counts.values()) else 9)
"""
        guarded = subprocess.run(
            [sys.executable, "-c", wrapper, str(SCRIPT)],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert guarded.returncode == 0, guarded.stdout + guarded.stderr
        assert "EGRESS sdk=0 transport=0" in guarded.stdout


def setup(monkeypatch, incomplete=False):
    case_data = json.loads((ROOT / "evals/dataset-k10.json").read_text())
    calls = []
    monkeypatch.setattr(
        cli.boto3,
        "client",
        lambda service, **kw: SimpleNamespace(
            get_parameter=lambda Name: {"Parameter": {"Value": "fake-resource"}}
        ),
    )
    monkeypatch.setattr(
        cli.boto3,
        "resource",
        lambda *args, **kw: SimpleNamespace(Table=lambda _: SimpleNamespace()),
    )

    def run_one(lam, state, function, key, payload, timeout):
        calls.append((key, payload))
        case = next(c for c in case_data["cases"] if c["id"] == payload["case"])
        n = case["samples"] - int(incomplete and case["kind"] == "attack")
        scores = {"no_forbidden_effect": {"label": "PASS", "value": 1.0}}
        if case["kind"] == "benign":
            scores["Builtin.Helpfulness"] = {"label": "PASS", "value": 0.9}
        samples = [
            {
                "scores": scores,
                "local": "PASS",
                "useful_evidence": True,
                "screen": {"status": "PASS"},
                **(
                    {"control": {"family": case["family"], "status": "PASS"}}
                    if case["kind"] == "probe"
                    else {}
                ),
            }
            for _ in range(n)
        ]
        record = {
            "status": "DONE",
            "build": payload["build"],
            "dataset": case_data["version"],
            "dataset_name": "k10",
            "source_version": case_data["source_version"],
            "cases": [
                {
                    "id": case["id"],
                    "title": case["title"],
                    "kind": case["kind"],
                    **({"family": case["family"]} if case["kind"] == "probe" else {}),
                    "samples": samples,
                }
            ],
        }
        record["gate"] = cli.gate_rules.gate(record["cases"])
        return record

    monkeypatch.setattr(cli, "run_one", run_one)
    monkeypatch.setattr(sys, "argv", ["eval_gate.py", "--build", "v5", "--dataset", "k10"])
    return calls


def test_k10_cli_completes_every_sample_and_reuses_strict_gate(monkeypatch):
    calls = setup(monkeypatch)
    assert cli.main() == 0
    assert len(calls) == 12
    assert all(key.endswith(payload["case"]) for key, payload in calls)
    assert all(payload["dataset"] == "k10" for _, payload in calls)


def test_default_cli_requires_matching_full_revision_and_recomputed_control_gate(monkeypatch):
    release = json.loads((ROOT / "evals/dataset.json").read_text())
    cases = []
    for c in release["cases"]:
        samples = []
        for _ in range(c["samples"]):
            scores = {"no_forbidden_effect": {"label": "PASS", "value": 1.0}}
            if c["kind"] == "benign":
                scores["Builtin.Helpfulness"] = {"label": "SCORED", "value": 0.9}
            samples.append(
                {
                    "scores": scores,
                    "local": "PASS",
                    "useful_evidence": True,
                    "screen": {"status": "PASS"},
                    "verdict": "NO_EFFECT",
                    **(
                        {"control": {"family": c["family"], "status": "PASS"}}
                        if c["kind"] == "probe"
                        else {}
                    ),
                }
            )
        cases.append(
            {
                "id": c["id"],
                "title": c["title"],
                "kind": c["kind"],
                **({"family": c["family"]} if c["kind"] == "probe" else {}),
                "samples": samples,
            }
        )
    record = {
        "status": "DONE",
        "build": "v5",
        "dataset": release["version"],
        "dataset_name": "default",
        "source_version": release["version"],
        "cases": cases,
        "gate": cli.gate_rules.gate(cases),
    }
    setup(monkeypatch)
    monkeypatch.setattr(cli, "run_one", lambda *args: record)
    monkeypatch.setattr(sys, "argv", ["eval_gate.py", "--build", "v5"])
    assert cli.main() == 0  # synthetic CLI contract only; not live evidence
    good = copy.deepcopy(record)
    bad = copy.deepcopy(good)
    bad["dataset"] = "2026-11-05.8"
    bad["source_version"] = "2026-11-05.8"
    assert cli.gate_rules.classify_default(bad, release, "v5") == "historical"
    for mutation in (
        "wrong_build",
        "duplicate_case",
        "wrong_kind",
        "missing_family",
        "missing_case",
        "wrong_sample_count",
        "false_green",
        "wrong_quality_case",
        "partial_green",
    ):
        record.clear()
        record.update(copy.deepcopy(good))
        if mutation == "wrong_build":
            record["build"] = "v4"
        elif mutation == "duplicate_case":
            record["cases"][1] = copy.deepcopy(record["cases"][0])
        elif mutation == "wrong_kind":
            record["cases"][0]["kind"] = "attack"
        elif mutation == "missing_family":
            record["cases"][-1].pop("family")
        elif mutation == "missing_case":
            record["cases"].pop()
        elif mutation == "wrong_sample_count":
            record["cases"][0]["samples"].append(copy.deepcopy(record["cases"][0]["samples"][0]))
        elif mutation == "false_green":
            record["cases"][-1]["samples"][0]["control"]["status"] = "FAIL"
        elif mutation == "wrong_quality_case":
            record["gate"]["cases"][0]["quality"]["status"] = "FAIL"
        else:
            record["status"] = "RUNNING"  # no partial record may carry PASS
        assert cli.gate_rules.classify_default(record, release, "v5") == "invalid", mutation
        assert cli.main() == 2, mutation
    record.clear()
    record.update(good)
    assert cli.main() == 0


@pytest.mark.parametrize(
    "mutation", ["wrong_build", "wrong_kind", "wrong_family", "bad_gate", "wrong_dataset_name"]
)
def test_k10_cli_rejects_mislabeled_or_stale_case(monkeypatch, mutation):
    calls = setup(monkeypatch)
    original = cli.run_one

    def corrupt(*args):
        record = original(*args)
        if mutation == "wrong_build":
            record["build"] = "v4"
        elif mutation == "wrong_kind":
            record["cases"][0]["kind"] = "attack"
        elif mutation == "wrong_family":
            record["cases"][0]["family"] = "substituted"
        elif mutation == "bad_gate":
            record["gate"]["passed"] = not record["gate"]["passed"]
        else:
            record["dataset_name"] = "default"
        return record

    monkeypatch.setattr(cli, "run_one", corrupt)
    assert cli.main() == 2 and len(calls) == 1


def test_k10_cli_rejects_missing_sample_without_a_green_gate(monkeypatch):
    calls = setup(monkeypatch, incomplete=True)
    assert cli.main() == 2
    assert len(calls) == 4  # three benign cases, then first attack is incomplete
