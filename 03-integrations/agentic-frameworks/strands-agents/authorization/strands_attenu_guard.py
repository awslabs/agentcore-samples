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

"""The AgentCore Runtime entrypoint.

    agentcore configure -e strands_attenu_guard.py
    agentcore launch
    agentcore invoke '{"prompt": "Investigate the 5xx spike overnight."}'

The agents, the tools and the permission model all live in `agents.py`
and `permissions.py`, which import nothing from AWS. `local_run.py`
builds the same session with a scripted model, so the enforcement path
this file deploys is the one the offline run and the tests exercise.

The response carries the run's decisions alongside the answer, so a
caller sees what was refused without reading the container's logs.
"""

import os

from agents import build_session, denials, reset
from bedrock_agentcore.runtime import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

DEFAULT_PROMPT = 'No prompt found in the payload. Send a JSON object with a "prompt" key.'

# Where the ledger is written inside the container. AgentCore Runtime
# gives you a writable /tmp; point this at a mounted volume or ship the
# exported bundle onward if you need the record to outlive the session.
LEDGER_PATH = os.getenv("ATTENU_GUARD_LEDGER", "/tmp/attenu-guard.jsonl")


@app.entrypoint
def agent_invocation(payload, context):
    """Handler for agent invocation.

    A fresh session per invocation, on purpose: the ledger, the
    delegation graph and the time-to-live all belong to a single run, and
    sharing them across callers would blur whose decision was whose.
    """
    prompt = payload.get("prompt", DEFAULT_PROMPT)

    reset()
    orchestrator, _analyst, guard = build_session(task=prompt, audit_path=LEDGER_PATH)
    result = orchestrator(prompt)

    return {
        "result": str(result.message),
        "denials": denials(guard),
        "ledger_events": len(guard.root_guard.audit_log().entries),
    }


if __name__ == "__main__":
    app.run()
