# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""U3 peer-agent. The newcomer greeter, a minimal Strands A2A agent on
AgentCore Runtime.

The peer is the newcomer in the arrival-signal walkthrough. It is deployed,
publishes an A2A 0.3 card through its registry record, and exposes one skill,
``greet`` (name in, "hello {name}" out). It is the
inverse of the orchestrator (U2):

  - It never self-registers, and there is no registration script here.
    U4's publisher/approver actors drive its registry record's lifecycle
    (create -> submit -> approve).
  - Its execution role needs zero registry permissions. It only receives
    inbound A2A calls and answers them. It never originates an outbound
    registry or agent call.

-- the deterministic, demonstrable proof that a real A2A call reached the
peer and the peer answered correctly.
"""

from __future__ import annotations

import json
import logging

from strands import Agent, tool

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def _log(step: str, **fields: object) -> None:
    """One structured JSON log line, matching U1's handler.py
    and U2's agent.py convention."""
    logger.info(json.dumps({"step": step, **fields}))


@tool
def greet(name: str) -> str:
    """Return exactly ``"hello {name}"`` for the given name.

    Takes one input (a name string) and returns the literal interpolation
    ``"hello {name}"`` with no wrapper, punctuation, extra fields, or LLM
    involvement. It is the end-to-end proof of a working A2A call.
    """
    result = f"hello {name}"
    _log("greet_invoked", name=name, result=result)
    return result


SYSTEM_PROMPT = """
You are the peer greeter agent for the AWS Agent Registry arrival-signal
sample. You expose exactly one tool, greet, which returns the literal string
"hello {name}" for a given name. Always use the greet tool to answer; return
its output verbatim with no additional text.
"""


def build_agent(model=None) -> Agent:
    """Construct the peer's Strands Agent with its single greet tool.

    The deployed entrypoint (main.py) injects the Bedrock model via
    load_model(); build_agent() stays callable with no model so the unit
    tests construct it without an AWS/model dependency (matching U2's
    build_agent() convention).
    """
    kwargs = {"name": "peer", "system_prompt": SYSTEM_PROMPT, "tools": [greet]}
    if model is not None:
        kwargs["model"] = model
    return Agent(**kwargs)
