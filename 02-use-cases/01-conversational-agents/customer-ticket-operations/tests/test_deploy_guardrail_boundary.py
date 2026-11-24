"""Named-profile Terraform subprocesses cannot inherit a foreign Bedrock bearer token."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "target,operation",
    [
        ("plan-guardrails", "plan"),
        ("deploy-guardrails", "apply"),
        ("plan-agent", "plan"),
        ("plan-evals", "plan"),
    ],
)
def test_actual_make_recipe_unsets_only_child_bearer(monkeypatch, tmp_path, target, operation):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "aws").write_text("#!/bin/sh\nprintf 'offline-account\\n'\n")
    (fake_bin / "terraform").write_text(
        "#!/bin/sh\n"
        'test "${AWS_BEARER_TOKEN_BEDROCK+x}" != x || exit 42\n'
        'printf \'%s\\n\' "$1" >> "$TF_SENTINEL_LOG"\n'
    )
    for command in ("aws", "terraform"):
        (fake_bin / command).chmod(0o755)
    log = tmp_path / "commands.log"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "AWS_BEARER_TOKEN_BEDROCK": "untrusted-parent-sentinel",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_ACCESS_KEY_ID": "offline-only",
        "AWS_SECRET_ACCESS_KEY": "offline-only",
        "TF_SENTINEL_LOG": str(log),
    }
    result = subprocess.run(
        ["make", "--no-print-directory", "-s", target, "PROFILE=offline"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert log.read_text().splitlines() == ["init", operation]
    assert env["AWS_BEARER_TOKEN_BEDROCK"] == "untrusted-parent-sentinel"
    assert "AWS_BEARER_TOKEN_BEDROCK" not in result.stdout + result.stderr


def test_guardrail_owner_postcondition_precedes_pointer_publication():
    root = ROOT / "infra/iac/roots/guardrails"
    guardrail = (root / "main.tf").read_text()
    pointer = (root / "outputs.tf").read_text()
    provider = (root / "provider.tf").read_text()
    expected = "arn:" + "aws:bedrock:${var.region}:${var.account_id}:guardrail/"
    assert "allowed_account_ids = [var.account_id]" in provider
    assert 'self.guardrail_arn == "' + expected + '${self.guardrail_id}"' in guardrail
    assert 'aws_bedrock_guardrail.prompt_attack.guardrail_arn == "' + expected in pointer
    assert "precondition" in pointer and "postcondition" in guardrail
    # Resolve the actual HCL conditions against a local mock provider's output;
    # no AWS read or Terraform provider download is involved.
    resource_template = re.search(
        r'condition\s*=\s*self.guardrail_arn == "([^"]+)"', guardrail
    ).group(1)
    pointer_template = re.search(
        r'condition\s*=\s*aws_bedrock_guardrail.prompt_attack.guardrail_arn == "([^"]+)"',
        pointer,
    ).group(1)
    expected_arn = "arn:" + "aws:bedrock:us-east-1:target-account:guardrail/fixture"
    resource_expected = (
        resource_template.replace("${var.region}", "us-east-1")
        .replace("${var.account_id}", "target-account")
        .replace("${self.guardrail_id}", "fixture")
    )
    pointer_expected = (
        pointer_template.replace("${var.region}", "us-east-1")
        .replace("${var.account_id}", "target-account")
        .replace("${aws_bedrock_guardrail.prompt_attack.guardrail_id}", "fixture")
    )
    assert resource_expected == pointer_expected == expected_arn
    for provider_arn in (
        expected_arn,
        "arn:" + "aws:bedrock:us-east-1:foreign-account:guardrail/fixture",
        "arn:" + "aws:bedrock:us-west-2:target-account:guardrail/fixture",
        "arn:" + "aws:bedrock:us-east-1:target-account:guardrail/other",
    ):
        expected = provider_arn == expected_arn
        assert (provider_arn == resource_expected) is expected
        assert (provider_arn == pointer_expected) is expected
