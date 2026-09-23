# AgentCore Memory: Memory Branching

## Overview

Memory branching lets an agent fork a conversation into alternative paths from any prior event, then explore each branch independently before choosing one to continue. Branches share the same parent history up to the fork point, so an agent can compare outcomes across scenarios without corrupting the canonical transcript.

## When to use branching

- **Parallel exploration**: run several specialist agents on the same base context, each in its own branch, and compare results
- **What-if analysis**: evaluate alternative recommendations from a shared conversation root
- **A/B comparisons**: capture two candidate responses, retrieve both later, and pick one to adopt
- **Speculative execution**: let an agent try a multi-step plan in a branch that can be discarded if it fails

## How branches work in AgentCore Memory

- A branch is anchored to a `rootEventId` — the event from which the branch diverges
- Each branch has a unique `name` within the session
- `fork_conversation(root_event_id, branch_name, messages)` starts a branch; `add_turns(..., branch={"name": ...})` continues it
- `list_branches()` returns all branches for a session; `list_events(branch_name=...)` scopes retrieval to one branch
- Long-term memory extraction runs across events within a branch based on the configured strategies

## Available sample notebooks

| Use Case                                                  | Description                                                                                                         | Notebook                                                                                                                                                 |
| --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Travel planning with alternate itineraries (single agent) | A travel planning agent that forks a session into multiple itinerary branches and compares them side by side        | [travel-planning-agent-with-memory-branching.ipynb](./travel-planning-agent-with-memory-branching.ipynb)                                                 |
| Multi-agent parallel execution (multi-agent)              | Specialist agents run in parallel branches off a shared root, then merge results via the coordinator agent          | [multi-agent-parallel-execution-with-memory-branching.ipynb](./multi-agent-parallel-execution-with-memory-branching.ipynb)                               |

See `architecture.png` in this folder for a visual overview of the branching flow.

## Prerequisites

- An AgentCore Memory resource (created once, reused across runs)
- AWS credentials with permission for `bedrock-agentcore` and `bedrock-agentcore-control`
- Python 3.10+ and dependencies from `requirements.txt`

## Getting started

1. Open one of the notebooks above
2. Install dependencies: `pip install -r requirements.txt`
3. Follow the cells — the notebook creates a memory resource, runs a base conversation, forks one or more branches, and inspects each branch

## Related tutorials

- [Short-Term Memory](../01-short-term-memory/) — event storage fundamentals
- [Long-Term Memory](../02-long-term-memory/) — memory strategies that run across branches
