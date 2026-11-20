"""Tests for how setup_insights builds the evaluation role's trust policy."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import setup_insights

ACCOUNT, REGION = "111122223333", "us-east-1"
EXTRA = {
    "Sid": "ExtraTrust",
    "Effect": "Allow",
    "Principal": {"Service": "example.service.test"},
    "Action": "sts:AssumeRole",
}


def test_default_trust_has_one_scoped_statement():
    trust, _ = setup_insights.evaluation_role_documents(ACCOUNT, REGION)
    assert len(trust["Statement"]) == 1
    statement = trust["Statement"][0]
    assert statement["Principal"] == {"Service": "bedrock-agentcore.amazonaws.com"}
    assert statement["Condition"]["StringEquals"]["aws:SourceAccount"] == ACCOUNT


def test_extra_statement_is_added_next_to_the_documented_one():
    trust, _ = setup_insights.evaluation_role_documents(ACCOUNT, REGION, EXTRA)
    assert len(trust["Statement"]) == 2
    assert trust["Statement"][0]["Principal"] == {"Service": "bedrock-agentcore.amazonaws.com"}  # kept, with conditions
    assert "Condition" in trust["Statement"][0]
    assert trust["Statement"][1] == EXTRA


def test_permissions_do_not_change_with_the_extra_statement():
    _, plain = setup_insights.evaluation_role_documents(ACCOUNT, REGION)
    _, with_extra = setup_insights.evaluation_role_documents(ACCOUNT, REGION, EXTRA)
    assert plain == with_extra


def test_permissions_cover_the_log_queries_the_evaluator_runs():
    _, permissions = setup_insights.evaluation_role_documents(ACCOUNT, REGION)
    actions = {
        a for s in permissions["Statement"] for a in (s["Action"] if isinstance(s["Action"], list) else [s["Action"]])
    }
    assert {"logs:StartQuery", "logs:GetQueryResults", "logs:StopQuery", "bedrock:InvokeModel"} <= actions


def test_load_trust_statement_reads_a_valid_file(tmp_path):
    path = tmp_path / "statement.json"
    path.write_text(json.dumps(EXTRA), encoding="utf-8")
    assert setup_insights.load_trust_statement(str(path)) == EXTRA


@pytest.mark.parametrize("content", [{"Effect": "Allow"}, ["not", "a", "statement"]])
def test_load_trust_statement_rejects_incomplete_input(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    with pytest.raises(SystemExit):
        setup_insights.load_trust_statement(str(path))
