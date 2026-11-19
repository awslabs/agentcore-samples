#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
"""
VLA Training Agent — Autonomous agent that orchestrates VLA fine-tuning via Slurm.

This agent demonstrates how Amazon Bedrock AgentCore can manage ML training:
  1. Submits a training job to a HyperPod Slurm cluster
  2. Monitors loss curve periodically
  3. Detects issues (divergence, stalling, NaN)
  4. Takes corrective action (adjust LR, restart with different params)
  5. Reports results

In production, this would run on AgentCore Runtime with:
  - Gateway providing the Slurm MCP tools
  - Memory storing experiment history
  - Policy enforcing cost guardrails
  - Observability logging all decisions

For local testing, it SSHs directly to the cluster.

Configuration:
  Set environment variables (or use a .env file):
    CLUSTER_HOST, CLUSTER_USER, SSH_KEY_PATH, VLA_HOME, MAX_STEPS
"""

import json
import math
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# Configuration (from environment)
# =============================================================================


def _load_dotenv():
    """Load .env file if present."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()


def _get_env(key: str, default: str = "") -> str:
    """Get environment variable with validation."""
    return os.environ.get(key, default)


# Cluster connection
CLUSTER_HOST = _get_env("CLUSTER_HOST")
CLUSTER_USER = _get_env("CLUSTER_USER")
SSH_KEY_PATH = os.path.expanduser(_get_env("SSH_KEY_PATH", "~/.ssh/id_rsa"))
VLA_HOME = _get_env("VLA_HOME")
MAX_STEPS = int(_get_env("MAX_STEPS", "500"))


@dataclass
class TrainingConfig:
    """What the agent knows about the training setup."""

    model_path: str = f"{VLA_HOME}/models/openvla-7b"
    data_dir: str = f"{VLA_HOME}/data/libero_rlds"
    dataset_name: str = _get_env("DATASET_NAME", "libero_10_no_noops")
    script_path: str = f"{VLA_HOME}/scripts/finetune_openvla.sbatch"
    max_steps: int = MAX_STEPS

    # Guardrails (would come from AgentCore Policy in production)
    max_gpu_hours: float = 16.0
    max_retries: int = 3
    loss_divergence_threshold: float = 10.0
    loss_stall_patience: int = 5


@dataclass
class ExperimentState:
    """Agent's working memory (would be AgentCore Memory in production)."""

    current_job_id: Optional[str] = None
    run_history: list = field(default_factory=list)
    loss_history: list = field(default_factory=list)
    decisions: list = field(default_factory=list)
    retries: int = 0
    status: str = "idle"


# =============================================================================
# Slurm Interface (SSH — in production, replaced by AgentCore Gateway + MCP)
# =============================================================================


def _validate_config():
    """Ensure required config is set."""
    missing = []
    if not CLUSTER_HOST:
        missing.append("CLUSTER_HOST")
    if not CLUSTER_USER:
        missing.append("CLUSTER_USER")
    if not VLA_HOME:
        missing.append("VLA_HOME")
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}. "
            f"Copy .env.example to .env and fill in your values."
        )


# NOTE: StrictHostKeyChecking=no is used for convenience in dev/test.
# For production, use known_hosts verification.
SSH_CMD_PREFIX = [
    "ssh",
    "-i",
    SSH_KEY_PATH,
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "ConnectTimeout=10",
    f"{CLUSTER_USER}@{CLUSTER_HOST}",
]


def ssh_exec(cmd: str) -> str:
    """Execute command on cluster via SSH."""
    full_cmd = SSH_CMD_PREFIX + [f"export PATH=/opt/slurm/bin:$PATH && {cmd}"]
    result = subprocess.run(full_cmd, capture_output=True, text=True, timeout=30)
    return result.stdout + result.stderr


def submit_job(script_path: str, overrides: dict = None, env_vars: dict = None) -> str:
    """Submit a Slurm job. Returns job ID.

    Args:
        script_path: Path to the .sbatch script on the cluster.
        overrides: sbatch option overrides (e.g. {"time": "01:00:00"}).
        env_vars: Environment variables to export for the job (e.g. {"LEARNING_RATE": "1e-4"}).
    """
    override_str = " ".join(f"--{k}={v}" for k, v in (overrides or {}).items())
    export_str = ""
    if env_vars:
        pairs = ",".join(f"{k}={v}" for k, v in env_vars.items())
        export_str = f"--export=ALL,{pairs}"
    output = ssh_exec(f"sbatch {override_str} {export_str} {script_path}".strip())
    match = re.search(r"Submitted batch job (\d+)", output)
    return match.group(1) if match else None


def get_job_status(job_id: str) -> dict:
    """Get job state via squeue, falling back to sacct for completed jobs."""
    output = ssh_exec(f"squeue -j {job_id} --format='%T' --noheader")
    state = output.strip()
    if not state or "error" in state.lower() or "invalid" in state.lower():
        output = ssh_exec(f"sacct -j {job_id} --format=State --noheader -P")
        state = output.strip().split("\n")[0] if output.strip() else "UNKNOWN"
    return {"job_id": job_id, "state": state}


def get_training_metrics(job_id: str) -> dict:
    """Parse training progress from log files.

    Sources (checked in priority order):
      1. [METRICS] lines in stdout — structured loss/acc/l1 per step
      2. tqdm progress bars in stderr — step counts and throughput
      3. Checkpoint existence — final artifact verification
    """
    metrics = {"job_id": job_id, "steps": [], "losses": []}
    max_steps = MAX_STEPS

    out_file = f"{VLA_HOME}/logs/finetune_{job_id}.out"
    err_file = f"{VLA_HOME}/logs/finetune_{job_id}.err"

    # --- Source 1: Structured [METRICS] lines from stdout ---
    cmd_metrics = f"""grep '\\[METRICS\\]' {out_file} 2>/dev/null"""
    metrics_output = ssh_exec(cmd_metrics)

    for line in metrics_output.strip().split("\n"):
        if "[METRICS]" not in line:
            continue
        step_m = re.search(r"step=(\d+)", line)
        loss_m = re.search(r"loss=([0-9.]+)", line)
        acc_m = re.search(r"acc=([0-9.]+)", line)
        l1_m = re.search(r"l1=([0-9.]+)", line)
        if step_m and loss_m:
            entry = {"step": int(step_m.group(1)), "loss": float(loss_m.group(1))}
            if acc_m:
                entry["acc"] = float(acc_m.group(1))
            if l1_m:
                entry["l1"] = float(l1_m.group(1))
            metrics["steps"].append(entry)
            metrics["losses"].append(entry["loss"])

    # --- Source 2: tqdm progress from stderr (fallback) ---
    if not metrics["steps"]:
        cmd = f"""grep -oP '\\d+/{max_steps} \\[\\d+:\\d+' {err_file} 2>/dev/null | sort -t/ -k1 -n -u | tail -20"""
        output = ssh_exec(cmd)
        for line in output.strip().split("\n"):
            step_match = re.search(rf"(\d+)/{max_steps}", line)
            if step_match:
                step = int(step_match.group(1))
                metrics["steps"].append({"step": step})

    # Throughput from last tqdm line
    cmd2 = f"""grep -oP '[0-9.]+it/s' {err_file} 2>/dev/null | tail -1"""
    throughput = ssh_exec(cmd2).strip()
    if throughput:
        metrics["throughput"] = throughput

    # --- Source 3: Status messages and checkpoint ---
    cmd3 = f"""grep -E 'Saving|Max step|Fine-tuning complete|trainable params' {out_file} 2>/dev/null | sort -u | tail -5"""
    status_output = ssh_exec(cmd3)
    if status_output.strip():
        metrics["status_messages"] = [line.strip()[:80] for line in status_output.strip().split("\n") if line.strip()]

    ckpt_dir = f"{VLA_HOME}/checkpoints/run_{job_id}"
    cmd4 = f"""ls -lh {ckpt_dir}/*/*.safetensors 2>/dev/null | wc -l; du -sh {ckpt_dir} 2>/dev/null"""
    ckpt_output = ssh_exec(cmd4)
    parts = ckpt_output.strip().split("\n")
    if parts:
        metrics["checkpoint_shards"] = parts[0].strip()
        if len(parts) > 1:
            metrics["checkpoint_size"] = parts[1].strip().split()[0] if parts[1].strip() else "unknown"

    # --- Summary ---
    if metrics["steps"]:
        metrics["latest_step"] = metrics["steps"][-1]["step"]
        metrics["total_steps"] = max_steps
        metrics["progress_pct"] = f"{metrics['latest_step'] / max_steps * 100:.0f}%"

    if metrics["losses"]:
        metrics["latest_loss"] = metrics["losses"][-1]
        metrics["min_loss"] = min(metrics["losses"])
        metrics["max_loss"] = max(metrics["losses"])

    return metrics


def cancel_job(job_id: str) -> bool:
    """Cancel a job."""
    output = ssh_exec(f"scancel {job_id}")
    return "error" not in output.lower()


# =============================================================================
# Agent Logic
# =============================================================================


class VLATrainingAgent:
    """
    Agent that manages VLA training autonomously.

    Decision loop:
      1. Submit job if none running
      2. Monitor metrics every N seconds
      3. Detect anomalies (divergence, NaN, stall)
      4. Take corrective action
      5. Report final results
    """

    def __init__(self, config: TrainingConfig = None):
        self.config = config or TrainingConfig()
        self.state = ExperimentState()
        self.check_interval = 30

    def log_decision(self, action: str, reason: str, details: dict = None):
        """Record agent decisions (AgentCore Observability in production)."""
        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": action,
            "reason": reason,
            "details": details or {},
        }
        self.state.decisions.append(entry)
        print(f"[AGENT] {action}: {reason}")

    def submit_training(self, overrides: dict = None, env_vars: dict = None) -> str:
        """Submit a training job."""
        self.log_decision("SUBMIT", "Starting training run", {"overrides": overrides, "env_vars": env_vars})
        job_id = submit_job(self.config.script_path, overrides, env_vars)

        if job_id:
            self.state.current_job_id = job_id
            self.state.status = "training"
            self.state.run_history.append({"job_id": job_id, "overrides": overrides})
            self.log_decision("SUBMITTED", f"Job {job_id} queued")
        else:
            self.log_decision("ERROR", "Failed to submit job")
            self.state.status = "failed"

        return job_id

    def check_health(self) -> str:
        """Check training health. Returns: healthy, diverged, stalled, failed, complete."""
        if not self.state.current_job_id:
            return "no_job"

        status = get_job_status(self.state.current_job_id)
        job_state = status["state"]

        if "COMPLETED" in job_state:
            self.state.status = "complete"
            return "complete"

        if "FAILED" in job_state or "CANCELLED" in job_state:
            return "failed"

        if "PENDING" in job_state:
            return "pending"

        if "RUNNING" not in job_state:
            return "unknown"

        # Parse metrics for anomaly detection
        metrics = get_training_metrics(self.state.current_job_id)

        if not metrics.get("steps"):
            return "starting"

        latest_loss = metrics.get("latest_loss")
        if latest_loss is not None:
            self.state.loss_history.append(latest_loss)

            if latest_loss > self.config.loss_divergence_threshold:
                self.log_decision("DETECT", f"Loss diverged: {latest_loss:.4f}", metrics)
                return "diverged"

            if math.isnan(latest_loss):
                self.log_decision("DETECT", "NaN loss detected", metrics)
                return "diverged"

            if len(self.state.loss_history) >= self.config.loss_stall_patience:
                recent = self.state.loss_history[-self.config.loss_stall_patience :]
                if all(abs(recent[i] - recent[i - 1]) < 0.001 for i in range(1, len(recent))):
                    self.log_decision("DETECT", f"Loss stalled at {latest_loss:.4f}", metrics)
                    return "stalled"

        return "healthy"

    def recover(self, issue: str):
        """Take corrective action based on the issue detected."""
        self.state.retries += 1

        if self.state.retries > self.config.max_retries:
            self.log_decision("ABORT", f"Max retries ({self.config.max_retries}) exceeded")
            self.state.status = "failed"
            return

        if self.state.current_job_id:
            cancel_job(self.state.current_job_id)
            self.log_decision("CANCEL", f"Cancelled job {self.state.current_job_id}")

        if issue == "diverged":
            current_lr = 5e-4 / (5**self.state.retries)
            self.log_decision("RECOVER", f"Reducing LR to {current_lr:.2e}")
            self.submit_training(
                overrides={"comment": f"retry_{self.state.retries}_lr_{current_lr}"},
                env_vars={"LEARNING_RATE": f"{current_lr:.2e}"},
            )

        elif issue == "stalled":
            current_lr = 5e-4 / (2**self.state.retries)
            self.log_decision("RECOVER", f"Loss stalled — restarting with LR {current_lr:.2e}")
            self.submit_training(
                overrides={"comment": f"retry_{self.state.retries}_stall_fix"},
                env_vars={"LEARNING_RATE": f"{current_lr:.2e}"},
            )

        elif issue == "failed":
            self.log_decision("RECOVER", "Job failed — retrying")
            self.submit_training({"comment": f"retry_{self.state.retries}_after_failure"})

    def run(self, max_checks: int = 100):
        """Main agent loop."""
        print("=" * 60)
        print("VLA Training Agent — Starting")
        print(f"Model: {self.config.model_path}")
        print(f"Dataset: {self.config.dataset_name}")
        print("=" * 60)

        self.submit_training()

        if self.state.status == "failed":
            return self.report()

        for check_num in range(max_checks):
            time.sleep(self.check_interval)

            health = self.check_health()
            print(f"[CHECK {check_num + 1}] Health: {health} | Job: {self.state.current_job_id}")

            if health == "complete":
                self.log_decision("COMPLETE", "Training finished successfully")
                break

            elif health in ("diverged", "stalled", "failed"):
                self.recover(health)
                if self.state.status == "failed":
                    break

            elif health == "pending":
                print("  Job still pending...")

            elif health == "starting":
                print("  Job initializing (no metrics yet)...")

        return self.report()

    def report(self) -> dict:
        """Generate final experiment report."""
        report = {
            "status": self.state.status,
            "total_runs": len(self.state.run_history),
            "total_retries": self.state.retries,
            "decisions": self.state.decisions,
            "final_loss": self.state.loss_history[-1] if self.state.loss_history else None,
            "loss_history_summary": {
                "first": self.state.loss_history[0] if self.state.loss_history else None,
                "last": self.state.loss_history[-1] if self.state.loss_history else None,
                "min": min(self.state.loss_history) if self.state.loss_history else None,
                "count": len(self.state.loss_history),
            },
        }

        print("\n" + "=" * 60)
        print("EXPERIMENT REPORT")
        print("=" * 60)
        print(json.dumps(report, indent=2, default=str))

        return report


# =============================================================================
# Entry point
# =============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="VLA Training Agent")
    parser.add_argument("--check-interval", type=int, default=30, help="Seconds between health checks")
    parser.add_argument("--max-checks", type=int, default=100, help="Maximum monitoring iterations")
    parser.add_argument("--submit-only", action="store_true", help="Just submit, don't monitor")
    parser.add_argument("--monitor-job", type=str, help="Monitor existing job ID instead of submitting")
    args = parser.parse_args()

    _validate_config()

    agent = VLATrainingAgent()
    agent.check_interval = args.check_interval

    if args.monitor_job:
        agent.state.current_job_id = args.monitor_job
        agent.state.status = "training"
        print("=" * 60)
        print(f"VLA Training Agent — Monitoring Job {args.monitor_job}")
        print("=" * 60)
        for i in range(args.max_checks):
            time.sleep(agent.check_interval)
            health = agent.check_health()
            print(f"[CHECK {i + 1}] Health: {health} | Job: {agent.state.current_job_id}")
            if health in ("complete", "failed"):
                break
            elif health in ("diverged", "stalled"):
                agent.recover(health)
                if agent.state.status == "failed":
                    break
        # Final metrics
        metrics = get_training_metrics(agent.state.current_job_id)
        if metrics.get("steps"):
            summary_parts = [f"Parsed {len(metrics['steps'])} unique steps"]
            if metrics.get("throughput"):
                summary_parts.append(f"Throughput: {metrics['throughput']}")
            if metrics.get("checkpoint_size"):
                summary_parts.append(f"Checkpoint: {metrics['checkpoint_size']}")
            if metrics.get("progress_pct"):
                summary_parts.append(f"Progress: {metrics['progress_pct']}")
            agent.log_decision("METRICS", " | ".join(summary_parts))
        if metrics.get("losses"):
            agent.log_decision(
                "LOSS_CURVE",
                f"Steps: {len(metrics['losses'])} | "
                f"First: {metrics['losses'][0]:.4f} | "
                f"Last: {metrics['losses'][-1]:.4f} | "
                f"Min: {min(metrics['losses']):.4f} | "
                f"Max: {max(metrics['losses']):.4f}",
            )
            last_5 = metrics["losses"][-5:]
            trend = " -> ".join(f"{val:.4f}" for val in last_5)
            agent.log_decision("LOSS_TREND", trend)
        if metrics.get("status_messages"):
            for msg in metrics["status_messages"][-3:]:
                agent.log_decision("STATUS", msg)
        agent.report()
    elif args.submit_only:
        agent.submit_training()
        print(f"Job submitted: {agent.state.current_job_id}")
    else:
        agent.run(max_checks=args.max_checks)
