#!/usr/bin/env python3
"""Deploy langchain_weather_agent to AgentCore Runtime."""

import subprocess
import os

AGENT_NAME = "langchain_agent"
ROLE_ARN = "arn:ab"
REGION = "us-east-1"

os.chdir(os.path.dirname(os.path.abspath(__file__)))

def run(cmd):
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

if __name__ == "__main__":
    run([
        "agentcore", "configure",
        "--entrypoint", "agent.py",
        "--name", AGENT_NAME,
        "--execution-role", ROLE_ARN,
        "--region", REGION,
        "--requirements-file", "requirements.txt",
        "--disable-memory",
        "--non-interactive",
    ])
    run(["agentcore", "launch", "--agent", AGENT_NAME])
