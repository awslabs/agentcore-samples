#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
Slurm MCP Server — Exposes Slurm cluster operations as MCP tools.

An LLM agent uses these tools to submit, monitor, and manage training jobs
on a HyperPod Slurm cluster via SSH.

Tools:
  - slurm_submit: Submit a batch job (sbatch)
  - slurm_status: Get job status (squeue/sacct)
  - slurm_logs: Read job stdout/stderr
  - slurm_cancel: Cancel a running job (scancel)
  - slurm_info: Get cluster/partition info (sinfo)
  - slurm_metrics: Parse training metrics from log files

Configuration:
  Set environment variables (or use a .env file):
    CLUSTER_HOST, CLUSTER_USER, SSH_KEY_PATH, VLA_HOME

Requires: Python 3.10+ (mcp library dependency)
"""

import asyncio
import json
import os
import re
from dataclasses import dataclass

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

# =============================================================================
# Configuration (from environment variables)
# =============================================================================


def _load_dotenv():
    """Load .env file if present (minimal implementation, no dependency)."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


@dataclass
class ClusterConfig:
    host: str = os.environ.get("CLUSTER_HOST", "")
    user: str = os.environ.get("CLUSTER_USER", "")
    ssh_key: str = os.environ.get("SSH_KEY_PATH", "~/.ssh/id_rsa")
    slurm_bin: str = os.environ.get("SLURM_BIN", "/opt/slurm/bin")
    work_dir: str = os.environ.get("VLA_HOME", "")

    def validate(self):
        missing = []
        if not self.host:
            missing.append("CLUSTER_HOST")
        if not self.user:
            missing.append("CLUSTER_USER")
        if not self.work_dir:
            missing.append("VLA_HOME")
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}. Set them in .env or export them."
            )


config = ClusterConfig()


# =============================================================================
# Input Validation
# =============================================================================


def _validate_job_id(job_id: str) -> str | None:
    """Validate job_id is a numeric Slurm job ID. Returns error message or None."""
    if not re.fullmatch(r"\d+", str(job_id)):
        return f"ERROR: job_id must be numeric, got: {job_id!r}"
    return None


# =============================================================================
# SSH Helper
# =============================================================================


async def ssh_exec(cmd: str, timeout: int = 30) -> tuple[str, str, int]:
    """Execute a command on the cluster via SSH.

    NOTE: StrictHostKeyChecking=no is used for convenience in dev/test.
    For production, use known_hosts verification.
    """
    ssh_key = os.path.expanduser(config.ssh_key)
    ssh_cmd = [
        "ssh",
        "-i",
        ssh_key,
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        f"ConnectTimeout={timeout}",
        f"{config.user}@{config.host}",
        f"export PATH={config.slurm_bin}:$PATH && {cmd}",
    ]
    proc = await asyncio.create_subprocess_exec(
        *ssh_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout + 10)
    return stdout.decode(), stderr.decode(), proc.returncode


# =============================================================================
# MCP Server
# =============================================================================

app = Server("slurm-mcp")


@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="slurm_submit",
            description="Submit a Slurm batch job. Returns the job ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "script_path": {
                        "type": "string",
                        "description": "Absolute path to the .sbatch script on the cluster",
                    },
                    "overrides": {
                        "type": "object",
                        "description": 'Optional sbatch overrides (e.g. {"time": "01:00:00", "job-name": "test"})',
                        "additionalProperties": {"type": "string"},
                    },
                },
                "required": ["script_path"],
            },
        ),
        Tool(
            name="slurm_status",
            description="Get status of jobs. Returns job ID, state, time, node, reason.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Specific job ID to check (optional — defaults to all user jobs)",
                    }
                },
            },
        ),
        Tool(
            name="slurm_logs",
            description="Read stdout/stderr from a training job. Returns last N lines.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The Slurm job ID"},
                    "stream": {
                        "type": "string",
                        "enum": ["stdout", "stderr", "both"],
                        "description": "Which log stream to read (default: both)",
                    },
                    "tail_lines": {"type": "integer", "description": "Number of lines from the end (default: 50)"},
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="slurm_cancel",
            description="Cancel a running or pending Slurm job.",
            inputSchema={
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The Slurm job ID to cancel"},
                    "reason": {"type": "string", "description": "Reason for cancellation (logged)"},
                },
                "required": ["job_id"],
            },
        ),
        Tool(
            name="slurm_info",
            description="Get cluster partition info — available nodes, GPUs, state.",
            inputSchema={
                "type": "object",
                "properties": {
                    "partition": {"type": "string", "description": "Partition name (optional — defaults to all)"}
                },
            },
        ),
        Tool(
            name="slurm_metrics",
            description="Parse training metrics from job logs. Extracts loss, learning rate, steps, GPU utilization.",
            inputSchema={
                "type": "object",
                "properties": {"job_id": {"type": "string", "description": "The Slurm job ID to parse metrics from"}},
                "required": ["job_id"],
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    config.validate()

    if name == "slurm_submit":
        return await _slurm_submit(arguments)
    elif name == "slurm_status":
        return await _slurm_status(arguments)
    elif name == "slurm_logs":
        return await _slurm_logs(arguments)
    elif name == "slurm_cancel":
        return await _slurm_cancel(arguments)
    elif name == "slurm_info":
        return await _slurm_info(arguments)
    elif name == "slurm_metrics":
        return await _slurm_metrics(arguments)
    else:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]


# =============================================================================
# Tool Implementations
# =============================================================================


async def _slurm_submit(args: dict):
    script = args["script_path"]
    overrides = args.get("overrides", {})

    override_flags = " ".join(f"--{k}={v}" for k, v in overrides.items())
    cmd = f"sbatch {override_flags} {script}".strip()

    stdout, stderr, rc = await ssh_exec(cmd)
    if rc != 0:
        return [TextContent(type="text", text=f"ERROR: sbatch failed\n{stderr}")]

    match = re.search(r"Submitted batch job (\d+)", stdout)
    job_id = match.group(1) if match else "unknown"

    return [
        TextContent(
            type="text",
            text=json.dumps(
                {"status": "submitted", "job_id": job_id, "script": script, "overrides": overrides}, indent=2
            ),
        )
    ]


async def _slurm_status(args: dict):
    job_id = args.get("job_id", "")

    if job_id:
        err = _validate_job_id(job_id)
        if err:
            return [TextContent(type="text", text=err)]
        cmd = f"squeue -j {job_id} --format='%i|%j|%T|%M|%N|%r' --noheader"
    else:
        cmd = f"squeue -u {config.user} --format='%i|%j|%T|%M|%N|%r' --noheader"

    stdout, stderr, rc = await ssh_exec(cmd)

    if not stdout.strip() or rc != 0:
        if not job_id:
            return [TextContent(type="text", text="No jobs found")]
        # Job may have completed — check sacct
        cmd2 = f"sacct -j {job_id} --format=JobID,JobName,State,Elapsed,ExitCode --noheader -P"
        stdout2, _, _ = await ssh_exec(cmd2)
        if stdout2.strip():
            return [TextContent(type="text", text=f"Job completed:\n{stdout2}")]
        return [TextContent(type="text", text="No jobs found")]

    jobs = []
    for line in stdout.strip().split("\n"):
        parts = line.split("|")
        if len(parts) >= 6:
            jobs.append(
                {
                    "job_id": parts[0].strip(),
                    "name": parts[1].strip(),
                    "state": parts[2].strip(),
                    "time": parts[3].strip(),
                    "node": parts[4].strip(),
                    "reason": parts[5].strip(),
                }
            )

    return [TextContent(type="text", text=json.dumps(jobs, indent=2))]


async def _slurm_logs(args: dict):
    job_id = args["job_id"]
    err = _validate_job_id(job_id)
    if err:
        return [TextContent(type="text", text=err)]
    stream = args.get("stream", "both")
    tail_lines = args.get("tail_lines", 50)

    log_dir = f"{config.work_dir}/logs"
    result = {}

    if stream in ("stdout", "both"):
        cmd = f"tail -n {tail_lines} {log_dir}/finetune_{job_id}.out 2>/dev/null || echo 'NO_FILE'"
        stdout, _, _ = await ssh_exec(cmd)
        result["stdout"] = stdout

    if stream in ("stderr", "both"):
        cmd = f"tail -n {tail_lines} {log_dir}/finetune_{job_id}.err 2>/dev/null || echo 'NO_FILE'"
        stdout, _, _ = await ssh_exec(cmd)
        result["stderr"] = stdout

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _slurm_cancel(args: dict):
    job_id = args["job_id"]
    err = _validate_job_id(job_id)
    if err:
        return [TextContent(type="text", text=err)]
    reason = args.get("reason", "cancelled by agent")

    cmd = f"scancel {job_id}"
    stdout, stderr, rc = await ssh_exec(cmd)

    if rc != 0:
        return [TextContent(type="text", text=f"ERROR: scancel failed\n{stderr}")]

    return [
        TextContent(type="text", text=json.dumps({"status": "cancelled", "job_id": job_id, "reason": reason}, indent=2))
    ]


async def _slurm_info(args: dict):
    partition = args.get("partition", "")

    if partition:
        cmd = f"sinfo -p {partition} --format='%P|%a|%D|%T|%G|%l' --noheader"
    else:
        cmd = "sinfo --format='%P|%a|%D|%T|%G|%l' --noheader"

    stdout, stderr, rc = await ssh_exec(cmd)

    # Also get GPU utilization
    cmd2 = "squeue --format='%i|%u|%T|%b' --noheader"
    stdout2, _, _ = await ssh_exec(cmd2)

    return [
        TextContent(
            type="text", text=json.dumps({"partitions": stdout.strip(), "running_jobs": stdout2.strip()}, indent=2)
        )
    ]


async def _slurm_metrics(args: dict):
    job_id = args["job_id"]
    err = _validate_job_id(job_id)
    if err:
        return [TextContent(type="text", text=err)]
    log_file = f"{config.work_dir}/logs/finetune_{job_id}.out"

    cmd = f"""python3 -c "
import re, json

metrics = {{'job_id': '{job_id}', 'steps': [], 'latest': {{}}}}

try:
    with open('{log_file}') as f:
        lines = f.readlines()

    for line in lines:
        # Match [METRICS] structured lines first
        if '[METRICS]' in line:
            step_m = re.search(r'step=(\\d+)', line)
            loss_m = re.search(r'loss=([0-9.]+)', line)
            if step_m and loss_m:
                entry = {{'step': int(step_m.group(1)), 'loss': float(loss_m.group(1))}}
                metrics['steps'].append(entry)
                continue

        # Fallback: generic loss/step patterns
        loss_match = re.search(r'loss[=:\\s]+([0-9.]+)', line, re.IGNORECASE)
        step_match = re.search(r'step[=:\\s]+(\\d+)', line, re.IGNORECASE)
        lr_match = re.search(r'lr[=:\\s]+([0-9.e-]+)', line, re.IGNORECASE)

        if loss_match and step_match:
            entry = {{'step': int(step_match.group(1)), 'loss': float(loss_match.group(1))}}
            if lr_match:
                entry['lr'] = float(lr_match.group(1))
            metrics['steps'].append(entry)

    if metrics['steps']:
        metrics['latest'] = metrics['steps'][-1]
        metrics['total_steps_logged'] = len(metrics['steps'])
        first = metrics['steps'][0]['loss']
        last = metrics['steps'][-1]['loss']
        metrics['loss_trend'] = 'decreasing' if last < first else 'increasing' if last > first else 'flat'

except FileNotFoundError:
    metrics['error'] = 'Log file not found (job may still be starting)'

print(json.dumps(metrics, indent=2))
"
"""
    stdout, stderr, rc = await ssh_exec(cmd, timeout=15)

    if rc != 0:
        return [TextContent(type="text", text=f"ERROR parsing metrics: {stderr}")]

    return [TextContent(type="text", text=stdout)]


# =============================================================================
# Main
# =============================================================================


async def main():
    config.validate()
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
