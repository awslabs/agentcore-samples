# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0
#
# OpenVLA fine-tuning container for HyperPod Slurm (P5/P5en)
# Build:
#   docker build -t openvla-finetune -f openvla.Dockerfile .
# Import for Pyxis/Enroot:
#   enroot import -o /fsx/$USER/openvla-finetune.sqsh dockerd://openvla-finetune:latest

# Base image: AWS HPC container with CUDA, EFA, NCCL, and aws-ofi-nccl pre-installed.
# Same base used by sibling test cases (nanoVLM). Provides:
#   CUDA 12.8.1, NCCL 2.27.7, EFA 1.43.2 (libfabric), aws-ofi-nccl 1.16.3
# Note: No published tag meets the CI floor (NCCL>=2.28, CUDA>=13.0) yet.
# PyTorch wheels bundle their own CUDA runtime (cu124), so training itself is unaffected.
FROM public.ecr.aws/hpc-cloud/nccl-tests:cuda12.8.1-efa1.43.2-ofiv1.16.3-ncclv2.27.7-1-testsv2.16.9

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        git-lfs \
        nvtop \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Python training dependencies — pinned to the exact versions validated on
# HyperPod P5en (8x H200), job 5631, ~10 min for 500 LoRA steps.
# ---------------------------------------------------------------------------
RUN pip install --no-cache-dir \
        torch==2.6.0 \
        torchvision==0.21.0 \
        transformers==4.44.2 \
        peft==0.13.2 \
        accelerate==1.2.1 \
        "datasets>=2.14.0,<3.0.0" \
        tensorflow-datasets==4.9.3 \
        "tensorflow>=2.15.0,<2.18.0" \
        "tensorflow-graphics==2021.12.3" \
        "huggingface-hub>=0.20.0,<1.0.0" \
        "wandb>=0.16.0,<1.0.0" \
        "pillow>=10.0.0,<11.0.0" \
        "scipy>=1.11.0,<2.0.0" \
        "einops>=0.7.0,<1.0.0" \
        timm==0.9.10 \
        draccus==0.8.0 \
        sentencepiece==0.1.99 \
        tokenizers==0.19.1

# dlimp (data loading for RLDS) — no-deps to avoid pulling conflicting versions
RUN pip install --no-cache-dir --no-deps \
        git+https://github.com/kvablack/dlimp.git@5edaa4691567873d495633f2708982b42edf1972

# dlimp's fork used by OpenVLA for RLDS dataset loading
RUN pip install --no-cache-dir --no-deps \
        git+https://github.com/moojink/dlimp_openvla.git@040105d256bd28866cc6620621a3d5f7b6b91b46

# ---------------------------------------------------------------------------
# Clone OpenVLA and install in editable mode WITHOUT dependencies.
# This ensures our explicit pins above are not overwritten by OpenVLA's
# pyproject.toml (which hard-pins torch==2.2.0, transformers==4.40.1, etc.).
# ---------------------------------------------------------------------------
ARG OPENVLA_COMMIT=c8f03f48af692657d3060c19588038c7220e9af9
RUN git clone https://github.com/openvla/openvla.git /openvla \
    && cd /openvla && git checkout ${OPENVLA_COMMIT}

RUN cd /openvla && pip install --no-cache-dir --no-deps -e .

# Symlink python for convenience
RUN ln -sf /usr/bin/python3 /usr/bin/python

WORKDIR /openvla
