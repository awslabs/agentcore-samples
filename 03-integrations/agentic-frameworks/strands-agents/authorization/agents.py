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

"""The agents, their tools, and the guard that binds them.

Nothing here imports `bedrock_agentcore`. The AgentCore entrypoint
(`strands_attenu_guard.py`) and the offline local run (`local_run.py`)
both build their session from this module, so the same permission model
is exercised on the laptop and on the Runtime.
"""

from typing import Any

from attenu_guard import Guard
from attenu_guard.adapters.strands import DelegationGuard
from permissions import (
    ANALYST_NAME,
    ORCHESTRATOR,
    ORCHESTRATOR_NAME,
    SCOPE_FOR,
    authority_for,
)
from strands import Agent, tool

# (tool_name, notable_argument) for every tool body that actually ran.
# The proof that a refusal happened *before* execution rather than after
# it. Demo scaffolding; drop it when you adapt this to your own tools.
EXECUTED: list[tuple[str, Any]] = []

# Bounds and lookups each tool checks against its own arguments below.
# attenu-guard decides whether an agent may call a tool at all, and under
# which ceilings (row limits, egress rank), before the body ever runs; it
# takes no view on whether a value the model supplied *within* that
# ceiling is itself well-formed. That's this file's job, same as it would
# be with no guard in front of it.
MAX_ROWS = 10_000
ALLOWED_ONCALL_CHANNELS = frozenset({"primary-oncall", "secondary-oncall", "escalation"})


def reset() -> None:
    EXECUTED.clear()


@tool
def read_logs(rows: int) -> str:
    """Read recent application log lines.

    Args:
        rows: how many lines to read.
    """
    EXECUTED.append(("read_logs", rows))
    if not isinstance(rows, int) or not (1 <= rows <= MAX_ROWS):
        return f"rejected: rows must be an integer between 1 and {MAX_ROWS}"
    return f"read {rows} lines: 3 spikes in 5xx between 02:10 and 02:40 UTC, all from the checkout service"


@tool
def export_findings(destination: str) -> str:
    """Write the findings to an external destination.

    Args:
        destination: where to write them, e.g. an S3 URI.
    """
    EXECUTED.append(("export_findings", destination))
    if not isinstance(destination, str) or not destination.startswith("s3://"):
        return "rejected: destination must be an s3:// URI"
    if len(destination) <= len("s3://"):
        return "rejected: destination must be an s3:// URI"
    return f"exported the findings to {destination}"


@tool
def page_oncall(channel: str, message: str) -> str:
    """Page the on-call engineer.

    Args:
        channel: which rota to page.
        message: what to tell them.
    """
    EXECUTED.append(("page_oncall", channel))
    if channel not in ALLOWED_ONCALL_CHANNELS:
        return f"rejected: unknown channel {channel!r}"
    if not isinstance(message, str) or not message.strip():
        return "rejected: message must be a non-empty string"
    return f"paged {channel}"


def build_session(
    orchestrator_model: Any = None,
    analyst_model: Any = None,
    *,
    task: str = "investigate the 5xx spike",
    audit_path: str | None = None,
) -> tuple[Agent, Agent, DelegationGuard]:
    """One orchestrator, one sub-agent reached as a tool, one guard.

    Build this per invocation, not once at import: the ledger, the
    delegation graph and the time-to-live all belong to a single run.

    `orchestrator_model` / `analyst_model` are `None` on the Runtime, so
    Strands uses its Bedrock default. The offline run passes a scripted
    model in their place.
    """
    analyst = Agent(
        name=ANALYST_NAME,
        description="Reads logs and explains what they show.",
        model=analyst_model,
        tools=[read_logs, export_findings, page_oncall],
        callback_handler=None,
    )
    orchestrator = Agent(
        name=ORCHESTRATOR_NAME,
        description="Handles operational questions end to end.",
        model=orchestrator_model,
        tools=[analyst.as_tool(name=ANALYST_NAME)],
        callback_handler=None,
    )

    guard = DelegationGuard(
        root_guard=Guard.issue(
            ORCHESTRATOR_NAME,
            ORCHESTRATOR,
            task=task,
            audit_path=audit_path,
        ),
        root_agent=orchestrator,
        scope_for=SCOPE_FOR,
        authority_for=authority_for,
    )
    # The guard is one hook provider, registered on every agent. The
    # sub-agent's own permissions do not exist yet: they are minted at the
    # moment of the hand-off, from the orchestrator's. Were the hook
    # missing from the sub-agent, its tool calls would go unchecked, so
    # registering it on every agent in the tree is not optional.
    for agent in (orchestrator, analyst):
        agent.hooks.add_hook(guard)

    require_guard(orchestrator, guard=guard)
    return orchestrator, analyst, guard


def require_guard(entry_agent: Agent, *, guard: DelegationGuard) -> None:
    """Refuse to run an entry agent the root permissions are not bound
    to. Cheap, and it turns a mis-wired guard into a startup failure
    rather than a run nobody was checking."""
    if guard.guard_for(entry_agent) is None:
        raise RuntimeError(f"attenu-guard holds no permissions for {entry_agent.name!r} — refusing to run unguarded")


def denials(guard: DelegationGuard) -> list[dict]:
    """The refusals in this run, read back off the ledger.

    Ledger entries name the chain node, not the agent, so the node names
    are recovered from the `root` and `spawn` events.
    """
    entries = guard.root_guard.audit_log().entries
    agent_of = {entry["node"]: entry["agent"] for entry in entries if entry["event"] in ("root", "spawn")}
    return [
        {
            "agent": agent_of.get(entry.get("node"), entry.get("node")),
            "tool": entry.get("tool"),
            "scope": entry.get("scope"),
            "reason": entry.get("reason"),
            "disposition": entry.get("disposition"),
        }
        for entry in entries
        if entry["event"] == "deny"
    ]
