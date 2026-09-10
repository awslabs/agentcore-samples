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

"""The permission model for this sample — written by hand, on purpose.

attenu-guard does not decide what a task needs. You declare three things
and it enforces them:

1. `ORCHESTRATOR`   — what the entry agent holds.
2. `SUB_AGENTS`     — what each sub-agent may *request*. The child gets
   the meet of the request and the parent, so a request can only ever
   shrink the child, never widen it.
3. `SCOPE_FOR`      — the scope, and the quantities the ceilings are
   measured against, that each tool call needs.

A sub-agent missing from `SUB_AGENTS` cannot be delegated to at all. A
tool missing from `SCOPE_FOR` resolves to `tool.<name>`, which no
permission set grants. Both fail closed.
"""

from attenu_guard import Authority, EgressRank, RowLimit
from attenu_guard.adapters.strands import ScopeRequest, scope_map

ORCHESTRATOR_NAME = "operations_assistant"
ANALYST_NAME = "log_analyst"

# --------------------------------------------------------------------
# 1. What the entry agent holds: read logs, export findings, page the
#    on-call engineer, and hand work to a sub-agent.
# --------------------------------------------------------------------
ORCHESTRATOR = Authority(
    scopes={"logs.read", "logs.export", "oncall.page", "agent.delegate"},
    ceilings=[RowLimit(50_000), EgressRank("any")],
    ttl=3600,
)

# --------------------------------------------------------------------
# 2. What the sub-agent may request: reads, bounded, no egress, and no
#    onward hand-off.
# --------------------------------------------------------------------
SUB_AGENTS = {
    ANALYST_NAME: Authority(
        scopes={"logs.read"},
        ceilings=[RowLimit(2_000), EgressRank("none")],
        ttl=900,
    ),
}

# A deliberately greedy request, used by the local run to show that
# asking for more than the parent holds does not produce more.
GREEDY_REQUEST = Authority(
    scopes={"logs.*", "iam.admin"},
    ceilings=[RowLimit(1_000_000), EgressRank("any")],
    ttl=999_999,
)


def authority_for(child_name: str, task: str) -> "Authority | None":
    """(sub-agent, task) -> the permissions it may request, or None to
    refuse the hand-off outright. Whatever this returns is only ever an
    input to the meet, so it cannot widen the child."""
    return SUB_AGENTS.get(child_name)


# --------------------------------------------------------------------
# 3. What each tool call needs. `unmapped="deny"` is what makes an
#    undeclared tool fail closed through the ordinary path, with a
#    reason code in the ledger, rather than as a special case in code.
# --------------------------------------------------------------------
SCOPE_FOR = scope_map(
    {
        "read_logs": lambda i: ScopeRequest("logs.read", {"rows": int(i["rows"])}),
        "export_findings": lambda i: ScopeRequest("logs.export", {"egress": "any"}),
        "page_oncall": lambda i: ScopeRequest("oncall.page", {"egress": "internal"}),
        # Calling the sub-agent IS the hand-off, so it is checked too.
        ANALYST_NAME: "agent.delegate",
    },
    unmapped="deny",
)
