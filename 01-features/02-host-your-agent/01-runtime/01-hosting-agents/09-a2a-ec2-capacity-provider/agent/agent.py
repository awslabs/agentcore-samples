"""
An A2A agent on AgentCore Runtime, running on your own EC2 instances.

This is NOT a BedrockAgentCoreApp. Sample 1's HTTP agent uses the SDK's app
object and answers POST /invocations. An A2A agent speaks the Agent-to-Agent
protocol — JSON-RPC 2.0 at the root path, plus a discoverable agent card — so
`BedrockAgentCoreApp` is not involved. Nothing here is hand-rolled either: the
protocol comes from the official `a2a-sdk`, and both Strands and
`bedrock-agentcore` ship A2A integrations on top of it.

THE THINGS THE RUNTIME REQUIRES
-------------------------------
1. Port 9000. The AgentCore Runtime A2A service contract fixes the container
   port at 9000 (HTTP is 8080, MCP is 8000). The SDK states this outright, and
   warns that binding anything else fails deployed invocations with HTTP 424
   RuntimeClientError, because the runtime proxies to 9000 only.
2. Host 0.0.0.0. Binding 127.0.0.1 makes the server unreachable from outside
   the instance and the health check fails. `serve_a2a` auto-detects this from
   /.dockerenv, which is NOT present on a zip artifact, so it is passed here.
3. `GET /ping`. Unlike MCP, the A2A contract keeps the HTTP health route.
   `serve_a2a` adds it; the plain a2a-sdk app does not have one.
4. JSON-RPC at `/`. Both integrations mount it there, which is where the
   runtime posts.

WHY THIS USES TWO SDKs
----------------------
Neither one alone gets the whole contract right:

  * `bedrock_agentcore.runtime.a2a.serve_a2a` knows the *runtime* — it adds
    `GET /ping`, and its `BedrockCallContextBuilder` reads the AgentCore
    session/request headers into `BedrockAgentCoreContext`. But its
    auto-generated agent card is generic: one skill called "main".
  * `strands.multiagent.a2a.A2AServer` knows the *agent* — it derives the card
    from the agent itself, turning every `@tool` into a properly named A2A
    skill. But it serves via plain `a2a-sdk` with no `/ping` route (verified:
    404), and no AgentCore header handling.

So the card comes from Strands and the serving from the AgentCore SDK. Using
`A2AServer.serve()` instead would still work today — the runtime tolerates a
missing /ping in the A2A contract — but it drops the session-header context and
the documented health route.

STREAMING IS ADVERTISED BUT NOT REACHABLE HERE
----------------------------------------------
The card says `streaming: true`, and `message/stream` genuinely works when you
reach the server directly. Through `InvokeAgentRuntime` it does not: that API
buffers the response body, so SSE frames arrive only after the task has already
finished. Use `message/send` (what invoke.py does).
"""

from __future__ import annotations

import os
import platform
import socket
import uuid

from bedrock_agentcore.runtime.a2a import serve_a2a
from strands import Agent, tool
from strands.multiagent.a2a import A2AServer
from strands.multiagent.a2a.executor import StrandsA2AExecutor

# Generated once per process. Two calls reporting the same PROCESS_ID were
# served by the same Python process on the same EC2 instance — the same trick
# the MCP sample uses to make session→instance affinity visible.
PROCESS_ID = uuid.uuid4().hex[:8]

# 9000 is fixed by the service contract. The env var is read only so a local
# `python agent.py` can move it out of the way; the SDK logs a warning if the
# resolved port is not 9000. Note the SDK deliberately ignores `PORT` here,
# because images shared across protocols set it to 8080 or 8000.
PORT = int(os.environ.get("A2A_PORT", "9000"))
HOST = os.environ.get("A2A_HOST", "0.0.0.0")  # nosec B104

MODEL_ID = os.environ.get("MODEL_ID", "global.anthropic.claude-sonnet-5")


@tool
def whoami() -> dict:
    """
    Report the machine this agent is running on: kernel, arch, CPUs, memory.

    This is the tool that proves the sample's point. A serverless agent cannot
    tell you its instance type; this one runs on an EC2 instance you chose, and
    the values below are read off that instance.
    """
    try:
        memory_mb = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") // (1024 * 1024)
    except (ValueError, OSError):
        memory_mb = None
    return {
        "process_id": PROCESS_ID,
        "hostname": socket.gethostname(),
        "machine": platform.machine(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": os.cpu_count(),
        "memory_mb": memory_mb,
        "protocol": "A2A",
        # Set by deploy.py, so the running agent can confirm which fleet it
        # landed on rather than us inferring it from the arch.
        "instance_type": os.environ.get("FLEET_INSTANCE_TYPE", "unknown"),
    }


@tool
def add_numbers(a: int, b: int) -> int:
    """Add two numbers together."""
    return a + b


# `name` and `description` are not cosmetic: they are copied onto the agent card,
# and A2AServer raises if either is empty. The tools become the card's skills.
agent = Agent(
    name="basic_a2a_agent",
    description=(
        "A2A sample agent running on an AgentCore CapacityProvider. Reports the "
        "EC2 instance it is running on, and can add two numbers."
    ),
    model=MODEL_ID,
    system_prompt=(
        "You are a concise assistant demonstrating the A2A protocol on AWS "
        "Bedrock AgentCore Runtime. When asked about the machine, host, "
        "instance or hardware you are running on, call the whoami tool and "
        "report every field it returns verbatim. Do not invent values."
    ),
    tools=[whoami, add_numbers],
)


def build_agent_card():
    """
    Derive an A2A agent card from the agent, one skill per `@tool`.

    A2AServer is constructed only to read `public_agent_card` off it — it is
    never served. That is the cheapest way to get Strands' tool→skill mapping
    without reimplementing it.

    Deliberately NOT passing `http_url`/`serve_at_root` here, even though they
    look like the way to set the callback URL a peer agent would use. Verified:
    `serve_a2a` overwrites the card's URL unconditionally with
    `AGENTCORE_RUNTIME_URL` if set, else `http://localhost:<port>/`. Anything set
    here is discarded, so that env var is the only thing that moves it.

    deploy.py does not set it: the value would have to be built from the runtime
    ARN, which does not exist until `create_agent_runtime` returns — the same call
    that takes `environmentVariables`. So the card advertises
    `http://localhost:9000/` and is informational on this path. See invoke.py → "THE AGENT CARD IS NOT REACHABLE
    THROUGH THIS API" for the way that does work on a CapacityProvider.
    """
    return A2AServer(agent=agent, host=HOST, port=PORT).public_agent_card


if __name__ == "__main__":
    serve_a2a(
        StrandsA2AExecutor(agent),
        build_agent_card(),
        host=HOST,
        port=PORT,
    )
