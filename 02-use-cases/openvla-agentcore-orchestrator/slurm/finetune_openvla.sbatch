#!/bin/bash
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
# OpenVLA LoRA Fine-Tuning on LIBERO (1 node, 8x GPUs)
# Tested on: HyperPod P5en (8x H200), ~10 min for 500 steps
#
# Prerequisites:
#   1. Build container: docker build -t openvla-finetune -f openvla.Dockerfile .
#   2. Import: enroot import -o $VLA_HOME/openvla-finetune.sqsh dockerd://openvla-finetune:latest
#   3. Download model + data (see README)
#
# Usage:
#   export VLA_HOME=/fsx/$USER/vla
#   cd $VLA_HOME        # logs are written to $VLA_HOME/logs/ relative to cwd
#   sbatch finetune_openvla.sbatch

#SBATCH --job-name=openvla-finetune
#SBATCH --partition=p5en
#SBATCH --nodes=1
#SBATCH --ntasks-per-node=1
#SBATCH --gres=gpu:8
#SBATCH --mem=0
#SBATCH --time=01:55:00
#SBATCH --exclusive
#SBATCH --output=logs/%j.out
#SBATCH --error=logs/%j.err

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — set VLA_HOME or edit paths below
# ---------------------------------------------------------------------------
VLA_HOME="${VLA_HOME:-/fsx/$USER/vla}"

MODEL_DIR="${VLA_HOME}/models/openvla-7b"
DATA_DIR="${VLA_HOME}/data/libero_rlds"
CKPT_DIR="${VLA_HOME}/checkpoints/run_${SLURM_JOB_ID}"
CONTAINER_IMAGE="${VLA_HOME}/openvla-finetune.sqsh"

# Training hyperparameters (override via environment before sbatch)
DATASET_NAME="${DATASET_NAME:-libero_10_no_noops}"
MAX_STEPS="${MAX_STEPS:-500}"
SAVE_STEPS="${SAVE_STEPS:-500}"
LEARNING_RATE="${LEARNING_RATE:-5e-4}"
BATCH_SIZE="${BATCH_SIZE:-16}"
LORA_RANK="${LORA_RANK:-32}"
WANDB_MODE="${WANDB_MODE:-disabled}"

GPUS_PER_NODE=8

# ---------------------------------------------------------------------------
# Environment (EFA + NCCL for multi-node readiness)
# ---------------------------------------------------------------------------
export NCCL_DEBUG=INFO
export FI_PROVIDER=efa
export FI_EFA_SET_CUDA_SYNC_MEMOPS=0
export NCCL_SOCKET_IFNAME=^lo,docker0
export TORCHDYNAMO_DISABLE=1

# ---------------------------------------------------------------------------
# Container mounts
# ---------------------------------------------------------------------------
declare -a SRUN_ARGS=(
    --container-image "${CONTAINER_IMAGE}"
    --container-mounts "/fsx:/fsx"
)

# ---------------------------------------------------------------------------
# Torchrun args
# For multi-node, replace --standalone with:
#   --nnodes=$SLURM_JOB_NUM_NODES --rdzv_id=$SLURM_JOB_ID
#   --rdzv_backend=c10d --rdzv_endpoint=$SLURMD_NODENAME:29500
# ---------------------------------------------------------------------------
declare -a TORCHRUN_ARGS=(
    --standalone
    --nnodes=1
    --nproc_per_node=${GPUS_PER_NODE}
)

# ---------------------------------------------------------------------------
# Training args
# ---------------------------------------------------------------------------
declare -a TRAINING_ARGS=(
    --vla_path "${MODEL_DIR}"
    --data_root_dir "${DATA_DIR}"
    --dataset_name "${DATASET_NAME}"
    --run_root_dir "${CKPT_DIR}"
    --adapter_tmp_dir "${CKPT_DIR}/adapter_tmp"
    --lora_rank "${LORA_RANK}"
    --batch_size "${BATCH_SIZE}"
    --grad_accumulation_steps 1
    --learning_rate "${LEARNING_RATE}"
    --max_steps "${MAX_STEPS}"
    --save_steps "${SAVE_STEPS}"
    --image_aug true
    --wandb_project openvla-finetune
)

# Only pass --wandb_entity if set (avoids passing empty string)
if [ -n "${WANDB_ENTITY:-}" ]; then
    TRAINING_ARGS+=(--wandb_entity "${WANDB_ENTITY}")
fi

# ---------------------------------------------------------------------------
# Create output dirs
# ---------------------------------------------------------------------------
mkdir -p "${CKPT_DIR}" "logs"

echo "========================================="
echo "Job ID: ${SLURM_JOB_ID}"
echo "Container: ${CONTAINER_IMAGE}"
echo "Model: ${MODEL_DIR}"
echo "Data: ${DATA_DIR}"
echo "Output: ${CKPT_DIR}"
echo "========================================="

# ---------------------------------------------------------------------------
# HyperPod auto-resume
# ---------------------------------------------------------------------------
declare -a AUTO_RESUME_ARGS=()
if [ -d "/opt/sagemaker_cluster" ]; then
    echo "Detected HyperPod cluster — enabling auto-resume"
    AUTO_RESUME_ARGS=("--auto-resume=1")
fi

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
export WANDB_MODE
srun "${AUTO_RESUME_ARGS[@]}" -l "${SRUN_ARGS[@]}" \
    torchrun "${TORCHRUN_ARGS[@]}" \
    /openvla/vla-scripts/finetune.py "${TRAINING_ARGS[@]}"

echo "========================================="
echo "Fine-tuning complete!"
echo "Checkpoints at: ${CKPT_DIR}"
echo "========================================="
