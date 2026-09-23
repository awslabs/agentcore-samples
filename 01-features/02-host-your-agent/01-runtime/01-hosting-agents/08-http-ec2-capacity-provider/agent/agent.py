"""
A basic HTTP Strands agent, hosted on an AgentCore CapacityProvider.

This is the SAME file for both deployments in this sample — the zip artifact
and the container artifact. That is the point: your agent code does not know
or care which artifact type carried it, nor that it is running on a
CapacityProvider EC2 instance rather than a serverless microVM.

The `whoami` tool exists to make the compute visible. It reports the kernel,
architecture, CPU count and memory of the machine the agent is on — so when
you invoke it you can see a real m6g.large Graviton instance answering, and
see that both artifacts land on the same instance type.
"""

import os
import platform
import subprocess

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool

app = BedrockAgentCoreApp()

# Which artifact carried this code — set by deploy.py via environmentVariables.
# Defaults to "unknown" so the agent still runs if it is unset.
ARTIFACT_KIND = os.environ.get("ARTIFACT_KIND", "unknown")

MODEL_ID = os.environ.get(
    "MODEL_ID", "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
)


@tool
def whoami() -> str:
    """Report the machine this agent is running on: kernel, arch, CPUs, memory."""
    uname = platform.uname()

    # Memory and CPU count, read from /proc — no extra dependencies.
    mem_total_kb = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_total_kb = int(line.split()[1])
                    break
    except OSError:
        pass

    return "\n".join(
        [
            f"artifact_kind: {ARTIFACT_KIND}",
            f"kernel:        {uname.system} {uname.release}",
            f"architecture:  {uname.machine}",
            f"cpus:          {os.cpu_count()}",
            f"memory_total:  {mem_total_kb // 1024} MiB",
            f"hostname:      {uname.node}",
        ]
    )


@tool
def run_command(command: str) -> str:
    """
    Run a short shell command on the instance and return its output.

    Useful for showing that this is an ordinary Linux machine — try
    `nproc`, `df -h`, or `cat /etc/os-release`.
    """
    try:
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return "Command timed out after 15s."
    output = (completed.stdout + completed.stderr).strip()
    return output[:4000] or "(no output)"


agent = Agent(
    model=MODEL_ID,
    tools=[whoami, run_command],
    system_prompt=(
        "You are a concise assistant running on an AWS Bedrock AgentCore "
        "CapacityProvider instance. When asked about the machine you are on, "
        "use the whoami tool and report exactly what it returns. Keep answers "
        "short."
    ),
)


@app.entrypoint
def invoke(payload):
    """HTTP entrypoint. AgentCore POSTs the invocation payload here."""
    prompt = payload.get("prompt", "Describe the machine you are running on.")
    result = agent(prompt)
    return {"result": result.message}


if __name__ == "__main__":
    # AgentCore sets PORT; BedrockAgentCoreApp honours it.
    app.run()
