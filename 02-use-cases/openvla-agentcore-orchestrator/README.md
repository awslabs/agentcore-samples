# OpenVLA AgentCore Orchestrator — Autonomous Training on HyperPod Slurm

## Overview

The ML Training Agent demonstrates how Amazon Bedrock AgentCore can autonomously manage GPU training jobs on a SageMaker HyperPod Slurm cluster. It uses a Slurm MCP server to expose cluster operations as tools, and an autonomous agent that submits OpenVLA fine-tuning jobs, monitors training metrics, detects anomalies (loss divergence, stalls, NaN), and takes corrective action — all without human intervention.

The concrete workload is [OpenVLA-7B](https://github.com/openvla/openvla) LoRA fine-tuning on the LIBERO robotics benchmark, but the MCP server and agent pattern are workload-agnostic.

### Use case details

| Information         | Details                                                              |
|---------------------|----------------------------------------------------------------------|
| Use case type       | autonomous orchestration                                             |
| Agent type          | Single-agent with MCP tools                                          |
| Use case components | MCP tools (Slurm operations over SSH), anomaly detection, recovery   |
| Use case vertical   | MLOps / ML Training                                                  |
| Example complexity  | Intermediate                                                         |
| SDK used            | MCP (Model Context Protocol)                                         |

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Local Machine / AgentCore Runtime                           │
│                                                              │
│  ┌──────────────────┐       ┌─────────────────────────────┐ │
│  │ VLA Training     │       │ Slurm MCP Server            │ │
│  │ Agent            │──────▶│ (6 tools: submit, status,   │ │
│  │                  │  MCP  │  logs, cancel, info, metrics)│ │
│  └──────────────────┘       └──────────────┬──────────────┘ │
└─────────────────────────────────────────────┼───────────────┘
                                              │ SSH
                                              ▼
                              ┌──────────────────────────────┐
                              │ HyperPod Slurm Cluster       │
                              │ (P5/P5en nodes, FSx, EFA)    │
                              │                              │
                              │  sbatch → torchrun → GPUs   │
                              └──────────────────────────────┘
```

### Key Features

- **MCP-based Slurm integration**: 6 tools (submit, status, logs, cancel, cluster info, metrics) exposed via the Model Context Protocol
- **Anomaly detection**: Monitors loss curves for divergence, stalls, and NaN values
- **Automatic recovery**: Cancels failing jobs, adjusts learning rate, resubmits
- **Input validation**: Job IDs validated against injection; SSH with configurable keys
- **Container-based training**: Pinned Dockerfile with EFA/NCCL for reproducibility
- **HyperPod auto-resume**: Handles node failures via `srun --auto-resume`
- **Environment-driven config**: All paths/credentials via `.env` file — no hardcoded values

## Prerequisites

| Requirement | Description |
|-------------|-------------|
| Python 3.10+ | For the MCP server (`mcp` library requires 3.10+) |
| SageMaker HyperPod cluster | Slurm scheduler with P5/P5en GPU nodes |
| SSH access | Key-based SSH to the cluster login node |
| FSx for Lustre | Shared filesystem mounted at `/fsx` |
| Docker + Enroot/Pyxis | For building and running the training container |

## File Structure

```
openvla-agentcore-orchestrator/
├── README.md                    # This file
├── .env.example                 # Configuration template
├── .gitignore
├── requirements.txt             # MCP server dependency
├── slurm_mcp_server.py          # MCP server (6 Slurm tools over SSH)
├── vla_training_agent.py        # Autonomous training agent
├── openvla.Dockerfile           # Training container (pinned deps)
└── slurm/
    └── finetune_openvla.sbatch  # Slurm batch script
```

## Setup

### 1. Clone this repository

```bash
git clone https://github.com/awslabs/agentcore-samples.git
cd agentcore-samples/02-use-cases/openvla-agentcore-orchestrator
```

### 2. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your cluster details:
#   CLUSTER_HOST, CLUSTER_USER, SSH_KEY_PATH, VLA_HOME
```

### 4. Prepare the training environment on the cluster

Build the container and set up the workspace:

```bash
# Build container image
docker build -t openvla-finetune -f openvla.Dockerfile .

# Import as Enroot squashfs (on cluster or transfer after build)
enroot import -o /fsx/$USER/vla/openvla-finetune.sqsh dockerd://openvla-finetune:latest

# Create workspace
ssh $CLUSTER_USER@$CLUSTER_HOST "mkdir -p /fsx/$USER/vla/{models,data,checkpoints,logs,scripts}"
```

### 5. Download model and data

On the cluster:

```bash
# Download OpenVLA-7B weights
python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('openvla/openvla-7b-prismatic', local_dir='/fsx/$USER/vla/models/openvla-7b')
"

# Download LIBERO dataset (RLDS format from Hugging Face Hub — not in stock TFDS catalog)
git lfs install
git clone https://huggingface.co/datasets/openvla/modified_libero_rlds /fsx/$USER/vla/data/libero_rlds
```

### 6. Copy the sbatch script to the cluster

```bash
scp slurm/finetune_openvla.sbatch $CLUSTER_USER@$CLUSTER_HOST:/fsx/$USER/vla/scripts/
```

## Usage

### Option A: Run the MCP Server (for any MCP client)

Start the MCP server as a long-running process. Any MCP-compatible client (Claude Desktop, Cursor, a custom agent, or AgentCore Runtime) can connect:

```bash
python3 slurm_mcp_server.py
```

The server exposes 6 tools over stdio:
- `slurm_submit` — Submit a training job
- `slurm_status` — Check job state
- `slurm_logs` — Read stdout/stderr
- `slurm_cancel` — Cancel a job
- `slurm_info` — Cluster/partition info
- `slurm_metrics` — Parse training metrics from logs

### Option B: Run the Autonomous Agent (full loop)

The agent submits a job and monitors it through completion, handling anomalies:

```bash
python3 vla_training_agent.py
```

This will:
1. Submit `finetune_openvla.sbatch` via sbatch
2. Poll every 30 seconds for status and metrics
3. Detect divergence/stall/NaN and recover (cancel + resubmit with adjusted LR)
4. Report final results

Expected runtime: ~12–15 minutes for 500 steps on 1 node (8x H200).

### Option C: Monitor an Existing Job

If you already have a running/completed job:

```bash
python3 vla_training_agent.py --monitor-job <JOB_ID> --check-interval 10 --max-checks 5
```

## Configuration

All settings are read from environment variables (or `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `CLUSTER_HOST` | *(required)* | Cluster login node hostname |
| `CLUSTER_USER` | *(required)* | SSH username |
| `SSH_KEY_PATH` | `~/.ssh/id_rsa` | Path to SSH private key |
| `VLA_HOME` | `/fsx/$USER/vla` | Base workspace on FSx |
| `MAX_STEPS` | `500` | Training steps |
| `LEARNING_RATE` | `5e-4` | Initial learning rate |
| `BATCH_SIZE` | `16` | Per-device batch size |
| `LORA_RANK` | `32` | LoRA adapter rank |

## Production Deployment with AgentCore

In production, this same MCP toolset can be hosted on Amazon Bedrock AgentCore Runtime:

- **AgentCore Gateway** exposes the Slurm MCP tools with authentication, rate limiting, and audit logging
- **AgentCore Memory** stores experiment history across sessions
- **AgentCore Policy** enforces cost guardrails (max GPU hours, budget caps)
- **AgentCore Observability** logs all agent decisions for review

The local agent (`vla_training_agent.py`) demonstrates the orchestration logic; in production, an LLM model on Bedrock invokes the same tools through the Gateway.

## Security Notes

- SSH uses `StrictHostKeyChecking=no` for demo convenience. In production, pin host keys or use a jump host with managed credentials.
- Job IDs are validated (digits only) to prevent shell injection through the MCP tools.
- The `.env` file contains credentials — never commit it to version control.

## References

- [Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore.html)
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
- [OpenVLA](https://github.com/openvla/openvla) — Vision-Language-Action model
- [LIBERO](https://libero-project.github.io/) — Robotic manipulation benchmark
- [SageMaker HyperPod](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod.html)
- [SRE Agent (sibling sample)](../SRE-agent/) — Similar architecture for infrastructure operations
