# Copyright 2026 Attenu
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied. See the License for the specific language governing
# permissions and limitations under the License.

"""The AgentCore packaging shape, exercised without AWS.

The Runtime is not started here and no model is called: the entrypoint
function is invoked directly with a payload, and `build_session` is
substituted for one that supplies the scripted models. That covers the
handler contract — payload in, JSON-serialisable dict out — and leaves
the deployed path itself unverified, which the README says plainly.
"""

import json

import local_run
import pytest
import strands_attenu_guard
from agents import build_session
from permissions import ANALYST_NAME


@pytest.fixture
def scripted_entrypoint(monkeypatch, tmp_path):
    def scripted(*_args, **kwargs):
        return build_session(
            local_run.ScriptedModel(local_run.orchestrator_script()),
            local_run.ScriptedModel(local_run.analyst_script()),
            **kwargs,
        )

    monkeypatch.setattr(strands_attenu_guard, "build_session", scripted)
    monkeypatch.setattr(
        strands_attenu_guard,
        "LEDGER_PATH",
        str(tmp_path / "ledger.jsonl"),
    )
    return strands_attenu_guard.agent_invocation


def test_the_entrypoint_is_registered_on_the_runtime_app():
    assert strands_attenu_guard.app is not None
    assert callable(strands_attenu_guard.agent_invocation)


def test_the_entrypoint_returns_the_answer_and_the_decisions(
    scripted_entrypoint,
):
    response = scripted_entrypoint({"prompt": "Investigate the 5xx spike overnight."}, None)

    assert "result" in response
    assert response["ledger_events"] > 0
    assert [d["scope"] for d in response["denials"]] == ["logs.export"]
    assert response["denials"][0]["agent"] == ANALYST_NAME
    # The Runtime serialises whatever the handler returns.
    json.dumps(response)


def test_a_payload_without_a_prompt_is_handled(scripted_entrypoint):
    response = scripted_entrypoint({}, None)

    assert "result" in response
