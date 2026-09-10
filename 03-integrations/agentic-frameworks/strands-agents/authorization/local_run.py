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

"""Run the same session the Runtime would, offline.

    python local_run.py

`ScriptedModel` is a `strands.models.Model` subclass that emits the
Bedrock-shaped stream events `strands.event_loop.streaming` consumes, so
this needs no AWS credentials, no model access and no network. Strands'
own agent loop, hooks and tool executor are the real ones; only the model
is substituted.

Exit code 0 if every expectation held, 1 otherwise.
"""

import json
import sys
import tempfile
import uuid
from collections.abc import AsyncIterable
from pathlib import Path
from typing import Any

import agents
from agents import build_session, denials
from attenu_guard import AuditLog, evidence
from attenu_guard.cli import main as attenu_guard_cli
from attenu_guard.wire import Ed25519Signer
from permissions import (
    ANALYST_NAME,
    GREEDY_REQUEST,
    ORCHESTRATOR,
    ORCHESTRATOR_NAME,
)
from strands.models import Model

QUESTION = "Investigate the 5xx spike overnight and write it up."

EXFIL_DESTINATION = "s3://not-our-bucket/findings.json"


class ScriptedModel(Model):
    """Replays a fixed script. Each step is ("tool", name, args) or
    ("text", body)."""

    def __init__(self, script: list[tuple]) -> None:
        self._script = list(script)
        self._i = 0

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> dict:
        return {}

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError("ScriptedModel has no structured output")
        yield  # makes this an async generator

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs) -> AsyncIterable[dict]:
        step = self._script[self._i] if self._i < len(self._script) else ("text", "(script exhausted)")
        self._i += 1

        yield {"messageStart": {"role": "assistant"}}
        if step[0] == "tool":
            _, name, args = step
            yield {
                "contentBlockStart": {
                    "start": {
                        "toolUse": {
                            "toolUseId": f"tu-{uuid.uuid4().hex[:8]}",
                            "name": name,
                        }
                    }
                }
            }
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(args)}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": step[1]}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}


def orchestrator_script() -> list[tuple]:
    return [
        ("tool", ANALYST_NAME, {"input": QUESTION}),
        ("text", "The analyst could not publish the findings."),
    ]


def analyst_script() -> list[tuple]:
    """A read it was delegated, then an export it was not."""
    return [
        ("tool", "read_logs", {"rows": 1500}),
        ("tool", "export_findings", {"destination": EXFIL_DESTINATION}),
        ("text", "5xx spike traced to the checkout service."),
    ]


def run(*, audit_path: str | None = None):
    """One invocation. Returns (result, guard, executed)."""
    agents.reset()
    orchestrator, _analyst, guard = build_session(
        ScriptedModel(orchestrator_script()),
        ScriptedModel(analyst_script()),
        task=QUESTION,
        audit_path=audit_path,
    )
    result = orchestrator(QUESTION)
    return result, guard, list(agents.EXECUTED)


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="attenu-guard-agentcore-"))
    ledger = workdir / "ledger.jsonl"

    print("1. one invocation: orchestrator -> log_analyst")
    _result, guard, executed = run(audit_path=str(ledger))

    orchestrator_guard = guard.guard_for_name(ORCHESTRATOR_NAME)
    analyst_guard = guard.guard_for_name(ANALYST_NAME)

    print("\n2. what each agent holds")
    print(f"    {ORCHESTRATOR_NAME}: {orchestrator_guard.authority}")
    print(f"    {ANALYST_NAME}: {analyst_guard.authority}")
    narrower = analyst_guard.is_narrower_than(orchestrator_guard)
    print(f"    sub-agent is narrower than orchestrator: {narrower}")

    print("\n3. the refusal")
    print(f"    tool bodies that ran: {executed}")
    for entry in denials(guard):
        print(f"    DENIED {entry}")
    body_ran = any(name == "export_findings" for name, _ in executed)
    refused = any(d["scope"] == "logs.export" for d in denials(guard))
    print(f"    nothing was written to {EXFIL_DESTINATION}: {not body_ran}")

    print("\n4. asking for more does not produce more")
    granted = ORCHESTRATOR.meet(GREEDY_REQUEST)
    print(f"    requested: {GREEDY_REQUEST}")
    print(f"    granted  : {granted}")
    met_down = granted.is_narrower_than(ORCHESTRATOR)
    print(f"    granted is narrower than orchestrator: {met_down}")

    print("\n5. the ledger, checked without this process")
    entries = guard.root_guard.audit_log().entries
    chain_ok, chain_err = AuditLog.verify(entries)
    print(f"    {len(entries)} events, hash chain: {chain_ok}")
    if not chain_ok:
        print(f"    {chain_err}")

    signer = Ed25519Signer.generate(kid="agentcore-sample")
    pubkey = signer.public_bytes_raw().hex()
    bundle_path = workdir / "evidence-bundle.json"
    bundle = evidence.export_bundle(guard.root_guard.audit_log(), signer)
    bundle_path.write_text(json.dumps(bundle, indent=2))

    print(f"    bundle: {bundle_path}")
    print("    verifying it with the packaged command:")
    print(f"      attenu-guard verify {bundle_path.name} --pubkey {pubkey[:16]}…")
    print("    ", end="")
    verify_rc = attenu_guard_cli(["verify", str(bundle_path), "--pubkey", pubkey])

    ok = refused and not body_ran and narrower and met_down and chain_ok and verify_rc == 0
    print("\nRESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
